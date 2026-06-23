"""Weekly results import: parsing, pnl calc, reconciliation, real metrics (Fase 8)."""
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.execution_result import ExecutionResult
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services.results_import import (
    _compute_pnl,
    _norm_dir,
    _parse_dt,
    _row_hash,
    compute_real_metrics,
    effective_pnl,
    import_results,
    parse_rows,
)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_compute_pnl_mes_math() -> None:
    # MES: tick_size 0.25, tick_value 1.25 → $5/point.
    assert _compute_pnl("long", 5601.25, 5610.50, 1, 0.25, 1.25) == 46.25
    assert _compute_pnl("short", 5618.0, 5624.75, 1, 0.25, 1.25) == -33.75
    assert _compute_pnl("long", 100, 101, 2, 0.25, 1.25) == 10.0
    assert _compute_pnl("long", None, 5610.0, 1, 0.25, 1.25) is None  # missing price
    assert _compute_pnl("long", 100, 101, 1, None, None) is None      # no tick info


def test_norm_dir_and_parse_dt() -> None:
    assert _norm_dir("long") == "long" and _norm_dir("buy") == "long"
    assert _norm_dir("short") == "short" and _norm_dir("SELL") == "short"
    assert _norm_dir("") is None and _norm_dir("x") is None
    assert _parse_dt("2026-06-22 09:35:00") == datetime(2026, 6, 22, 9, 35, 0)
    assert _parse_dt("2026-06-22T13:05:00") == datetime(2026, 6, 22, 13, 5, 0)
    assert _parse_dt("") is None


def test_row_hash_stable_and_distinct() -> None:
    a = {"signal_id": "S", "symbol": "MESU2026", "direction": "long",
         "entry_time": "2026-06-22 09:35:00", "entry_price": "5601.25",
         "quantity": "1", "exit_time": "2026-06-22 10:10:00", "exit_price": "5610.5"}
    b = dict(a)
    assert _row_hash(a) == _row_hash(b)
    b["exit_price"] = "5611.0"
    assert _row_hash(a) != _row_hash(b)


def test_parse_rows() -> None:
    text = ("signal_id,strategy_id,symbol,direction,quantity,entry_time,"
            "entry_price,exit_time,exit_price,pnl,exit_reason,fees\n"
            ",S,MESU2026,long,1,2026-06-22 09:35:00,5601.25,"
            "2026-06-22 10:10:00,5610.5,46.25,target,1.24\n")
    rows = parse_rows(text)
    assert len(rows) == 1 and rows[0]["symbol"] == "MESU2026"


# ---------------------------------------------------------------------------
# Import + pnl_calc + idempotency
# ---------------------------------------------------------------------------

async def _mes_symbol_map(db: AsyncSession) -> None:
    db.add(SymbolMap(
        tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
        contract_type="futures_micro", pine_script_config='{"ticker":"MES"}',
        tick_value=1.25, tick_size=0.25, active=True,
    ))
    await db.flush()


@pytest.mark.asyncio
async def test_import_computes_pnl_and_is_idempotent(db: AsyncSession) -> None:
    await _mes_symbol_map(db)
    rows = [
        {"signal_id": "", "strategy_id": "S", "symbol": "MESU2026", "direction": "long",
         "quantity": "1", "entry_time": "2026-06-22 09:35:00", "entry_price": "5601.25",
         "exit_time": "2026-06-22 10:10:00", "exit_price": "5610.50", "pnl": "",
         "exit_reason": "target", "fees": ""},
    ]
    summary = await import_results(db, rows)
    await db.commit()
    assert summary["imported"] == 1
    er = (await db.execute(select(ExecutionResult))).scalars().one()
    assert float(er.pnl_calc) == 46.25  # computed from prices × tick value
    assert er.pnl is None

    # Re-import the same row → skipped, no duplicate.
    summary2 = await import_results(db, rows)
    await db.commit()
    assert summary2["imported"] == 0 and summary2["skipped_existing"] == 1
    assert len((await db.execute(select(ExecutionResult))).scalars().all()) == 1


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _sent_delivery(signal_id, ticker, action, sent_at) -> WebhookDelivery:
    extras = {"strategy_id": "S"}
    if signal_id:
        extras["signal_id"] = signal_id
    return WebhookDelivery(
        decision_id=uuid.uuid4(), strategy_id="S", destination="traderspost",
        payload_json={"ticker": ticker, "action": action, "extras": extras},
        status="SENT", sent_at=sent_at,
    )


@pytest.mark.asyncio
async def test_reconcile_signal_id_and_heuristic(db: AsyncSession) -> None:
    db.add(_sent_delivery("SIG1", "MESU2026", "buy", datetime(2026, 6, 22, 9, 0, 0)))
    db.add(_sent_delivery(None, "MESU2026", "sell", datetime(2026, 6, 22, 13, 4, 0)))
    await db.flush()

    rows = [
        # exact match by signal_id
        {"signal_id": "SIG1", "strategy_id": "S", "symbol": "MESU2026", "direction": "long",
         "quantity": "1", "entry_time": "2026-06-22 09:01:00", "entry_price": "5600",
         "exit_time": "2026-06-22 09:30:00", "exit_price": "5605", "pnl": "25"},
        # heuristic: same base symbol + short + within 15 min of the SENT sell
        {"signal_id": "", "strategy_id": "S", "symbol": "MESU2026", "direction": "short",
         "quantity": "1", "entry_time": "2026-06-22 13:05:00", "entry_price": "5618",
         "exit_time": "2026-06-22 13:40:00", "exit_price": "5624.75", "pnl": "-33.75"},
    ]
    summary = await import_results(db, rows)
    await db.commit()
    assert summary["matched_signal_id"] == 1
    assert summary["matched_heuristic"] == 1

    longs = (await db.execute(select(ExecutionResult).where(
        ExecutionResult.direction == "long"))).scalars().one()
    shorts = (await db.execute(select(ExecutionResult).where(
        ExecutionResult.direction == "short"))).scalars().one()
    assert longs.match_method == "signal_id" and longs.matched_decision_id is not None
    assert shorts.match_method == "heuristic" and shorts.matched_decision_id is not None


@pytest.mark.asyncio
async def test_reconcile_unmatched_when_out_of_window(db: AsyncSession) -> None:
    db.add(_sent_delivery(None, "MESU2026", "buy", datetime(2026, 6, 22, 9, 0, 0)))
    await db.flush()
    rows = [
        {"signal_id": "", "strategy_id": "S", "symbol": "MESU2026", "direction": "long",
         "quantity": "1", "entry_time": "2026-06-22 11:30:00", "entry_price": "5600",
         "exit_time": "2026-06-22 12:00:00", "exit_price": "5605", "pnl": "25"},
    ]
    summary = await import_results(db, rows)
    await db.commit()
    assert summary["unmatched"] == 1


# ---------------------------------------------------------------------------
# Real metrics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compute_real_metrics(db: AsyncSession) -> None:
    def _row(pnl, etime):
        return {"signal_id": "", "strategy_id": "S", "symbol": "MESU2026",
                "direction": "long", "quantity": "1",
                "entry_time": etime, "entry_price": "5600",
                "exit_time": etime, "exit_price": "5605", "pnl": str(pnl)}

    rows = [
        _row(100, "2026-06-22 09:00:00"),
        _row(50, "2026-06-22 10:00:00"),
        _row(-40, "2026-06-22 11:00:00"),
    ]
    await import_results(db, rows)
    await db.commit()

    metrics = await compute_real_metrics(db, strategy_id="S")
    m = metrics["S"]
    assert m["trades"] == 3 and m["with_pnl"] == 3
    assert m["wins"] == 2 and m["losses"] == 1
    assert m["win_rate"] == round(2 / 3 * 100, 1)
    assert m["total_pnl"] == 110.0
    assert m["gross_profit"] == 150.0 and m["gross_loss"] == 40.0
    assert m["profit_factor"] == round(150 / 40, 2)
    assert m["expectancy"] == round(110 / 3, 2)
    assert m["max_drawdown"] == -40.0
