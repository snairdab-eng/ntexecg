"""BUG-HONESTIDAD SEMÁFORO/OOS (parte 1) — el semáforo y la fila OOS aplican LX-7
consistentemente (una sola verdad). Hallazgo GC 07-17: semáforo 🟢 PF 2.87 vs
fila OOS 'n/s (2 perdedores)'. La causa: `robustez_semaforo` aplicaba LX-14 (n<10)
pero NO LX-7 (n_perdedores<3). El gate LX-11 lee esta misma fuente.
"""
import pytest

import scripts.mr_luxy as mrl

R = mrl.robustez_semaforo
N_MIN = mrl.RETENCION_N_MIN            # 10
NL_MIN = mrl.MIN_PERDEDORES_PF         # 3


# ---------------------------------------------------------------------------
# 1) LX-7 en el semáforo — <3 perdedores ⇒ ⚪ sin veredicto (aunque n≥10 y PF alto)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("nl", [0, 1, 2])
def test_pocos_perdedores_sin_veredicto(nl):
    # el caso GC exacto: n≥10, PF 2.87, pero <3 perdedores → NO es 🟢
    r = R({"net_usd": 5000, "pf": 2.87, "n": 15, "n_perdedores": nl})
    assert r["verdict"] == "sin_veredicto"
    assert r["reason"] == "pocos_perdedores"


@pytest.mark.parametrize("nl,esperado", [(3, "verde"), (5, "verde")])
def test_perdedores_suficientes_evalua(nl, esperado):
    r = R({"net_usd": 5000, "pf": 2.87, "n": 15, "n_perdedores": nl})
    assert r["verdict"] == esperado and r["reason"] is None


def test_lx14_sigue_vigente_muestra_chica():
    # n<10 → sin veredicto (muestra chica) aun con perdedores suficientes
    r = R({"net_usd": 5000, "pf": 2.0, "n": N_MIN - 1, "n_perdedores": 8})
    assert r["verdict"] == "sin_veredicto" and r["reason"] == "muestra_chica"


def test_n_perdedores_ausente_retrocompat():
    # caller sin el dato → LX-7 no se juzga, solo LX-14
    assert R({"net_usd": 5000, "pf": 2.0, "n": 15})["verdict"] == "verde"


def test_evaluable_completo_verde_amarillo_rojo():
    base = {"n": 15, "n_perdedores": 6}
    assert R({**base, "net_usd": 1000, "pf": 1.5})["verdict"] == "verde"
    assert R({**base, "net_usd": 500, "pf": 1.1})["verdict"] == "amarillo"
    assert R({**base, "net_usd": -1, "pf": 2.0})["verdict"] == "rojo"


# ---------------------------------------------------------------------------
# 2) El GATE lee la MISMA fuente — <3 perdedores gatea ÁMBAR, no verde
# ---------------------------------------------------------------------------

def test_gate_lee_lx7_del_semaforo():
    # con la robustez correcta (sin_veredicto por pocos perdedores) el gate es amber
    verd = R({"net_usd": 5000, "pf": 2.87, "n": 15, "n_perdedores": 2})["verdict"]
    study = {"dashboard": {"robustez": {"verdict": verd}, "implausible": False,
                           "table3": {}, "notes": []}}
    assert mrl.gate_aplicar(study)["nivel"] == "amber"
