"""LX-7 — PF honesto en muestras filtradas: con < MIN_PERDEDORES_PF perdedores el
PF es aritméticamente correcto pero estadísticamente vacío. El motor expone
`n_perdedores` por fila y el front decide el rotulado ("n/s (1 perdedor)")."""
import scripts.mr_luxy as mrl
from scripts.mr_sims import metrics_usd


def test_metrics_usd_expone_n_perdedores():
    assert metrics_usd([100.0, 200.0, -50.0])["n_perdedores"] == 1
    assert metrics_usd([100.0, -1.0, -2.0, -3.0])["n_perdedores"] == 3
    assert metrics_usd([50.0, 60.0])["n_perdedores"] == 0        # sin perdedores
    assert metrics_usd([]) == {"n": 0}                           # vacío


def test_card_propaga_n_perdedores():
    # 1 perdedor → el PF (aritmético) existe, pero n_perdedores=1 dispara "n/s"
    c1 = mrl._card(metrics_usd([500.0, 300.0, 200.0, -3.0]))
    assert c1["n_perdedores"] == 1 and c1["pf"] is not None
    # 3+ perdedores → PF normal
    c3 = mrl._card(metrics_usd([100.0, -50.0, -20.0, -10.0]))
    assert c3["n_perdedores"] == 3
    # sin muestra → None (no revienta el front)
    assert mrl._card(metrics_usd([])).get("n_perdedores") is None
