#!/usr/bin/env python3
"""
diag_profiles — diagnóstico de SOLO LECTURA antes del --apply de la política v1.0.

(1) Lista todos los asset_profiles (id, symbol, contract_type=instrument,
    active=enabled, ventana, sl_atr_multiplier, atr_timeframe) y señala si falta MCL.
(2) Lista las strategies de los 8 micros (id, strategy_id, name, asset_symbol,
    status, enabled) — donde se persiste production/shadow (Strategy.status).
(3) Recuerda la convención de días de session_config_json.

NO escribe nada. Uso:  python -m scripts.diag_profiles
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.asset_profile import AssetProfile
from app.models.strategy import Strategy

MICROS = ["MES", "MNQ", "MYM", "MGC", "M2K", "M6E", "MJY", "MCL"]


def win(cfg: dict | None) -> str:
    if not cfg:
        return "—"
    return (f"{cfg.get('entry_start','?')}-{cfg.get('entry_end','?')} "
            f"days={cfg.get('days_enabled')} nde={cfg.get('next_day_end')}")


async def main() -> None:
    async with AsyncSessionLocal() as db:
        # (1) asset_profiles
        profs = (await db.execute(select(AssetProfile).order_by(AssetProfile.symbol))).scalars().all()
        print(f"=== (1) asset_profiles ({len(profs)} filas) ===")
        print(f"{'symbol':<8}{'instrument(ct)':<16}{'enab(active)':<13}{'sl':>5} {'atr_tf':>7}  ventana")
        present = set()
        for p in profs:
            present.add(p.symbol)
            sl = "None" if p.sl_atr_multiplier is None else f"{float(p.sl_atr_multiplier):g}"
            print(f"{p.symbol:<8}{str(p.contract_type):<16}{str(p.active):<13}{sl:>5} "
                  f"{str(p.atr_timeframe):>7}  {win(p.session_config_json)}")
        faltan = [m for m in MICROS if m not in present]
        print(f"\nMicros de la política presentes: {[m for m in MICROS if m in present]}")
        print(f"Micros FALTANTES: {faltan if faltan else 'ninguno'}")
        if "MCL" in faltan:
            print("→ MCL no existe en esta BD: el seed lo incluye pero esta BD se sembró antes "
                  "de agregarlo. Fix: python scripts/seed_dev_data.py (idempotente, solo inserta).")

        # (2) strategies de los 8 micros
        strats = (await db.execute(
            select(Strategy).where(Strategy.asset_symbol.in_(MICROS))
            .order_by(Strategy.asset_symbol)
        )).scalars().all()
        print(f"\n=== (2) strategies ligadas a los micros ({len(strats)}) ===")
        if not strats:
            print("(ninguna) — aún no hay estrategias para estos micros. production/shadow se "
                  "define en Strategy.status al crear/editar la estrategia (no en asset_profiles).")
        else:
            print(f"{'asset_symbol':<13}{'strategy_id':<28}{'status':<14}{'enabled':<8} name")
            for s in strats:
                print(f"{str(s.asset_symbol):<13}{s.strategy_id:<28}{s.status:<14}"
                      f"{str(s.enabled):<8} {s.name}")
        # también buscar por nombre/strategy_id que contengan el símbolo (por si asset_symbol es None)
        alls = (await db.execute(select(Strategy))).scalars().all()
        print(f"\n(total strategies en la BD: {len(alls)})")

        # (3) convención de días
        print("\n=== (3) convención session_config_json ===")
        print("days_enabled usa strftime('%w'): 0=Domingo, 1=Lunes, ..., 6=Sábado.")
        print("  → day=0 es DOMINGO; day=1 es LUNES. [1..5]=L-V; [0,1,2,3,4,5]=Dom-Vie.")
        print("18:00-17:00 con next_day_end=True: 'en sesión' si hora>=18:00 O hora<17:00,")
        print("  evaluado sobre el DÍA ACTUAL (no el día de inicio de sesión). Cubre la semana")
        print("  Globex activa, pero nominalmente también marca 'en sesión' el Domingo antes de")
        print("  18:00 y el Viernes después de 18:00 (bordes). En la práctica esos tramos quedan")
        print("  bloqueados por el heartbeat de datos (sin barras → Nivel 1.6 BLOCK entradas).")


if __name__ == "__main__":
    asyncio.run(main())
