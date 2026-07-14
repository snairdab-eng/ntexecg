"""LX-12 — Guardia de CONTENCIÓN al integrar (fail-honest).

El HOLC del share puede estar en un contorno de contrato distinto al del master
(roll/back-adjust ≠ continuo `<sym>1!` de LuxAlgo): los timestamps alinean pero
el NIVEL de precio está desplazado un escalón constante. La sanity de
`detect_tz_offset` NO lo ve (corrige el nivel con los vecinos). Estos tests
prueban la contención CRUDA: master desalineado (+N ticks constantes) → marca
`intrabar_no_confiable` y el estudio Luxy DEGRADA con banner rojo; alineado → %
registrado y flujo intacto; determinismo; y la guardia sobre el ES real.
"""
import asyncio
import csv
import glob
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import scripts.nt_riesgo as nr
from scripts.nt_riesgo import CONTENCION_MIN_PCT, _contencion

_ES_CSV = sorted(glob.glob("ListaDeOperaciones/*_ES1!_*.csv"))
_ES_HOLC = Path("NINJATRADER/HOLC/ES_5m.csv")
_HAY_DATOS = bool(_ES_CSV) and _ES_HOLC.exists()

_COLS = ["Trade number", "Tipo", "Fecha y hora", "Señal", "Precio USD",
         "Tamaño (cant.)", "Tamaño de la posición (valor)", "PyG netas USD",
         "Rentabilidad %", "Desviación favorable USD", "Desviación favorable %",
         "Desviación adversa USD", "Desviación adversa %", "PyG acumuladas USD",
         "PyG acumuladas %", "Duration (bars)"]

_PPT = 50.0                       # ES $/punto (USD_PER_POINT_KNOWN["ES"])
_TICK = 0.25                      # ES tick full-size
_BASE = datetime(2026, 5, 1, 0, 0)
_START_LEVEL = 5000.0
_N = 20


def _trades_spec():
    """20 trades sobre rejilla 5m: entrada cada 12 barras, precios 5000–5004.5."""
    specs = []
    cum = 0.0
    for i in range(_N):
        entry_dt = _BASE + timedelta(minutes=5 * 12 * i)
        exit_dt = entry_dt + timedelta(minutes=5 * 6)
        price = _START_LEVEL + (i % 10) * 0.5
        side = "largo" if i % 2 == 0 else "corto"
        pnl = 25.0 if i % 4 else -12.5           # mayoría ganadora
        cum += pnl
        specs.append((i + 1, side, entry_dt, exit_dt, price, pnl, cum))
    return specs


def _write_master(path: Path, specs) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLS)
        w.writeheader()
        for num, side, e_dt, x_dt, price, pnl, cum in specs:
            base = {c: "" for c in _COLS}
            base.update({
                "Trade number": num, "Precio USD": f"{price}",
                "Tamaño (cant.)": "1",
                "Tamaño de la posición (valor)": f"{price * _PPT}",
                "PyG netas USD": f"{pnl}", "Rentabilidad %": "0.0",
                "Desviación favorable %": "0.5", "Desviación adversa %": "-0.3",
                "PyG acumuladas USD": f"{cum}", "Duration (bars)": "6"})
            w.writerow({**base, "Tipo": f"Salida en {side}",
                        "Fecha y hora": x_dt.strftime("%Y-%m-%d %H:%M"),
                        "Señal": "Scripted Exit All",
                        "Precio USD": f"{price}"})
            w.writerow({**base, "Tipo": f"Entrada en {side}",
                        "Fecha y hora": e_dt.strftime("%Y-%m-%d %H:%M"),
                        "Señal": "Scripted"})


def _write_holc(path: Path, specs, shift: float = 0.0) -> None:
    """HOLC 5m continuo; la barra de cada entrada centra su precio (banda ±0.25).
    `shift` desplaza TODO el nivel (simula el escalón de roll: master fuera de la
    banda pero la sanity level-corregida sigue pasando)."""
    entry_level = {e_dt: price for _n, _s, e_dt, _x, price, _p, _c in specs}
    first = specs[0][2] - timedelta(minutes=5 * 60)
    last = specs[-1][3] + timedelta(minutes=5 * 10)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["DateTime", "Open", "High", "Low", "Close", "Volume"])
        t = first
        while t <= last:
            lvl = entry_level.get(t, _START_LEVEL) + shift
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"),
                        lvl, lvl + 0.25, lvl - 0.25, lvl, 100])
            t += timedelta(minutes=5)


def _integrar(tmp_path, monkeypatch, shift):
    """Integra el master sintético con un HOLC (alineado shift=0 / roll shift>0).
    Devuelve el manifest."""
    holc_dir = tmp_path / "holc"
    holc_dir.mkdir(exist_ok=True)
    specs = _trades_spec()
    _write_holc(holc_dir / "ES_5m.csv", specs, shift=shift)
    csv_path = tmp_path / "ES5m_Test_010526.csv"
    _write_master(csv_path, specs)
    monkeypatch.setenv("HOLC_DIR", str(holc_dir))
    monkeypatch.setattr(nr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    return asyncio.run(nr.integrar(csv_path, codigo="Test", activo="ES"))


# ---------------------------------------------------------------------------
# 1) _contencion PURA — alineado vs desalineado (+N ticks constantes)
# ---------------------------------------------------------------------------

def test_contencion_alineado_100():
    from scripts.lab_analyze import load_holc_from_path, parse_luxalgo_csv
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        specs = _trades_spec()
        _write_holc(td / "h.csv", specs, shift=0.0)
        _write_master(td / "m.csv", specs)
        trades = parse_luxalgo_csv(td / "m.csv")
        bars = load_holc_from_path(td / "h.csv")
        c = _contencion(trades, bars, 0, "ES")
    assert c["pct"] == 100.0
    assert c["confiable"] is True
    assert c["umbral_pct"] == CONTENCION_MIN_PCT == 80


def test_contencion_desalineado_marca_y_gap():
    from scripts.lab_analyze import load_holc_from_path, parse_luxalgo_csv
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        specs = _trades_spec()
        _write_holc(td / "h.csv", specs, shift=5.0)   # +5 pts = +20 ticks
        _write_master(td / "m.csv", specs)
        trades = parse_luxalgo_csv(td / "m.csv")
        bars = load_holc_from_path(td / "h.csv")
        c = _contencion(trades, bars, 0, "ES")
    assert c["pct"] == 0.0                              # ningún precio en su barra
    assert c["confiable"] is False
    # gap mediano = precio − close = −5 pts = −20 ticks (el escalón de roll)
    gaps = set(c["gap_mediano_ticks_por_mes"].values())
    assert gaps == {-20}


# ---------------------------------------------------------------------------
# 2) integrar — desalineado MARCA y DEGRADA (master igual integrado)
# ---------------------------------------------------------------------------

def test_integrar_desalineado_marca_intrabar_no_confiable(tmp_path, monkeypatch):
    man = _integrar(tmp_path, monkeypatch, shift=5.0)
    assert man["intrabar_no_confiable"] is True
    assert man["contencion"]["pct"] == 0.0
    assert man["contencion"]["confiable"] is False
    # fail-honest: el master SÍ se integra (no bloquea) — línea base presente.
    base_dir = (tmp_path / "MotorRiesgo" / "ES_Test")
    assert (base_dir / "master.csv").exists()
    assert (base_dir / "enriched.csv").exists()
    assert man["linea_base_usd"]["n"] == _N


def test_integrar_alineado_registra_y_flujo_intacto(tmp_path, monkeypatch):
    man = _integrar(tmp_path, monkeypatch, shift=0.0)
    assert man["intrabar_no_confiable"] is False
    assert man["contencion"]["pct"] == 100.0
    assert man["contencion"]["confiable"] is True
    assert man["linea_base_usd"]["n"] == _N            # flujo idéntico al de siempre


# ---------------------------------------------------------------------------
# 3) El estudio Luxy DEGRADA con el banner ROJO específico
# ---------------------------------------------------------------------------

def test_luxy_study_degrada_con_banner_rojo():
    import scripts.mr_luxy as mrl
    from tests.test_mr_luxy_l2 import _fake_trades      # helper de trades del Lab
    r = mrl.luxy_study(_fake_trades(30), _PPT, oos=0.3, has_intrabar=False,
                       degradado_motivo="intrabar_no_confiable")
    assert r["degradado"] is True
    assert r["degradado_motivo"] == "intrabar_no_confiable"
    assert any("contorno de contrato" in a for a in r["avisos"])
    assert any("Merge policy" in a for a in r["avisos"])


def test_run_for_clave_degrada_sobre_master_desalineado(tmp_path, monkeypatch):
    import scripts.mr_luxy as mrl
    _integrar(tmp_path, monkeypatch, shift=5.0)
    study = mrl.run_for_clave("ES_Test", tmp_path / "MotorRiesgo")
    assert study["degradado"] is True
    assert study["degradado_motivo"] == "intrabar_no_confiable"
    assert study["contencion"]["pct"] == 0.0
    assert any("Merge policy" in a for a in study["avisos"])


def test_run_for_clave_no_degrada_si_alineado(tmp_path, monkeypatch):
    import scripts.mr_luxy as mrl
    _integrar(tmp_path, monkeypatch, shift=0.0)
    study = mrl.run_for_clave("ES_Test", tmp_path / "MotorRiesgo")
    assert study["degradado"] is False
    assert study["degradado_motivo"] is None


# ---------------------------------------------------------------------------
# 4) Determinismo (recrear/repetir intactos): mismas entradas → mismo veredicto
# ---------------------------------------------------------------------------

def test_contencion_determinista(tmp_path, monkeypatch):
    m1 = _integrar(tmp_path, monkeypatch, shift=5.0)["contencion"]
    m2 = _integrar(tmp_path, monkeypatch, shift=5.0)["contencion"]
    assert m1 == m2


# ---------------------------------------------------------------------------
# 5) Gated ES real — la guardia corre y su veredicto es COHERENTE con el umbral.
#    (Realidad 2026-07: el HOLC del share está en otro contorno de roll que el
#    master ES → contención baja; la guardia lo MARCA correctamente. El test
#    verifica la coherencia interna, no un valor "alto" que ya no se cumple.)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAY_DATOS, reason="datos reales ES no disponibles")
def test_es_real_contencion_coherente(tmp_path, monkeypatch):
    monkeypatch.setenv("HOLC_DIR", "NINJATRADER/HOLC")
    monkeypatch.setattr(nr, "MOTOR_DIR", tmp_path / "MotorRiesgo")
    man = asyncio.run(nr.integrar(Path(_ES_CSV[-1]), codigo="RealCont"))
    c = man["contencion"]
    assert c is not None and 0.0 <= c["pct"] <= 100.0
    assert 0.0 <= c["pct_pm1"] <= 100.0
    # invariante fail-honest: la bandera es EXACTAMENTE contención < umbral.
    assert man["intrabar_no_confiable"] == (c["pct"] < CONTENCION_MIN_PCT)
    assert c["confiable"] == (c["pct"] >= CONTENCION_MIN_PCT)
