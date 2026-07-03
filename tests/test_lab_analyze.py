"""Laboratorio Fase 1 — tests de respuesta conocida (parser, métricas, SL
re-sim, split in/out y detección BLOQUEANTE de zona horaria)."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from scripts.lab_analyze import (
    Trade,
    _ema_series,
    _first_touch,
    aggregate,
    baseline,
    compute_phase2_features,
    detect_tz_offset,
    enrich_with_bars,
    filter_lift,
    hourly_edge,
    parse_luxalgo_csv,
    resim_sl,
    resim_sl_tp,
    resim_tp,
    split_in_out,
)

# CSV sintético con el formato REAL de LuxAlgo (BOM, Salida antes que Entrada,
# columnas en español, MFE/MAE en USD y %):
_CSV = "﻿" + """Trade number,Tipo,Fecha y hora,Señal,Precio USD,Tamaño (cant.),Tamaño de la posición (valor),PyG netas USD,PyG netas %,Desviación favorable USD,Desviación favorable %,Desviación adversa USD,Desviación adversa %,PyG acumuladas USD,PyG acumuladas %
1,Salida en largo,2026-03-16 14:30,Scripted Exit All,6711.5,1,335150,425,0.13,575,0.17,-487.5,-0.15,425,4.25
1,Entrada en largo,2026-03-16 13:10,Scripted Long,6703,1,335150,425,0.13,575,0.17,-487.5,-0.15,425,4.25
2,Salida en corto,2026-03-17 03:20,Scripted Long,6727.25,1,335362.5,-1000,-0.30,562.5,0.17,-2262.5,-0.67,-575,-5.75
2,Entrada en corto,2026-03-16 15:20,Scripted Short,6707.25,1,335362.5,-1000,-0.30,562.5,0.17,-2262.5,-0.67,-575,-5.75
3,Salida en largo,2026-03-18 10:00,Scripted Exit All,6800,1,340000,850,0.25,900,0.26,-170,-0.05,275,2.75
3,Entrada en largo,2026-03-18 09:00,Scripted Long,6783,1,340000,850,0.25,900,0.26,-170,-0.05,275,2.75
"""


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    p = tmp_path / "lux_ES.csv"
    p.write_bytes(_CSV.encode("utf-8"))
    return p


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parser_pairs_and_fields(csv_file: Path):
    trades = parse_luxalgo_csv(csv_file)
    assert len(trades) == 3
    t1, t2, t3 = trades                       # ordenados por entry_ts
    assert (t1.number, t1.side) == (1, "long")
    assert t1.entry_ts == datetime(2026, 3, 16, 13, 10)
    assert t1.exit_ts == datetime(2026, 3, 16, 14, 30)
    assert t1.entry_price == 6703.0 and t1.exit_price == 6711.5
    assert t1.pnl_pct == 0.13 and t1.pnl_usd == 425.0
    assert t1.mfe_pct == 0.17
    assert t1.mae_pct == 0.15                 # |−0.15| — siempre positivo
    assert (t2.number, t2.side) == (2, "short")
    assert t2.pnl_pct == -0.30 and t2.mae_pct == 0.67
    assert t3.side == "long" and t3.pnl_pct == 0.25


# ---------------------------------------------------------------------------
# Métricas de línea base (respuesta conocida)
# ---------------------------------------------------------------------------

def test_aggregate_known_answer():
    m = aggregate([1.0, -0.5, 2.0, -1.0])
    assert m["n"] == 4
    assert m["wr"] == 50.0
    assert m["pf"] == 2.0                     # 3.0 / 1.5
    assert m["expectancy_pct"] == 0.375
    assert m["net_pct"] == 1.5
    assert m["worst_pct"] == -1.0
    # curva 1.0, 0.5, 2.5, 1.5 → peor caída desde pico = −1.0
    assert m["max_dd_pct"] == -1.0


def test_baseline_includes_mae_tail(csv_file: Path):
    trades = parse_luxalgo_csv(csv_file)
    split_in_out(trades, oos=0.3)
    b = baseline(trades)
    assert b["total"]["n"] == 3
    assert b["total"]["mae_p95_pct"] == 0.67  # p95 de {0.15, 0.67, 0.05}
    assert b["in"]["n"] == 2 and b["out"]["n"] == 1


# ---------------------------------------------------------------------------
# SL re-sim (Anexo 25 §8.1: SL ⟺ |mae%| ≥ k·atr%)
# ---------------------------------------------------------------------------

def _trade(pnl, mae, atr_pct, in_sample=True) -> Trade:
    t = Trade(number=1, side="long", entry_ts=datetime(2026, 3, 16, 13, 10),
              exit_ts=None, entry_price=100.0, exit_price=None,
              pnl_usd=pnl * 10, pnl_pct=pnl, mfe_pct=abs(pnl) + 0.1,
              mae_pct=mae)
    t.atr_pct = atr_pct
    t.in_sample = in_sample
    return t


def test_resim_sl_known_answer():
    # pnl=+1.0, mae=0.5, atr%=0.2 → k=2: umbral 0.4 ≤ 0.5 → SL, desenlace −0.4
    trades = [_trade(1.0, 0.5, 0.2)]
    m = resim_sl(trades, k=2.0)["in"]
    assert m["n"] == 1
    assert m["expectancy_pct"] == -0.4
    assert m["stopped_pct"] == 100.0
    # k=3: umbral 0.6 > 0.5 → no dispara, conserva +1.0
    m = resim_sl(trades, k=3.0)["in"]
    assert m["expectancy_pct"] == 1.0
    assert m["stopped_pct"] == 0.0


def test_resim_sl_skips_uncovered():
    t = _trade(1.0, 0.5, 0.2)
    t.atr_pct = None                          # sin cobertura de barras
    m = resim_sl([t], k=2.0)["in"]
    assert m["n"] == 0


# ---------------------------------------------------------------------------
# Fase 2 — TP re-sim, SL+TP conjunto (orden intrabar), EMA, lift, subscores
# ---------------------------------------------------------------------------

def test_resim_tp_known_answer():
    # mfe=1.0, atr%=0.2 → tp=4: umbral 0.8 ≤ 1.0 → +0.8; tp=6: 1.2 > 1.0 → pnl
    t = _trade(-0.3, 0.5, 0.2)
    t.mfe_pct = 1.0
    m = resim_tp([t], tp=4.0)["in"]
    assert m["expectancy_pct"] == 0.8 and m["tp_pct"] == 100.0
    m = resim_tp([t], tp=6.0)["in"]
    assert m["expectancy_pct"] == -0.3 and m["tp_pct"] == 0.0


def _flat_bars(start: datetime, seq: list[tuple[float, float]], base=100.0):
    """Barras 5m con (high, low) dados y close=base (para el walk intrabar)."""
    out = {}
    for i, (h, low) in enumerate(seq):
        ts = start + timedelta(minutes=5 * i)
        out[ts] = (base, h, low, base, 100.0)
    return out


def test_first_touch_orders():
    start = datetime(2026, 3, 16, 9, 0)
    # long @100, atr%=1 → k=2: SL 98; tp=3: TP 103
    t = _trade(0.5, 5.0, 1.0)          # mae y mfe alcanzan ambos umbrales
    t.mfe_pct = 5.0
    t.aligned_ts = start
    t.bar_close = 100.0
    t.exit_ts = None

    # baja a 97.9 primero (barra 1), luego sube → SL primero
    bars = _flat_bars(start, [(100.5, 99.5), (100.2, 97.9), (103.5, 99.0)])
    keys = sorted(bars)
    idx = {k: i for i, k in enumerate(keys)}
    assert _first_touch(t, 2.0, 3.0, keys, idx, bars) == "sl"

    # sube a 103.2 primero → TP primero
    bars = _flat_bars(start, [(100.5, 99.5), (103.2, 99.8), (100.0, 97.0)])
    keys = sorted(bars)
    idx = {k: i for i, k in enumerate(keys)}
    assert _first_touch(t, 2.0, 3.0, keys, idx, bars) == "tp"

    # ambos en la MISMA barra → ambiguo → SL (conservador)
    bars = _flat_bars(start, [(103.5, 97.5)])
    keys = sorted(bars)
    idx = {k: i for i, k in enumerate(keys)}
    assert _first_touch(t, 2.0, 3.0, keys, idx, bars) == "ambiguous_sl"


def test_resim_sl_tp_uses_touch_order():
    start = datetime(2026, 3, 16, 9, 0)
    t = _trade(0.5, 5.0, 1.0)
    t.mfe_pct = 5.0
    t.aligned_ts = start
    t.bar_close = 100.0
    bars = _flat_bars(start, [(103.2, 99.8), (100.0, 97.0)])  # TP primero
    keys = sorted(bars)
    idx = {k: i for i, k in enumerate(keys)}
    m = resim_sl_tp([t], 2.0, 3.0, keys, idx, bars)["in"]
    assert m["expectancy_pct"] == 3.0          # +tp·atr% = +3·1.0
    assert m["tp_pct"] == 100.0 and m["sl_pct"] == 0.0


def test_ema_series_known():
    ema = _ema_series([10.0] * 30, 20)
    assert ema[18] is None and ema[19] == 10.0 and ema[29] == 10.0
    rising = _ema_series(list(range(1, 61)), 20)
    assert rising[59] < 60 and rising[59] > rising[40]   # EMA persigue por abajo


def test_filter_lift_kept_pct():
    a, b = _trade(1.0, 0.1, 0.5), _trade(-1.0, 0.1, 0.5)
    a.sub_volume, b.sub_volume = 0.9, 0.3
    d = filter_lift([a, b], lambda t: (t.sub_volume or 0) * 100 >= 50)
    assert d["in"]["n"] == 1 and d["in"]["kept_pct"] == 50.0
    assert d["in"]["expectancy_pct"] == 1.0    # solo sobrevive el bueno


def test_phase2_features_time_of_day_tz(tmp_path, monkeypatch):
    """El adapter debe pasar signal_ts tz-aware: 10:30 ET = franja prime → 1.0
    (naive se trataría como UTC y correría la hora)."""
    import scripts.lab_analyze as lab

    start = datetime(2026, 3, 16, 4, 0)
    bars5 = _bars(start, 200)
    entry = start + timedelta(minutes=5 * 78)          # 10:30 ET
    assert entry.hour == 10 and entry.minute == 30
    t = _trade(1.0, 0.2, 0.5)
    t.entry_ts = entry
    t.aligned_ts = entry
    t.bar_close = bars5[entry][3]

    def _fake_load(sym, tf="5m"):
        assert tf in ("1h", "4h")
        return {start + timedelta(hours=i): (100, 101, 99, 100 + i, 10)
                for i in range(60)}

    monkeypatch.setattr(lab, "load_holc", _fake_load)
    compute_phase2_features([t], bars5, "ES")
    assert t.sub_time == 1.0                            # prime 10:00–11:30 ET
    assert t.regime_1h in ("trending_bull", "trending_bear", "ranging", "unknown")
    assert set(t.ema_with) == {"1h20", "1h50", "4h20", "4h50"}


# ---------------------------------------------------------------------------
# Detección de TZ (bloqueante) — barras sintéticas con offset conocido
# ---------------------------------------------------------------------------

def _bars(start: datetime, n: int, price0=100.0) -> dict:
    """Barras 5m sintéticas RUIDOSAS (determinista): un paseo con vaivenes
    grandes para que solo el offset correcto alinee precio↔barra (una serie
    lineal degenera la detección — cualquier offset da diffs constantes)."""
    out = {}
    px = price0
    for i in range(n):
        ts = start + timedelta(minutes=5 * i)
        out[ts] = (px, px + 0.5, px - 0.5, px + 0.1, 100.0)
        # ruido determinista APERIÓDICO (dos senos inconmensurables): un ruido
        # periódico realinea en su periodo y vuelve ambiguo el offset.
        import math
        px += 0.3 + math.sin(i * 0.7) * 6 + math.sin(i * 0.113) * 9
    return out


def test_detect_tz_offset_finds_known_shift():
    start = datetime(2026, 3, 16, 0, 0)
    bars = _bars(start, 600)
    # entradas cuyo CSV está 120 min DETRÁS del OHLC (offset real = +120)
    trades = []
    for i in range(30):
        bar_ts = start + timedelta(minutes=5 * (20 + i * 12))
        o, h, low, c, _ = bars[bar_ts]
        trades.append(Trade(number=i + 1, side="long",
                            entry_ts=bar_ts - timedelta(minutes=120),
                            exit_ts=None, entry_price=c, exit_price=None,
                            pnl_usd=0, pnl_pct=0, mfe_pct=0, mae_pct=0))
    off, sanity, detail = detect_tz_offset(trades, bars, sample=30)
    assert off == 120, f"offset detectado {off} (esperado +120)"
    assert sanity >= 0.9


def test_detect_tz_survives_roll_level_offset():
    """El continuo back-ajustado difiere del precio TV por un delta de NIVEL
    ~constante — la detección (MAD + corrección de nivel) debe absorberlo."""
    start = datetime(2026, 3, 16, 0, 0)
    bars = _bars(start, 600)
    trades = []
    for i in range(30):
        bar_ts = start + timedelta(minutes=5 * (20 + i * 12))
        c = bars[bar_ts][3]
        trades.append(Trade(number=i + 1, side="long", entry_ts=bar_ts,
                            exit_ts=None, entry_price=c - 65.0,  # δ de roll
                            exit_price=None, pnl_usd=0, pnl_pct=0,
                            mfe_pct=0, mae_pct=0))
    off, sanity, _ = detect_tz_offset(trades, bars, sample=30)
    assert off == 0
    assert sanity >= 0.9


def test_detect_tz_sparse_trades_with_roll_steps():
    """Caso YM: ~1 trade/semana durante meses, con ESCALONES de δ por roll
    trimestral (cientos de puntos). La dispersión global mezcla los escalones
    y ahoga el offset horario — el score local (pares ≤10 días) no."""
    import math
    start = datetime(2025, 8, 1, 0, 0)
    bars = {}
    px = 45000.0
    n_bars = 12 * 24 * 300              # 300 días de barras 5m
    for i in range(n_bars):
        ts = start + timedelta(minutes=5 * i)
        bars[ts] = (px, px + 15, px - 15, px + 3, 100.0)
        px += math.sin(i * 0.7) * 30 + math.sin(i * 0.113) * 45

    trades = []
    for w in range(40):                  # 40 trades, ~1 por semana
        bar_ts = start + timedelta(days=7 * w, hours=6)
        c = bars[bar_ts][3]
        # δ de roll: escalón cada ~13 semanas (−100, −400, −700, …)
        delta_roll = -100.0 - 300.0 * (w // 13)
        trades.append(Trade(number=w + 1, side="long",
                            entry_ts=bar_ts - timedelta(minutes=60),  # off +60
                            exit_ts=None, entry_price=c + delta_roll,
                            exit_price=None, pnl_usd=0, pnl_pct=0,
                            mfe_pct=0, mae_pct=0))
    off, sanity, detail = detect_tz_offset(trades, bars, sample=40)
    assert off == 60, f"offset {off} (esperado +60); detalle={detail}"
    assert sanity >= 0.85, detail


# ---------------------------------------------------------------------------
# Enriquecimiento ATR + hora + cobertura
# ---------------------------------------------------------------------------

def test_enrich_atr_hour_and_uncovered():
    start = datetime(2026, 3, 16, 9, 0)
    bars = _bars(start, 200)
    inside = Trade(number=1, side="long",
                   entry_ts=start + timedelta(minutes=5 * 100), exit_ts=None,
                   entry_price=0, exit_price=None, pnl_usd=0, pnl_pct=1.0,
                   mfe_pct=1.0, mae_pct=0.2)
    outside = Trade(number=2, side="long",
                    entry_ts=start + timedelta(days=30), exit_ts=None,
                    entry_price=0, exit_price=None, pnl_usd=0, pnl_pct=1.0,
                    mfe_pct=1.0, mae_pct=0.2)
    uncovered = enrich_with_bars([inside, outside], bars, offset_min=0)
    assert uncovered == 1
    assert inside.atr_entry is not None and inside.atr_pct > 0
    assert inside.hour == (start + timedelta(minutes=500)).hour
    assert outside.atr_entry is None
    # hourly_edge solo usa cubiertos
    split_in_out([inside, outside], oos=0.5)
    hours = hourly_edge([inside, outside])
    assert list(hours) == [inside.hour]
