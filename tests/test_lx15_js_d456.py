"""LX-15-JS bug-fix 2 — D4 (Aplicar tras Calcular), D5 (C1 visible en Config), D6.

D5 se testea de verdad (render del detalle: la profundidad de C1 aplicada aparece,
read-only). D4/D6 son cambios de template (Alpine/Jinja) → tests de presencia del
wiring. La lógica pura validado/dirty (D1/D2) sigue cubierta por test_lx15_js_state.
"""
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile

_TPL = Path("app/templates/strategy_detail.html").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_d456")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _seed(db: AsyncSession, sid: str, scale_entry: dict | None) -> None:
    db.add(AssetProfile(symbol="MES", name="Micro S&P",
                        contract_type="futures_micro", active=True,
                        session_config_json={"timezone": "America/New_York",
                                             "days_enabled": [1, 2, 3, 4, 5],
                                             "entry_start": "09:30",
                                             "entry_end": "15:45",
                                             "next_day_end": False},
                        sl_atr_multiplier=2.0))
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="MES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=sid,
                           pipeline_config_json={"scale_entry": scale_entry}
                           if scale_entry else {}))
    await db.commit()


# ---------------------------------------------------------------------------
# D5 — la profundidad de C1 aplicada es VISIBLE (read-only) en el form de Config
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_d5_c1_depth_visible_en_config(client: AsyncClient, db: AsyncSession):
    await _seed(db, "C1vis", {"mode": "design_only", "levels": [1.64, 3.28],
                              "quantities": [5, 3, 2], "c1_depth_atr": 0.5})
    r = await client.get("/ui/strategies/C1vis")
    assert r.status_code == 200, r.text
    html = r.text
    assert "C1 profundidad" in html                 # palanca aplicada ⇒ visible
    assert "0.5×ATR" in html                         # el valor aplicado
    assert "se aplica desde Luxy" in html            # read-only, sin ruta sin gate


@pytest.mark.asyncio
async def test_d5_c1_market_muestra_mercado(client: AsyncClient, db: AsyncSession):
    await _seed(db, "C1mkt", {"mode": "design_only", "levels": [1.64, 3.28],
                              "quantities": [5, 3, 2]})     # sin c1_depth_atr
    r = await client.get("/ui/strategies/C1mkt")
    assert r.status_code == 200, r.text
    assert "C1 profundidad" in r.text
    assert "0 = mercado" in r.text                   # 0 = mercado (no hay línea/límite)


# ---------------------------------------------------------------------------
# D4 — Calcular marca "fresh"; el init omite toda restauración → baseline validado
# ---------------------------------------------------------------------------

def test_d4_calcular_marca_fresh_y_init_omite_restore():
    # el job Calcular (done) setea la marca en sessionStorage antes de recargar
    assert "sessionStorage.setItem('ntexecg_luxy_fresh_'+this.sid,'1')" in _TPL
    # el init la consume y OMITE local + server (arranca del baseline)
    assert "sessionStorage.getItem('ntexecg_luxy_fresh_'+SID)==='1'" in _TPL
    assert "_fresh ? false : restoreExplore()" in _TPL
    assert "loadServer(!_local && !_fresh)" in _TPL


# ---------------------------------------------------------------------------
# D6 — el badge de Config es del estudio Riesgo v1 (etiquetado), no del Luxy
# ---------------------------------------------------------------------------

def test_d6_badge_config_etiquetado_motor_riesgo():
    # el badge de deriva de Config ya nombra su familia de estudio (Motor de Riesgo,
    # familia distinta del Luxy) — no es un glob/estudio equivocado.
    assert "Estudio del Motor de Riesgo:" in _TPL
