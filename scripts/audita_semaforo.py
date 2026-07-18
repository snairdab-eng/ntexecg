"""BUG-HONESTIDAD SEMÁFORO/OOS — auditor READ-ONLY de las 7 estrategias.

Responde tres preguntas del operador SIN tocar nada:
  (parte 2)  ¿En cuántas de las 7 el SEMÁFORO miente HOY? Compara, por estudio
             vigente, el verdict CONGELADO (dashboard.robustez) contra el verdict
             CORRECTO recomputado con LX-7 (`robustez_semaforo` sobre la fila OOS
             table3.oos, que ya trae n_perdedores). Discrepancia = mentira.
  (parte 5c) ¿Qué estrategias tienen STOP DENTRO DE LA ESCALERA (una pierna C2/C3
             opera más allá del backstop, huérfana R-RA6)? Geometría en ×ATR desde
             levers_in_sample (b_pts, ladder.levels) + atr_med del estudio.
  (parte 4)  ¿Algún APPLY pasado se gateó con robustez EQUIVOCADA? Recorre el
             AuditLog APPLY_LUXY_* y marca los que gatearon 🟢/limpio mientras su
             evidencia OOS tenía <3 perdedores (LX-7) — el gate leyó un semáforo
             mentiroso. NO corrige nada: el operador decide.

INVARIANTES: solo lectura. Estudios desde disco (lo mismo que lee el front) y
SELECT sobre AuditLog. No escribe, no despacha. Sale 0.

Uso:  .venv/Scripts/python.exe scripts/audita_semaforo.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

# Correr directo (`python scripts/audita_semaforo.py`) desde la raíz del repo:
# asegura la raíz en sys.path para `import scripts.*` / `import app.*`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.mr_luxy import (MIN_PERDEDORES_PF, robustez_semaforo,
                             stop_dentro_escalera)

MOTOR_DIR = Path(os.environ.get("MOTOR_RIESGO_DIR") or "MotorRiesgo")


def _find_key(node, key):
    """Primer valor de `key` en el árbol (para atr_med_pts, anidado)."""
    if isinstance(node, dict):
        if key in node and node[key] is not None:
            return node[key]
        for v in node.values():
            r = _find_key(v, key)
            if r is not None:
                return r
    elif isinstance(node, list):
        for v in node:
            r = _find_key(v, key)
            if r is not None:
                return r
    return None


def _latest_study(clave_dir: Path) -> dict | None:
    hits = sorted((clave_dir / "runs").glob("luxy_*.json"))
    if not hits:
        return None
    try:
        return json.loads(hits[-1].read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _semaforo_check(study: dict) -> dict:
    """(frozen, correcto, miente) del semáforo vs la fila OOS con LX-7."""
    dash = (study or {}).get("dashboard") or {}
    frozen = (dash.get("robustez") or {}).get("verdict")
    oos = (dash.get("table3") or {}).get("oos") or {}
    correcto = robustez_semaforo(oos)              # aplica LX-14 + LX-7
    return {"frozen": frozen, "correcto": correcto["verdict"],
            "reason": correcto.get("reason"),
            "n": oos.get("n"), "n_perdedores": oos.get("n_perdedores"),
            "pf": oos.get("pf"),
            "miente": frozen is not None and frozen != correcto["verdict"]}


def _stop_check(study: dict) -> dict | None:
    lev = (study or {}).get("levers_in_sample") or {}
    b_pts = lev.get("b_pts")
    levels = ((lev.get("ladder") or {}).get("levels")) or []
    atr_med = _find_key(study, "atr_med_pts")
    # ladder.levels = [C1, C2, C3] ×ATR → las piernas de escalera son [1:]
    return stop_dentro_escalera(b_pts, levels[1:], atr_med)


def _fs_sweep() -> None:
    print("=" * 78)
    print("AUDITORÍA SEMÁFORO/OOS (solo lectura) — estudios vigentes en disco")
    print("=" * 78)
    if not MOTOR_DIR.exists():
        print(f"\n(sin {MOTOR_DIR} — corre esto en el server con los estudios)")
        return
    claves = sorted(p for p in MOTOR_DIR.iterdir()
                    if p.is_dir() and (p / "runs").exists())
    mienten = stop_dentro = 0
    for cd in claves:
        study = _latest_study(cd)
        if not study:
            continue
        sc = _semaforo_check(study)
        st = _stop_check(study)
        flags = []
        if sc["miente"]:
            mienten += 1
            flags.append(f"⛔ SEMÁFORO MIENTE: congelado '{sc['frozen']}' vs "
                         f"correcto '{sc['correcto']}' (LX-7: {sc['reason']}; "
                         f"n={sc['n']} · perdedores={sc['n_perdedores']} · "
                         f"PF={sc['pf']})")
        if st:
            stop_dentro += 1
            hp = (f" · huérfana R-RA6 {st['huerfana_pct']}%"
                  if st.get("huerfana_pct") is not None else "")
            flags.append(f"⚠ STOP DENTRO DE ESCALERA: {st['pierna']} a "
                         f"{st['pierna_atr']}×ATR opera más allá del backstop "
                         f"({st['backstop_atr']}×ATR){hp}")
        estado = "⛔/⚠" if flags else "✓"
        print(f"\n[{estado}] {cd.name}")
        if not flags:
            print(f"      semáforo '{sc['frozen']}' coherente · sin stop-en-escalera")
        for f in flags:
            print("      · " + f)
    print("\n" + "-" * 78)
    print(f"RESUMEN estudios: {mienten} semáforo(s) MENTIROSO(s) · "
          f"{stop_dentro} con stop dentro de la escalera (de {len(claves)} claves).")


async def _audit_gate_impact() -> None:
    """Parte 4 — APPLY_LUXY_* que gatearon con robustez equivocada (LX-7)."""
    print("\n" + "=" * 78)
    print("IMPACTO EN EL GATE (parte 4) — APPLY_LUXY_* con robustez equivocada")
    print("=" * 78)
    try:
        from sqlalchemy import select
        from app.db.session import AsyncSessionLocal
        from app.models.audit_log import AuditLog
    except Exception as exc:                       # sin entorno DB → se omite
        print(f"\n(sin acceso a DB: {exc} — corre en el server para la parte 4)")
        return
    sospechosos = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.action.like("APPLY_LUXY%"))
            .order_by(AuditLog.created_at))).scalars().all()
        for r in rows:
            nv = r.new_value_json or {}
            gate = nv.get("_gate_lx11") or {}
            oos = ((nv.get("_evidencia_oos") or {}).get("oos")) or {}
            nl = oos.get("n_perdedores")
            nivel = gate.get("nivel")
            # gateó verde/limpio pero la evidencia OOS tenía <3 perdedores (LX-7):
            # el gate leyó un semáforo que hoy sabemos mentiroso.
            if nl is not None and nl < MIN_PERDEDORES_PF and nivel == "verde":
                sospechosos += 1
                print(f"\n  ⛔ {r.object_id} · {r.created_at} · gate={nivel} pero "
                      f"OOS perdedores={nl} (<{MIN_PERDEDORES_PF}, LX-7) — gateado "
                      f"con robustez equivocada. Revisar (el operador decide).")
    if not sospechosos:
        print("\n  ✓ Ningún APPLY_LUXY_* gateó verde con evidencia OOS <3 perdedores.")
    else:
        print(f"\n  {sospechosos} APPLY(s) sospechoso(s) — NO se corrige nada aquí.")


async def main() -> None:
    _fs_sweep()
    await _audit_gate_impact()
    print("\n" + "=" * 78)
    print("Read-only. El operador decide correcciones (re-Calcular estudios, "
          "re-aplicar palancas). Este script NO escribe nada.")
    print("=" * 78)


if __name__ == "__main__":
    asyncio.run(main())
