"""LX-10 — snapshot server-side de la exploración Luxy (botón explícito).

Almacén PROPIO (tabla luxy_exploracion), JAMÁS pipeline_config_json: guardar/
borrar aquí es diagnóstico, no config de producción. Endpoints GET/PUT/DELETE en
el router protegido. El front descarta el snapshot si el estudio_id no
corresponde al vigente (misma invalidación que LX-9); el server lo entrega tal
cual. La restauración nunca viaja como validada (VLAST es solo front).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.luxy_exploracion import LuxyExploracion
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile

_EST = {"S": {"tp": True, "dir": "long"}, "dir": "long",
        "ZON": [True, False, True], "DON": {"0": True, "5": False}}
_URL = "/ui/strategies/ES5m_X10/luxy/exploracion"


@pytest.fixture
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lx10")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))
    return client


async def _seed_strategy(db: AsyncSession, sid: str = "ES5m_X10",
                         pcfg: dict | None = None) -> None:
    db.add(Strategy(strategy_id=sid, name="X10", asset_symbol="ES",
                    status="paper", enabled=True))
    if pcfg is not None:
        db.add(StrategyProfile(strategy_id=sid, pipeline_config_json=pcfg))
    await db.commit()


# ── round-trip ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_put_get_roundtrip(_auth: AsyncClient, db: AsyncSession) -> None:
    await _seed_strategy(db)
    # antes de guardar: no existe
    g0 = await _auth.get(_URL)
    assert g0.status_code == 200 and g0.json()["existe"] is False

    put = await _auth.put(_URL, json={"estado": _EST,
                                      "estudio_id": "2026-07-12:abcdef012345"})
    assert put.status_code == 200
    pj = put.json()
    assert pj["ok"] is True and pj["updated_at"]

    g1 = await _auth.get(_URL)
    gj = g1.json()
    assert gj["existe"] is True
    assert gj["estado"] == _EST
    assert gj["estudio_id"] == "2026-07-12:abcdef012345"
    assert gj["updated_at"]

    # guardar de nuevo SOBREESCRIBE (uno por estrategia)
    put2 = await _auth.put(_URL, json={"estado": {"S": {}, "ZON": [], "DON": {}},
                                       "estudio_id": "2026-07-12:abcdef012345"})
    assert put2.status_code == 200
    g2 = (await _auth.get(_URL)).json()
    assert g2["estado"] == {"S": {}, "ZON": [], "DON": {}}
    row_count = (await db.execute(select(LuxyExploracion).where(
        LuxyExploracion.strategy_id == "ES5m_X10"))).scalars().all()
    assert len(row_count) == 1                    # una sola fila por estrategia


# ── validación de shape / tamaño ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_shape_invalido_400(_auth: AsyncClient, db: AsyncSession) -> None:
    await _seed_strategy(db)
    # llave desconocida en estado
    r = await _auth.put(_URL, json={"estado": {"S": {}, "hack": 1},
                                    "estudio_id": "x"})
    assert r.status_code == 400 and "llaves" in r.json()["error"]
    # estado ausente
    r2 = await _auth.put(_URL, json={"estudio_id": "x"})
    assert r2.status_code == 400
    # estado no-objeto
    r3 = await _auth.put(_URL, json={"estado": [1, 2, 3]})
    assert r3.status_code == 400
    # nada quedó guardado
    assert (await _auth.get(_URL)).json()["existe"] is False


@pytest.mark.asyncio
async def test_oversize_400(_auth: AsyncClient, db: AsyncSession) -> None:
    await _seed_strategy(db)
    # DON enorme para pasar de 8KB (el payload real es un JSON chico)
    gordo = {"S": {}, "ZON": [], "DON": {str(i): True for i in range(4000)}}
    r = await _auth.put(_URL, json={"estado": gordo, "estudio_id": "x"})
    assert r.status_code == 400 and "grande" in r.json()["error"]
    assert (await _auth.get(_URL)).json()["existe"] is False


# ── auth ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sin_sesion_redirige_login(client: AsyncClient,
                                         db: AsyncSession) -> None:
    await _seed_strategy(db)
    client.cookies.clear()
    for call in (client.get(_URL),
                 client.put(_URL, json={"estado": {}, "estudio_id": "x"}),
                 client.delete(_URL)):
        r = await call
        assert r.status_code == 303
        assert r.headers["location"] == "/ui/login"


# ── invalidación por estudio (server entrega, front descarta) ───────────────

@pytest.mark.asyncio
async def test_estudio_viejo_lo_entrega_para_que_el_front_lo_descarte(
    _auth: AsyncClient, db: AsyncSession
) -> None:
    await _seed_strategy(db)
    await _auth.put(_URL, json={"estado": _EST,
                                "estudio_id": "2026-07-01:VIEJOsha0000"})
    # el GET SIEMPRE entrega el snapshot con su estudio_id; es el front quien
    # compara contra D.estudio_id vigente y lo descarta con nota discreta.
    gj = (await _auth.get(_URL)).json()
    assert gj["existe"] is True
    assert gj["estudio_id"] == "2026-07-01:VIEJOsha0000"       # eid distinto → front descarta


# ── borrar ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_borra_snapshot(_auth: AsyncClient,
                                     db: AsyncSession) -> None:
    await _seed_strategy(db)
    await _auth.put(_URL, json={"estado": _EST, "estudio_id": "e"})
    d = await _auth.delete(_URL)
    assert d.status_code == 200 and d.json()["borrada"] is True
    assert (await _auth.get(_URL)).json()["existe"] is False
    # borrar de nuevo: idempotente, borrada=False
    d2 = await _auth.delete(_URL)
    assert d2.status_code == 200 and d2.json()["borrada"] is False


# ── adversarial: JAMÁS toca pipeline_config ─────────────────────────────────

@pytest.mark.asyncio
async def test_no_toca_pipeline_config(_auth: AsyncClient,
                                       db: AsyncSession) -> None:
    orig = {"backstop_points": 90.0, "windows": [{"days": [1], "start": "00:00",
                                                  "end": "23:59"}]}
    await _seed_strategy(db, pcfg=dict(orig))
    await _auth.put(_URL, json={"estado": _EST, "estudio_id": "e"})
    await _auth.delete(_URL)
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ES5m_X10"))).scalar_one()
    await db.refresh(prof)
    assert prof.pipeline_config_json == orig       # intacto: ni una llave tocada

    # y si NO había profile, guardar la exploración NO crea uno
    db.add(Strategy(strategy_id="ES5m_X10b", name="b", asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    await _auth.put("/ui/strategies/ES5m_X10b/luxy/exploracion",
                    json={"estado": _EST, "estudio_id": "e"})
    prof_b = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "ES5m_X10b"))).scalar_one_or_none()
    assert prof_b is None                          # ningún StrategyProfile fabricado


@pytest.mark.asyncio
async def test_put_estrategia_inexistente_404(_auth: AsyncClient,
                                              db: AsyncSession) -> None:
    r = await _auth.put("/ui/strategies/NOPE/luxy/exploracion",
                        json={"estado": {}, "estudio_id": "e"})
    assert r.status_code == 404


# ── migración aplica y revierte ─────────────────────────────────────────────

def test_migracion_aplica_y_revierte(tmp_path: Path) -> None:
    import sqlite3
    root = Path(__file__).resolve().parents[1]
    dbfile = tmp_path / "mig.db"
    env = dict(os.environ, DATABASE_URL=f"sqlite+aiosqlite:///{dbfile.as_posix()}")

    def _alembic(*args: str) -> None:
        r = subprocess.run([sys.executable, "-m", "alembic", *args],
                           cwd=root, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr

    _alembic("upgrade", "head")
    con = sqlite3.connect(dbfile)
    try:
        assert con.execute("select name from sqlite_master where type='table' "
                           "and name='luxy_exploracion'").fetchall()
        cols = {r[1] for r in con.execute("pragma table_info(luxy_exploracion)")}
        assert cols == {"strategy_id", "estado_json", "estudio_id", "updated_at"}
    finally:
        con.close()

    _alembic("downgrade", "-1")
    con = sqlite3.connect(dbfile)
    try:
        assert not con.execute("select name from sqlite_master where "
                               "type='table' and name='luxy_exploracion'").fetchall()
    finally:
        con.close()
