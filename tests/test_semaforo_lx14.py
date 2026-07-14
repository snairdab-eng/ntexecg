"""LX-14 — Semáforo de robustez con mínimo de muestra.

Hallazgo: GC mostraba 🟢 (PF 2.87) con OOS de n=2 — un verde sin sustancia. Con
n_oos < RETENCION_N_MIN (10) el veredicto pasa a ⚪ "sin veredicto" (ni verde ni
rojo) y el gate LX-11 lo trata como ÁMBAR (checkbox), no como verde.
"""
import scripts.mr_luxy as mrl

R = mrl.robustez_semaforo
N_MIN = mrl.RETENCION_N_MIN


# ---------------------------------------------------------------------------
# 1) robustez_semaforo — muestra chica → sin veredicto; n≥10 → actual
# ---------------------------------------------------------------------------

def test_muestra_chica_sin_veredicto():
    # el caso GC: PF alto pero OOS de n=2 → NO es verde
    assert R({"net_usd": 100, "pf": 2.87, "n": 2})["verdict"] == "sin_veredicto"
    assert R({"net_usd": 1000, "pf": 1.5, "n": N_MIN - 1})["verdict"] == "sin_veredicto"


def test_n_suficiente_comportamiento_actual():
    assert R({"net_usd": 1000, "pf": 1.5, "n": N_MIN})["verdict"] == "verde"
    assert R({"net_usd": 500, "pf": 1.1, "n": N_MIN})["verdict"] == "amarillo"
    assert R({"net_usd": -1, "pf": 2.0, "n": N_MIN})["verdict"] == "rojo"
    # el umbral es EXACTO: n == RETENCION_N_MIN ya tiene veredicto
    assert R({"net_usd": 1000, "pf": 1.5, "n": N_MIN})["verdict"] != "sin_veredicto"


# ---------------------------------------------------------------------------
# 2) El gate LX-11 trata "sin veredicto" como ÁMBAR (no verde)
# ---------------------------------------------------------------------------

def _study(verdict, part=95.0):
    return {"dashboard": {"robustez": {"verdict": verdict}, "implausible": False,
                          "notes": [],
                          "table3": {"crudo_plus": {"participacion_pct": part}}},
            "contencion": {"pct": 100.0, "confiable": True}}


def test_gate_sin_veredicto_es_amber():
    g = mrl.gate_aplicar(_study("sin_veredicto"))
    assert g["nivel"] == "amber"
    assert any("SIN VEREDICTO" in t for t in g["triggers"])


def test_gate_verde_sigue_verde():
    assert mrl.gate_aplicar(_study("verde"))["nivel"] == "verde"


# ---------------------------------------------------------------------------
# 3) Réplica del hallazgo: GC (n=2) ⚪ + gate ámbar · ES (n≥10, PF≥1.3) 🟢 + verde
# ---------------------------------------------------------------------------

def test_gc_gris_es_verde():
    gc = R({"net_usd": 100, "pf": 2.87, "n": 2})        # GC real: OOS n=2
    es = R({"net_usd": 5000, "pf": 1.5, "n": 30})       # ES real: OOS amplio
    assert gc["verdict"] == "sin_veredicto"
    assert es["verdict"] == "verde"
    assert mrl.gate_aplicar(_study(gc["verdict"]))["nivel"] == "amber"
    assert mrl.gate_aplicar(_study(es["verdict"]))["nivel"] == "verde"
