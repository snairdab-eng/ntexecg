"""LX-15-JS bug-fix 3 — guarda anti-regresión de HTML malformado.

El bug (bugfix2): un comentario con comillas DOBLES ("fresh") dentro del atributo
x-data="…" del botón «Calcular estudio» cerró el atributo antes de tiempo → el
resto del handler se derramó como TEXTO visible y el botón desapareció.
node --check (JS) y Jinja parse NO lo detectan (JS/Jinja válidos ≠ HTML válido).

Esta guarda RENDERIZA el detalle real y verifica con BeautifulSoup:
  a) el texto VISIBLE (sin <script>) no contiene fragmentos de código;
  b) el botón «Calcular estudio» existe (su x-text lo nombra) y su handler x-data
     está en el ATRIBUTO (no derramado como texto);
  c) el marcador exacto de la regresión no aparece.
"""
import pytest
from bs4 import BeautifulSoup
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile

# Fragmentos del handler JS que JAMÁS deben ser TEXTO visible (si aparecen, un
# atributo se rompió y el JS se derramó — la regresión de bugfix2).
_CODE_LEAKS = ("this.err", "=>{", "r.json()", "sessionStorage.setItem",
               "location.reload", "this.busy")


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_reg3")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


async def _seed(db: AsyncSession) -> None:
    db.add(AssetProfile(symbol="MES", name="Micro S&P",
                        contract_type="futures_micro", active=True,
                        session_config_json={"timezone": "America/New_York",
                                             "days_enabled": [1, 2, 3, 4, 5],
                                             "entry_start": "09:30",
                                             "entry_end": "15:45",
                                             "next_day_end": False},
                        sl_atr_multiplier=2.0))
    db.add(Strategy(strategy_id="RegHtml", name="RegHtml", asset_symbol="MES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="RegHtml",
                           pipeline_config_json={"scale_entry": {
                               "mode": "design_only", "levels": [1.64, 3.28],
                               "quantities": [5, 3, 2], "c1_depth_atr": 0.5}}))
    await db.commit()


def _visible_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.extract()
    return soup.get_text()


@pytest.mark.asyncio
async def test_detalle_no_derrama_codigo_y_boton_calcular_presente(
        client: AsyncClient, db: AsyncSession):
    await _seed(db)
    r = await client.get("/ui/strategies/RegHtml")
    assert r.status_code == 200, r.text

    # (a) el JS del handler NO se derrama como texto visible
    visible = _visible_text(r.text)
    for frag in _CODE_LEAKS:
        assert frag not in visible, (
            f"fragmento {frag!r} visible como texto → atributo x-data roto")

    # (b) el botón «Calcular estudio» existe con su handler en el ATRIBUTO x-data
    soup = BeautifulSoup(r.text, "html.parser")
    calc_btns = [b for b in soup.find_all("button")
                 if "Calcular estudio" in (b.get("x-text") or "")]
    assert calc_btns, "botón «Calcular estudio» ausente o su x-text se rompió"
    # el contenedor con el handler poll()/calcular() debe conservar el JS en x-data
    luxy_div = soup.find(lambda t: t.name == "div" and t.has_attr("x-data")
                         and "calcular()" in (t.get("x-data") or ""))
    assert luxy_div is not None, "el x-data del Calcular se derramó (no está en atributo)"
    assert "sessionStorage.setItem" in luxy_div.get("x-data")   # el fix D4 vive ahí
    # NOTA: la cola «this.err=String(e);}); } }"> » vive en el ATRIBUTO x-data también
    # en el HTML correcto — por eso el guardián es (a): que NO sea texto VISIBLE.


@pytest.mark.asyncio
async def test_d4_marca_fresh_intacta_tras_el_fix(
        client: AsyncClient, db: AsyncSession):
    await _seed(db)
    r = await client.get("/ui/strategies/RegHtml")
    assert r.status_code == 200
    soup = BeautifulSoup(r.text, "html.parser")
    luxy_div = soup.find(lambda t: t.name == "div" and t.has_attr("x-data")
                         and "calcular()" in (t.get("x-data") or ""))
    # D4 vivo: la marca fresh se SETEA en el handler done (dentro del x-data)
    assert luxy_div is not None
    assert "ntexecg_luxy_fresh_" in luxy_div.get("x-data")
