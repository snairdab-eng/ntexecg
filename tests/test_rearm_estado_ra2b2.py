"""RA-2b SUB-PASO 2 — estado del ciclo PERSISTENTE (diseño §2 + E1/E2).

Solo la capa de estado: sembrado al despachar (webhooks → set_rearm_state),
lectura/validación FAIL-CLOSED (`leer_estado` → None si ilegible, jamás
excepción ni estado parcial) y transicionadores puros. El RearmJob NO existe
aún — cero despacho nuevo.

Invariantes fijados aquí (lote 2026-07-19):
  (a) restart = releer de DB, jamás "ciclo 1 otra vez" (round-trip JSON).
  (b) ilegible ⇒ None, nunca excepción.
  (c) E2: assumed_filled NO muta state/quantity/direction de la posición.
  (d) el escritor solo toca risk_plan_json["rearm"].
  (e) E1: TTL≠3600 con rearm ON ⇒ ttl_incoherente registrado en el estado.
"""
import copy
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.position_state import PositionState
from app.services.position_service import PositionService
from app.services.rearm import (
    avanzar_ciclo,
    leer_estado,
    marcar_assumed_filled,
    marcar_muerta,
    sembrar_estado,
)

_NOW = "2026-07-19T14:00:00+00:00"
_T2 = "2026-07-19T15:02:00+00:00"


def _payloads():
    """Como los emite build_scaled: C1 a mercado + C2/C3 límite (extras con
    leg_index/level_atr/atr_value; stopLoss/takeProfit compartidos)."""
    base = {"ticker": "MESU2025", "action": "buy", "sentiment": "long",
            "signalPrice": 5500.0,
            "stopLoss": {"type": "stop", "stopPrice": 5488.0},
            "takeProfit": {"type": "limit", "limitPrice": 5620.0}}
    c1 = {**base, "quantity": 4,
          "extras": {"leg_index": 1, "level_atr": 0.0, "atr_value": 8.0}}
    c2 = {**base, "quantity": 3, "orderType": "limit", "limitPrice": 5492.0,
          "cancelAfter": 3600,
          "extras": {"leg_index": 2, "level_atr": 1.0, "atr_value": 8.0}}
    c3 = {**base, "quantity": 3, "orderType": "limit", "limitPrice": 5484.0,
          "cancelAfter": 3600,
          "extras": {"leg_index": 3, "level_atr": 2.0, "atr_value": 8.0}}
    return [c1, c2, c3]


def _estado_ok():
    return sembrar_estado(_payloads(), side="long", now_iso=_NOW, ttl_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# 1) Sembrado — shape del diseño §2
# ═══════════════════════════════════════════════════════════════════════════

def test_sembrar_shape_del_diseno():
    e = _estado_ok()
    assert set(e) == {"legs", "signal_atr", "sl_price", "tp_price",
                      "updated_at"}
    assert e["signal_atr"] == 8.0            # congelado en la entrada (R-RA7)
    assert e["sl_price"] == 5488.0 and e["tp_price"] == 5620.0   # R-RA6
    assert e["updated_at"] == _NOW
    assert [l["leg_index"] for l in e["legs"]] == [2, 3]   # SOLO límite (C1 no)
    for leg, lp, lv, q in zip(e["legs"], (5492.0, 5484.0), (1.0, 2.0), (3, 3)):
        assert leg == {"leg_index": leg["leg_index"], "side": "long",
                       "level_atr": lv, "limit_price": lp, "qty": q,
                       "cycle_n": 1, "last_client_id": None,
                       "last_sent_at": _NOW, "state": "working",
                       "death_reason": None}


def test_sembrar_sin_piernas_limite_devuelve_none():
    solo_mercado = [_payloads()[0]]
    assert sembrar_estado(solo_mercado, side="long", now_iso=_NOW,
                          ttl_ok=True) is None
    assert sembrar_estado([], side="long", now_iso=_NOW, ttl_ok=True) is None
    assert sembrar_estado(None, side="long", now_iso=_NOW, ttl_ok=True) is None


def test_e1_ttl_incoherente_queda_registrado():
    e = sembrar_estado(_payloads(), side="long", now_iso=_NOW, ttl_ok=False)
    assert e["ttl_incoherente"] is True      # el job lo leerá como no-re-armar
    # y el estado sigue siendo LEGIBLE (el flag es información, no corrupción)
    assert leer_estado({"rearm": e}) is not None


# ═══════════════════════════════════════════════════════════════════════════
# 2) Round-trip (invariante a: restart ⇒ releer, jamás "ciclo 1 otra vez")
# ═══════════════════════════════════════════════════════════════════════════

def test_round_trip_serializar_releer_cycle_n_intacto():
    e = _estado_ok()
    e["legs"][0] = avanzar_ciclo(e["legs"][0], "sig-abc-r2", _T2)
    e["legs"][1] = marcar_muerta(e["legs"][1], "R-RA6")
    plan = json.loads(json.dumps({"opened_at": _NOW, "rearm": e}))
    releido = leer_estado(plan)
    assert releido is not None
    assert releido["legs"][0]["cycle_n"] == 2            # NO "ciclo 1 otra vez"
    assert releido["legs"][0]["last_client_id"] == "sig-abc-r2"
    assert releido["legs"][0]["last_sent_at"] == _T2
    assert releido["legs"][1]["state"] == "dead"
    assert releido["legs"][1]["death_reason"] == "R-RA6"
    assert releido == e                                   # bit a bit


def test_leer_estado_devuelve_copia_no_alias():
    plan = {"rearm": _estado_ok()}
    r = leer_estado(plan)
    r["legs"][0]["cycle_n"] = 99
    assert plan["rearm"]["legs"][0]["cycle_n"] == 1       # el JSON ni se entera


# ═══════════════════════════════════════════════════════════════════════════
# 3) Ilegible ⇒ None, jamás excepción (invariante b) — caso por caso
# ═══════════════════════════════════════════════════════════════════════════

def _con(mutador):
    plan = {"rearm": _estado_ok()}
    mutador(plan["rearm"])
    return plan


_ILEGIBLES = {
    "sin_rearm": lambda: {},
    "rearm_no_dict": lambda: {"rearm": "x"},
    "plan_none": lambda: None,
    "plan_no_dict": lambda: "x",
    "legs_vacias": lambda: _con(lambda e: e.update(legs=[])),
    "legs_no_lista": lambda: _con(lambda e: e.update(legs={"0": {}})),
    "leg_no_dict": lambda: _con(lambda e: e["legs"].append("x")),
    "falta_signal_atr": lambda: _con(lambda e: e.pop("signal_atr")),
    "falta_sl_price": lambda: _con(lambda e: e.pop("sl_price")),
    "falta_tp_price": lambda: _con(lambda e: e.pop("tp_price")),
    "falta_updated_at": lambda: _con(lambda e: e.pop("updated_at")),
    "signal_atr_cero": lambda: _con(lambda e: e.update(signal_atr=0)),
    "signal_atr_str": lambda: _con(lambda e: e.update(signal_atr="8")),
    "sl_none": lambda: _con(lambda e: e.update(sl_price=None)),
    "tp_str": lambda: _con(lambda e: e.update(tp_price="5620")),
    "updated_at_basura": lambda: _con(lambda e: e.update(updated_at="ayer")),
    "ttl_flag_no_bool": lambda: _con(lambda e: e.update(ttl_incoherente="sí")),
    "leg_index_str": lambda: _con(
        lambda e: e["legs"][0].update(leg_index="2")),
    "leg_index_cero": lambda: _con(lambda e: e["legs"][0].update(leg_index=0)),
    "side_invalido": lambda: _con(lambda e: e["legs"][0].update(side="up")),
    "level_atr_negativo": lambda: _con(
        lambda e: e["legs"][0].update(level_atr=-1.0)),
    "limit_price_cero": lambda: _con(
        lambda e: e["legs"][0].update(limit_price=0)),
    "qty_cero": lambda: _con(lambda e: e["legs"][0].update(qty=0)),
    "qty_bool": lambda: _con(lambda e: e["legs"][0].update(qty=True)),
    "cycle_cero": lambda: _con(lambda e: e["legs"][0].update(cycle_n=0)),
    "state_zombie": lambda: _con(lambda e: e["legs"][0].update(state="zombie")),
    "ts_no_parseable": lambda: _con(
        lambda e: e["legs"][0].update(last_sent_at="14:31 de ayer")),
    "client_id_no_str": lambda: _con(
        lambda e: e["legs"][0].update(last_client_id=7)),
    "death_reason_no_str": lambda: _con(
        lambda e: e["legs"][0].update(death_reason=1)),
}


@pytest.mark.parametrize("caso", sorted(_ILEGIBLES))
def test_ilegible_devuelve_none_sin_excepcion(caso):
    plan = _ILEGIBLES[caso]()
    # llave faltante POR PIERNA también es ilegible
    assert leer_estado(plan) is None


@pytest.mark.parametrize("campo", ["leg_index", "side", "level_atr",
                                   "limit_price", "qty", "cycle_n",
                                   "last_client_id", "last_sent_at",
                                   "state", "death_reason"])
def test_ilegible_por_campo_faltante_en_pierna(campo):
    plan = {"rearm": _estado_ok()}
    plan["rearm"]["legs"][0].pop(campo)
    assert leer_estado(plan) is None


def test_estado_valido_si_es_legible():
    assert leer_estado({"rearm": _estado_ok()}) is not None


# ═══════════════════════════════════════════════════════════════════════════
# 4) Transicionadores puros (copias; jamás mutan la pierna dada)
# ═══════════════════════════════════════════════════════════════════════════

def test_marcar_muerta_pura():
    leg = _estado_ok()["legs"][0]
    antes = copy.deepcopy(leg)
    m = marcar_muerta(leg, "R-RA4")
    assert m["state"] == "dead" and m["death_reason"] == "R-RA4"
    assert leg == antes                                   # la original intacta


def test_marcar_assumed_filled_pura():
    leg = _estado_ok()["legs"][0]
    antes = copy.deepcopy(leg)
    m = marcar_assumed_filled(leg)
    assert m["state"] == "assumed_filled" and m["death_reason"] is None
    assert leg == antes


def test_avanzar_ciclo_solo_desde_working():
    leg = _estado_ok()["legs"][0]
    av = avanzar_ciclo(leg, "sig-r2", _T2)
    assert av["cycle_n"] == 2 and av["last_client_id"] == "sig-r2"
    assert av["last_sent_at"] == _T2 and av["state"] == "working"
    assert leg["cycle_n"] == 1                            # pura
    with pytest.raises(ValueError):
        avanzar_ciclo(marcar_muerta(leg, "R-RA6"), "x", _T2)
    with pytest.raises(ValueError):
        avanzar_ciclo(marcar_assumed_filled(leg), "x", _T2)


# ═══════════════════════════════════════════════════════════════════════════
# 5) Escritor acotado (d) + E2 (c) — nivel servicio, con DB
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_escritor_solo_toca_rearm(db: AsyncSession):
    import uuid
    svc = PositionService()
    ps = await svc.on_entry_approved(db, "ra2b2_s", "acct", "MESU2025",
                                     "long", 10, 5500.0, uuid.uuid4(),
                                     entry_style="limit_only")
    antes = (ps.state, ps.direction, ps.quantity, ps.entry_price)
    plan_antes = dict(ps.risk_plan_json)                  # opened_at, entry_style
    await svc.set_rearm_state(db, "ra2b2_s", "acct", "MESU2025", _estado_ok())
    assert (ps.state, ps.direction, ps.quantity, ps.entry_price) == antes
    for k, v in plan_antes.items():                       # el resto del plan intacto
        assert ps.risk_plan_json[k] == v
    assert leer_estado(ps.risk_plan_json) is not None


@pytest.mark.asyncio
async def test_e2_assumed_filled_no_muta_la_posicion(db: AsyncSession):
    import uuid
    svc = PositionService()
    ps = await svc.on_entry_approved(db, "ra2b2_e2", "acct", "MESU2025",
                                     "long", 10, 5500.0, uuid.uuid4())
    await svc.set_rearm_state(db, "ra2b2_e2", "acct", "MESU2025", _estado_ok())
    foto = (ps.state, ps.direction, ps.quantity, ps.entry_price,
            ps.entry_signal_id)
    estado = leer_estado(ps.risk_plan_json)
    estado["legs"][0] = marcar_assumed_filled(estado["legs"][0])
    await svc.set_rearm_state(db, "ra2b2_e2", "acct", "MESU2025", estado)
    # E2: SOLO cambió risk_plan_json["rearm"] — la posición es idéntica
    assert (ps.state, ps.direction, ps.quantity, ps.entry_price,
            ps.entry_signal_id) == foto
    assert ps.risk_plan_json["rearm"]["legs"][0]["state"] == "assumed_filled"


# ═══════════════════════════════════════════════════════════════════════════
# 6) Integración: el despacho REAL siembra (con enabled) / no siembra (sin)
# ═══════════════════════════════════════════════════════════════════════════

from tests.test_despacho_e2e_lx import _MD_ES, _body, _raw, _seed  # noqa: E402


def _cfg_escalonada(rearm=None, ttl=3600):
    cfg = {"backstop_points": 12.0, "tp_nominal_long": 15.0,
           "entry_reserve_timeout_seconds": ttl,
           "scale_entry": {"mode": "execute", "quantities": [1, 1, 1],
                           "levels": [1.0, 2.0], "max_micro_contracts": 10}}
    if rearm is not None:
        cfg["scale_entry"]["rearm"] = rearm
    return cfg


async def _despacha(db, sid, cfg):
    from app.api.webhooks_luxalgo import process_signal
    await _seed(db, sid=sid, asset="MES", tv="MES", mapped="MESU2025",
                tick="0.25", base_webhook="https://webhooks.traderspost.io/x/t",
                pipeline_config=cfg)
    raw = await _raw(db, sid, _body("MES"))
    dec = await process_signal(db, sid, raw.id, _body("MES"), _MD_ES)
    assert dec.outcome == "APPROVE"
    row = (await db.execute(select(PositionState).where(
        PositionState.strategy_id == sid))).scalar_one()
    return row


@pytest.mark.asyncio
async def test_despacho_con_enabled_siembra_estado_valido(db: AsyncSession):
    row = await _despacha(db, "ra2b2_on",
                          _cfg_escalonada({"enabled": True, "max_ciclos": 3}))
    estado = leer_estado(row.risk_plan_json)
    assert estado is not None                             # legible fail-closed
    assert [l["leg_index"] for l in estado["legs"]] == [2, 3]
    assert all(l["cycle_n"] == 1 and l["state"] == "working"
               for l in estado["legs"])
    # los MISMOS números que viajaron al broker: ATR 8, P0 5500 → C2/C3
    assert estado["legs"][0]["limit_price"] == pytest.approx(5492.0)
    assert estado["legs"][1]["limit_price"] == pytest.approx(5484.0)
    assert estado["signal_atr"] == pytest.approx(8.0)
    assert estado["sl_price"] == pytest.approx(5488.0)    # P0 − backstop 12
    assert "ttl_incoherente" not in row.risk_plan_json["rearm"]
    # y el comportamiento previo intacto (opened_at del entry approved)
    assert "opened_at" in row.risk_plan_json


@pytest.mark.asyncio
async def test_despacho_sin_rearm_no_siembra_nada(db: AsyncSession):
    # rearm AUSENTE → cero cambio de comportamiento
    row = await _despacha(db, "ra2b2_off", _cfg_escalonada(None))
    assert "rearm" not in (row.risk_plan_json or {})
    assert "opened_at" in row.risk_plan_json              # lo previo intacto


@pytest.mark.asyncio
async def test_despacho_rearm_sin_enabled_explicito_no_siembra(db: AsyncSession):
    # rearm presente pero SIN enabled=true explícito → OFF igual (jamás nace sola)
    row = await _despacha(db, "ra2b2_off2",
                          _cfg_escalonada({"max_ciclos": 3}))
    assert "rearm" not in (row.risk_plan_json or {})


@pytest.mark.asyncio
async def test_despacho_e1_ttl_distinto_registra_incoherente(db: AsyncSession):
    row = await _despacha(db, "ra2b2_e1",
                          _cfg_escalonada({"enabled": True}, ttl=1800))
    assert row.risk_plan_json["rearm"]["ttl_incoherente"] is True
    assert leer_estado(row.risk_plan_json) is not None    # legible, con el flag
