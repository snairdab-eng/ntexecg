#!/usr/bin/env python3
"""pullback_timing — estudio por estrategia del TIEMPO hasta el pullback.

Para las entradas APPROVE de una estrategia (o todas), mide cuánto tarda el
precio en TOCAR cada pierna límite después de la señal (barras 5m del bridge).
Agrega la distribución (mediana, p75, p90) y sugiere un valor de
`Cancel entry after` por estrategia = p90 del tiempo al toque, topado a 3600 s.

Sirve para parametrizar en TradersPost, por estrategia, cuánto debe vivir la
orden de entrada antes de cancelarse.

Uso (servidor, venv):
  source .venv/bin/activate
  python -m scripts.pullback_timing --strategy NQ5m_ConfAny_ST_TC
  python -m scripts.pullback_timing --all --lookback-days 30 --window-min 180
"""
from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.ohlcv_bar import OhlcvBar
from app.models.webhook_delivery import WebhookDelivery
from app.services.symbol_mapper import SymbolMapper


def suggest_cancel_after(
    touch_min: list[float],
    cushion_seconds: int = 60,
    cap_seconds: int = 3600,
) -> int | None:
    """NX-17 — sugerencia de `Cancel entry after` (= entry_reserve_timeout_seconds).

    p90 del tiempo al pullback (minutos) convertido a segundos + colchón,
    topado a `cap_seconds`. None si no hay datos.
    """
    p90 = pctl(touch_min, 0.90)
    if p90 is None:
        return None
    return min(cap_seconds, int(p90 * 60) + cushion_seconds)


async def apply_suggestion(
    db, strategy_id: str, seconds: int,
    actor: str = "pullback_timing",
    reason: str = "cancel_after por estrategia (p90 pullback + colchon, NX-17)",
):
    """NX-17 — escribe `entry_reserve_timeout_seconds` (= cancel_after) en el
    StrategyProfile. UNA sola caducidad: pierna límite (TradersPost), reserva
    de símbolo (NX-28) y cancel_after comparten este valor.

    ⚠ TradersPost no tiene API para esto: tras aplicar aquí, fija el MISMO
    valor a mano en TradersPost → Strategy → Settings → "Cancel entry after".
    Devuelve el valor anterior (None si no había). Merge, no reemplazo; audit
    (actor/reason parametrizables para que otros CLI auditen honesto, p. ej.
    apply_cancel_after con el valor de DISEÑO del Laboratorio).
    """
    from app.models.strategy_profile import StrategyProfile
    from app.services.audit_service import AuditService

    prof = (await db.execute(
        select(StrategyProfile).where(StrategyProfile.strategy_id == strategy_id)
    )).scalar_one_or_none()
    if prof is None:
        prof = StrategyProfile(strategy_id=strategy_id, mode="paper")
        db.add(prof)
    cfg = dict(prof.pipeline_config_json or {})
    old = cfg.get("entry_reserve_timeout_seconds")
    cfg["entry_reserve_timeout_seconds"] = int(seconds)
    prof.pipeline_config_json = cfg
    await db.flush()
    await AuditService().log(
        db, actor=actor, action="UPDATE",
        object_type="StrategyProfile", object_id=strategy_id,
        old_value={"entry_reserve_timeout_seconds": old},
        new_value={"entry_reserve_timeout_seconds": int(seconds)},
        reason=reason,
    )
    return old


def pctl(vals: list[float], p: float):
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * p
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return s[f]
    return s[f] + (s[c] - s[f]) * (k - f)


async def study_strategy(db, mapper, strat, since, window_min):
    decs = (await db.execute(
        select(StrategyDecision, NormalizedSignal)
        .join(NormalizedSignal,
              StrategyDecision.normalized_signal_id == NormalizedSignal.id)
        .where(StrategyDecision.strategy_id == strat,
               StrategyDecision.outcome == "APPROVE",
               StrategyDecision.created_at >= since)
        .order_by(StrategyDecision.created_at.desc())
    )).all()

    entries = 0
    legs_total = 0
    touch_min = []                       # minutos al primer toque (piernas que tocaron)
    by_level = defaultdict(list)         # level_atr -> [minutos]
    never = 0

    for dec, sig in decs:
        if sig.action == "exit":
            continue
        ts = sig.signal_ts or dec.created_at
        dels = (await db.execute(
            select(WebhookDelivery)
            .where(WebhookDelivery.decision_id == dec.id)
        )).scalars().all()
        base = [d for d in dels if (d.destination or "") == "traderspost"]
        legs = [(d.payload_json or {}) for d in base]
        legs = [p for p in legs if p.get("limitPrice") is not None]
        if not legs:
            continue
        entries += 1

        # barras en la ventana [ts, ts+window]
        end = ts + timedelta(minutes=window_min)
        data_sym = await mapper.resolve_market_data_symbol(db, sig.ticker_received)
        bars = []
        for cand in [data_sym, sig.mapped_symbol, sig.ticker_received]:
            if not cand:
                continue
            rows = (await db.execute(
                select(OhlcvBar.bar_time, OhlcvBar.high, OhlcvBar.low)
                .where(OhlcvBar.symbol == cand,
                       OhlcvBar.bar_time >= ts, OhlcvBar.bar_time <= end)
                .order_by(OhlcvBar.bar_time)
            )).all()
            rows = [(b[0], float(b[1]), float(b[2]))
                    for b in rows if b[1] is not None and b[2] is not None]
            if rows:
                bars = rows
                break

        for p in legs:
            legs_total += 1
            lp = float(p["limitPrice"])
            side = p.get("action")
            lvl = (p.get("extras") or {}).get("level_atr")
            first = None
            for bt, bh, bl in bars:
                hit = (bl <= lp) if side == "buy" else (bh >= lp)
                if hit:
                    first = bt
                    break
            if first is None:
                never += 1
            else:
                mins = (first - ts).total_seconds() / 60.0
                touch_min.append(mins)
                if lvl is not None:
                    by_level[float(lvl)].append(mins)

    return {
        "strat": strat, "entries": entries, "legs": legs_total,
        "touched": len(touch_min), "never": never,
        "touch_min": touch_min, "by_level": by_level,
    }


def print_report(r, window_min):
    strat = r["strat"]
    if r["legs"] == 0:
        print(f"\n== {strat}: sin piernas límite en el rango")
        return
    trate = 100 * r["touched"] / r["legs"] if r["legs"] else 0
    med = pctl(r["touch_min"], 0.5)
    p75 = pctl(r["touch_min"], 0.75)
    p90 = pctl(r["touch_min"], 0.90)
    mx = max(r["touch_min"]) if r["touch_min"] else None
    print(f"\n== {strat}")
    print(f"   entradas={r['entries']} piernas={r['legs']} "
          f"tocaron={r['touched']} ({trate:.0f}%) nunca(≤{window_min}m)={r['never']}")
    if r["touched"]:
        print(f"   tiempo al toque (min): mediana={med:.0f} p75={p75:.0f} "
              f"p90={p90:.0f} max={mx:.0f}")
        rec = suggest_cancel_after(r["touch_min"])
        print(f"   → sugerencia Cancel entry after ≈ {rec} s "
              f"(p90 {p90:.0f}m + colchón, tope 3600) "
              f"[= entry_reserve_timeout_seconds, NX-17/NX-28]")
        for lvl in sorted(r["by_level"]):
            vals = r["by_level"][lvl]
            print(f"      nivel {lvl}×ATR: n={len(vals)} "
                  f"mediana={pctl(vals,0.5):.0f}m p90={pctl(vals,0.9):.0f}m")
    else:
        print("   (ninguna pierna tocó dentro de la ventana — datos insuficientes "
              "o niveles muy profundos)")


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--strategy")
    g.add_argument("--all", action="store_true")
    ap.add_argument("--lookback-days", type=int, default=30)
    ap.add_argument("--window-min", type=int, default=180,
                    help="ventana máx tras la señal para buscar el toque (min)")
    ap.add_argument("--apply", action="store_true",
                    help="NX-17: escribir la sugerencia como "
                         "entry_reserve_timeout_seconds (dry-run sin esto)")
    args = ap.parse_args()
    since = datetime.now(timezone.utc) - timedelta(days=args.lookback_days)

    async with AsyncSessionLocal() as db:
        mapper = SymbolMapper()
        if args.all:
            strbs = (await db.execute(
                select(StrategyDecision.strategy_id)
                .where(StrategyDecision.outcome == "APPROVE",
                       StrategyDecision.created_at >= since)
                .group_by(StrategyDecision.strategy_id)
            )).scalars().all()
            strats = sorted(set(strbs))
        else:
            strats = [args.strategy]

        print(f"=== Estudio de pullback (lookback {args.lookback_days}d, "
              f"ventana {args.window_min}m) — {len(strats)} estrategia(s) "
              f"[{'APPLY' if args.apply else 'DRY-RUN'}] ===")
        applied: dict = {}
        for s in strats:
            r = await study_strategy(db, mapper, s, since, args.window_min)
            print_report(r, args.window_min)
            rec = suggest_cancel_after(r["touch_min"])
            if args.apply and rec is not None:
                old = await apply_suggestion(db, s, rec)
                applied[s] = {"old": old, "new": rec}
                print(f"   ✅ entry_reserve_timeout_seconds: {old} → {rec}")
        if args.apply and applied:
            import json
            from pathlib import Path
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            Path("REPORTES").mkdir(exist_ok=True)
            bp = Path("REPORTES") / f"cancel_after_backup_{ts}.json"
            bp.write_text(json.dumps(applied, indent=2), encoding="utf-8")
            await db.commit()
            print(f"\n🗄️  {bp}")
            print("⚠  RECUERDA: fijar el MISMO valor a mano en TradersPost "
                  "(Strategy → Settings → 'Cancel entry after') por estrategia "
                  "— una sola caducidad para pierna, reserva y orden.")
        elif args.apply:
            print("\n(nada que aplicar: sin sugerencias)")


if __name__ == "__main__":
    asyncio.run(main())
