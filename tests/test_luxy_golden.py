"""FIXTURE DE ORO — AUDITORÍA TOTAL DE LUXY (2026-07-18).

LA pregunta que responde este archivo: una estrategia NUEVA, integrada desde
cero, ¿produce números correctos en CADA eslabón del pipeline real?
    integrar (scripts.nt_riesgo) → mr_luxy (estudio) → evaluate_overrides
    (Recalcular) → config_from_overrides / activacion_from_study (Aplicar).

TODOS los valores esperados están calculados A MANO en este archivo (tablas en
los comentarios). El fixture está construido para que cada derivación caiga en
un número exacto verificable con lápiz:

  · HOLC 5m sintético con rango FIJO high−low = 5.0 y saltos de nivel ≤ 2.0
    → True Range = 5.0 en TODAS las barras → ATR(14) = 5.0 EXACTO en toda
    entrada (cualquier suavizado da 5 con TR constante).
  · Escalera de nivel (staircase) + micro-onda de cierres (±0.5, período 5
    barras) → el offset TZ correcto (+60 min) es el ÚNICO con dispersión local
    0; todo |offset| < 60 ve variación (k_i = i%3 escalones pre-entrada +
    la onda) y los empates de |offset| > 60 los pierde por el desempate |off|.
  · entry_price = close de su barra alineada → contención LX-12 = 33/34
    (el trade 7 se escribe 50 pts abajo = outlier de frontera de roll LX-13).
  · ES: $/punto 50, tick 0.25. ATR mediano 5.0 → conversiones USD→pts→×ATR
    triviales de verificar ($250 = 5 pts = 1×ATR).

Universo (34 trades, 2 filas por trade, largos y cortos):
  · #7  = outlier de roll (precio fuera de banda) → excluido del simulable.
  · #34 = trade ABIERTO al final (sin fila de salida, PnL provisional +160).
  · #5  = perdedor catastrófico (−4000, MAE 34×ATR) → ancla el backstop.
  · split 70/30 → in-sample = trades 1..24 (23 simulables), OOS = 25..34 (10).

Palancas derivadas in-sample (verificadas a mano, ver tabla):
  backstop $2000 = 40 pts = 8×ATR · TP fallback 15×ATR ambos lados (ganadoras
  por lado < 10) · escalera [5,3,2] micros @ 0 / 2.0 / 4.0 ×ATR (mediana MFE
  4.0×, f2 = 14/23, f3 = 6/23, reparto por mayor residuo) · BE no recomendado
  (las barras nunca arman) · sin corte de lado (ambos lados net > 0).

Simulación Crudo+ a mano (ppt=50, ATR=5 → w·d·ATR·ppt: C2 aporta +150,
C3 +200 cuando llenan):
  mae<2×ATR             → 0.5·nativo
  2×≤mae<4×             → 0.8·nativo + 150
  mae≥4× (no stoppeado) → 1.0·nativo + 350
  #5 stoppeado (8×ATR)  → (0.5·(−8) + 0.3·(−6) + 0.2·(−4))·250 = −1650

HALLAZGO D (operador, GC 2026-07-18) — modelo optimista en pierna más profunda
que el stop: ver test_pierna_mas_profunda_que_stop_* al final. El fix va a
triage del arquitecto; aquí queda FIJADO el comportamiento correcto (xfail
estricto) y el vigente (pin, borrar al aplicar el fix).
"""
import asyncio
import csv
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.mr_luxy as mrl
import scripts.nt_riesgo as nr
from scripts.mr_sims import HaircutCfg, SimTrade, ladder_outcome
from scripts.nt_riesgo import _sha256

PPT = 50.0                 # ES $/punto
TICK = 0.25                # ES tick
ATR = 5.0                  # ATR(14) EXACTO por construcción (TR constante)
OFF_MIN = 60               # offset TZ construido: CSV = HOLC − 60 min
FECHA = "2026-07-18"
CLAVE = "ES_Golden"

_T0 = datetime(2026, 5, 4, 3, 0)       # 1ª entrada (hora HOLC/ET)
_GAP = timedelta(hours=4)              # entre entradas
_DUR = timedelta(minutes=60)           # entrada → salida (cerrados)

# ── Los 34 trades: (number, lado, pnl_usd, mae_pts, mfe_pts) ────────────────
# mae/mfe ×ATR = pts/5. Ningún MFE ≥ 15×ATR (75 pts) → el TP nominal fallback
# jamás dispara. Ningún MAE ≥ 40 pts salvo #5 → solo #5 toca el grid de
# backstop y solo #5 queda stoppeado a 8×ATR.
SPECS = [
    (1,  "largo", +100.0,   4.0, 22.0),
    (2,  "corto", +150.0,  11.0, 30.0),
    (3,  "largo", -200.0,  22.0,  6.0),
    (4,  "corto", +250.0,   4.0, 20.0),
    (5,  "corto", -4000.0, 170.0,  2.0),   # catastrófico: ancla el backstop
    (6,  "largo", +300.0,  11.0, 20.0),
    (7,  "largo",  -50.0,   8.0,  8.0),    # OUTLIER roll (precio −50 pts)
    (8,  "corto", +2000.0,  4.0, 45.0),
    (9,  "largo", -150.0,  22.0,  4.0),
    (10, "corto", +500.0,  11.0, 25.0),
    (11, "largo", +100.0,   4.0, 15.0),
    (12, "corto", +600.0,  11.0, 28.0),
    (13, "largo", -100.0,  22.0,  5.0),
    (14, "corto", +400.0,   4.0, 21.0),
    (15, "largo", +200.0,  11.0, 24.0),
    (16, "corto", +350.0,  22.0, 18.0),
    (17, "largo", -150.0,   4.0,  3.0),
    (18, "largo", +300.0,   4.0, 20.0),
    (19, "corto", +250.0,  11.0, 22.0),
    (20, "largo", -180.0,  22.0,  5.0),
    (21, "corto", +420.0,   4.0, 24.0),
    (22, "largo", +150.0,  11.0, 18.0),
    (23, "corto",  -80.0,   4.0, 26.0),
    (24, "largo", -120.0,  11.0,  6.0),
    # ── OOS (25..34): 8 ganadores nativos, 2 perdedores; bajo las palancas
    #    in-sample queda UN solo perdedor simulado (#32) → LX-7 ⚪.
    (25, "largo", +300.0,   4.0, 20.0),
    (26, "corto", +250.0,  11.0, 22.0),
    (27, "largo", -180.0,  22.0,  5.0),    # nativo −180 → simulado +170
    (28, "corto", +420.0,   4.0, 24.0),
    (29, "largo", +150.0,  11.0, 18.0),
    (30, "corto", +330.0,   4.0, 26.0),
    (31, "largo", +200.0,   4.0, 16.0),
    (32, "corto", -400.0,  22.0,  4.0),    # único perdedor simulado OOS (−50)
    (33, "corto", +280.0,  11.0, 20.0),
    (34, "largo", +160.0,  11.0, 12.0),    # ABIERTO (PnL provisional, sin salida)
]
OUTLIER = 7
ABIERTO = 34
OUTLIER_SHIFT = -50.0                  # pts bajo el nivel de su barra (−200 ticks)

_COLS = ["Trade number", "Tipo", "Fecha y hora", "Señal", "Precio USD",
         "Tamaño (cant.)", "Tamaño de la posición (valor)", "PyG netas USD",
         "Rentabilidad %", "Desviación favorable USD", "Desviación favorable %",
         "Desviación adversa USD", "Desviación adversa %", "PyG acumuladas USD",
         "PyG acumuladas %", "Duration (bars)"]


def _entry_holc(n: int) -> datetime:
    return _T0 + _GAP * (n - 1)


def _steps() -> list[datetime]:
    """Escalones +2.0 del nivel: k_i = i%3 escalones a T_i−5 / T_i−10 min.
    Viven en la zona muerta (fuera de toda ventana entrada→salida) → las barras
    de cada trade quedan planas (el BE jamás arma) y todo |offset|<60 ve
    dispersión > 0 (junto con la micro-onda de cierres)."""
    out = []
    for n, *_ in SPECS:
        t = _entry_holc(n)
        for k in range(1, n % 3 + 1):
            out.append(t - timedelta(minutes=5 * k))
    return sorted(out)


def _level_fn():
    steps = _steps()

    def lvl(t: datetime) -> float:
        return 5000.0 + 2.0 * sum(1 for s in steps if s <= t)
    return lvl


def _wiggle(t: datetime) -> float:
    """Micro-onda del close (período 5 barras, ±0.5): rompe los empates de
    offset que la escalera sola no rompe (55/50/45/40 min). |w| ≤ 0.5 mantiene
    TR = 5.0 (|Δnivel − w| ≤ 2.5)."""
    idx = int(t.timestamp() // 300)
    return 0.25 * (idx % 5 - 2)


def _close(t: datetime) -> float:
    return _level_fn()(t) + _wiggle(t)


def _entry_price(n: int) -> float:
    p = _close(_entry_holc(n))
    return p + OUTLIER_SHIFT if n == OUTLIER else p


def _write_holc(path: Path) -> None:
    lvl = _level_fn()
    first = _T0 - timedelta(minutes=5 * 30)          # warmup > ATR_PERIOD+1
    last = _entry_holc(len(SPECS)) + timedelta(minutes=5 * 12)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["DateTime", "Open", "High", "Low", "Close", "Volume"])
        t = first
        while t <= last:
            L = lvl(t)
            w.writerow([t.strftime("%Y-%m-%d %H:%M:%S"),
                        L, L + 2.5, L - 2.5, _close(t), 100])
            t += timedelta(minutes=5)


def _write_master(path: Path) -> None:
    cum = 0.0
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_COLS)
        w.writeheader()
        for n, lado, pnl, mae, mfe in SPECS:
            e_holc = _entry_holc(n)
            e_csv = e_holc - timedelta(minutes=OFF_MIN)
            x_csv = e_csv + _DUR
            price = _entry_price(n)
            cum += pnl
            base = {c: "" for c in _COLS}
            base.update({
                "Trade number": n, "Precio USD": repr(price),
                "Tamaño (cant.)": "1",
                "Tamaño de la posición (valor)": repr(price * PPT),
                "PyG netas USD": repr(pnl), "Rentabilidad %": "0.0",
                "Desviación favorable %": repr(mfe / price * 100.0),
                "Desviación adversa %": repr(-mae / price * 100.0),
                "PyG acumuladas USD": repr(round(cum, 2)),
                "Duration (bars)": "12"})
            if n != ABIERTO:                          # el 34 queda ABIERTO
                w.writerow({**base, "Tipo": f"Salida en {lado}",
                            "Fecha y hora": x_csv.strftime("%Y-%m-%d %H:%M"),
                            "Señal": "Scripted Exit All"})
            w.writerow({**base, "Tipo": f"Entrada en {lado}",
                        "Fecha y hora": e_csv.strftime("%Y-%m-%d %H:%M"),
                        "Señal": "Scripted"})


@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    """Integra el fixture por el PIPELINE REAL una sola vez por módulo."""
    mp = pytest.MonkeyPatch()
    tmp = tmp_path_factory.mktemp("luxy_golden")
    holc_dir = tmp / "holc"
    holc_dir.mkdir()
    _write_holc(holc_dir / "ES_5m.csv")
    csv_path = tmp / "Golden_ES1!_2026-07-18_aaaaa.csv"
    _write_master(csv_path)
    mp.setenv("HOLC_DIR", str(holc_dir))
    motor = tmp / "MotorRiesgo"
    mp.setattr(nr, "MOTOR_DIR", motor)
    man = asyncio.run(nr.integrar(csv_path, codigo="Golden", activo="ES",
                                  fecha=FECHA))
    study = mrl.run_for_clave(CLAVE, motor, fecha=FECHA)
    yield SimpleNamespace(man=man, study=study, motor=motor,
                          csv_path=csv_path, base_dir=motor / CLAVE)
    mp.undo()


def _fila(study, nombre):
    return next(f for f in study["tabla_a"] if f["fila"] == nombre)


# ═══════════════════════════════════════════════════════════════════════════
# 1) INTEGRAR — cuadre al dólar, sha, TZ, contención LX-12/LX-13
# ═══════════════════════════════════════════════════════════════════════════

def test_integrar_cuadre_al_dolar_exacto(golden):
    # A MANO: Σ ganadores 8160 − Σ perdedores 5610 = +2550 (34 trades).
    man = golden.man
    assert man["cuadre"]["ok"] is True
    assert man["cuadre"]["pnl_parseado"] == 2550.0
    assert man["cuadre"]["pnl_export"] == 2550.0
    assert man["trades"]["n"] == 34


def test_integrar_sha_del_export(golden):
    assert golden.man["export"]["sha256_master"] == _sha256(golden.csv_path)
    # el master persistido es byte-a-byte el export
    assert _sha256(golden.base_dir / "master.csv") == \
        golden.man["export"]["sha256_master"]


def test_integrar_tz_offset_detectado(golden):
    # El fixture escribe el CSV 60 min DETRÁS del HOLC → offset = +60 exacto.
    assert golden.man["tz"]["offset_minutes"] == OFF_MIN
    assert golden.man["tz"]["sanity"] >= 0.9          # 33/34 (el outlier falla)


def test_integrar_usd_por_punto_verificado(golden):
    u = golden.man["usd_por_punto"]
    assert u["usado"] == PPT and u["inferido"] == pytest.approx(PPT)
    assert u["ok"] is True


def test_integrar_contencion_lx12_lx13_exacta(golden):
    # 33 de 34 entradas dentro de su barra → 97.1% ≥ 80% (confiable);
    # el 7 queda fuera con gap = −50 pts / 0.25 = −200.0 ticks EXACTO.
    c = golden.man["contencion"]
    assert c["pct"] == round(100 * 33 / 34, 1) == 97.1
    assert c["confiable"] is True
    assert golden.man["intrabar_no_confiable"] is False
    ncs = c["no_contenidos"]
    assert [d["number"] for d in ncs] == [OUTLIER]
    assert ncs[0]["gap_ticks"] == pytest.approx(-200.0)


def test_integrar_atr_exacto_y_cobertura_total(golden):
    # TR constante = 5.0 → ATR(14) = 5.0 EXACTO en las 34 entradas.
    assert golden.man["holc"]["sin_cobertura"] == 0
    assert golden.man["holc"]["atr_estimado"] == 0
    with open(golden.base_dir / "enriched.csv", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 34
    for r in rows:
        assert float(r["atr_entry"]) == pytest.approx(ATR, abs=1e-9)


def test_trade_abierto_parsea_con_pnl_provisional(golden):
    """BORDE (caso ~$160 del operador): el trade ABIERTO entra al listado con
    su PnL PROVISIONAL como si fuera final — sin salida, sin marca. Este test
    FIJA el comportamiento vigente; si algún día se etiqueta/excluye, debe
    cambiar a propósito (hallazgo B-3 de la auditoría 2026-07-18)."""
    from scripts.lab_analyze import parse_luxalgo_csv
    trades = parse_luxalgo_csv(golden.base_dir / "master.csv")
    t34 = next(t for t in trades if t.number == ABIERTO)
    assert t34.exit_ts is None
    assert t34.pnl_usd == 160.0
    # ...y su provisional está DENTRO del Crudo y del universo simulable:
    assert _fila(golden.study, "Crudo")["n"] == 34
    assert golden.study["dashboard"]["n_simulable"] == 33


# ═══════════════════════════════════════════════════════════════════════════
# 2) MR_LUXY — Crudo / palancas / Crudo+ / OOS espejo / split / semáforo
# ═══════════════════════════════════════════════════════════════════════════

def test_luxy_fila_cruda_valores_a_mano(golden):
    # A MANO (34 nativos): net 8160−5610 = 2550 · PF 8160/5610 = 1.45 ·
    # WR 23/34 = 67.6% · DD 4000 (pico +300 tras #4 → valle −3700 tras #5) ·
    # peor −4000 (#5) · 11 perdedores.
    crudo = _fila(golden.study, "Crudo")
    assert crudo["n"] == 34
    assert crudo["net_usd"] == 2550.0
    assert crudo["pf"] == 1.45
    assert crudo["wr_pct"] == 67.6
    assert crudo["max_dd_usd"] == 4000.0
    assert crudo["peor_trade_usd"] == -4000.0
    assert crudo["n_perdedores"] == 11
    assert crudo["participacion_pct"] == 100.0
    # misma fila, misma fuente, en la tabla reactiva del dashboard:
    assert golden.study["dashboard"]["table3"]["crudo"] == crudo


def test_luxy_palancas_in_sample_a_mano(golden):
    # A MANO sobre los 23 simulables in-sample:
    #  backstop: solo #5 (MAE 34×) toca el grid → net(b) = 5090−b, DD(b) = b
    #    → score máximo en $2000 (1.545 > base 1090/4000 = 0.27) → 40 pts.
    #  TP: ganadoras por lado 6L/9S < 10 → fallback 15×ATR ambos lados.
    #  escalera: mediana MFE = 4.0× → niveles 2.0/4.0; f2 = 14/23, f3 = 6/23
    #    → mayor residuo → [5,3,2] micros.
    #  suelo: MAE p95 de 15 ganadoras (7×0.8, 7×2.2, 1×4.4) = 2.2+0.3·2.2 = 2.86.
    #  BE: las barras del fixture nunca arman → ningún BE mejora → None.
    #  lado: largos +250 / cortos +840 (ambos > 0) → sin recorte.
    lev = golden.study["levers_in_sample"]
    assert lev["backstop_usd"] == 2000.0
    assert lev["b_pts"] == pytest.approx(40.0)
    assert lev["tp_por_lado_atr"] == {"long": 15.0, "short": 15.0}
    ld = lev["ladder"]
    assert ld["alloc"] == [5, 3, 2]
    assert ld["levels"] == [0.0, pytest.approx(2.0), pytest.approx(4.0)]
    assert ld["f2"] == pytest.approx(round(14 / 23, 4))
    assert ld["f3"] == pytest.approx(round(6 / 23, 4))
    assert lev["suelo_mae_p95_ganadoras"] == pytest.approx(2.86)
    assert lev["breakeven"]["be_atr"] is None
    assert lev["lado"] is None


def test_luxy_crudo_plus_valores_a_mano(golden):
    # A MANO (33 simulables, palancas in-sample, fills por MAE):
    #   multiplicadores: <2× → 0.5·nativo · 2–4× → 0.8·nativo+150 ·
    #   ≥4× → nativo+350 · #5 stoppeado → −1650.
    #   in-sample: 50+270+150+125−1650+390+1000+200+550+50+630+250+200+310
    #              +700−75+150+350+170+210+270−40+54 = 4314
    #   OOS:       150+350+170+210+270+165+100−50+374+278 = 2017
    #   net 6331 · PF 8146/1815 = 4.49 · WR 29/33 = 87.9 · DD 1650 (pico 595
    #   → valle −1055) · peor −1650 (#5) · perdedores {#5,#17,#23,#32} = 4.
    cp = golden.study["dashboard"]["table3"]["crudo_plus"]
    assert cp["n"] == 33
    assert cp["net_usd"] == pytest.approx(6331.0, abs=0.01)
    assert cp["pf"] == pytest.approx(4.49, abs=0.01)
    assert cp["wr_pct"] == 87.9
    assert cp["max_dd_usd"] == pytest.approx(1650.0, abs=0.01)
    assert cp["peor_trade_usd"] == pytest.approx(-1650.0, abs=0.01)
    assert cp["n_perdedores"] == 4
    assert cp["participacion_pct"] == 100.0


def test_luxy_fila_oos_espejo_a_mano(golden):
    # A MANO (10 OOS con las palancas IN-SAMPLE — R-T10):
    #   [150, 350, 170, 210, 270, 165, 100, −50, 374, 278]
    #   net 2017 · PF 2067/50 = 41.34 · WR 9/10 = 90 · DD 50 · peor −50 ·
    #   1 perdedor (#32). Nota: #27 nativo −180 pasa a +170 (las piernas
    #   C2/C3 ganan (ex+d)·ATR — matemática correcta del modelo vigente).
    oos = golden.study["dashboard"]["table3"]["oos"]
    assert oos["n"] == 10
    assert oos["net_usd"] == pytest.approx(2017.0, abs=0.01)
    assert oos["pf"] == pytest.approx(41.34, abs=0.01)
    assert oos["wr_pct"] == 90.0
    assert oos["max_dd_usd"] == pytest.approx(50.0, abs=0.01)
    assert oos["peor_trade_usd"] == pytest.approx(-50.0, abs=0.01)
    assert oos["n_perdedores"] == 1


def test_luxy_split_exacto(golden):
    # 34 trades · corte 70/30 → 24 in / 10 OOS; simulables 23 in / 10 OOS
    # (el outlier 7 cae en in-sample).
    s = golden.study["split"]
    assert s["n_total"] == 33
    assert s["n_in_sample"] == 23
    assert s["n_oos"] == 10
    assert s["n_trades_in"] == 24
    assert s["n_trades_oos"] == 10


def test_luxy_semaforo_blanco_pocos_perdedores_lx7(golden):
    # LX-7: OOS con n=10 (≥10, no es muestra chica) pero 1 solo perdedor
    # simulado (< 3) → PF 41.34 NO evaluable → ⚪ SIN VEREDICTO por
    # pocos_perdedores. Jamás 🟢 con esa muestra (el bug del 07-18).
    rob = golden.study["dashboard"]["robustez"]
    assert rob["verdict"] == "sin_veredicto"
    assert rob["reason"] == "pocos_perdedores"
    assert rob["n"] == 10 and rob["n_perdedores"] == 1


def test_luxy_semaforo_blanco_muestra_chica_lx14():
    # LX-14 (unidad, fuente única robustez_semaforo): n < 10 → ⚪ muestra_chica
    # aunque net y PF sean gloriosos.
    r = mrl.robustez_semaforo({"net_usd": 5000.0, "pf": 9.9, "n": 7,
                               "n_perdedores": 5})
    assert r["verdict"] == "sin_veredicto" and r["reason"] == "muestra_chica"


def test_luxy_gate_lx11_ambar_por_semaforo_blanco(golden):
    # El gate de Aplicar hereda el ⚪ → mínimo ÁMBAR (checkbox), nunca verde.
    gate = mrl.gate_aplicar(golden.study)
    assert gate["nivel"] == "amber"
    assert any("SIN VEREDICTO" in t for t in gate["triggers"])


def test_luxy_contencion_lx13_en_estudio(golden):
    d = golden.study["dashboard"]
    assert d["n_total"] == 34
    assert d["n_simulable"] == 33
    assert d["n_fuera_contencion"] == 1
    assert [x["number"] for x in d["fuera_contencion"]] == [OUTLIER]
    assert "fuera de contención" in (d["muestra_banner"] or "")


# ═══════════════════════════════════════════════════════════════════════════
# 3) EVALUATE_OVERRIDES — conversiones USD→pts/×ATR a mano + variante FX 6J
# ═══════════════════════════════════════════════════════════════════════════

OVERRIDES = {"sl_usd": 3000.0, "tp_usd": 2500.0,
             "l2_usd": 750.0, "l3_usd": 1250.0}
# A MANO (ppt 50, ATR mediano 5): sl 3000$ → 60 pts → 12×ATR · tp 2500$ →
# 50 pts → 10×ATR · l2 750$ → 15 pts → 3×ATR · l3 1250$ → 25 pts → 5×ATR.
# Simulación (multiplicadores nuevos): <3× → 0.5·nativo · 3–5× (solo los MAE
# 4.4×) → 0.8·nativo+225 · #5 stoppeado 12× → (0.5·(−12)+0.3·(−9)+0.2·(−7))·250
# = −2525. Sumas a mano: config (33) = 2092 · oos (10) = 1031.


def test_evaluate_overrides_conversiones_y_teselas_a_mano(golden):
    res = mrl.evaluate_overrides(CLAVE, golden.motor, dict(OVERRIDES))
    assert res["validado"] is True
    lv = res["levers"]
    assert lv["backstop_usd"] == 3000.0
    assert lv["TP_long_atr"] == pytest.approx(10.0)
    assert lv["TP_short_atr"] == pytest.approx(10.0)
    assert lv["levels_atr"] == [0.0, pytest.approx(3.0), pytest.approx(5.0)]
    assert lv["C1"] == 5 and lv["C2"] == 3 and lv["C3"] == 2
    # teselas validadas — mismos números que llamar al evaluador directo:
    assert res["base"]["net"] == 2550.0 and res["base"]["n"] == 34
    assert res["config"]["net"] == pytest.approx(2092.0, abs=0.01)
    assert res["config"]["n"] == 33
    assert res["config"]["part"] == 100.0
    assert res["config"]["worst"] == pytest.approx(-2525.0, abs=0.01)
    assert res["oos"]["net"] == pytest.approx(1031.0, abs=0.01)
    assert res["oos"]["n"] == 10 and res["oos"]["n_perdedores"] == 1
    assert res["robustez"]["verdict"] == "sin_veredicto"     # LX-7 de nuevo
    s = res["señales"]
    assert s["flip_signo"] is False and s["mejora_3x"] is False
    assert s["participacion_pct"] == 100.0


def test_evaluate_overrides_aplicable_llave_por_llave(golden):
    # config_from_overrides — el APLICABLE esperado, llave por llave.
    res = mrl.evaluate_overrides(CLAVE, golden.motor, dict(OVERRIDES))
    ap = res["aplicable"]
    assert set(ap) == {"backstop_points", "tp_nominal_long",
                       "tp_nominal_short", "entry_reserve_timeout_seconds",
                       "scale_entry"}
    assert ap["backstop_points"] == pytest.approx(60.0)      # en rejilla 0.25
    assert ap["tp_nominal_long"] == pytest.approx(10.0)
    assert ap["tp_nominal_short"] == pytest.approx(10.0)
    assert ap["entry_reserve_timeout_seconds"] == 3600
    se = ap["scale_entry"]
    assert se["quantities"] == [5, 3, 2]
    assert se["levels"] == [pytest.approx(3.0), pytest.approx(5.0)]
    assert se["max_micro_contracts"] == 10
    assert "c1_depth_atr" not in se                          # C1 no movido


def test_fx_6j_sub_tick_no_representable():
    # Variante FX (6J: tick 5e-7, ppt 12.5M — precios escala 1e-2/1e-5):
    # $4 → 3.2e-7 pts < 1 tick → NO representable → la llave se OMITE
    # (jamás un 0 colapsado) y viaja el aviso _no_representable.
    from scripts.fx_levers import tick_de
    tick = tick_de("6J")
    assert tick == 5e-7
    out = mrl.config_from_overrides({"sl_usd": 4.0}, None, 12_500_000.0,
                                    [], None, tick=tick, activo="6J")
    assert "backstop_points" not in out
    nr_ = out["_no_representable"]
    assert nr_[0]["campo"] == "backstop_points"
    assert nr_[0]["pts_crudos"] == pytest.approx(3.2e-7)


def test_fx_6j_representable_snap_al_tick():
    # $570 → 4.56e-5 pts = 91.2 ticks → snap 91 ticks = 4.55e-5 (el caso real
    # del FIX-FX-BACKSTOP, que un round(_,2) aplastaba a 0.0).
    from scripts.fx_levers import tick_de
    out = mrl.config_from_overrides({"sl_usd": 570.0}, None, 12_500_000.0,
                                    [], None, tick=tick_de("6J"), activo="6J")
    assert out["backstop_points"] == pytest.approx(4.55e-5, rel=1e-9)


# ═══════════════════════════════════════════════════════════════════════════
# 4) APLICABLE del estudio — llave por llave, kill-switch, R-T10
# ═══════════════════════════════════════════════════════════════════════════

KILL_SWITCH = {"mode", "dry_run", "traderspost", "status"}


def test_activacion_del_estudio_llave_por_llave(golden):
    ap = mrl.activacion_from_study(golden.study)
    assert set(ap) == {"backstop_points", "tp_nominal_long",
                       "tp_nominal_short", "entry_reserve_timeout_seconds",
                       "scale_entry"}
    assert ap["backstop_points"] == pytest.approx(40.0)       # $2000 / 50$/pt
    assert ap["tp_nominal_long"] == 15.0
    assert ap["tp_nominal_short"] == 15.0
    assert ap["entry_reserve_timeout_seconds"] == 3600
    assert ap["scale_entry"] == {
        "mode": "execute", "quantities": [5, 3, 2],
        "levels": [pytest.approx(2.0), pytest.approx(4.0)],
        "max_micro_contracts": 10}


def test_aplicable_jamas_lleva_kill_switch(golden):
    for ap in (mrl.activacion_from_study(golden.study),
               mrl.config_from_overrides(dict(OVERRIDES), ATR, PPT,
                                         [5, 3, 2], 3600.0, tick=TICK,
                                         activo="ES")):
        assert not (set(ap) & KILL_SWITCH)


def test_rt10_la_fila_oos_jamas_es_aplicable(golden):
    # R-T10: la ventana OOS deriva SUS propias palancas (espejo de robustez) —
    # en este fixture el backstop OOS ni existe (ningún trade OOS toca el
    # grid) — y AUN ASÍ el aplicable sale de la fila IN-SAMPLE.
    st = golden.study
    assert st["levers_oos"]["backstop_usd"] is None           # OOS ≠ in-sample
    assert st["tabla_b"]["convergencia"]["backstop_usd"] == "divergen"
    ap = mrl.activacion_from_study(st)
    assert ap["backstop_points"] == pytest.approx(40.0)       # la IN-SAMPLE
    assert "ESPEJO" in st["tabla_b"]["nota_oos"]
    # estructural: si borro la fila in-sample, NINGUNA palanca queda aplicable
    # (nunca cae en silencio a la OOS). El cancel_after sobrevive porque es
    # del ESTUDIO (corte de fills), no una palanca derivada de ventana.
    mutilado = {**st, "levers_in_sample": {}}
    assert set(mrl.activacion_from_study(mutilado)) <= \
        {"entry_reserve_timeout_seconds"}


# ═══════════════════════════════════════════════════════════════════════════
# 5) DETERMINISMO — dos corridas → el MISMO JSON
# ═══════════════════════════════════════════════════════════════════════════

def test_determinismo_dos_corridas_mismo_json(golden):
    s2 = mrl.run_for_clave(CLAVE, golden.motor, fecha=FECHA)
    a = json.dumps(golden.study, sort_keys=True, ensure_ascii=False)
    b = json.dumps(s2, sort_keys=True, ensure_ascii=False)
    assert a == b


# ═══════════════════════════════════════════════════════════════════════════
# 6) HALLAZGO D — pierna MÁS PROFUNDA que el stop (GC, operador 2026-07-18)
#    FIX-D-EJECUCION aplicado: el contrato correcto es VERDE en las 4 rutas.
# ═══════════════════════════════════════════════════════════════════════════
# Construcción a mano: ATR 5, backstop 40 pts (8×ATR), pierna profunda a
# 9×ATR = 45 pts, MAE 50 pts → el trade ESTÁ stoppeado y la pierna llena MÁS
# ALLÁ del stop. El stop ya reventó cuando la pierna llena → salida ≈ al
# precio del fill → pnl ≈ 0 − gap (JAMÁS positivo). El modelo viejo la
# valoraba saliendo al precio del stop: +(d·ATR − b) = +$100 fantasma por
# 0.4 de contrato (cuantificado en flota: GC +$5,167 · NQ +$5,901 con OOS
# honesto NEGATIVO — CONTRATO/AUDITORIA_Total_Luxy_FixtureOro_2026-07-18.md).

_ST_D = SimTrade(number=99, side="long", in_sample=True, entry_price=5000.0,
                 atr_pts=ATR, mae_pts=50.0, mfe_pts=0.0,
                 native_pnl_usd=-2000.0)
_LEGS_D = ((0.0, 0.6), (9.0, 0.4))
_B_PTS_D = 40.0


def test_pierna_mas_profunda_que_stop_COMPORTAMIENTO_CORRECTO():
    """El contrato honesto: exit ≈ fill para la pierna con d·ATR > b_pts.
    A MANO: 0.6·(−40 pts)·50$ + 0.4·(0 pts)·50$ = −$1200 — y el evaluador
    Luxy debe dar EXACTAMENTE lo mismo (paridad v1↔Luxy)."""
    usd, fw, _amb = ladder_outcome(_ST_D, _LEGS_D, _B_PTS_D, None, PPT,
                                   HaircutCfg())
    assert usd == pytest.approx(-1200.0)
    assert fw == pytest.approx(1.0)
    lux, part = mrl.luxy_outcome(_ST_D, {}, {}, legs=_LEGS_D, b_pts=_B_PTS_D,
                                 tp_by_side=None, be_atr=None, ppt=PPT,
                                 cancel_after_s=None)
    assert part is True
    assert lux == pytest.approx(-1200.0)


def test_pierna_mas_profunda_que_stop_paga_el_gap_no_gana_jamas():
    """Con estrés de gap, la pierna profunda paga el gap del exit (0 − gap):
    0.6·(−(40+5)) + 0.4·(−(0+5)) = −29 pts → −$1450. Nunca positivo."""
    usd, _fw, _amb = ladder_outcome(_ST_D, _LEGS_D, _B_PTS_D, None, PPT,
                                    HaircutCfg(gap_pts=5.0))
    assert usd == pytest.approx(-1450.0)
    # y una pierna ligeramente MENOS honda que el stop sigue perdiendo lo suyo
    usd2, _, _ = ladder_outcome(_ST_D, ((0.0, 0.6), (7.0, 0.4)), _B_PTS_D,
                                None, PPT, HaircutCfg())
    assert usd2 == pytest.approx((0.6 * -40.0 + 0.4 * -5.0) * PPT)  # −$1300
