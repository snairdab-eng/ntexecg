"""LX-11 — Gate de robustez en los puentes de Aplicar (fricción de UI + registro).

Impide el camino silencioso del 2026-07-13/14 (aplicar con semáforo rojo /
tripwire implausible / flip de signo / intrabar no confiable): cada nivel del
gate pide fricción proporcional (verde limpio · amber checkbox · rojo frase
exacta) y la entrada APPLY_LUXY_RECO registra SIEMPRE qué se sabía al aplicar.
"""
import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.web.routes_riesgo as rr
import scripts.mr_luxy as mrl
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.strategy import Strategy

FRASE = mrl.GATE_FRASE_ROJO


# ---------------------------------------------------------------------------
# Estudios sintéticos (control total de las señales — sin depender de datos reales)
# ---------------------------------------------------------------------------

_LEVERS = {"b_pts": 90.0, "backstop_usd": 4500.0,
           "tp_por_lado_atr": {"long": 11.5, "short": 8.0},
           "ladder": {"alloc": [5, 3, 2], "levels": [0.0, 1.81, 3.61]}}


def _dash(*, robustez="verde", implausible=False, notes=None, part=95.0):
    return {"robustez": {"verdict": robustez}, "implausible": implausible,
            "implausible_msg": "PF 58.7 implausible" if implausible else None,
            "notes": notes or [],
            "table3": {"crudo_plus": {"participacion_pct": part}}}


def _study(dash, *, contencion=None, degradado_motivo=None):
    return {"degradado": False, "fecha": "2026-07-14", "usd_por_punto": 50.0,
            "cancel_after_s": 3600, "levers_in_sample": dict(_LEVERS),
            "dashboard": dash, "contencion": contencion,
            "degradado_motivo": degradado_motivo}


def _verde():
    return _study(_dash(), contencion={"pct": 100.0, "confiable": True})


def _amber():
    return _study(_dash(robustez="amarillo", part=85.0))


def _rojo():
    return _study(_dash(robustez="rojo", implausible=True,
                        notes=["flip de signo crudo→config"], part=70.0))


# ---------------------------------------------------------------------------
# 1) Gate PURO — niveles y triggers (sin HTTP)
# ---------------------------------------------------------------------------

def test_gate_verde_sin_alertas():
    g = mrl.gate_aplicar(_verde())
    assert g["nivel"] == "verde" and g["triggers"] == []


def test_gate_amber_robustez_o_participacion():
    g = mrl.gate_aplicar(_amber())
    assert g["nivel"] == "amber"
    assert any("ÁMBAR" in t for t in g["triggers"])
    assert any("participación" in t for t in g["triggers"])


def test_gate_rojo_multiples_triggers():
    g = mrl.gate_aplicar(_rojo())
    assert g["nivel"] == "rojo"
    tj = " ".join(g["triggers"])
    assert "ROJO" in tj and "implausib" in tj.lower() and "flip de signo" in tj
    assert g["frase_rojo"] == FRASE


def test_gate_rojo_por_intrabar_no_confiable():
    st = _study(_dash(), degradado_motivo="intrabar_no_confiable",
                contencion={"pct": 20.0, "confiable": False})
    g = mrl.gate_aplicar(st)
    assert g["nivel"] == "rojo"
    assert any("intrabar" in t.lower() for t in g["triggers"])


def test_gate_amber_por_mejora_3x():
    g = mrl.gate_aplicar(_study(_dash(notes=["mejora >3× (revisar sobreajuste)"])))
    assert g["nivel"] == "amber"
    assert any("sobreajuste" in t for t in g["triggers"])


def test_gate_ventanas_solo_implausible_o_intrabar():
    # robustez roja + flip + participación baja NO disparan ventanas…
    assert mrl.gate_ventanas(_study(_dash(robustez="rojo", part=50.0,
                                          notes=["flip de signo crudo→config"])))["nivel"] == "verde"
    # …pero implausible sí.
    assert mrl.gate_ventanas(_study(_dash(implausible=True)))["nivel"] == "rojo"
    # …y intrabar no confiable también.
    assert mrl.gate_ventanas(_study(_dash(),
                                    degradado_motivo="intrabar_no_confiable",
                                    contencion={"pct": 20.0, "confiable": False}
                                    ))["nivel"] == "rojo"


# ---------------------------------------------------------------------------
# 2) HTTP — enforcement por nivel + AuditLog (estudio sintético en disco)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lx11")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


@pytest.fixture()
def motor_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "MotorRiesgo"
    d.mkdir()
    monkeypatch.setattr(rr, "MOTOR_DIR", d)
    return d


async def _setup(db: AsyncSession, motor_dir: Path, sid: str, study: dict) -> str:
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="ES",
                    status="paper", enabled=True))
    await db.commit()
    clave = rr.clave_de(sid, "ES")
    runs = motor_dir / clave / "runs"
    runs.mkdir(parents=True)
    (runs / "luxy_2026-07-14.json").write_text(
        json.dumps(study, ensure_ascii=False), encoding="utf-8")
    return clave


async def _last_apply_audit(db: AsyncSession, sid: str) -> dict:
    row = (await db.execute(
        select(AuditLog).where(AuditLog.action == "APPLY_LUXY_RECO")
        .order_by(AuditLog.id.desc()))).scalars().first()
    return row.new_value_json if row else {}


@pytest.mark.asyncio
async def test_http_verde_aplica_limpio(client, db, motor_dir):
    sid = "ES5m_GateVerde"
    await _setup(db, motor_dir, sid, _verde())
    prev = (await client.get(f"/ui/strategies/{sid}/luxy/aplicar/preview")).json()
    assert prev["gate"]["nivel"] == "verde"
    r = await client.post(f"/ui/strategies/{sid}/luxy/aplicar", json={})
    assert r.status_code == 200, r.text
    # AuditLog SIEMPRE con la huella de señales
    audit = await _last_apply_audit(db, sid)
    assert audit["_gate_lx11"]["nivel"] == "verde"
    assert "robustez" in audit["_gate_lx11"]["señales"]


@pytest.mark.asyncio
async def test_http_amber_exige_checkbox(client, db, motor_dir):
    sid = "ES5m_GateAmber"
    await _setup(db, motor_dir, sid, _amber())
    prev = (await client.get(f"/ui/strategies/{sid}/luxy/aplicar/preview")).json()
    assert prev["gate"]["nivel"] == "amber"
    # sin checkbox → 400
    r = await client.post(f"/ui/strategies/{sid}/luxy/aplicar", json={})
    assert r.status_code == 400 and "ÁMBAR" in r.json()["error"]
    # con checkbox → 200 + huella del override
    r = await client.post(f"/ui/strategies/{sid}/luxy/aplicar",
                          json={"confirm_riesgo": True})
    assert r.status_code == 200, r.text
    audit = await _last_apply_audit(db, sid)
    assert audit["_gate_lx11"]["nivel"] == "amber"
    assert audit["_gate_lx11"]["overrides"]["confirm_riesgo"] is True


@pytest.mark.asyncio
async def test_http_rojo_exige_frase_exacta(client, db, motor_dir):
    sid = "ES5m_GateRojo"
    await _setup(db, motor_dir, sid, _rojo())
    prev = (await client.get(f"/ui/strategies/{sid}/luxy/aplicar/preview")).json()
    assert prev["gate"]["nivel"] == "rojo"
    # sin frase → 400
    assert (await client.post(f"/ui/strategies/{sid}/luxy/aplicar",
                              json={})).status_code == 400
    # frase mal → 400
    r = await client.post(f"/ui/strategies/{sid}/luxy/aplicar",
                          json={"frase": "aplicar"})
    assert r.status_code == 400 and FRASE in r.json()["error"]
    # el checkbox NO basta para rojo → 400
    assert (await client.post(f"/ui/strategies/{sid}/luxy/aplicar",
                              json={"confirm_riesgo": True})).status_code == 400
    # frase exacta → 200
    r = await client.post(f"/ui/strategies/{sid}/luxy/aplicar",
                          json={"frase": FRASE})
    assert r.status_code == 200, r.text
    audit = await _last_apply_audit(db, sid)
    assert audit["_gate_lx11"]["nivel"] == "rojo"
    assert len(audit["_gate_lx11"]["triggers"]) >= 2


@pytest.mark.asyncio
async def test_http_ventanas_gate_solo_rojo_por_implausible(client, db, motor_dir):
    sid = "ES5m_GateVent"
    # estudio implausible pero con nube/reco mínimos para el compilador
    st = _study(_dash(implausible=True))
    st["dashboard"]["reco"] = {"days": [{"dow": 0}, {"dow": 1}]}
    st["dashboard"]["trades"] = []
    await _setup(db, motor_dir, sid, st)
    prev = (await client.post(f"/ui/strategies/{sid}/luxy/ventanas/preview",
                              json={})).json()
    assert prev["gate"]["nivel"] == "rojo"
    # aplicar sin frase → 400
    r = await client.post(f"/ui/strategies/{sid}/luxy/ventanas/aplicar", json={})
    assert r.status_code == 400 and FRASE in r.json()["error"]
