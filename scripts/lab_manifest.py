#!/usr/bin/env python3
"""lab_manifest — mapa CSV ↔ ESTRATEGIA del Laboratorio (B6.1).

Cada CSV de LuxAlgo es UNA estrategia (un activo corre varias con números
distintos). El manifest (REPORTES/lab_manifest.json) llavea el estudio por
strategy_id:

  {"version": 1, "entries": {
     "<strategy_id>": {"instrument": "ES",
                        "csv": "ListaDeOperaciones/....csv",
                        "confirmed": false,
                        "candidates": ["...otras estrategias del símbolo"]}}}

`propose` lee las estrategias existentes (DB) y los CSV presentes y PROPONE
el mapeo: cada CSV → la estrategia PRIMARIA de su símbolo (la primera por
orden alfabético — cámbiala editando el JSON si la primaria es otra);
símbolo sin estrategia registrada → el instrumento como id (retrocompat).
El OPERADOR confirma (--confirm marca todo, o edita "confirmed" a mano).
No pisa entradas confirmadas salvo --force.

Uso (servidor, venv):
  python -m scripts.lab_manifest propose            # propone y muestra
  python -m scripts.lab_manifest propose --confirm  # propone y confirma
  python -m scripts.lab_manifest show
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import re
import sys
from datetime import datetime
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, OSError):
    pass

MANIFEST_PATH = Path("REPORTES/lab_manifest.json")
TRADES_DIR = Path("ListaDeOperaciones")

# Los 8 instrumentos del lab y el mapeo micro→lab (mismo que apply_cancel_after)
INSTRUMENTS = ("ES", "NQ", "RTY", "GC", "CL", "6E", "6J", "YM")
MICRO_TO_LAB = {
    "MES": "ES", "MNQ": "NQ", "M2K": "RTY", "MGC": "GC",
    "MCL": "CL", "M6E": "6E", "MJY": "6J", "M6J": "6J", "MYM": "YM",
    **{s: s for s in INSTRUMENTS},
}

_CSV_SYM = re.compile(r"_([A-Z0-9]{1,4})1!_")


def csv_instrument(path: str) -> str | None:
    """Instrumento del lab desde el nombre del CSV (patrón `_<SYM>1!_`)."""
    m = _CSV_SYM.search(Path(path).name)
    sym = m.group(1) if m else None
    return sym if sym in INSTRUMENTS else None


def propose_entries(csvs: list[str],
                    strategies: list[tuple[str, str]]) -> dict:
    """Propuesta pura: {strategy_id: entry} desde los CSV presentes y las
    estrategias registradas [(strategy_id, asset_symbol)]."""
    by_instr: dict[str, list[str]] = {}
    for sid, asset in strategies:
        instr = MICRO_TO_LAB.get((asset or "").strip().upper())
        if instr:
            by_instr.setdefault(instr, []).append(sid)
    entries: dict[str, dict] = {}
    for csv_path in sorted(csvs):
        instr = csv_instrument(csv_path)
        if instr is None:
            continue
        candidates = sorted(by_instr.get(instr, []))
        key = candidates[0] if candidates else instr
        entries[key] = {
            "instrument": instr,
            "csv": str(Path(csv_path).as_posix()),
            "confirmed": False,
            "candidates": candidates or None,
        }
    return entries


def merge_proposal(existing: dict, proposed: dict, force: bool = False) -> dict:
    """Mezcla propuesta sobre lo existente sin pisar lo CONFIRMADO por el
    operador (salvo force)."""
    out = dict(existing)
    for key, entry in proposed.items():
        if key in out and out[key].get("confirmed") and not force:
            continue
        out[key] = entry
    return out


def load_manifest(path: Path | None = None) -> dict | None:
    p = path or MANIFEST_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def save_manifest(entries: dict, path: Path | None = None) -> Path:
    p = path or MANIFEST_PATH
    p.parent.mkdir(exist_ok=True)
    p.write_text(json.dumps({
        "version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
    }, indent=1, ensure_ascii=False), encoding="utf-8")
    return p


async def _db_strategies() -> list[tuple[str, str]]:
    """[(strategy_id, asset_symbol)] desde la DB (solo lectura)."""
    from sqlalchemy import select
    from app.db.session import AsyncSessionLocal
    from app.models.strategy import Strategy

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(Strategy))).scalars().all()
        return [(s.strategy_id, s.asset_symbol or "") for s in rows]


def print_entries(entries: dict) -> None:
    print(f"{'strategy_id':34} {'instr':5} {'conf':5} csv")
    for key, e in sorted(entries.items(), key=lambda kv: (kv[1]["instrument"],
                                                          kv[0])):
        mark = "sí" if e.get("confirmed") else "NO"
        print(f"{key:34} {e['instrument']:5} {mark:5} {Path(e['csv']).name}")
        cands = e.get("candidates") or []
        if len(cands) > 1:
            print(f"{'':46}↳ candidatas del símbolo: {', '.join(cands)}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("propose", "show"))
    ap.add_argument("--confirm", action="store_true",
                    help="marcar las entradas propuestas como confirmadas")
    ap.add_argument("--force", action="store_true",
                    help="pisar también las entradas confirmadas")
    args = ap.parse_args()

    existing = (load_manifest() or {}).get("entries") or {}
    if args.cmd == "show":
        if not existing:
            print("(sin manifest — corre `propose`)")
            return
        print_entries(existing)
        return

    try:
        strategies = await _db_strategies()
    except Exception as exc:                       # DB no disponible (NTDEV)
        print(f"⚠ sin DB ({exc.__class__.__name__}) — propongo solo por "
              "instrumento (retrocompat)")
        strategies = []
    csvs = sorted(glob.glob(str(TRADES_DIR / "*.csv")))
    proposed = propose_entries(csvs, strategies)
    if args.confirm:
        for e in proposed.values():
            e["confirmed"] = True
    merged = merge_proposal(existing, proposed, force=args.force)
    p = save_manifest(merged)
    print(f"✅ manifest → {p}  (confirma editando \"confirmed\" o con "
          "--confirm)\n")
    print_entries(merged)


if __name__ == "__main__":
    asyncio.run(main())
