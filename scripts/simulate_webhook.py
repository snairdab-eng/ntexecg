"""Simulate a LuxAlgo webhook from the terminal (NTDEV testing).

The webhook endpoint is fire-and-forget: it returns 200 + signal_id immediately
and processes the signal in a background task. This script POSTs the webhook,
then polls the DB for the resulting StrategyDecision so it can show the outcome
and the calculated SL.

Usage:
    python scripts/simulate_webhook.py \
        --strategy-id mes5m_confirmation_normal \
        --action sell --ticker MES --sentiment short \
        --price 5500.00 --interval 5 --token dev_global_token

    python scripts/simulate_webhook.py --strategy-id mes5m --exit \
        --ticker MES --token dev_global_token

    python scripts/simulate_webhook.py --strategy-id mes5m --ticker MES --dry
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx  # noqa: E402


def build_payload(args: argparse.Namespace) -> dict:
    """Build the LuxAlgo-style JSON payload."""
    if args.exit:
        # Exit signal: sentiment=flat → action=exit downstream. Per spec the
        # raw payload sends action=sell + sentiment=flat (LuxAlgo Builtin-Exit).
        sentiment = "flat"
        action = "sell"
    else:
        sentiment = args.sentiment
        action = args.action

    return {
        "ticker": args.ticker,           # exactly as typed — never transformed
        "action": action,
        "sentiment": sentiment,
        "quantity": str(args.quantity),
        "price": f"{args.price:.2f}" if args.price is not None else "0",
        "time": datetime.now(timezone.utc).isoformat(),
        "interval": str(args.interval),
    }


async def _poll_decision(raw_signal_id: str, timeout_s: float = 6.0) -> dict | None:
    """Poll the DB for the StrategyDecision produced by the background task."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.models.decision import StrategyDecision
    from app.models.normalized_signal import NormalizedSignal

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    deadline = time.monotonic() + timeout_s
    try:
        while time.monotonic() < deadline:
            async with factory() as db:
                result = await db.execute(
                    select(StrategyDecision, NormalizedSignal)
                    .join(
                        NormalizedSignal,
                        StrategyDecision.normalized_signal_id == NormalizedSignal.id,
                    )
                    .where(NormalizedSignal.raw_signal_id == raw_signal_id)
                    .order_by(StrategyDecision.created_at.desc())
                    .limit(1)
                )
                row = result.first()
                if row is not None:
                    d, _ = row
                    return {
                        "outcome": d.outcome,
                        "block_reason": d.block_reason,
                        "block_level": d.block_level,
                        "score": d.score,
                        "sl_price": float(d.sl_price) if d.sl_price is not None else None,
                        "atr_value": float(d.atr_value) if d.atr_value is not None else None,
                    }
            await asyncio.sleep(0.4)
    finally:
        await engine.dispose()
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulate a LuxAlgo webhook.")
    parser.add_argument("--strategy-id", required=True)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--action", default="buy", choices=["buy", "sell"])
    parser.add_argument("--sentiment", default="long", choices=["long", "short", "flat"])
    parser.add_argument("--price", type=float, default=5500.00)
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--interval", default="5")
    parser.add_argument("--token", default="dev_global_token")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL")
    parser.add_argument("--exit", action="store_true", help="Send a flat/exit signal")
    parser.add_argument("--dry", action="store_true", help="Print payload, do not send")
    args = parser.parse_args()

    payload = build_payload(args)

    print("Payload:")
    print(json.dumps(payload, indent=2))

    if args.dry:
        print("\n[--dry] Not sent.")
        return 0

    webhook_url = (
        f"{args.url.rstrip('/')}/webhooks/luxalgo/{args.strategy_id}"
        f"?token={args.token}"
    )
    print(f"\nPOST {args.url.rstrip('/')}/webhooks/luxalgo/{args.strategy_id}?token=***")

    try:
        resp = httpx.post(webhook_url, json=payload, timeout=10.0)
    except httpx.HTTPError as exc:
        print(f"\n❌ Request failed: {exc}")
        return 1

    print(f"HTTP {resp.status_code}")
    if resp.status_code == 401:
        print("❌ Invalid token (401).")
        return 1
    if resp.status_code != 200:
        print(f"❌ Unexpected response: {resp.text[:300]}")
        return 1

    body = resp.json()
    signal_id = body.get("signal_id")
    print(f"✅ Received. signal_id={signal_id}")

    # Poll the DB for the async decision
    print("\nEsperando decisión (procesamiento en background)…")
    decision = asyncio.run(_poll_decision(signal_id))
    if decision is None:
        print("⏳ Decisión aún no disponible. Ver en:",
              f"{args.url.rstrip('/')}/ui/signals")
        return 0

    outcome = decision["outcome"]
    icon = {"APPROVE": "✅", "BLOCK": "❌"}.get(outcome, "•")
    print(f"\nDECISIÓN: {icon} {outcome}")
    if outcome == "APPROVE":
        print(f"  Score:    {decision['score']}")
        print(f"  ATR:      {decision['atr_value']}")
        print(f"  SL price: {decision['sl_price']}")
    elif outcome == "BLOCK":
        print(f"  Razón:    {decision['block_reason']} (Nivel {decision['block_level']})")
    else:
        print(f"  {decision}")
    print(f"\nDetalle: {args.url.rstrip('/')}/ui/signals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
