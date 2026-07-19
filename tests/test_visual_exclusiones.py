"""LOTE VISUAL-EXCLUSIONES + PIERNAS-CLARIDAD (2026-07-19) — guardas de render/JS.

Parte 1 — lenguaje ÚNICO de exclusión en el diagrama: toda barra excluida de
la fila (día, sesión O DIRECCIÓN) se atenúa con el mismo estilo; tooltip con
el motivo; el contador del SL y el atenuado comparten UNA definición de
"fuera de la fila". Decisión de escala: se mantiene (con nota) — justificado
en el propio código.

Parte 2 — panel Piernas/Re-armado: conversión ATR↔$ en el header y junto a
cada profundidad; ancla al ESTUDIO declarada + chip ámbar si las palancas
difieren; el VEREDICTO (constantes RA-2) encabeza el panel.

Patrón de la casa (test_lx15_js_state / test_display_fx_slrespiro): asserts
de fuente sobre el template + sintaxis JS verificada en node (skip sin node).
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_TPL = Path("app/templates/strategy_detail.html")
_SRC = _TPL.read_text(encoding="utf-8")


def _extract_fn(name: str) -> str:
    """Cuerpo completo de `function name(){...}` por conteo de llaves."""
    i = _SRC.index("function " + name + "(")
    j = _SRC.index("{", i)
    depth = 0
    for k in range(j, len(_SRC)):
        c = _SRC[k]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return _SRC[i:k + 1]
    raise AssertionError(f"function {name} no cierra llaves")


# ---------------------------------------------------------------------------
# Parte 1 — exclusión por dirección atenuada + tooltip + escala con nota
# ---------------------------------------------------------------------------

def test_exclusion_por_direccion_atenua_igual_que_dia_sesion():
    dc = _extract_fn("drawChart")
    # la definición ÚNICA de excluida incluye dirección junto a día/sesión
    assert "BH.has(d.hr)||BD.has(d.dow)||!dirOk" in dc
    assert "S.dir==='both'||(S.dir==='long'?d.long:!d.long)" in dc
    # y el atenuado usa esa definición (mismo estilo que día/sesión)
    assert "if(excl) ctx.globalAlpha=0.30" in dc


def test_tooltip_declara_el_motivo_de_exclusion():
    dc = _extract_fn("drawChart")
    assert "excluida de la fila — " in dc
    for motivo in ("dirección: solo ", "sesión apagada", "día apagado"):
        assert motivo in dc, f"falta el motivo '{motivo}' en el tooltip"
    assert "_lxTipBound" in dc                     # bind una sola vez


def test_contador_sl_y_atenuado_comparten_definicion():
    """El contador del SL cuenta participantes con la MISMA `excl` que atenúa
    (una sola verdad de 'fuera de la fila') y declara los excluidos."""
    dc = _extract_fn("drawChart")
    assert "if(slOn && d.mae<0){ if(!excl){ slTot++;" in dc
    assert "EXCLUIDA(s) por dirección/toggles" in dc


def test_escala_se_mantiene_con_nota_justificada():
    dc = _extract_fn("drawChart")
    assert "la escala incluye también las barras atenuadas" in dc
    assert "recortada (p95)" in dc                 # el remedio a la distorsión


# ---------------------------------------------------------------------------
# Parte 2 — Piernas/Re-armado: conversión, ancla + chip, jerarquía
# ---------------------------------------------------------------------------

def test_piernas_header_con_conversion_atr_usd():
    pi = _extract_fn("piernas")
    assert "1 ATR (mediana del estudio)" in pi
    assert "atr_med_pts" in pi                     # mediana DEL ESTUDIO (units)
    assert "window.luxyFmtPts(atrUsd" in pi        # FX en ticks (espejo fmt_pts)
    # equivalente $ junto a cada profundidad (C2/C3 en curva y header)
    assert pi.count("cUsd(P.c2)") >= 2 and pi.count("cUsd(P.c3)") >= 2


def test_piernas_ancla_declarada_y_chip():
    pi = _extract_fn("piernas")
    assert "⚓ Anclado a la escalera del" in pi
    assert "NO sigue las palancas del panel" in pi
    assert "lx-piernas-ancla-chip" in pi
    # el chip lo enciende refresh() comparando contra RECO0 (palancas del estudio)
    rf = _extract_fn("refresh")
    assert "lx-piernas-ancla-chip" in rf
    assert "RECO0.l2" in rf and "RECO0.l3" in rf


def test_piernas_veredicto_encabeza_el_panel():
    """Jerarquía (3): el recuadro del veredicto + constantes RA-2 va ANTES de
    la curva de llegada y de la tabla de oro (evidencia debajo)."""
    pi = _extract_fn("piernas")
    i_ver = pi.index("Constantes propuestas para RA-2")
    i_curva = pi.index("Curva de llegada")
    i_oro = pi.index("Pregunta de oro")
    assert i_ver < i_curva < i_oro
    # y no quedó un segundo bloque de veredicto duplicado al final
    assert pi.count("Constantes propuestas para RA-2") == 1


# ---------------------------------------------------------------------------
# Sintaxis JS real (node) de las funciones tocadas — skip sin node
# ---------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
@pytest.mark.parametrize("fn", ["drawChart", "piernas", "refresh", "legend"])
def test_sintaxis_js_en_node(fn):
    code = _extract_fn(fn)
    assert "{{" not in code, "Jinja dentro de la función — no verificable"
    harness = ("let s='';process.stdin.setEncoding('utf8');"
               "process.stdin.on('data',d=>s+=d);"
               "process.stdin.on('end',()=>{try{new Function(s);"
               "console.log('OK')}catch(e){console.error('SYNTAX: '+e.message);"
               "process.exit(1)}});")
    # bytes UTF-8 explícitos: en Windows text=True encodearía cp1252 y los
    # −/— del template revientan ANTES de llegar a node.
    r = subprocess.run(["node", "-e", harness], input=code.encode("utf-8"),
                       capture_output=True)
    out = (r.stdout or b"").decode("utf-8", "replace")
    err = (r.stderr or b"").decode("utf-8", "replace")
    assert r.returncode == 0, out + err
    assert "OK" in out
