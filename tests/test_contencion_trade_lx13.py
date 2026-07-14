"""LX-13 — Contención POR TRADE (cierre del residuo de roll).

Tras el fix del Merge policy la contención global pasa LX-12, pero sobreviven
outliers puntuales en fechas-frontera de roll cuyo intrabar individual es basura
(p.ej. −857 ticks). Se EXCLUYEN honestos del universo simulable (como los
ATR-estimados) sin tocar el crudo ni el cuadre. Estos tests cubren: marca por
trade (±1 barra tolerada), exclusión de sts, derivaciones sin veneno, banner con
categoría, manifest con fechas, y determinismo.
"""
import asyncio
import datetime as dt
from pathlib import Path

import pytest

import scripts.nt_riesgo as nr
from scripts.lab_analyze import (CONTENCION_TRADE_VECINAS, Trade,
                                 mark_no_contenido)
from scripts.mr_sims import from_trades, mae_floor_study

# Reusa los constructores sintéticos alineados de LX-12 (master+HOLC cuadrados).
from tests.test_contencion_lx12 import (_HAY_DATOS, _ES_CSV, _trades_spec,
                                        _write_holc, _write_master)


def _t(number, price, minute, atr=1.0, pnl=10.0, mae_pct=0.3, mfe_pct=0.5):
    tr = Trade(number, "long", dt.datetime(2026, 5, 1, 0, 0)
               + dt.timedelta(minutes=minute), None, price, None, pnl, 0.1,
               mfe_pct, mae_pct)
    tr.atr_entry = atr
    tr.atr_pct = round(atr / price * 100, 6)
    return tr


def _bars(level=5000.0, n=40, rng=0.25):
    base = dt.datetime(2026, 5, 1, 0, 0)
    return {base + dt.timedelta(minutes=5 * i):
            (level, level + rng, level - rng, level, 10) for i in range(n)}


# ---------------------------------------------------------------------------
# 1) Marca por trade — outlier fuera, contenido dentro, ±1 barra tolerado
# ---------------------------------------------------------------------------

def test_constante_vecinas():
    assert CONTENCION_TRADE_VECINAS == 1


def test_outlier_marcado_con_gap():
    bars = _bars(5000.0)
    dentro = _t(1, 5000.0, 25)          # dentro de [4999.75, 5000.25]
    outlier = _t(2, 4900.0, 50)         # −100 pts = fuera lejísimos
    fuera = mark_no_contenido([dentro, outlier], bars, 0, tick=0.25)
    assert dentro.no_contenido is False
    assert outlier.no_contenido is True
    assert outlier.gap_ticks == -400.0                 # (4900−5000)/0.25
    assert [d["number"] for d in fuera] == [2]


def test_vecina_pm1_tolerada():
    """Precio que cae en la barra ±1 (no la exacta) NO se marca (timing de fill,
    p.ej. 6E/6J quedan 100% simulables)."""
    base = dt.datetime(2026, 5, 1, 0, 0)
    bars = {base + dt.timedelta(minutes=5 * i):
            (5000.0 + i, 5000.0 + i + 0.25, 5000.0 + i - 0.25, 5000.0 + i, 10)
            for i in range(40)}
    # trade en el minuto 125 (índice 25, barra nivel 5025) con el precio de la
    # barra vecina 5026 (índice 26): ±1 barra debe tolerarlo.
    tr = _t(1, 5026.0, 125)
    mark_no_contenido([tr], bars, 0, tick=0.25)
    assert tr.no_contenido is False                    # ±1 barra la contiene


# ---------------------------------------------------------------------------
# 2) Exclusión de sts + derivación SIN veneno (el suelo MAE cambia)
# ---------------------------------------------------------------------------

def test_from_trades_excluye_no_contenido():
    a, b = _t(1, 5000.0, 25), _t(2, 5000.0, 50)
    b.no_contenido = True
    sts = from_trades([a, b], 50.0)
    assert [s.number for s in sts] == [1]


def test_suelo_mae_sin_el_veneno():
    """Un outlier GANADOR con excursión adversa fantasma envenena el suelo MAE
    p95; excluirlo (no_contenido) lo saca de la derivación."""
    normales = [_t(i, 5000.0, i * 5, atr=10.0, pnl=50.0, mae_pct=0.4)
                for i in range(1, 6)]          # mae_atr ≈ 0.2
    veneno = _t(99, 5000.0, 60, atr=10.0, pnl=50.0, mae_pct=17.0)  # mae_atr ≈ 8.5
    con = from_trades(normales + [veneno], 50.0)
    veneno.no_contenido = True
    sin = from_trades(normales + [veneno], 50.0)
    p95_con = mae_floor_study(con, 50.0)["ganadoras_mae_atr"]["p95"]
    p95_sin = mae_floor_study(sin, 50.0)["ganadoras_mae_atr"]["p95"]
    assert p95_con > p95_sin                     # el veneno inflaba el suelo
    assert len(sin) == len(con) - 1


# ---------------------------------------------------------------------------
# 3) Banner de muestra con la categoría LX-13
# ---------------------------------------------------------------------------

def test_banner_categoria_fuera_contencion():
    import scripts.mr_luxy as mrl
    b = mrl.muestra_banner(20, 19, n_fuera=1)
    assert b is not None and "fuera de contención (frontera de roll)" in b


# ---------------------------------------------------------------------------
# 4) integrar synthetic alineado + 1 outlier → manifest lo registra y lo excluye
# ---------------------------------------------------------------------------

def _integrar_con_outlier(tmp_path, monkeypatch):
    holc_dir = tmp_path / "holc"; holc_dir.mkdir(exist_ok=True)
    specs = _trades_spec()
    _write_holc(holc_dir / "ES_5m.csv", specs, shift=0.0)     # HOLC alineado
    # master: mismos specs pero UN trade con precio outlier (+50 pts, fuera de ±1)
    n, side, e, x, price, pnl, cum = specs[10]
    specs_out = list(specs); specs_out[10] = (n, side, e, x, price + 50.0, pnl, cum)
    csv_path = tmp_path / "ES5m_Out_010526.csv"
    _write_master(csv_path, specs_out)
    monkeypatch.setenv("HOLC_DIR", str(holc_dir))
    monkeypatch.setattr(nr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    return asyncio.run(nr.integrar(csv_path, codigo="Out", activo="ES")), n


def test_integrar_registra_no_contenido(tmp_path, monkeypatch):
    man, num_outlier = _integrar_con_outlier(tmp_path, monkeypatch)
    assert man["intrabar_no_confiable"] is False        # global sigue confiable
    nc = man["contencion"]["no_contenidos"]
    assert len(nc) == 1 and nc[0]["number"] == num_outlier
    assert nc[0]["gap_ticks"] == 200.0                  # +50 pts / 0.25


def test_luxy_excluye_outlier_del_universo(tmp_path, monkeypatch):
    import scripts.mr_luxy as mrl
    _integrar_con_outlier(tmp_path, monkeypatch)
    study = mrl.run_for_clave("ES_Out", tmp_path / "MotorRiesgo")
    dash = study["dashboard"]
    assert dash["n_fuera_contencion"] == 1
    assert len(dash["fuera_contencion"]) == 1
    assert dash["n_simulable"] == dash["n_total"] - 1   # el outlier fuera de sts
    assert "fuera de contención" in (dash["muestra_banner"] or "")


# ---------------------------------------------------------------------------
# 5) Determinismo (recrear intacto): mismas entradas → mismas marcas
# ---------------------------------------------------------------------------

def test_determinismo_marca(tmp_path, monkeypatch):
    m1, _ = _integrar_con_outlier(tmp_path, monkeypatch)
    m2, _ = _integrar_con_outlier(tmp_path, monkeypatch)
    assert m1["contencion"]["no_contenidos"] == m2["contencion"]["no_contenidos"]


# ---------------------------------------------------------------------------
# 6) Gated ES real — con el HOLC alineado excluiría ~3; hoy el HOLC local está
#    desalineado (LX-12) → global no confiable → NO se marca (skip honesto).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales ES no disponibles")
def test_es_real_no_contenidos_coherente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")
    monkeypatch.setattr(nr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    man = asyncio.run(nr.integrar(Path(_ES_CSV[-1]), codigo="RealNC"))
    nc = man["contencion"]["no_contenidos"]
    if man["intrabar_no_confiable"]:
        # HOLC del share aún desalineado globalmente → per-trade NO aplica.
        assert nc == []
    else:
        # HOLC ya alineado (post-reintegración): outliers de roll excluidos.
        assert all(d["gap_ticks"] is not None for d in nc)
