"""LOTE L7b — retiro NO destructivo de Riesgo v1 + Lab fuera del nav (patrón P3).

Invariantes del lote:
  1. /ui/riesgo redirige (con y sin ?strategy); NO renderiza la página v1.
  2. El nav no trae Riesgo ni Lab; /ui/lab sigue vivo (iframe L6 + bookmark).
  3. NADA de lógica muere: los helpers que L5/L7a y las fichas del detalle reusan
     siguen vivos en routes_riesgo (criterio 2). El 'aplicar desde Luxy' e2e lo
     cubre test_luxy_aplicar_l5.py; las fichas R-obs-2 del detalle (rango/ventana
     por lado, cuenta, protección) las cubren test_estrategias_l1
     ::test_luxy_ventana_paridad_v1_real y test_perfiles_l4.py / test_robs2.py.
  4. Rollback trivial: `git revert` del commit restaura la página v1 (la
     plantilla riesgo.html y la lógica de contexto siguen en el repo).
"""
import pytest
from httpx import AsyncClient

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_l7b")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


# --- 1. redirect (P3) --------------------------------------------------------

@pytest.mark.asyncio
async def test_riesgo_redirige_sin_strategy(client: AsyncClient) -> None:
    r = await client.get("/ui/riesgo")
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/strategies"


@pytest.mark.asyncio
async def test_riesgo_redirige_con_strategy_al_detalle(client: AsyncClient) -> None:
    r = await client.get("/ui/riesgo?strategy=ES5m_Foo")
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/strategies/ES5m_Foo"


@pytest.mark.asyncio
async def test_riesgo_strategy_basura_no_rompe(client: AsyncClient) -> None:
    # un `strategy` que no matchea la forma de clave → al índice, sin 500
    r = await client.get("/ui/riesgo?strategy=../../etc/passwd")
    assert r.status_code == 302
    assert r.headers["location"] == "/ui/strategies"


# --- 2. nav sin Riesgo ni Lab; /ui/lab vivo ---------------------------------

@pytest.mark.asyncio
async def test_nav_sin_riesgo_ni_lab(client: AsyncClient) -> None:
    html = (await client.get("/ui/strategies")).text
    assert ">Riesgo<" not in html
    assert ">Lab<" not in html
    # el resto del nav sigue
    for entrada in (">Dashboard<", ">Estrategias<", ">Posiciones<",
                    ">Portafolio<", ">Activos<", ">Settings<"):
        assert entrada in html, entrada


@pytest.mark.asyncio
async def test_lab_directo_sigue_200(client: AsyncClient) -> None:
    # el iframe de L6 y los bookmarks dependen de que /ui/lab siga respondiendo
    r = await client.get("/ui/lab")
    assert r.status_code == 200


# --- 3. criterio 2 — nada de lógica muere -----------------------------------

def test_helpers_del_motor_sobreviven() -> None:
    """Los helpers que reusan L5 (Puente), L7a (ventana) y las fichas del
    detalle siguen vivos e importables en routes_riesgo."""
    import app.web.routes_riesgo as rr
    for nombre in ("deriva_estudio", "_merge_activacion", "_diff_aplicar",
                   "_activacion_json", "_leer_cuenta", "_pct_trades_fuera",
                   "clave_de", "_motor_manifest", "holc_disponible",
                   "integrar_lista", "_latest_estudio"):
        assert callable(getattr(rr, nombre)), nombre


@pytest.mark.asyncio
async def test_endpoints_del_puente_y_gestion_vivos(client: AsyncClient) -> None:
    """Los endpoints operativos del motor NO se retiraron (P3: solo la página).
    Sin datos devuelven 4xx (no 404 de ruta inexistente)."""
    # el Puente que Luxy/Estrategias reusa
    r = await client.get("/ui/riesgo/aplicar/preview?strategy=NoExiste")
    assert r.status_code != 404
    # cuenta / heatmap / reporte siguen registrados
    r = await client.post("/ui/riesgo/cuenta", json={"cuenta_usd": 0})
    assert r.status_code != 404
    r = await client.get("/ui/riesgo/heatmap?strategy=NoExiste")
    assert r.status_code != 404
