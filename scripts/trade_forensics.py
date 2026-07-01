#!/usr/bin/env python3
"""trade_forensics — reconstruye barra por barra qué pasó tras una entrada.

Para una entrada (la última de la estrategia, o una por --signal), imprime la
secuencia de barras 5m tras la señal marcando cuándo el precio tocó el SL, el TP
o volvió a la entrada, y busca señales de SALIDA (LuxAlgo flat) del mismo símbolo
en la ventana. Así distingues si cerró el SL nuestro o una señal de LuxAlgo, y en
qué orden ocurrió el rebote.

Uso (servidor, venv):
  source .venv/bin/activate
  python -m scripts.trade_forensics --strategy NQ5m_ConfAny_ST_TC
  python -m scripts.trade_forensics --strategy NQ5m_ConfAny_ST_TC --signal fa25ddd1
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta

from sqlalchemy import or_, select

from app.db.session import AsyncSessionLocal
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.ohlcv_bar import OhlcvBar
from app.services.symbol_mapper import SymbolMapper


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--signal", help="prefijo del id de decisión o de señal (8 chars)")
    ap.add_argument("--window-min", type=int, default=360)
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        mapper = SymbolMapper()
        q = (select(StrategyDecision, NormalizedSignal)
             .join(NormalizedSignal,
                   StrategyDecision.normalized_signal_id == NormalizedSignal.id)
             .where(StrategyDecision.strategy_id == args.strategy,
                    StrategyDecision.outcome == "APPROVE",
                    NormalizedSignal.action != "exit")
             .order_by(StrategyDecision.created_at.desc()))
        rows = (await db.execute(q)).all()
        pick = None
        for dec, sig in rows:
            if args.signal:
                if str(dec.id).startswith(args.signal) or str(sig.id).startswith(args.signal):
                    pick = (dec, sig); break
            else:
                pick = (dec, sig); break
        if not pick:
            print("No encontré la entrada."); return
        dec, sig = pick

        ts = sig.signal_ts or dec.created_at
        entry = float(sig.price) if sig.price is not None else None
        sl = float(dec.sl_price) if dec.sl_price is not None else None
        tp = float(dec.tp_price) if dec.tp_price is not None else None
        side = sig.action  # buy / sell
        end = ts + timedelta(minutes=args.window_min)
        print(f"=== {args.strategy} | {sig.ticker_received}->{sig.mapped_symbol} "
              f"{side} @ {entry} | {ts} UTC ===")
        print(f"   SL={sl}  TP={tp}  (decisión {str(dec.id)[:8]})")

        # ── Señales de SALIDA del mismo símbolo en la ventana ──
        exits = (await db.execute(
            select(NormalizedSignal)
            .where(NormalizedSignal.strategy_id == args.strategy,
                   NormalizedSignal.mapped_symbol == sig.mapped_symbol,
                   NormalizedSignal.signal_ts > ts,
                   NormalizedSignal.signal_ts <= end,
                   or_(NormalizedSignal.action == "exit",
                       NormalizedSignal.signal_role.like("exit%")))
            .order_by(NormalizedSignal.signal_ts)
        )).scalars().all()
        if exits:
            print("\n   Señales de SALIDA (LuxAlgo) en la ventana:")
            for e in exits:
                print(f"     • {e.signal_ts} UTC  action={e.action} role={e.signal_role} "
                      f"price={e.price}")
        else:
            print("\n   Señales de SALIDA (LuxAlgo) en la ventana: ninguna")

        # ── Barras ──
        data_sym = await mapper.resolve_market_data_symbol(db, sig.ticker_received)
        bars = []
        for cand in [data_sym, sig.mapped_symbol, sig.ticker_received]:
            if not cand:
                continue
            r = (await db.execute(
                select(OhlcvBar.bar_time, OhlcvBar.high, OhlcvBar.low, OhlcvBar.close)
                .where(OhlcvBar.symbol == cand,
                       OhlcvBar.bar_time >= ts, OhlcvBar.bar_time <= end)
                .order_by(OhlcvBar.bar_time)
            )).all()
            r = [(b[0], float(b[1]), float(b[2]),
                  float(b[3]) if b[3] is not None else None)
                 for b in r if b[1] is not None and b[2] is not None]
            if r:
                bars = r; break
        if not bars:
            print("\n   ⚠ Sin barras del bridge en la ventana."); return

        is_long = side == "buy"
        t_sl = t_tp = t_back = None
        print("\n   hora (UTC)        high      low      close   marcas")
        for bt, bh, bl, bc in bars:
            marks = []
            if sl is not None:
                hit_sl = (bl <= sl) if is_long else (bh >= sl)
                if hit_sl:
                    marks.append("SL")
                    if t_sl is None:
                        t_sl = bt
            if tp is not None:
                hit_tp = (bh >= tp) if is_long else (bl <= tp)
                if hit_tp:
                    marks.append("TP")
                    if t_tp is None:
                        t_tp = bt
            if entry is not None:
                back = (bh >= entry) if is_long else (bl <= entry)
                if back:
                    marks.append("=entrada")
                    if t_back is None:
                        t_back = bt
            hhmm = bt.strftime("%m-%d %H:%M")
            print(f"   {hhmm}   {bh:>9} {bl:>9} {str(bc):>9}   {' '.join(marks)}")
            # cortar poco después de cerrar (SL o TP)
            if (t_sl or t_tp) and bt >= (t_sl or t_tp) + timedelta(minutes=20):
                print("   … (se corta 20m después del cierre)")
                break

        print("\n   RESUMEN:")
        print(f"     primer toque SL:  {t_sl or '—'}")
        print(f"     primer toque TP:  {t_tp or '—'}")
        print(f"     regreso a entrada: {t_back or 'nunca en la ventana'}")
        if exits:
            first_exit = exits[0].signal_ts
            if t_sl and first_exit < t_sl:
                print(f"     → LuxAlgo mandó salida ({first_exit}) ANTES del SL ({t_sl}).")
            elif t_sl:
                print(f"     → El SL se tocó ({t_sl}) antes/igual que cualquier salida LuxAlgo.")
        elif t_sl:
            print("     → Sin salida de LuxAlgo: lo cerró NUESTRO SL.")


if __name__ == "__main__":
    asyncio.run(main())
