"""LOTE SIZING-GATEWAY (2026-07-21) — la cantidad de TODA entrada la decide
NTEXECG, jamás la alerta.

Doctrina del operador: NTEXECG no es un passthrough, es un GATEWAY que SIEMPRE
reconstruye el payload. En execute/live la cantidad sale del reparto del
estudio (build_scaled); en cualquier otro modo, el MODO TESTIGO (1 micro a
mercado). La quantity de la alerta queda SOLO como traza forense en
extras.signal_quantity. Absorbe A-5 (cota de quantity) y A-6 (el perfil
re-escala también en entrada simple).
"""
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from app.models.normalized_signal import NormalizedSignal
from app.services import dispatch_profiles as dprof
from app.services.payload_builder import MODO_TESTIGO_QTY, PayloadBuilder


def _signal(action="buy", sentiment="long", role="entry_long",
            price=5500.0, qty=1) -> NormalizedSignal:
    s = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="mes_strat",
        ticker_received="MES", mapped_symbol="MESU2025",
        action=action, sentiment=sentiment, signal_role=role,
        price=price, quantity=qty,
        signal_ts=datetime.now(timezone.utc), dedupe_key=uuid.uuid4().hex,
    )
    s.id = uuid.uuid4()
    return s


def _pr(sl=5484.0, tp=None, atr=8.0):
    return SimpleNamespace(sl_price=sl, tp_price=tp, score=100, atr_value=atr,
                           quality=None, filters_active=None,
                           market_data_provider="Mock")


def _cfg(mode, quantities=(5, 3, 2), profiles=None, **extra):
    cfg = {
        "sl_atr_multiplier": 2.5,
        "traderspost_webhook_url": "https://base.example/hook",
        "traderspost_enabled": True, "dry_run": False,
        "scale_entry": {"mode": mode, "levels": [0.75, 1.25],
                        "quantities": list(quantities),
                        "max_micro_contracts": 10},
        "profiles": profiles or [],
    }
    cfg.update(extra)
    return cfg


# ----------------------------------------------------------------- #
# (1) LA CANTIDAD NUNCA VIENE DE LA ALERTA                            #
# ----------------------------------------------------------------- #

def test_design_only_despacha_un_micro_a_mercado_sin_importar_la_alerta():
    # Alerta pide 7 (número arbitrario del backtest) → NTEXECG manda 1.
    out = PayloadBuilder().build_scaled(
        _signal(qty=7), None, _cfg("design_only"), _pr())
    assert len(out) == 1                              # una sola orden
    assert out[0]["quantity"] == MODO_TESTIGO_QTY == 1
    assert "orderType" not in out[0]                  # a mercado
    assert out[0]["extras"]["signal_quantity"] == 7   # forense, jamás en la orden


def test_alerta_quantity_jamas_viaja_queda_en_extras():
    payload = PayloadBuilder().build(_signal(qty=7), None, {}, _pr())
    assert payload["quantity"] == 1
    assert payload["extras"]["signal_quantity"] == 7


def test_modo_off_o_ausente_tambien_es_testigo():
    # Sin scale_entry (config vacía) y con mode="off": 1 micro en ambos.
    for cfg in ({}, _cfg("off")):
        out = PayloadBuilder().build_scaled(_signal(qty=9), None, cfg, _pr())
        assert len(out) == 1
        assert out[0]["quantity"] == 1


def test_execute_conserva_el_reparto_del_estudio_sin_regresion():
    out = PayloadBuilder().build_scaled(
        _signal(qty=99), None, _cfg("execute", (5, 3, 2)), _pr())
    assert [p["quantity"] for p in out] == [5, 3, 2]  # el reparto, NO 99
    assert "orderType" not in out[0]                  # C1 mercado
    assert out[1]["orderType"] == "limit" and out[2]["orderType"] == "limit"


def test_exit_sin_cambios_no_lleva_quantity():
    payload = PayloadBuilder().build(
        _signal(action="exit", sentiment="flat", role="exit_long", qty=5),
        None, {}, _pr(sl=None))
    assert "quantity" not in payload                  # aplana completo
    assert payload["extras"]["omitted_quantity"] == 5


# ----------------------------------------------------------------- #
# (2) A-6 — EL PERFIL RE-ESCALA TAMBIÉN EN ENTRADA SIMPLE (TESTIGO)   #
# ----------------------------------------------------------------- #

def _profiles():
    return [{"enabled": True, "name": "conservador",
             "webhook_url": "https://prof.example/hook",
             "quantities": [3, 2, 1], "max_contracts": 6}]


def test_a6_testigo_cada_destino_recibe_un_micro():
    # ANTES del lote: en design_only base Y perfil recibían el tamaño íntegro
    # de la alerta (GC mandó 2 a base Y 2 al conservador). AHORA: cada destino
    # cae a build()→testigo, así que base=1 y perfil=1 (NX-02: 1 ≤ 1).
    cfg = _cfg("design_only", profiles=_profiles())
    dests = dprof.resolve_destinations(cfg)
    assert len(dests) == 2                             # base + conservador
    for dest in dests:
        dc = dprof.make_dest_config(cfg, dest)
        out = PayloadBuilder().build_scaled(_signal(), None, dc, _pr())
        assert len(out) == 1 and out[0]["quantity"] == 1


def test_a6_execute_el_perfil_reescala_su_reparto():
    cfg = _cfg("execute", profiles=_profiles())
    dests = dprof.resolve_destinations(cfg)
    by_name = {}
    for dest in dests:
        dc = dprof.make_dest_config(cfg, dest)
        out = PayloadBuilder().build_scaled(_signal(), None, dc, _pr())
        by_name[dest["name"]] = [p["quantity"] for p in out]
    assert by_name[None] == [5, 3, 2]                  # base: reparto del estudio
    assert by_name["conservador"] == [3, 2, 1]         # perfil: su reparto (cap 6)


def test_nx02_el_perfil_jamas_supera_a_la_base():
    # El perfil solo puede ENDURECER: total del perfil ≤ total de la base.
    cfg = _cfg("execute", (5, 3, 2), profiles=_profiles())
    dests = dprof.resolve_destinations(cfg)
    totals = {}
    for dest in dests:
        dc = dprof.make_dest_config(cfg, dest)
        out = PayloadBuilder().build_scaled(_signal(), None, dc, _pr())
        totals[dest["name"]] = sum(p["quantity"] for p in out)
    assert totals["conservador"] <= totals[None]       # 6 ≤ 10


# ----------------------------------------------------------------- #
# (3) INTERACCIONES: short_size_factor · max_micro_contracts          #
# ----------------------------------------------------------------- #

def test_short_size_factor_sobre_un_micro_se_queda_en_uno():
    # Un corto TESTIGO no baja de 1: max(1, round(1·0.5)) = 1. El factor viaja
    # como traza (efecto nulo sobre el piso).
    cfg = _cfg("design_only", short_size_factor=0.5)
    out = PayloadBuilder().build_scaled(
        _signal(action="sell", sentiment="short", role="entry_short"),
        None, cfg, _pr(sl=5516.0))
    assert len(out) == 1 and out[0]["quantity"] == 1
    assert out[0]["extras"]["short_size_factor"] == 0.5


def test_execute_short_size_factor_reparte_sin_regresion():
    # En execute el factor SÍ reduce (mayor resto conserva el total objetivo).
    cfg = _cfg("execute", (5, 3, 2), short_size_factor=0.5)
    out = PayloadBuilder().build_scaled(
        _signal(action="sell", sentiment="short", role="entry_short"),
        None, cfg, _pr(sl=5516.0))
    total = sum(p["quantity"] for p in out)
    assert total == 5                                  # round(10·0.5)
