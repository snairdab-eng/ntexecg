"""LOTE L2 — estudio Luxy sobre el motor: evaluador BE pesimista, disciplina
OOS, reparto, suelo, fills con corte, degradado, reproducibilidad y
reconciliación contra v1.

Los 8 adversariales del encargo (rojo→verde)."""
import glob
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts.mr_sims import (
    BALANCEADA, SimTrade, from_trades, metrics_usd, eval_config, leg_filled,
)
import scripts.mr_luxy as mrl

UTC = timezone.utc

_ES_CSV = sorted(glob.glob("ListaDeOperaciones/*_ES1!_*.csv"))
_ES_HOLC = Path("NINJATRADER/HOLC/ES_5m.csv")
_HAY_DATOS = bool(_ES_CSV) and _ES_HOLC.exists()


def _st(number, side="long", pnl=100.0, mae_atr=0.5, mfe_atr=3.0,
        atr=10.0, entry=5000.0, in_sample=True) -> SimTrade:
    return SimTrade(
        number=number, side=side, in_sample=in_sample, entry_price=entry,
        atr_pts=atr, mae_pts=mae_atr * atr, mfe_pts=mfe_atr * atr,
        native_pnl_usd=pnl)


# ---------------------------------------------------------------------------
# 1. Barra ambigua del BE → desenlace PESIMISTA (R-T3)
# ---------------------------------------------------------------------------

def test_be_iii_clean_mismo_minuto_be_tp_gana_be():
    """(iii) Retorno LIMPIO en el mismo minuto que el TP → gana BE (0)."""
    st = _st(1, mae_atr=0.6, mfe_atr=5.0, atr=10.0)     # no stop, arma y toca TP
    ex, motivo = mrl._luxy_exit_atr(
        st, sl_atr=9.0, be_atr=2.0, tp_atr=4.0,
        t_sl=None, t_tp=5.0, be_return=(5.0, "clean"), native_close_atr=3.0)
    assert ex == 0.0 and motivo == "breakeven", (ex, motivo)


def test_be_ii_clean_mismo_minuto_sl_be_gana_sl():
    """(ii) Retorno LIMPIO en el mismo minuto que el SL → gana el stop."""
    st = _st(2, mae_atr=1.2, mfe_atr=3.0, atr=10.0)     # stopped (mae≥sl) y armó
    ex, motivo = mrl._luxy_exit_atr(
        st, sl_atr=1.0, be_atr=2.0, tp_atr=8.0,
        t_sl=4.0, t_tp=None, be_return=(4.0, "clean"), native_close_atr=-1.0)
    assert ex == -1.0 and motivo == "stop", (ex, motivo)


def test_be_i_retorno_antes_de_armar_no_dispara():
    """(i) Retorno a 0 ANTES de armar → be_return None → BE NO dispara (corre a
    TP). Es el hueco que el primer-toque puro no resolvía."""
    st = _st(3, mae_atr=0.6, mfe_atr=5.0, atr=10.0)
    ex, motivo = mrl._luxy_exit_atr(
        st, sl_atr=9.0, be_atr=2.0, tp_atr=4.0,
        t_sl=None, t_tp=5.0, be_return=None, native_close_atr=3.0)
    assert motivo == "tp" and ex == 4.0, (ex, motivo)


def test_be_same_bar_ganadora_recortada_a_cero():
    """MISMA barra arm+retorno en una GANADORA (llegaría a TP) → recortada a 0
    (motivo breakeven_ambiguo): en producción esa barra ejecuta el stop de BE."""
    st = _st(4, mae_atr=0.6, mfe_atr=5.0, atr=10.0)     # no stop, tocaría TP=+4
    ex, motivo = mrl._luxy_exit_atr(
        st, sl_atr=9.0, be_atr=2.0, tp_atr=4.0,
        t_sl=None, t_tp=5.0, be_return=(5.0, "same_bar"), native_close_atr=3.0)
    assert ex == 0.0 and motivo == "breakeven_ambiguo", (ex, motivo)


def test_be_same_bar_perdedora_conserva_desenlace():
    """MISMA barra en una PERDEDORA (cierre nativo negativo) → NO se rescata:
    conserva su desenlace (< 0), no queda en 0."""
    st = _st(5, side="long", pnl=-90.0, mae_atr=0.6, mfe_atr=3.0, atr=10.0)
    # native_close_atr negativo, sin stop ni TP alcanzados
    ex, motivo = mrl._luxy_exit_atr(
        st, sl_atr=9.0, be_atr=2.0, tp_atr=8.0,
        t_sl=None, t_tp=None, be_return=(5.0, "same_bar"),
        native_close_atr=-0.6)
    assert ex == -0.6 and motivo == "native", (ex, motivo)


# ---- walk aditivo be_return_minutes: casos (i) y (iii) sobre barras reales ----

class _WT:
    """Trade sintético mínimo para el walk (atributos que usa be_return_minutes /
    touch_minutes)."""
    def __init__(self, side, ref, entry, atr_pct, aligned_ts, exit_ts):
        self.side = side
        self.bar_close = ref
        self.entry_price = entry
        self.atr_pct = atr_pct
        self.aligned_ts = aligned_ts
        self.exit_ts = exit_ts
        self.entry_ts = aligned_ts


def _bars(seq, t0):
    """seq = [(high, low)] a partir de t0; incluye una barra pre-entrada."""
    keys = [t0 - timedelta(minutes=5)] + [t0 + timedelta(minutes=5 * i)
                                          for i in range(len(seq))]
    bars = {keys[0]: (5000.0, 5000.0, 5000.0, 5000.0, 0.0)}
    for i, (hi, lo) in enumerate(seq):
        k = t0 + timedelta(minutes=5 * i)
        bars[k] = (lo, hi, lo, hi, 0.0)
    idx = {k: i for i, k in enumerate(keys)}
    return keys, idx, bars


def test_be_return_walk_i_retorno_antes_de_armar():
    from scripts.lab_analyze import be_return_minutes
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    # atr_pct 0.2 → atr_pts=10; be=2 arma con favor≥0.4% → high≥5020. La barra
    # alineada (t0) se excluye (pre-entrada). t0+5: dip a 4990 (retorno) SIN
    # armar; t0+10: arma (high 5030) sin dip; t0+15: 5010/5005 (no retorno).
    keys, idx, bars = _bars([(5000.0, 5000.0), (5005.0, 4990.0),
                             (5030.0, 5025.0), (5010.0, 5005.0)], t0)
    wt = _WT("long", 5000.0, 5000.0, 0.2, t0, t0 + timedelta(minutes=40))
    out = be_return_minutes(wt, keys, idx, bars, (2.0,))
    assert out["2.0"] is None            # retorno previo al armado no cuenta


def test_be_return_walk_iii_retorno_posterior():
    from scripts.lab_analyze import be_return_minutes, touch_minutes
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    # t0 excluida (pre-entrada); t0+5: arma (high 5030); t0+10: high 5060
    # (TP tp=5 → ≥5050) y low 4998 (retorno a BE) en la MISMA barra →
    # be_return y TP en el mismo minuto (10).
    keys, idx, bars = _bars([(5000.0, 5000.0), (5030.0, 5028.0),
                             (5060.0, 4998.0)], t0)
    wt = _WT("long", 5000.0, 5000.0, 0.2, t0, t0 + timedelta(minutes=40))
    br = be_return_minutes(wt, keys, idx, bars, (2.0,))
    _adv, fav = touch_minutes(wt, keys, idx, bars, adverse_lvls=(),
                              favor_lvls=(5.0,))
    assert br["2.0"] == (10.0, "clean") and fav["5.0"] == 10.0   # limpio, mismo min


def test_be_return_walk_same_bar():
    """(iii-same_bar) Una barra que sube al trigger Y baja a la entrada → la
    barra de armado se marca same_bar (ambigua)."""
    from scripts.lab_analyze import be_return_minutes
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    # t0 excluida; t0+5: high 5060 (arma be=2) y low 4998 (vuelve a la entrada)
    # en la MISMA barra → same_bar en el minuto 5.
    keys, idx, bars = _bars([(5000.0, 5000.0), (5060.0, 4998.0)], t0)
    wt = _WT("long", 5000.0, 5000.0, 0.2, t0, t0 + timedelta(minutes=40))
    out = be_return_minutes(wt, keys, idx, bars, (2.0,))
    assert out["2.0"] == (5.0, "same_bar")


def test_be_solo_recomienda_si_mejora_pesimista():
    """derive_breakeven no recomienda un BE que no mejora bajo la convención:
    ganadoras que corren y NUNCA retornan (be_ret vacío) → BE nunca dispara."""
    win = [_st(i, side="long", pnl=500.0, mae_atr=0.2, mfe_atr=6.0)
           for i in range(1, 11)]
    touches = {s.number: ({6.0: 1.0}, {}) for s in win}   # (fav, be_ret vacío)
    be = mrl.derive_breakeven(
        win, 50.0, b_pts=180.0, tp_by_side={"long": 6.0, "short": 6.0},
        legs=((0.0, 1.0),), suelo=1.0, cancel_after_s=3600.0, touches=touches,
        has_intrabar=True)
    assert be["disponible"] is True
    assert be["be_atr"] is None            # ningún BE mejora → no se recomienda


# ---------------------------------------------------------------------------
# 3. Fills con corte ≤ sin corte en toda la grilla (R-T1)
# ---------------------------------------------------------------------------

def test_fills_con_corte_menor_igual_sin_corte():
    sts = []
    for i in range(1, 21):
        st = SimTrade(number=i, side="long", in_sample=True, entry_price=5000.0,
                      atr_pts=10.0, mae_pts=30.0, mfe_pts=20.0,
                      native_pnl_usd=50.0,
                      pb_touch_min={"1.0": 10.0, "2.0": 80.0, "3.0": None})
        sts.append(st)
    for lvl in (0.5, 1.0, 2.0, 3.0):
        sin = sum(1 for s in sts if leg_filled(s, lvl, None)[0])
        con = sum(1 for s in sts if leg_filled(s, lvl, 3600.0)[0])
        assert con <= sin, f"nivel {lvl}: corte {con} > sin corte {sin}"


# ---------------------------------------------------------------------------
# 4. Suelo del SL = MAE p95 de GANADORAS (R-T5)
# ---------------------------------------------------------------------------

def test_suelo_sl_es_mae_p95_ganadoras():
    from scripts.mr_sims import mae_floor_study
    win = ([_st(i, side="long", pnl=100.0, mae_atr=1.0 + i * 0.1)
            for i in range(1, 11)] +
           [_st(20 + i, side="long", pnl=-100.0, mae_atr=5.0)
            for i in range(1, 6)])
    lev = mrl.derive_levers(win, 50.0, cancel_after_s=3600.0, touches=None,
                            has_intrabar=False)
    floor_v1 = mae_floor_study(win, 50.0)["ganadoras_mae_atr"]["p95"]
    assert lev["suelo_mae_p95_ganadoras"] == floor_v1
    # el SL reportado en la Tabla B nunca queda por debajo del suelo
    assert mrl._lever_summary(lev)["SL_suelo_atr"] == floor_v1


# ---------------------------------------------------------------------------
# 5. Reparto: suma total, C1≥1, mayor residuo (fronteras f2=f3=0 y f2=1)
# ---------------------------------------------------------------------------

def test_reparto_alloc_fronteras():
    assert mrl.alloc_from([1.0, 0.0, 0.0]) == [10, 0, 0]     # sin pullbacks
    assert mrl.alloc_from([1.0, 1.0, 0.0]) == [5, 5, 0]      # f2=1, f3=0
    a = mrl.alloc_from([1.0, 1.0, 1.0])
    assert sum(a) == 10 and a[0] >= 1 and a == [4, 3, 3]     # mayor residuo
    b = mrl.alloc_from([0.0, 1.0, 1.0])                      # C1 forzado ≥1
    assert sum(b) == 10 and b[0] >= 1


# ---------------------------------------------------------------------------
# Fixtures de trades falsos (Trade-like) para el estudio completo
# ---------------------------------------------------------------------------

class _FakeTrade:
    def __init__(self, number, side, entry_price, atr_entry, mae_pct, mfe_pct,
                 pnl_usd, entry_ts):
        self.number = number
        self.side = side
        self.entry_price = entry_price
        self.atr_entry = atr_entry
        self.mae_pct = mae_pct
        self.mfe_pct = mfe_pct
        self.pnl_usd = pnl_usd
        self.entry_ts = entry_ts
        self.in_sample = True
        self.t_pb_touch = {}


def _fake_trades(n=30):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    out = []
    for i in range(n):
        side = "long" if i % 2 == 0 else "short"
        win = (i % 3 != 0)
        pnl = 120.0 if win else -90.0
        mae = 0.3 if win else 1.6          # % de precio
        mfe = 1.2 if win else 0.4
        out.append(_FakeTrade(i + 1, side, 5000.0, 10.0, mae, mfe, pnl,
                              t0 + timedelta(hours=i)))
    return out


# ---------------------------------------------------------------------------
# 2. Contaminación OOS: alterar OOS no cambia las palancas in-sample y viceversa
# ---------------------------------------------------------------------------

def test_contaminacion_oos_no_cambia_palancas():
    trades = _fake_trades(30)
    r1 = mrl.luxy_study([t for t in trades], 50.0, oos=0.3, has_intrabar=False)
    # alterar SOLO los trades OOS (últimos 30%) — pnl al extremo
    trades2 = _fake_trades(30)
    n_in = r1["split"]["n_in_sample"]
    for t in trades2[n_in:]:
        t.pnl_usd = -99999.0
    r2 = mrl.luxy_study(trades2, 50.0, oos=0.3, has_intrabar=False)
    assert r2["levers_in_sample"] == r1["levers_in_sample"], \
        "alterar OOS cambió una palanca in-sample (contaminación)"

    # alterar SOLO in-sample no cambia la derivación OOS
    trades3 = _fake_trades(30)
    for t in trades3[:n_in]:
        t.pnl_usd = 99999.0
    r3 = mrl.luxy_study(trades3, 50.0, oos=0.3, has_intrabar=False)
    assert r3["levers_oos"] == r1["levers_oos"], \
        "alterar in-sample cambió la derivación OOS"


# ---------------------------------------------------------------------------
# 6. Degradado: estudio limitado sin tronar; BE/intrabar no disponibles
# ---------------------------------------------------------------------------

def test_degradado_estudio_limitado():
    trades = _fake_trades(20)
    r = mrl.luxy_study(trades, 50.0, oos=0.3, has_intrabar=False)
    assert r["degradado"] is True
    assert r["avisos"]
    assert r["levers_in_sample"]["breakeven"]["disponible"] is False
    # el crudo SIEMPRE existe
    crudo = next(f for f in r["tabla_a"] if f["fila"] == "Crudo")
    assert crudo["n"] == 20 and crudo["net_usd"] is not None


# ---------------------------------------------------------------------------
# 7. Reproducibilidad: dos corridas sobre el mismo master → JSON idéntico
# ---------------------------------------------------------------------------

def test_reproducibilidad_json_identico():
    a = mrl.luxy_study(_fake_trades(30), 50.0, oos=0.3, has_intrabar=False,
                       fecha="2026-07-11")
    b = mrl.luxy_study(_fake_trades(30), 50.0, oos=0.3, has_intrabar=False,
                       fecha="2026-07-11")
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------------------
# 8a. Reconciliación (unidad): luxy_outcome sin BE ≡ mr_sims.ladder_outcome
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# L3 — partición ÚNICA de sesiones (R-T7), unidades FX, payload dashboard
# ---------------------------------------------------------------------------

def test_zones_particion_unica_rt7():
    # cobertura total 0..23 sin solapes
    covered = [h for _n, _e, hrs in mrl.LUXY_ZONES for h in hrs]
    assert sorted(covered) == list(range(24))
    assert len(covered) == 24                      # sin duplicados
    assert mrl.zone_of_hour(9) == "Apertura US"
    assert mrl.zone_of_hour(3) == "Europa/Londres"
    assert mrl.zone_of_hour(None) is None


class _DT:
    def __init__(self, hour, dow, dur_min):
        from datetime import datetime as _d, timedelta as _td
        base = _d(2026, 1, 5, 0, 0, tzinfo=UTC) + timedelta(days=dow, hours=hour)
        self.entry_ts = base
        self.exit_ts = base + _td(minutes=dur_min)
        self.hour = hour


def _payload_fixture(ppt=50.0):
    sts = []
    fakes = {}
    for i in range(1, 25):
        side = "long" if i % 2 else "short"
        win = i % 3 != 0
        st = _st(i, side=side, pnl=120.0 if win else -90.0,
                 mae_atr=0.4 if win else 1.6, mfe_atr=1.5 if win else 0.4)
        sts.append(st)
        fakes[i] = _DT(hour=(i % 24), dow=(i % 5), dur_min=45 if win else 200)
    levers = mrl.derive_levers(sts, ppt, cancel_after_s=3600.0, touches=None,
                               has_intrabar=False)
    crudo = metrics_usd([s.native_pnl_usd for s in sts])
    return mrl._dashboard_payload(sts, fakes, levers, ppt, crudo, crudo)


def test_dashboard_payload_zonas_front_igual_motor():
    d = _payload_fixture()
    # el front renderiza reco.zones y zones_partition — AMBOS de LUXY_ZONES
    names_motor = [z[0] for z in mrl.LUXY_ZONES]
    assert [z["name"] for z in d["reco"]["zones"]] == names_motor
    assert [z["name"] for z in d["zones_partition"]] == names_motor
    assert [z["hours"] for z in d["zones_partition"]] == [z[2] for z in mrl.LUXY_ZONES]
    # el rango ET viaja junto al nombre (R-T7)
    assert all("ET" in z["et"] for z in d["reco"]["zones"])


def test_dashboard_units_fx_sin_pts():
    assert _payload_fixture(ppt=50.0)["units"]["show_pts"] is True       # índice
    assert _payload_fixture(ppt=1250.0)["units"]["show_pts"] is False    # FX: solo USD


def test_dashboard_payload_estructura():
    d = _payload_fixture()
    assert d["n"] == 24 and len(d["trades"]) == 24
    assert set(d["trades"][0]) >= {"i", "mfe", "mae", "pnl", "long", "hr", "dow", "in"}
    assert "net" in d["base"] and "net" in d["config"]
    assert d["timestop"]["verdict"] == "descartado"
    assert len(d["timestop"]["buckets"]) == 5


def test_be_honesty_client_estimate_en_template():
    """La estimación client-side NUNCA acredita el BE (excepción obligatoria):
    la nota está y el crédito optimista del prototipo (e=0 si pnl<0) NO está."""
    html = Path("app/templates/strategy_detail.html").read_text(encoding="utf-8")
    assert "BE: requiere recálculo del motor" in html
    assert "no acredita su beneficio" in html       # nota de la excepción
    # el crédito optimista del prototipo (§3) quedó FUERA de estimate()
    assert "d.pnl<0) e=0" not in html
    assert "d.mfe>=S.beV && d.pnl<0" not in html


def test_session_toggles_no_persist_note_en_template():
    html = Path("app/templates/strategy_detail.html").read_text(encoding="utf-8")
    assert "no persiste" in html            # bloqueo de sesiones = solo explorar


def test_reconciliacion_unidad_vs_v1():
    sts = [_st(i, side=("long" if i % 2 else "short"),
               pnl=(80.0 if i % 3 else -120.0),
               mae_atr=0.4 + (i % 5) * 0.5, mfe_atr=1.0 + (i % 4))
           for i in range(1, 25)]
    tp = {"long": 6.0, "short": 4.0}
    for st in sts:
        lux, v1 = mrl.reconcile_trade_vs_v1(
            st, BALANCEADA, 3000.0, tp, 50.0, 3600.0)
        assert lux == v1, f"trade {st.number}: luxy {lux} ≠ v1 {v1}"


# ---------------------------------------------------------------------------
# 8b. Reconciliación (datos reales de ES, gated): crudo idéntico a v1 +
#     palancas comparables (BALANCEADA+backstop, sin BE) = mismos números
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales de ES no disponibles")
def test_reconciliacion_es_real_vs_v1():
    from scripts.lab_analyze import (
        detect_tz_offset, enrich_with_bars, load_holc, parse_luxalgo_csv,
        split_in_out,
    )
    trades = parse_luxalgo_csv(Path(sorted(_ES_CSV)[-1]))
    bars = load_holc("ES", "5m")
    off, _s, _d = detect_tz_offset(trades, bars)
    enrich_with_bars(trades, bars, off)
    split_in_out(trades, 0.3)
    ppt = 50.0
    sts = from_trades(trades, ppt)

    # CRUDO idéntico: metrics_usd sobre los MISMOS pnl nativos (toda la muestra)
    crudo_luxy = metrics_usd([t.pnl_usd for t in trades])
    r = mrl.luxy_study([t for t in trades], ppt, oos=0.3, has_intrabar=False)
    fila_crudo = next(f for f in r["tabla_a"] if f["fila"] == "Crudo")
    assert fila_crudo["net_usd"] == crudo_luxy["net_usd"]
    assert fila_crudo["n"] == crudo_luxy["n"]

    # PALANCAS COMPARABLES: BALANCEADA + backstop, SIN BE → luxy ≡ v1 eval_config
    b_usd = 3000.0
    v1 = eval_config(sts, "recon", BALANCEADA, b_usd, ppt, None,
                     cancel_after_s=3600.0)
    lux_pnls = []
    b_pts = b_usd / ppt
    for st in sts:
        usd, _ = mrl.luxy_outcome(st, {}, {}, legs=BALANCEADA, b_pts=b_pts,
                                  tp_by_side=None, be_atr=None, ppt=ppt,
                                  cancel_after_s=3600.0)
        lux_pnls.append(usd)
    lux = metrics_usd(lux_pnls)
    assert round(lux["net_usd"], 2) == round(v1["total"]["net_usd"], 2), \
        f"reconciliación palancas: luxy {lux['net_usd']} ≠ v1 {v1['total']['net_usd']}"
