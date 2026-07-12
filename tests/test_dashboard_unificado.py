"""P2 (auditoría 2026-07-06) — Dashboard + Analítica unificados en /ui.

Candados verificados:
  - la página unificada renderiza con el selector de rango {hoy,7,14,30,90}
    y los charts de la vieja Analítica (+ KPIs, bridge, decisiones, tabla
    por estrategia);
  - /ui/analytics redirige (301) a la unificada preservando days — cero
    bookmarks/links rotos;
  - los partials HTMX siguen siendo load-bearing app-wide: base.html:72
    consume /ui/partials/bridge-badge en el navbar de TODA la app (se
    verifica en otra página, /ui/riesgo) y el endpoint sigue sirviendo;
  - cero referencias a /ui/analytics en los templates (solo el redirect
    del router la conoce).
"""
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.webhook_delivery import WebhookDelivery

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_dash")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _decision(db: AsyncSession, sid: str, outcome: str = "APPROVE",
                    block_reason: str | None = None,
                    block_level: int | None = None) -> None:
    raw = RawSignal(source="luxalgo", strategy_id=sid, payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, strategy_id=sid, ticker_received="MES",
        action="buy", sentiment="long", signal_ts=datetime.now(UTC),
        dedupe_key=uuid.uuid4().hex,
    )
    db.add(norm)
    await db.flush()
    dec = StrategyDecision(normalized_signal_id=norm.id, strategy_id=sid,
                           outcome=outcome, score=100,
                           block_reason=block_reason,
                           block_level=block_level)
    db.add(dec)
    await db.flush()
    return dec


@pytest.mark.asyncio
async def test_unificada_renderiza_selector_y_charts(
    client: AsyncClient, db: AsyncSession
) -> None:
    """La página unificada: selector de rango, KPIs del rango, charts de la
    vieja Analítica, fila operacional (bridge/entregas) y las dos tablas."""
    dec = await _decision(db, "ES5m_Uni")
    await _decision(db, "ES5m_Uni", outcome="BLOCK",
                    block_reason="score_below_minimum", block_level=4)
    db.add(WebhookDelivery(decision_id=dec.id, strategy_id="ES5m_Uni",
                           payload_json={}, status="SENT"))
    await db.commit()

    r = await client.get("/ui")                       # default: hoy
    assert r.status_code == 200
    html = r.text
    # selector de rango completo
    for etiqueta in (">hoy<", ">7d<", ">14d<", ">30d<", ">90d<"):
        assert etiqueta in html, etiqueta
    # KPIs del rango (recibidas/decisiones/aprobadas/bloqueadas/tasa/TP)
    for kpi in ("Recibidas", "Decisiones", "Aprobadas", "Bloqueadas",
                "Tasa aprobación", "Enviadas TP", "Fallidas"):
        assert kpi in html, kpi
    # charts de la vieja Analítica
    assert "chartOutcomes" in html
    assert "chartLevels" in html
    assert "chartReasons" in html
    assert "score_below_minimum" in html              # motivo en el blob
    # en "hoy" el flujo diario se omite (serie de 1 punto no dice nada) —
    # el canvas no se renderiza (el JS queda guardado por DATA.timeseries)
    assert 'id="chartTimeseries"' not in html
    # fila operacional del viejo Dashboard sigue
    assert "/ui/partials/bridge-status" in html
    assert "/ui/partials/delivery-alerts" in html
    assert "Estrategias activas" in html
    assert "Decisiones recientes" in html
    assert "Por estrategia" in html

    # rango multi-día → la serie diaria aparece
    r = await client.get("/ui?days=7")
    assert r.status_code == 200
    assert 'id="chartTimeseries"' in r.text
    assert "los últimos 7 días" in r.text

    # days fuera del selector → cae a hoy (sin 500)
    r = await client.get("/ui?days=13")
    assert r.status_code == 200
    assert "de hoy" in r.text


@pytest.mark.asyncio
async def test_analytics_redirige_a_unificada(client: AsyncClient) -> None:
    """Cero links rotos: /ui/analytics (con o sin days) redirige permanente
    a la página unificada preservando la ventana."""
    r = await client.get("/ui/analytics")
    assert r.status_code == 301
    assert r.headers["location"] == "/ui?days=14"     # su default viejo
    r = await client.get("/ui/analytics?days=30")
    assert r.status_code == 301
    assert r.headers["location"] == "/ui?days=30"
    # y el destino responde
    r = await client.get("/ui/analytics", follow_redirects=True)
    assert r.status_code == 200
    assert "Dashboard" in r.text


@pytest.mark.asyncio
async def test_partials_siguen_sirviendo_app_wide(client: AsyncClient) -> None:
    """Los partials son load-bearing app-wide (base.html:72): el navbar de
    OTRA página (Posiciones) sigue pidiendo bridge-badge y el endpoint sirve.
    (L7b — antes se usaba /ui/riesgo, hoy redirige; Posiciones sirve igual.)"""
    r = await client.get("/ui/positions")
    assert r.status_code == 200
    assert 'hx-get="/ui/partials/bridge-badge"' in r.text
    for partial in ("/ui/partials/bridge-badge", "/ui/partials/bridge-status",
                    "/ui/partials/recent-signals",
                    "/ui/partials/delivery-alerts"):
        r = await client.get(partial)
        assert r.status_code == 200, partial


def test_cero_links_a_analytics_en_templates() -> None:
    """La pestaña removida no deja links rotos: ningún template referencia
    /ui/analytics (solo el redirect del router la conoce)."""
    templates = Path("app/templates")
    culpables = [p.name for p in templates.rglob("*.html")
                 if "/ui/analytics" in p.read_text(encoding="utf-8")]
    assert culpables == [], f"links a la pestaña removida en: {culpables}"
    assert not (templates / "analytics.html").exists()
