#!/usr/bin/env python3
"""forense_exit_parcial — barrido SOLO LECTURA del P0-EXIT-PARCIAL (2026-07-20).

El bug: TODOS los exits viajaron con "quantity" explícita. Para TradersPost un
exit CON quantity es un cierre PARCIAL de exactamente esa cantidad (docs:
"TradersPost is only able to partially exit open positions by sending the
explicit quantity"); solo el exit SIN quantity aplana la posición completa.
El exit de LuxAlgo enviaba la quantity de la ALERTA (p.ej. 1) y los cierres
autónomos (EOD/max_holding/reversal/Flatten) el estimado TOTAL DESPACHADO —
ninguno la cantidad realmente llenada en el broker.

Este script reconstruye, por (estrategia, destino, ticker), los episodios
entrada→exit de los envíos REALES (status SENT) y estima el residuo huérfano
que cada exit pudo dejar vivo en el broker:

  huerfano_min = max(0, qty_C1_mercado − qty_exit)
      lo SEGURO llenado (las piernas a mercado llenan al instante) menos lo
      cerrado — si es > 0, quedó posición huérfana con certeza práctica.
  huerfano_max = max(0, qty_total_despachada − qty_exit)
      si además llenaron todas las límites (C2/C3/re-armadas) antes del
      cancelAfter/cancel:true del exit.

El número REAL está entre ambos: depende de qué límites llenaron, que NTEXECG
no observa — el operador coteja contra el log de la cuenta del broker.

SOLO SELECT — cero escrituras. Uso (en el server, sobre la DB de producción):
  python -m scripts.forense_exit_parcial                       # todo el historial
  python -m scripts.forense_exit_parcial --desde 2026-07-01    # acotar fecha
  python -m scripts.forense_exit_parcial --strategy <id>
  python -m scripts.forense_exit_parcial --todo                # también episodios limpios
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.webhook_delivery import WebhookDelivery


def _qty(p: dict) -> int:
    try:
        return int(p.get("quantity") or 0)
    except (TypeError, ValueError):
        return 0


def _es_mercado(p: dict) -> bool:
    return p.get("orderType", "market") != "limit"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--desde", default=None,
                    help="fecha ISO (YYYY-MM-DD); default: todo el historial")
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--todo", action="store_true",
                    help="mostrar tambien episodios sin residuo estimado")
    args = ap.parse_args()

    stmt = select(WebhookDelivery).where(WebhookDelivery.status == "SENT")
    if args.strategy:
        stmt = stmt.where(WebhookDelivery.strategy_id == args.strategy)
    if args.desde:
        stmt = stmt.where(
            WebhookDelivery.created_at >= datetime.fromisoformat(args.desde))
    stmt = stmt.order_by(WebhookDelivery.created_at.asc())

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(stmt)).scalars().all()

    if not rows:
        print("(sin envios SENT en el rango — nada que reconstruir)")
        return

    # (estrategia, destino, ticker) → lista cronológica de deliveries
    grupos: dict[tuple, list] = defaultdict(list)
    for r in rows:
        p = r.payload_json or {}
        grupos[(r.strategy_id, r.destination or "base",
                p.get("ticker") or "?")].append(r)

    sospechosos = 0
    episodios_totales = 0
    print(f"=== Barrido forense exit-parcial — {len(rows)} envios SENT ===\n")

    for (sid, dest, ticker), rs in sorted(grupos.items()):
        # Episodio: acumula entradas (buy/sell, incluidas piernas re-armadas)
        # hasta el primer exit; el exit lo cierra y abre episodio nuevo.
        ep = None
        episodios = []
        for r in rs:
            p = r.payload_json or {}
            action = p.get("action")
            # "add" = pierna que suma (C2/C3/re-armadas post P0-2 ESCALERA)
            if action in ("buy", "sell", "add"):
                if ep is None:
                    ep = {"desde": r.created_at, "qty_total": 0,
                          "qty_mercado": 0, "n_legs": 0, "rearm_legs": 0,
                          "decisiones": set()}
                ep["qty_total"] += _qty(p)
                ep["n_legs"] += 1
                ep["decisiones"].add(str(r.decision_id)[:8])
                if _es_mercado(p):
                    ep["qty_mercado"] += _qty(p)
                if (p.get("extras") or {}).get("rearm_cycle"):
                    ep["rearm_legs"] += 1
            elif action == "exit":
                if ep is None:
                    # exit sin entrada previa registrada en el rango: reportarlo
                    # igual (el residuo no es computable desde aqui).
                    episodios.append({"desde": r.created_at, "qty_total": None,
                                      "qty_mercado": None, "n_legs": 0,
                                      "rearm_legs": 0, "decisiones": set(),
                                      "exit_at": r.created_at,
                                      "exit_qty": (p.get("quantity")),
                                      "exit_cancel": bool(p.get("cancel"))})
                    continue
                ep["exit_at"] = r.created_at
                ep["exit_qty"] = p.get("quantity")   # None = aplana completo
                ep["exit_cancel"] = bool(p.get("cancel"))
                episodios.append(ep)
                ep = None
        if ep is not None:                 # episodio aun abierto (sin exit)
            ep["exit_at"] = None
            episodios.append(ep)

        for ep in episodios:
            episodios_totales += 1
            qt, qm = ep.get("qty_total"), ep.get("qty_mercado")
            eq = ep.get("exit_qty")
            if ep.get("exit_at") is None:
                etiqueta = "ABIERTO (sin exit registrado)"
                hmin = hmax = None
            elif qt is None:
                etiqueta = "EXIT SIN ENTRADA EN RANGO (revisar a mano)"
                hmin = hmax = None
            elif eq is None:
                etiqueta = "exit sin quantity (aplana completo) — OK"
                hmin = hmax = 0
            else:
                e = int(eq)
                hmin = max(0, qm - e)
                hmax = max(0, qt - e)
                etiqueta = ("RESIDUO POSIBLE" if hmax > 0 else "cuadra")
            grave = (hmin or 0) > 0
            interesante = hmax is None or (hmax or 0) > 0
            if not interesante and not args.todo:
                continue
            if interesante and hmax is not None:
                sospechosos += 1
            marca = "!!" if grave else ("? " if interesante else "  ")
            desde = ep["desde"].strftime("%Y-%m-%d %H:%M") if ep.get("desde") else "?"
            hasta = (ep["exit_at"].strftime("%Y-%m-%d %H:%M")
                     if ep.get("exit_at") else "—")
            print(f" {marca} {sid} [{dest}] {ticker}")
            print(f"     entrada {desde}: total={qt} (mercado={qm}, "
                  f"legs={ep.get('n_legs')}, rearm={ep.get('rearm_legs')}) "
                  f"decisiones={sorted(ep.get('decisiones') or [])}")
            print(f"     exit    {hasta}: quantity={eq} "
                  f"cancel={ep.get('exit_cancel')}")
            if hmax is not None:
                print(f"     -> huerfano estimado: min={hmin} (solo mercado) / "
                      f"max={hmax} (si llenaron las limites) — {etiqueta}")
            else:
                print(f"     -> {etiqueta}")
            print()

    print(f"── Resumen: {episodios_totales} episodios reconstruidos; "
          f"{sospechosos} con residuo posible (huerfano_max>0 o no computable).")
    print("   El numero real por episodio esta entre min y max: cotejar cada")
    print("   uno contra el historial de ordenes/posiciones del broker.")


if __name__ == "__main__":
    asyncio.run(main())
