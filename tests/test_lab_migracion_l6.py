"""LOTE L6 — Migrar Lab a Estrategias (mudanza, NO rediseño).

El Lab es GLOBAL (herramienta con selector instrument/strategy). Se conserva
global y la sub-pestaña Lab del detalle lo EMBEBE apuntado a la estrategia
(criterio del arquitecto: no inventar scope por-estrategia). Cero regresiones:
/ui/lab, sus endpoints y la entrada del nav quedan intactos. Banner de Parte C
(filtros/régimen dormidos; lógica viva conservada) en el Lab.
"""
import pytest
from httpx import AsyncClient

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.strategy import Strategy


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_l6")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


# ---------------------------------------------------------------------------
# Nueva casa: la sub-pestaña Lab embebe el Lab global apuntado a la estrategia
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lab_embebido_en_subpestana(client: AsyncClient, db) -> None:
    db.add(Strategy(strategy_id="ES5m_Lab", name="Lab", asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    r = await client.get("/ui/strategies/ES5m_Lab")
    assert r.status_code == 200
    html = r.text
    # embebe el Lab GLOBAL apuntado a esta estrategia (mudanza, no rediseño)
    assert '<iframe' in html and 'src="/ui/lab?strategy=ES5m_Lab"' in html
    assert "abrir en pestaña completa" in html


# ---------------------------------------------------------------------------
# Cero regresiones: el Lab global + su nav + endpoints intactos
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lab_global_sigue_vivo(client: AsyncClient) -> None:
    r = await client.get("/ui/lab?instrument=ES")
    assert r.status_code == 200                       # la casa global no se movió
    # L7b — el Lab sale del nav (decisión del operador): su acceso canónico es la
    # sub-pestaña Lab del detalle; /ui/lab sigue vivo (iframe L6 + bookmark).
    assert ">Lab<" not in r.text


@pytest.mark.asyncio
async def test_banner_parte_c_en_lab(client: AsyncClient) -> None:
    r = await client.get("/ui/lab?instrument=ES")
    assert r.status_code == 200
    html = r.text
    assert "dormidos en producción" in html
    assert "quality_scorer.py" in html and "hmm_service.py" in html
    assert "el Lab los importa" in html


def test_servicios_conservados_los_importa_el_lab():
    # Parte C: los servicios NO se arrancan pero se CONSERVAN — el Lab los usa.
    from app.services.quality_scorer import QualityScorer          # noqa: F401
    from app.services.hmm_service import HMMService                # noqa: F401
    import scripts.lab_analyze as la
    src = __import__("inspect").getsource(la)
    assert "quality_scorer" in src or "hmm" in src.lower()         # el Lab los importa
