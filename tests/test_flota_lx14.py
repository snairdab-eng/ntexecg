"""LX-14 Parte B — Concentrado de semáforos en la lista de Estrategias.

Al terminar Calcular estudio se persiste runs/luxy_resumen.json (digest chico); la
lista lo lee por fila (nunca el estudio completo) y muestra Robustez/Alertas/
Deriva/Estudio, ordenadas por atención. Nombre y Pass rate salen.
"""
import asyncio
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_riesgo as rr
import app.web.routes_strategies as rs
import scripts.mr_luxy as mrl
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.strategy import Strategy

from tests.test_contencion_lx12 import _integrar   # integra un master sintético alineado


# ---------------------------------------------------------------------------
# 1) study_resumen — digest chico con los campos del contrato
# ---------------------------------------------------------------------------

def _study(verdict="verde", pf=1.5, n=30, implausible=False, notes=None,
           nsim=42, ntot=42, cont=100.0):
    return {"fecha": "2026-07-14", "degradado": False, "cancel_after_s": 3600,
            "levers_in_sample": {"b_pts": 90, "backstop_usd": 4500,
                                 "tp_por_lado_atr": {"long": 11.5},
                                 "ladder": {"alloc": [5, 3, 2],
                                            "levels": [0, 1.8, 3.6]}},
            "contencion": {"pct": cont, "confiable": cont >= 80},
            "dashboard": {"estudio_id": "2026-07-14:abc",
                          "robustez": {"verdict": verdict, "pf": pf, "n": n},
                          "implausible": implausible, "notes": notes or [],
                          "n_simulable": nsim, "n_total": ntot,
                          "n_fuera_contencion": ntot - nsim,
                          "table3": {"crudo_plus": {"pf": 1.5, "net_usd": 3000, "n": ntot},
                                     "oos": {"pf": pf, "net_usd": 200, "n": n}}}}


def test_study_resumen_digest():
    r = mrl.study_resumen(_study(verdict="sin_veredicto", pf=2.87, n=2,
                                 notes=["flip de signo crudo→config"],
                                 nsim=40, ntot=42))
    assert r["robustez"] == {"verdict": "sin_veredicto", "pf": 2.87, "n": 2}
    assert r["chips"] == {"flip": True, "mejora3x": False}
    assert r["n_simulable"] == 40 and r["n_total"] == 42
    assert r["oos"]["usd_trade"] == 100.0            # 200/2
    assert "backstop_points" in r["activacion"]      # para la deriva de la lista


# ---------------------------------------------------------------------------
# 2) _flota_signals — semáforo/alertas/deriva/prioridad
# ---------------------------------------------------------------------------

def test_flota_sin_estudio():
    s = rs._flota_signals(None, {})
    assert s["semaforo"] == "—" and s["prio"] == 3 and s["deriva"] is None


def test_flota_implausible_prevalece_y_atencion():
    r = mrl.study_resumen(_study(verdict="verde", implausible=True))
    s = rs._flota_signals(r, {})
    assert s["semaforo"] == "implausible" and s["prio"] == 0


def test_flota_sin_veredicto_con_alertas():
    r = mrl.study_resumen(_study(verdict="sin_veredicto", pf=2.87, n=2,
                                 notes=["flip de signo crudo→config"],
                                 nsim=40, ntot=42, cont=70.0))
    s = rs._flota_signals(r, {})
    assert s["semaforo"] == "sin_veredicto" and s["n_oos"] == 2 and s["prio"] == 0
    claves = {a[0] for a in s["alertas"]}
    assert {"flip", "cobertura", "contencion"} <= claves


def test_flota_verde_limpio_prio2():
    r = mrl.study_resumen(_study(verdict="verde"))
    s = rs._flota_signals(r, {})
    assert s["semaforo"] == "verde" and s["prio"] == 2 and s["alertas"] == []


def test_flota_deriva_estados():
    r = mrl.study_resumen(_study())
    # sin config viva → sin_aplicar; con la MISMA config → aplicada
    assert rs._flota_signals(r, {})["deriva"]["estado"] == "sin_aplicar"
    aplicada = rs._flota_signals(r, dict(r["activacion"]))["deriva"]
    assert aplicada["estado"] == "aplicada"


# ---------------------------------------------------------------------------
# 3) run_for_clave escribe runs/luxy_resumen.json (al Calcular)
# ---------------------------------------------------------------------------

def test_resumen_escrito_al_calcular(tmp_path, monkeypatch):
    _integrar(tmp_path, monkeypatch, shift=0.0)        # master sintético alineado
    mrl.run_for_clave("ES_Test", tmp_path / "MotorRiesgo")
    p = tmp_path / "MotorRiesgo" / "ES_Test" / "runs" / "luxy_resumen.json"
    assert p.exists()
    r = json.loads(p.read_text(encoding="utf-8"))
    assert r["fecha"] and "robustez" in r and "activacion" in r


# ---------------------------------------------------------------------------
# 4) La LISTA — HTTP (con y sin resumen, orden, columnas)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_flota")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def motor_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "MotorRiesgo"
    d.mkdir()
    monkeypatch.setattr(rr, "MOTOR_DIR", d)
    return d


def _write_resumen(motor_dir: Path, clave: str, **kw):
    runs = motor_dir / clave / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    (runs / "luxy_resumen.json").write_text(
        json.dumps(mrl.study_resumen(_study(**kw)), ensure_ascii=False),
        encoding="utf-8")


async def _mk(db, sid, asset="ES"):
    db.add(Strategy(strategy_id=sid, name=f"Nombre largo {sid}", asset_symbol=asset,
                    status="paper", enabled=True))
    await db.commit()


@pytest.mark.asyncio
async def test_lista_con_y_sin_resumen(client, db, motor_dir):
    await _mk(db, "ES5m_ConEstudio")
    await _mk(db, "ES5m_SinEstudio")
    _write_resumen(motor_dir, rr.clave_de("ES5m_ConEstudio", "ES"), verdict="verde")
    html = (await client.get("/ui/strategies")).text
    assert "ES5m_ConEstudio" in html and "ES5m_SinEstudio" in html
    assert "🟢" in html                               # semáforo del que tiene estudio
    # columnas nuevas presentes, viejas ausentes
    assert "Robustez" in html and "Alertas" in html and "Deriva" in html
    assert "Pass rate" not in html and ">Nombre<" not in html
    # el nombre largo pasa a tooltip del strategy_id
    assert 'title="Nombre largo ES5m_ConEstudio"' in html


@pytest.mark.asyncio
async def test_lista_orden_atencion(client, db, motor_dir):
    await _mk(db, "ES5m_Verde")
    await _mk(db, "ES5m_Rojo")
    _write_resumen(motor_dir, rr.clave_de("ES5m_Verde", "ES"), verdict="verde")
    _write_resumen(motor_dir, rr.clave_de("ES5m_Rojo", "ES"),
                   verdict="rojo", pf=0.8, n=20)
    html = (await client.get("/ui/strategies")).text
    assert html.index("ES5m_Rojo") < html.index("ES5m_Verde")   # atención primero


@pytest.mark.asyncio
async def test_lista_semaforo_sin_estudio_guion(client, db, motor_dir):
    await _mk(db, "ES5m_Nada")
    html = (await client.get("/ui/strategies")).text
    assert "ES5m_Nada" in html                        # renderiza sin tronar
