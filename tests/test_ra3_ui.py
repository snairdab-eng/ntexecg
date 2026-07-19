"""RA-3 — UI + sembrado del re-armado (el interruptor supervisado).

Sembrado server-side desde el veredicto RA-0v3 (jamás confiar el guard al
front): 🟢 + checkbox ⇒ enabled:true con las constantes del veredicto y gate
ÁMBAR; 🟢 sin checkbox ⇒ enabled:false (sembrar ≠ encender); 🔴/⚪ ⇒ sección
deshabilitada Y rechazo 409 server-side; el bloque `rearm` del cliente se
DESCARTA. Apagado sin fricción (un clic + REARM_DISABLED; encender desde
Config no existe). Visibilidad de ciclos en Posiciones. Guardas de render.
"""
import json
import uuid
from datetime import datetime, timezone
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
from app.models.position_state import PositionState
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.web.routes_strategies import _rearm_desde_veredicto

_SID = "ES5m_RA3"
_CLAVE = "ES_RA3"
_OV = {"sl_usd": 4500.0, "tp_usd": 3200.0, "l2_usd": 656.0, "l3_usd": 1312.0}
_R2 = {"veredicto": "recomendado", "veredicto_texto": "recomendado",
       "MAX_CICLOS": 3, "K_SOBRE_C0": 1.0, "UMBRAL_ATR_EXPANSION": 1.5}


def _study(r2=_R2):
    return {"fecha": "2026-07-19", "degradado": False, "cancel_after_s": 3600,
            "dashboard": {"piernas": {"recomendacion": r2}},
            "contencion": {"confiable": True, "pct": 100.0}}


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_ra3")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


def _motor(tmp_path, monkeypatch, study):
    monkeypatch.setattr(rr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    base = tmp_path / "MotorRiesgo" / _CLAVE
    (base / "runs").mkdir(parents=True, exist_ok=True)
    (base / "manifest.json").write_text("{}", encoding="utf-8")
    (base / "runs" / "luxy_2026-07-19.json").write_text(
        json.dumps(study), encoding="utf-8")


@pytest.fixture()
def motor_verde(tmp_path: Path, monkeypatch):
    _motor(tmp_path, monkeypatch, _study())


@pytest.fixture()
def motor_ns(tmp_path: Path, monkeypatch):
    _motor(tmp_path, monkeypatch, _study(dict(_R2, veredicto="n/s")))


def _fake_eval(overrides):
    """evaluate_overrides controlado: el APLICABLE es REAL
    (config_from_overrides — la plomería del rearm es la de producción);
    señales limpias para aislar el ámbar del re-armado."""
    aplicable = mrl.config_from_overrides(overrides, atr_med=8.0, ppt=50.0,
                                          alloc=[5, 3, 2], cancel_after_s=3600)
    return {"validado": True, "aplicable": aplicable,
            "señales": {"robustez": "verde", "implausible": False,
                        "flip_signo": False, "mejora_3x": False,
                        "participacion_pct": 100.0},
            "base": {"net": 1000.0}, "config": {"net": 1400.0},
            "oos": {"net": 500.0, "pf": 1.4},
            "robustez": {"verdict": "verde", "pf": 1.4, "n": 20},
            "retencion": {}}


@pytest.fixture()
def patch_eval(monkeypatch):
    monkeypatch.setattr(
        mrl, "evaluate_overrides",
        lambda clave, motor_dir, overrides, **kw: _fake_eval(overrides))


async def _seed(db: AsyncSession, rearm_cfg=None):
    se = {"mode": "design_only", "quantities": [5, 3, 2], "levels": [1.0, 2.0]}
    if rearm_cfg is not None:
        se["rearm"] = rearm_cfg
    db.add(Strategy(strategy_id=_SID, name="RA3", asset_symbol="ES",
                    status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=_SID, mode="paper", dry_run=True,
                           traderspost_enabled=False,
                           pipeline_config_json={"scale_entry": se}))
    await db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# 1) _rearm_desde_veredicto — unidad (el guard, exacto)
# ═══════════════════════════════════════════════════════════════════════════

def test_sembrado_exacto_desde_veredicto_con_checkbox():
    o, info, err = _rearm_desde_veredicto(
        _study(), {**_OV, "rearm_incluir": True,
                   "rearm": {"enabled": True, "max_ciclos": 99}})  # inyección
    assert err is None
    # el bloque del CLIENTE se descartó; el sembrado sale del VEREDICTO
    assert o["rearm"] == {"enabled": True, "max_ciclos": 3, "k_sobre_c0": 1.0,
                          "umbral_atr": 1.5, "min_antes_cierre_min": 30,
                          "timeframe": "5m"}
    assert "rearm_incluir" not in o
    assert info["disponible"] is True and info["incluido"] is True
    assert info["constantes"] == {"max_ciclos": 3, "k_sobre_c0": 1.0,
                                  "umbral_atr": 1.5}


def test_sembrado_sin_checkbox_enabled_false():
    o, info, err = _rearm_desde_veredicto(_study(), dict(_OV))
    assert err is None
    assert o["rearm"]["enabled"] is False          # sembrar ≠ encender
    assert o["rearm"]["max_ciclos"] == 3
    assert info["incluido"] is False


def test_veredicto_ns_rechaza_enabled_server_side():
    st = _study(dict(_R2, veredicto="n/s"))
    _o, _i, err = _rearm_desde_veredicto(st, {"rearm_incluir": True})
    assert err is not None and err.status_code == 409
    assert "RECHAZADO server-side" in err.body.decode("utf-8")
    # sin pedirlo: no se siembra nada y la sección queda deshabilitada
    o, info, err2 = _rearm_desde_veredicto(st, dict(_OV))
    assert err2 is None and "rearm" not in o
    assert info["disponible"] is False and "n/s" in info["motivo"]


def test_veredicto_ausente_o_constantes_ns_fail_safe():
    _o, _i, err = _rearm_desde_veredicto({"dashboard": {}},
                                         {"rearm_incluir": True})
    assert err is not None and err.status_code == 409
    # constantes 'n/s' del veredicto → defaults conservadores (P2)
    st = _study({"veredicto": "recomendado", "MAX_CICLOS": "n/s",
                 "K_SOBRE_C0": None, "UMBRAL_ATR_EXPANSION": "n/s"})
    o, _info, err2 = _rearm_desde_veredicto(st, {"rearm_incluir": True})
    assert err2 is None
    assert o["rearm"]["max_ciclos"] == 1           # n/s → 1 = OFF efectivo
    assert o["rearm"]["k_sobre_c0"] == 1.0
    assert o["rearm"]["umbral_atr"] == 1.5


# ═══════════════════════════════════════════════════════════════════════════
# 2) Endpoints — preview/aplicar con el guard cableado
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_preview_verde_checkbox_incluye_rearm_y_gate_ambar(
        client, motor_verde, db, patch_eval):
    await _seed(db)
    r = await client.post(
        f"/ui/strategies/{_SID}/luxy/aplicar_palancas/preview",
        json={**_OV, "rearm_incluir": True})
    assert r.status_code == 200
    j = r.json()
    assert j["rearm"]["disponible"] is True and j["rearm"]["incluido"] is True
    se = j["aplicable"]["scale_entry"]
    assert se["rearm"]["enabled"] is True and se["rearm"]["max_ciclos"] == 3
    # jamás verde con re-armado: el gate LX-11 lo marca ÁMBAR
    assert j["gate"]["nivel"] == "amber"
    assert any("re-armado" in t for t in j["gate"]["triggers"])


@pytest.mark.asyncio
async def test_preview_verde_sin_checkbox_siembra_off_y_gate_limpio(
        client, motor_verde, db, patch_eval):
    await _seed(db)
    r = await client.post(
        f"/ui/strategies/{_SID}/luxy/aplicar_palancas/preview", json=_OV)
    assert r.status_code == 200
    j = r.json()
    se = j["aplicable"]["scale_entry"]
    assert se["rearm"]["enabled"] is False         # sembrado, NO encendido
    assert not any("re-armado" in t for t in j["gate"]["triggers"])
    assert j["gate"]["nivel"] == "verde"


@pytest.mark.asyncio
async def test_preview_ns_deshabilitado_y_aplicar_409(
        client, motor_ns, db, patch_eval):
    await _seed(db)
    r = await client.post(
        f"/ui/strategies/{_SID}/luxy/aplicar_palancas/preview", json=_OV)
    assert r.status_code == 200
    j = r.json()
    assert j["rearm"]["disponible"] is False
    assert "rearm" not in (j["aplicable"].get("scale_entry") or {})
    # pedir enabled con veredicto n/s → 409 en preview Y en aplicar
    r2 = await client.post(
        f"/ui/strategies/{_SID}/luxy/aplicar_palancas/preview",
        json={**_OV, "rearm_incluir": True})
    assert r2.status_code == 409
    r3 = await client.post(
        f"/ui/strategies/{_SID}/luxy/aplicar_palancas",
        json={"overrides": {**_OV, "rearm_incluir": True},
              "confirm_riesgo": True})
    assert r3.status_code == 409


@pytest.mark.asyncio
async def test_cliente_no_puede_inyectar_rearm_directo(
        client, motor_ns, db, patch_eval):
    """Con veredicto n/s, un `rearm` crafteado en los overrides se DESCARTA:
    el aplicable sale limpio (el guard no vive en el front)."""
    await _seed(db)
    r = await client.post(
        f"/ui/strategies/{_SID}/luxy/aplicar_palancas/preview",
        json={**_OV, "rearm": {"enabled": True, "max_ciclos": 99}})
    assert r.status_code == 200
    assert "rearm" not in (r.json()["aplicable"].get("scale_entry") or {})


@pytest.mark.asyncio
async def test_aplicar_escribe_rearm_sin_tocar_kill_switch(
        client, motor_verde, db, patch_eval):
    await _seed(db)
    r = await client.post(
        f"/ui/strategies/{_SID}/luxy/aplicar_palancas",
        json={"overrides": {**_OV, "rearm_incluir": True},
              "confirm_riesgo": True})           # gate ámbar por re-armado
    assert r.status_code == 200
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == _SID))).scalar_one()
    rearm = prof.pipeline_config_json["scale_entry"]["rearm"]
    assert rearm["enabled"] is True and rearm["max_ciclos"] == 3
    # kill-switch INTACTO (R-T10/NX-11: aplicar jamás toca el modo)
    assert prof.mode == "paper" and prof.dry_run is True
    assert prof.traderspost_enabled is False


# ═══════════════════════════════════════════════════════════════════════════
# 3) Apagado sin fricción (Config) — una puerta de entrada, dos de salida
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_rearm_off_un_clic_conserva_constantes_y_audita(client, db):
    await _seed(db, rearm_cfg={"enabled": True, "max_ciclos": 3,
                               "k_sobre_c0": 1.0, "umbral_atr": 1.5,
                               "min_antes_cierre_min": 30, "timeframe": "5m"})
    r = await client.post(f"/ui/strategies/{_SID}/rearm/off",
                          follow_redirects=False)
    assert r.status_code in (302, 303)
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == _SID))).scalar_one()
    rearm = prof.pipeline_config_json["scale_entry"]["rearm"]
    assert rearm["enabled"] is False
    assert rearm["max_ciclos"] == 3                # constantes conservadas
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "REARM_DISABLED"))).scalars().first()
    assert audit is not None and audit.actor == "operador"
    assert audit.old_value_json == {"enabled": True}
    assert audit.new_value_json == {"enabled": False}


@pytest.mark.asyncio
async def test_rearm_off_sin_sembrar_no_hace_nada(client, db):
    await _seed(db)
    r = await client.post(f"/ui/strategies/{_SID}/rearm/off",
                          follow_redirects=False)
    assert r.status_code in (302, 303)
    prof = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == _SID))).scalar_one()
    assert "rearm" not in prof.pipeline_config_json["scale_entry"]
    assert (await db.execute(select(AuditLog).where(
        AuditLog.action == "REARM_DISABLED"))).scalars().first() is None


@pytest.mark.asyncio
async def test_encender_desde_config_no_existe(client, db):
    await _seed(db)
    r = await client.post(f"/ui/strategies/{_SID}/rearm/on",
                          follow_redirects=False)
    assert r.status_code in (404, 405)             # una sola puerta de entrada


# ═══════════════════════════════════════════════════════════════════════════
# 4) Visibilidad de ciclos en Posiciones (dashboard, read-only)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_dashboard_muestra_ciclos_y_ultima_accion(client, db):
    from app.services.audit_service import AuditService
    db.add(PositionState(
        strategy_id=_SID, account_id="paper_default", symbol="MESU2025",
        state="LONG", direction="long", quantity=10, entry_price=5500.0,
        risk_plan_json={"opened_at": "2026-07-19T13:30:00+00:00", "rearm": {
            "legs": [{"leg_index": 2, "side": "long", "level_atr": 1.0,
                      "limit_price": 5492.0, "qty": 3, "cycle_n": 2,
                      "last_client_id": "x-r2",
                      "last_sent_at": "2026-07-19T13:30:00+00:00",
                      "state": "working", "death_reason": None},
                     {"leg_index": 3, "side": "long", "level_atr": 2.0,
                      "limit_price": 5484.0, "qty": 2, "cycle_n": 1,
                      "last_client_id": None,
                      "last_sent_at": "2026-07-19T13:30:00+00:00",
                      "state": "dead", "death_reason": "R-RA6"}],
            "signal_atr": 8.0, "sl_price": 5488.0, "tp_price": None,
            "updated_at": "2026-07-19T13:30:00+00:00"}}))
    await AuditService().log(
        db, actor="rearm_job", action="REARM_KILL",
        object_type="PositionState", object_id="paper_default:MESU2025",
        new_value={"leg_index": 3, "regla": "R-RA6", "detalle": "x"})
    await db.commit()
    r = await client.get("/ui")
    assert r.status_code == 200
    html = r.text
    assert "re-armado:" in html
    assert "C2·working·c2" in html
    assert "C3·dead·c1" in html
    assert "REARM_KILL · R-RA6" in html


@pytest.mark.asyncio
async def test_dashboard_sin_rearm_no_pinta_fila(client, db):
    db.add(PositionState(strategy_id=_SID, account_id="paper_default",
                         symbol="MESU2025", state="LONG", direction="long",
                         quantity=1))
    await db.commit()
    r = await client.get("/ui")
    assert r.status_code == 200
    assert "re-armado:" not in r.text


# ═══════════════════════════════════════════════════════════════════════════
# 5) Guardas de render (fuente de los templates)
# ═══════════════════════════════════════════════════════════════════════════

_TPL = Path("app/templates/strategy_detail.html").read_text(encoding="utf-8")
_DASH = Path("app/templates/dashboard.html").read_text(encoding="utf-8")


def test_guarda_modal_checkbox_default_desmarcado_y_repreview():
    assert "rearmOn:false" in _TPL                 # DEFAULT DESMARCADO
    assert "rearm_incluir:this.rearmOn" in _TPL    # viaja en preview Y apply
    assert _TPL.count("JSON.stringify(this.ov())") >= 1
    assert "@change=\"load(true)\"" in _TPL        # marcar re-pide el preview
    assert "prev.rearm && prev.rearm.disponible" in _TPL
    assert "prev.rearm && !prev.rearm.disponible" in _TPL   # sección deshabilitada
    assert "sembrar ≠ encender" in _TPL


def test_guarda_config_apagar_sin_encender():
    assert "Apagar re-armado" in _TPL
    assert "/rearm/off" in _TPL
    assert "/rearm/on" not in _TPL                 # encender desde Config NO existe


def test_guarda_dashboard_fila_rearm():
    assert "re-armado:" in _DASH
    assert "estado ilegible" in _DASH              # legs vacías → honesto
