"""DASH-2 — fila de vigilancia de la demo en /ui (READ-ONLY).

Cuatro widgets nuevos, cada uno con su caso poblado y su caso vacío:
  - Posiciones abiertas: cuenta + lista (mismo universo que /ui/positions,
    solo las no-FLAT) + link a la pestaña.
  - Últimas entregas con bracket: SENT/FAILED del rango con stopLoss.stopPrice
    y takeProfit.limitPrice del payload persistido (o '—' si no viajan).
  - Badge de deriva global: contador aplicadas/difieren/sin_aplicar/sin
    estrategia viva computado con deriva_estudio + _activacion_json (cacheado).
  - Kill-switch por capas: chips env/global/armadas (solo lectura).
"""
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.decision import StrategyDecision
from app.models.global_profile import GlobalProfile
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.webhook_delivery import WebhookDelivery
import app.web.routes_dashboard as routes_dashboard

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_vig")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture(autouse=True)
def _reset_deriva_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """El badge global cachea en memoria (TTL 60s) con un dict de módulo — se
    resetea entre tests para que cada uno vea su propio manifest. Por defecto
    el manifest se stubbea vacío (tests herméticos, sin leer disco real); los
    tests de deriva lo re-monkeypatchean con su propio manifest."""
    routes_dashboard._DERIVA_CACHE.update(ts=0.0, value=None)
    monkeypatch.setattr("app.web.manifest_store.load_manifest", dict)


def _position(state="LONG", symbol="MESU2026", qty=1,
              risk_plan_json=None) -> PositionState:
    direction = ("long" if "LONG" in state else
                 "short" if "SHORT" in state else None)
    return PositionState(
        strategy_id="fx", account_id="paper_default", symbol=symbol,
        state=state, state_source="estimated", direction=direction, quantity=qty,
        risk_plan_json=risk_plan_json,
    )


async def _delivery(db: AsyncSession, sid: str, status: str, payload: dict) -> None:
    """Una delivery cuelga de una decisión (FK NOT NULL) — se crea la mínima."""
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
                           outcome="APPROVE", score=100)
    db.add(dec)
    await db.flush()
    db.add(WebhookDelivery(decision_id=dec.id, strategy_id=sid,
                           payload_json=payload, status=status))


# ── Posiciones abiertas ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_posiciones_abiertas_con_datos(client: AsyncClient, db: AsyncSession) -> None:
    db.add(_position("LONG", symbol="MESU2026", qty=2))
    db.add(_position("PENDING_SHORT", symbol="MNQU2026", qty=1))
    db.add(_position("FLAT", symbol="MJYU2026", qty=0))   # FLAT NO cuenta
    await db.commit()

    html = (await client.get("/ui")).text
    assert "Posiciones abiertas" in html
    assert 'href="/ui/positions"' in html
    assert "MESU2026" in html and "MNQU2026" in html
    assert "MJYU2026" not in html                          # la FLAT se excluye
    # cuenta = 2 abiertas (el bold junto al título)
    assert 'text-white font-bold">2</span>' in html


@pytest.mark.asyncio
async def test_posiciones_abiertas_vacio(client: AsyncClient, db: AsyncSession) -> None:
    await db.commit()
    html = (await client.get("/ui")).text
    assert "Sin posiciones abiertas." in html


@pytest.mark.asyncio
async def test_posicion_since_usa_opened_at(
    client: AsyncClient, db: AsyncSession
) -> None:
    """'Abierta desde' FIEL: con opened_at en risk_plan_json se muestra ESE
    timestamp (no updated_at) y se rotula 'desde'."""
    opened = datetime(2026, 7, 1, 13, 37, tzinfo=UTC)
    db.add(_position("LONG", symbol="MESU2026", qty=1,
                     risk_plan_json={"opened_at": opened.isoformat()}))
    await db.commit()

    html = (await client.get("/ui")).text
    # el opened_at real (07-01 13:37), NO la fecha de hoy (updated_at)
    assert "07-01 13:37" in html
    assert "desde" in html


@pytest.mark.asyncio
async def test_posicion_since_fallback_updated_at(
    client: AsyncClient, db: AsyncSession
) -> None:
    """Sin opened_at → cae a updated_at, rotulado honestamente 'actualizado'
    (no 'desde')."""
    db.add(_position("LONG", symbol="MESU2026", qty=1, risk_plan_json=None))
    await db.commit()

    html = (await client.get("/ui")).text
    assert "actualizado" in html


@pytest.mark.asyncio
async def test_live_card_rotula_solo_paper(
    client: AsyncClient, db: AsyncSession
) -> None:
    """La tarjeta 'Live' (invariante 0 — solo paper/demo) se rotula como
    confirmación de seguridad, no como un cero ambiguo."""
    await db.commit()
    html = (await client.get("/ui")).text
    assert "solo paper" in html


# ── Últimas entregas con bracket ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_entregas_con_bracket_con_datos(client: AsyncClient, db: AsyncSession) -> None:
    await _delivery(db, "ESbrk", "SENT", {
        "action": "buy",
        "stopLoss": {"type": "stop", "stopPrice": 5123.5},
        "takeProfit": {"type": "limit", "limitPrice": 5140.0},
    })
    await _delivery(db, "ESbrk", "FAILED", {"action": "exit"})  # sin bracket → —
    await db.commit()

    html = (await client.get("/ui")).text
    assert "Últimas entregas" in html
    assert "5123.5" in html and "5140.0" in html          # bracket a la vista
    assert "ESbrk" in html


@pytest.mark.asyncio
async def test_entregas_vacio(client: AsyncClient, db: AsyncSession) -> None:
    await db.commit()
    html = (await client.get("/ui")).text
    assert "Sin entregas en el rango." in html


@pytest.mark.asyncio
async def test_entregas_solo_sent_failed_del_rango(
    client: AsyncClient, db: AsyncSession
) -> None:
    """DRY_RUN no es entrega real; y fuera del rango (hoy) no aparece."""
    await _delivery(db, "ESdry", "DRY_RUN", {"action": "buy"})
    await db.commit()
    html = (await client.get("/ui")).text
    assert "Sin entregas en el rango." in html


# ── Badge de deriva global ───────────────────────────────────────────────

def _fake_estudio(_clave: str) -> dict:
    # act = _activacion_json → {"backstop_points": 100}
    return {"recomendacion": {"backstop": {"pts": 100}}, "_fecha": "2026-07-08"}


def _fake_manifest() -> dict:
    return {sid: {"instrument": "ES"}
            for sid in ("aplicES", "difiES", "sinapES", "novivaES")}


async def _viva(db: AsyncSession, sid: str, pcfg: dict | None = None) -> None:
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    if pcfg is not None:
        db.add(StrategyProfile(strategy_id=sid, mode="paper",
                               pipeline_config_json=pcfg))


@pytest.mark.asyncio
async def test_deriva_global_cuatro_estados(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.web.manifest_store.load_manifest", _fake_manifest)
    monkeypatch.setattr("app.web.routes_riesgo._latest_estudio", _fake_estudio)

    await _viva(db, "aplicES", {"backstop_points": 100})   # == act → aplicada
    await _viva(db, "difiES", {"backstop_points": 999})    # presente≠ → difiere
    await _viva(db, "sinapES", None)                       # sin config → sin_aplicar
    # novivaES: en el manifest pero SIN estrategia viva → sin_estrategia_viva
    await db.commit()

    html = (await client.get("/ui")).text
    assert "1</span> aplicadas" in html
    assert "1</span> difieren" in html
    assert "1</span> sin aplicar" in html
    assert "1</span> sin estrategia viva" in html


@pytest.mark.asyncio
async def test_deriva_global_vacio(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.web.manifest_store.load_manifest", dict)
    await db.commit()
    html = (await client.get("/ui")).text
    assert "0</span> aplicadas" in html
    assert "0</span> difieren" in html
    assert "0</span> sin aplicar" in html
    assert "sin estrategia viva" not in html               # 0 → chip oculto


# ── Kill-switch por capas ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_kill_switch_todo_desarmado(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "TRADERSPOST_ENABLED", False)
    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True,
                         traderspost_enabled=False, dry_run=True))
    await db.commit()

    html = (await client.get("/ui")).text
    assert "env ✗" in html
    assert "global ✗" in html
    assert "armadas 0/0" in html


@pytest.mark.asyncio
async def test_kill_switch_capas_mixtas(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """env armado, global armado, y 1 de 2 estrategias armada."""
    monkeypatch.setattr(settings, "TRADERSPOST_ENABLED", True)
    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True,
                         traderspost_enabled=True, dry_run=False))
    db.add(Strategy(strategy_id="armed", name="armed", asset_symbol="MES",
                    timeframe="5m", status="live", enabled=True))
    db.add(Strategy(strategy_id="safe", name="safe", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="armed", mode="live",
                           traderspost_enabled=True, dry_run=False))  # armada
    db.add(StrategyProfile(strategy_id="safe", mode="paper",
                           traderspost_enabled=True, dry_run=True))   # dry → NO
    await db.commit()

    html = (await client.get("/ui")).text
    assert "env ✓" in html
    assert "global ✓" in html
    assert "armadas 1/2" in html
