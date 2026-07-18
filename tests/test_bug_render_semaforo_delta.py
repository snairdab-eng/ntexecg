"""BUG-HONESTIDAD — guardas de render (fuente JS del detalle) para:
  · parte 1: el semáforo aplica LX-7 desde la fila OOS a la vista (<3 perdedores ⇒ ⚪).
  · parte 3: el Δ del re-armado se computa vs CORTE 1h (baseline despacho), no vs la
    fila anterior → re-armado(1 ciclo)==1h ⇒ Δ=$0 sin verde.

Guardas de FUENTE (mismo criterio que test_lx15_js_regresion_html): fijan el marcador
exacto del fix para que un refactor no lo revierta en silencio.
"""
from pathlib import Path

_HTML = Path(__file__).resolve().parents[1] / "app" / "templates" / "strategy_detail.html"
_SRC = _HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Parte 3 — Δ del re-armado vs corte 1h (baseline), no vs fila anterior
# ---------------------------------------------------------------------------

def test_delta_rearmado_baseline_corte_1h():
    # la referencia del re-armado es el corte 1h, no `prev` (la fila anterior 3h)
    assert "es_rearm?((C['1h']||{}).net_usd):prev" in _SRC


def test_delta_cero_no_es_verde():
    # net-ref===0 (re-armado==1h) → color MUTED, jamás verde
    assert "dz)?'var(--lx-muted)'" in _SRC
    assert "net-ref===0" in _SRC


def test_rearmado_no_desplaza_baseline():
    # el re-armado es informativo → NO mueve el baseline (duracion sigue vs 3h)
    assert "!es_rearm) prev=net" in _SRC


# ---------------------------------------------------------------------------
# Parte 1 — semáforo aplica LX-7 (fila OOS a la vista) → ⚪ con <3 perdedores
# ---------------------------------------------------------------------------

def test_semaforo_aplica_lx7_desde_fila_oos():
    assert "semaforo(mode, oosR)" in _SRC          # recibe la tarjeta OOS
    assert "_oc.nl<_NLMIN" in _SRC                  # <3 perdedores → ⚪
    assert "no evaluable — muy pocos perdedores" in _SRC


# ---------------------------------------------------------------------------
# Parte 5a — banner de stop-dentro-de-escalera JUNTO al panel ENTRADA/STOP
# ---------------------------------------------------------------------------

def test_banner_stop_escalera_junto_al_panel_stop():
    # el elemento vive dentro del panel "Entrada / stop (USD)" (tras el SL backstop)
    i_panel = _SRC.index("Entrada / stop (USD)")
    i_banner = _SRC.index('id="lx-stop-escalera"')
    i_sl = _SRC.index("SL backstop")
    assert i_panel < i_sl < i_banner               # banner tras el SL, en el panel
    # poblado desde D.stop_dentro_escalera con el dato R-RA6 de ESA estrategia
    assert "D.stop_dentro_escalera" in _SRC
    assert "huérfana R-RA6 '+sd.huerfana_pct" in _SRC
    assert "Stop DENTRO de la escalera" in _SRC
