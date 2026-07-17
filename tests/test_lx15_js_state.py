"""LX-15-JS bug-fix — wiring del estado validado/dirty (fuente única VSNAP).

Extrae las funciones PURAS window.luxySnap / window.luxyDirty del template y las
ejecuta en node, reproduciendo el bug D1 (con C1≠0 el estado quedaba dirty perpetuo
→ Aplicar deshabilitado contra el chip 'validado·motor'). Skip si node no está.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_TPL = Path("app/templates/strategy_detail.html")


def _extract_pure() -> str:
    src = _TPL.read_text(encoding="utf-8")
    m = re.search(
        r"(window\.luxySnap = function.*?window\.luxyDirty = function.*?\};)",
        src, re.S)
    assert m, "no encuentro window.luxySnap/luxyDirty en el template"
    return m.group(1)


def test_pure_state_functions_present_and_single_source():
    """Las funciones puras existen y el template las usa como fuente única."""
    src = _TPL.read_text(encoding="utf-8")
    assert "window.luxySnap = function" in src
    assert "window.luxyDirty = function" in src
    # dirty() del IIFE se apoya en VSNAP (no en RECO0) — fuente única
    assert "window.luxyDirty(snap(), VSNAP)" in src
    # Recalcular y Restablecer re-fijan VSNAP
    assert src.count("VSNAP=snap()") >= 2 or src.count("VSNAP = snap()") >= 2 \
        or (src.count("VSNAP=snap()") + src.count("VSNAP = snap()")) >= 2


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible")
def test_luxy_state_wiring_node():
    harness = "var window={};\n" + _extract_pure() + "\n" + r"""
      var snap = window.luxySnap, dirty = window.luxyDirty;
      function A(c,m){ if(!c){ console.error('FAIL: '+m); process.exit(1); } }
      // (1) C1/l1 ENTRA en el snapshot (el bug era que no)
      A(snap({l1:0,l2:100,l3:200,dir:'both'},[],{}) !==
        snap({l1:1680,l2:100,l3:200,dir:'both'},[],{}), 'l1 debe entrar al snapshot');
      // (2) BUG D1: un estado con C1≠0 VALIDADO contra sí mismo NO es dirty
      var st = {l1:1680,l2:100,l3:200,dir:'both',sl:true,slV:500,sc:true};
      var VSNAP = snap(st,[],{});
      A(dirty(snap(st,[],{}), VSNAP) === false, 'C1 validado NO debe ser dirty perpetuo');
      // (3) mover una palanca tras validar -> dirty (Recalcular se re-habilita)
      var st2 = Object.assign({}, st, {l1:2000});
      A(dirty(snap(st2,[],{}), VSNAP) === true, 'mover C1 tras validar -> dirty');
      // (4) los toggles de sesion/dia entran en el snapshot
      A(dirty(snap(st,[false],{}), VSNAP) === true, 'toggles deben entrar al snapshot');
      console.log('OK');
    """
    r = subprocess.run(["node", "-e", harness], capture_output=True, text=True)
    assert r.returncode == 0, (r.stdout or "") + (r.stderr or "")
    assert "OK" in r.stdout
