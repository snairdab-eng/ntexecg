"""MR-2 — estudios de riesgo (mr_sims): respuestas conocidas + datos reales.

Unitarios a mano (escalera, backstop, TP, gating) + integración ES real:
paridad TP con resim_rows (Directiva: el núcleo del Lab es el ancla de
verificación), reconciliación de fills escalera↔pullback del Lab, y el
`calcular` end-to-end persistiendo runs/estudios_<fecha>.json.
"""
import asyncio
import glob
import json
from pathlib import Path

import pytest

from scripts.mr_sims import (
    BALANCEADA,
    CONFIG_A,
    SENAL,
    HaircutCfg,
    SimTrade,
    backstop_sweep,
    eval_config,
    gate_config,
    ladder_outcome,
    reconcile_fills,
    tp_nominal_study,
)

_ES_CSV = sorted(glob.glob("ListaDeOperaciones/*_ES1!_*.csv"))
_ES_HOLC = Path("NINJATRADER/HOLC/ES_5m.csv")
_HAY_DATOS = bool(_ES_CSV) and _ES_HOLC.exists()

PPT = 50.0
HC0 = HaircutCfg()


def st(number=1, side="long", in_sample=True, atr=4.0, mae=0.0, mfe=0.0,
       pnl_usd=0.0, entry=7000.0):
    return SimTrade(number=number, side=side, in_sample=in_sample,
                    entry_price=entry, atr_pts=atr, mae_pts=mae,
                    mfe_pts=mfe, native_pnl_usd=pnl_usd)


# ---------------------------------------------------------------------------
# Escalera (a mano)
# ---------------------------------------------------------------------------

def test_ladder_config_a_sin_stop():
    # ATR 4, MAE 30 pts (7.5×) → ambas piernas llenan; nativo −$500 (−10 pts)
    # pierna 6.5×: −10 + 26 = +16 pts · pierna 7.0×: −10 + 28 = +18 pts
    # 0.6·16 + 0.4·18 = 16.8 pts → $840
    t = st(atr=4.0, mae=30.0, pnl_usd=-500.0)
    usd, fw, amb = ladder_outcome(t, CONFIG_A, b_pts=100.0,
                                  tp_atr_by_side=None, ppt=PPT, hc=HC0)
    assert usd == pytest.approx(840.0)
    assert fw == pytest.approx(1.0)
    assert amb is False


def test_ladder_config_a_stopped():
    # MAE 120 ≥ backstop 100 → piernas salen en el stop anclado a la señal:
    # −(100−26) = −74 · −(100−28) = −72 → 0.6·−74 + 0.4·−72 = −73.2 → −$3,660
    t = st(atr=4.0, mae=120.0, pnl_usd=-9000.0)
    usd, fw, _ = ladder_outcome(t, CONFIG_A, 100.0, None, PPT, HC0)
    assert usd == pytest.approx(-3660.0)
    assert fw == pytest.approx(1.0)


def test_ladder_pierna_profunda_no_llena():
    # MAE 6.8×ATR: llena 6.5× pero no 7.0× → solo 0.6 del contrato
    t = st(atr=4.0, mae=27.2, pnl_usd=1000.0)     # nativo +20 pts
    usd, fw, _ = ladder_outcome(t, CONFIG_A, 100.0, None, PPT, HC0)
    # pierna 6.5×: 20 + 26 = 46 pts · 0.6 → 27.6 pts → $1,380
    assert usd == pytest.approx(1380.0)
    assert fw == pytest.approx(0.6)


def test_ladder_tp_anclado_a_senal():
    # short, TP 1.0×ATR, MFE 2×ATR ≥ TP → pierna 6.5× gana (1+6.5)·4 = 30 pts
    t = st(side="short", atr=4.0, mae=27.0, mfe=8.0, pnl_usd=-200.0)
    usd, fw, amb = ladder_outcome(t, ((6.5, 1.0),), None,
                                  {"short": 1.0}, PPT, HC0)
    assert usd == pytest.approx(30.0 * PPT)
    assert amb is True                    # orden pierna↔TP asumido


def test_ladder_stop_manda_sobre_tp():
    # alcanzó TP y backstop → conservador: manda el stop
    t = st(atr=4.0, mae=120.0, mfe=50.0, pnl_usd=0.0)
    usd, _, _ = ladder_outcome(t, SENAL, 100.0, {"long": 2.0}, PPT, HC0)
    assert usd == pytest.approx(-100.0 * PPT)


def test_ladder_haircut_gap_y_comision():
    hc = HaircutCfg(comision_rt_usd=10.0, gap_pts=5.0)
    t = st(atr=4.0, mae=120.0, pnl_usd=0.0)
    usd, _, _ = ladder_outcome(t, SENAL, 100.0, None, PPT, hc)
    assert usd == pytest.approx(-(100.0 + 5.0) * PPT - 10.0)


def test_senal_mas_backstop_pariedad_con_sweep():
    """eval_config(SENAL, B) ≡ fila B del backstop_sweep (dos caminos, un
    resultado — candado interno del motor)."""
    sts = [st(1, mae=120.0, pnl_usd=-9000.0),
           st(2, mae=20.0, pnl_usd=1500.0, in_sample=False),
           st(3, mae=60.0, pnl_usd=-800.0)]
    sweep = backstop_sweep(sts, PPT, HC0, grid_usd=(5000.0,))
    row = sweep["grid"][0]
    cfg = eval_config(sts, "señal+B", SENAL, 5000.0, PPT)
    assert cfg["total"]["net_usd"] == row["net_usd"]
    assert cfg["total"]["max_dd_usd"] == row["max_dd_usd"]
    assert cfg["total"]["peor_trade_usd"] == row["peor_trade_usd"]
    assert row["tocados"] == 1
    assert row["peor_con_gap_usd"]["25.0"] == pytest.approx(-(100 + 25) * PPT)


# ---------------------------------------------------------------------------
# TP nominal (a mano)
# ---------------------------------------------------------------------------

def test_tp_nominal_por_encima_del_p99():
    # ganadoras long cierran en 1..4 ×ATR (ATR 4 → pnl 4·k pts → $200·k)
    sts = [st(i, atr=4.0, mfe=4.0 * k + 2, pnl_usd=200.0 * k)
           for i, k in enumerate([1, 2, 3, 4], start=1)]
    sts.append(st(9, atr=4.0, mae=10.0, pnl_usd=-500.0))    # perdedor
    r = tp_nominal_study(sts, PPT, HC0)
    lado = r["por_lado"]["long"]
    p99 = lado["cierre_atr"]["p99"]
    tp = lado["tp_nominal_atr"]
    assert tp > p99                        # estrictamente POR ENCIMA
    assert tp == 4.0                       # p99≈3.97 → medio paso arriba
    assert lado["n_ganadoras"] == 4
    # en la mesa = Σ(MFE−salida) de ganadoras = Σ 2 pts·$50 = $400
    assert lado["en_la_mesa_usd"] == pytest.approx(400.0)


# ---------------------------------------------------------------------------
# Gating (a mano)
# ---------------------------------------------------------------------------

def _cfg_fake(net, dd, pf_out, etiquetas=()):
    return {"total": {"n": 10, "net_usd": net, "max_dd_usd": dd},
            "out": {"pf": pf_out}, "low_n_out": False,
            "etiquetas": list(etiquetas)}


BASE_FAKE = _cfg_fake(10000.0, 5000.0, 1.5)          # score 2.0


def test_gate_aprobada():
    g = gate_config(_cfg_fake(9000.0, 3000.0, 2.0), BASE_FAKE)   # score 3.0
    assert g["estado"] == "aprobada"      # cede net pero mejora score y OOS


def test_gate_descartado():
    g = gate_config(_cfg_fake(8000.0, 6000.0, 1.0), BASE_FAKE)
    assert g["estado"] == "descartado – no aporta"


def test_gate_no_sobrevive_oos():
    g = gate_config(_cfg_fake(12000.0, 4000.0, 1.2), BASE_FAKE)
    assert g["estado"] == "no sobrevive OOS"


def test_gate_no_supera_base():
    g = gate_config(_cfg_fake(11000.0, 6000.0, 2.0), BASE_FAKE)
    assert g["estado"] == "no supera la base (score)"


# ---------------------------------------------------------------------------
# Reconciliación (a mano)
# ---------------------------------------------------------------------------

def test_reconcile_fills_deltas():
    sts = [st(1, atr=4.0, mae=4.0), st(2, atr=4.0, mae=8.0),
           st(3, atr=4.0, mae=1.0), st(4, atr=4.0, mae=0.5)]
    # mae_atr: 1.0, 2.0, 0.25, 0.125
    r = reconcile_fills(sts, {0.5: 50.0, 1.0: 50.0, 2.0: 30.0})
    por_lvl = {f["nivel_atr"]: f for f in r["niveles"]}
    assert por_lvl[0.5]["fill_mae_pct"] == 50.0      # 2 de 4
    assert por_lvl[1.0]["fill_mae_pct"] == 50.0
    assert por_lvl[2.0]["fill_mae_pct"] == 25.0
    assert por_lvl[2.0]["delta_pp"] == -5.0
    assert r["max_delta_somero_pp"] == 5.0


# ---------------------------------------------------------------------------
# Integración ES real: paridad con resim_rows + calcular end-to-end
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales ES no disponibles")
class TestESReal:

    @pytest.fixture(scope="class")
    def corrida(self, tmp_path_factory):
        import scripts.nt_riesgo as nr

        motor_dir = tmp_path_factory.mktemp("MotorRiesgo")
        original = nr.MOTOR_DIR
        nr.MOTOR_DIR = motor_dir
        try:
            asyncio.run(nr.integrar(Path(_ES_CSV[-1]), codigo="test",
                                    stitch=False))
            res = asyncio.run(nr.calcular("ES_test", fecha="2026-07-04"))
        finally:
            nr.MOTOR_DIR = original
        return motor_dir, res

    def test_persistencia_y_manifest(self, corrida):
        motor_dir, res = corrida
        out = motor_dir / "ES_test" / "runs" / "estudios_2026-07-04.json"
        assert out.exists()
        en_disco = json.loads(out.read_text(encoding="utf-8"))
        assert en_disco["meta"]["fecha"] == "2026-07-04"
        man = json.loads((motor_dir / "ES_test" / "manifest.json")
                         .read_text(encoding="utf-8"))
        assert man["ultima_corrida"] == "estudios_2026-07-04.json"

    def test_backstop_recorta_riesgo(self, corrida):
        _, res = corrida
        opt = res["backstop"]["optimo"]
        assert opt is not None
        assert opt["delta_dd_pct"] < 0                 # el airbag recorta DD
        assert opt["peor_trade_usd"] == -opt["backstop_usd"]
        # estrés de gap: el peor con gap es peor que sin gap (honestidad)
        assert (opt["peor_con_gap_usd"]["25.0"]
                < opt["peor_con_gap_usd"]["0.0"])

    def test_sl_duro_descartado(self, corrida):
        _, res = corrida
        assert all(r["estado"].startswith("descartado")
                   for r in res["mae_floor"]["sl_duro_x_atr"])

    def test_motor_de_largos(self, corrida):
        _, res = corrida
        assert res["ls"]["lectura"].startswith("motor de LARGOS")
        assert res["ls"]["long"]["pf"] > res["ls"]["short"]["pf"]

    def test_tp_nominal_casi_nunca_dispara(self, corrida):
        _, res = corrida
        for lado, d in res["tp"]["por_lado"].items():
            # por construcción el TP va estrictamente por ENCIMA del p99
            # crudo; el p99 reportado está redondeado a 2 decimales → >=
            assert d["tp_nominal_atr"] >= d["cierre_atr"]["p99"]
            assert d["tp_nominal_dispararia_pct"] <= 10.0

    def test_reconciliacion_fills_lab(self, corrida):
        """Los fills por MAE de la escalera coinciden con el pullback del
        Lab en los niveles someros (≤2×ATR) dentro de 10 pp (observado 4.8;
        el residuo = ventana 180 min del Lab vs límite todo-el-trade)."""
        _, res = corrida
        rec = res["reconciliacion_fills"]
        assert rec is not None and rec["max_delta_somero_pp"] is not None
        assert rec["max_delta_somero_pp"] <= 10.0

    def test_paridad_tp_con_resim_rows(self, corrida):
        """Ancla de verificación (Directiva 1): el disparo de TP del motor
        (mfe_atr ≥ tp, dominio puntos) coincide con resim_rows del Lab
        (mfe% ≥ tp·atr%, dominio %) dentro de 2 pp — misma semántica,
        denominadores distintos (entry vs close de barra)."""
        from app.services.lab_metrics import resim_rows
        from scripts.lab_analyze import (
            detect_tz_offset, enrich_with_bars, feature_rows, load_holc,
            parse_luxalgo_csv, split_in_out,
        )
        from scripts.mr_sims import from_trades

        trades = parse_luxalgo_csv(Path(_ES_CSV[-1]))
        bars = load_holc("ES", "5m")
        off, _, _ = detect_tz_offset(trades, bars)
        enrich_with_bars(trades, bars, off)
        split_in_out(trades, 0.3)
        covered = [t for t in trades if t.atr_pct]
        rows = feature_rows(covered)
        sts = from_trades(covered, PPT)
        for tp in (3.0, 6.0, 10.0):
            r = resim_rows(rows, tp=tp)
            for blk, ins in (("in", True), ("out", False)):
                sel = [s for s in sts if s.in_sample == ins]
                fired = 100 * sum(1 for s in sel if s.mfe_atr >= tp) / len(sel)
                assert abs(fired - r[blk]["tp_pct"]) <= 2.0, (tp, blk)

    def test_gating_coherente(self, corrida):
        _, res = corrida
        assert res["configs"]
        base_score = None
        for c in res["configs"]:
            g = c["gate"]
            if g["estado"] == "descartado – no aporta":
                assert g["delta_net_usd"] <= 0
            if g["estado"] == "aprobada" and g["score"] and g["score_base"]:
                assert g["score"] > g["score_base"]
        # alta participación presente y de primera clase (Directiva 3.1)
        alta = [c for c in res["configs"]
                if "alta_participacion" in c["etiquetas"]]
        assert alta and any(c["gate"]["estado"] == "aprobada" for c in alta)
        assert all(c["participacion_pct"] >= 80.0 for c in alta
                   if c["legs"][0]["depth_atr"] <= 0.5)
