"""PerformanceTracker tests — accumulated per-strategy metrics."""
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import StrategyDecision
from app.services.performance_tracker import PerformanceTracker

_STRAT = "mes_strat"


def _decision(outcome="APPROVE", level=None, reason=None, score=100) -> StrategyDecision:
    return StrategyDecision(
        normalized_signal_id=uuid.uuid4(),
        strategy_id=_STRAT,
        outcome=outcome,
        block_level=level,
        block_reason=reason,
        score=score,
    )


@pytest.mark.asyncio
async def test_first_update_creates_row(db: AsyncSession) -> None:
    perf = await PerformanceTracker().update(db, _STRAT, _decision())
    assert perf.total_signals_received == 1
    assert perf.total_approved == 1
    assert perf.first_signal_at is not None


@pytest.mark.asyncio
async def test_approve_increments_approved(db: AsyncSession) -> None:
    tracker = PerformanceTracker()
    await tracker.update(db, _STRAT, _decision(outcome="APPROVE"))
    perf = await tracker.update(db, _STRAT, _decision(outcome="APPROVE"))
    assert perf.total_signals_received == 2
    assert perf.total_approved == 2
    assert perf.total_blocked == 0


@pytest.mark.asyncio
async def test_block_increments_level_counter(db: AsyncSession) -> None:
    tracker = PerformanceTracker()
    perf = await tracker.update(
        db, _STRAT, _decision(outcome="BLOCK", level=2, reason="outside_session_hours")
    )
    assert perf.total_blocked == 1
    assert perf.blocks_level_2 == 1
    assert perf.blocks_level_1 == 0


@pytest.mark.asyncio
async def test_filter_pass_rate(db: AsyncSession) -> None:
    tracker = PerformanceTracker()
    await tracker.update(db, _STRAT, _decision(outcome="APPROVE"))
    await tracker.update(db, _STRAT, _decision(outcome="APPROVE"))
    await tracker.update(db, _STRAT, _decision(outcome="BLOCK", level=1, reason="x"))
    perf = await tracker.update(db, _STRAT, _decision(outcome="BLOCK", level=1, reason="x"))
    # 2 approved / 4 received = 0.5
    assert float(perf.filter_pass_rate) == 0.5


@pytest.mark.asyncio
async def test_top_block_reasons_tracked(db: AsyncSession) -> None:
    tracker = PerformanceTracker()
    await tracker.update(db, _STRAT, _decision(outcome="BLOCK", level=2, reason="reason_a"))
    await tracker.update(db, _STRAT, _decision(outcome="BLOCK", level=2, reason="reason_a"))
    perf = await tracker.update(
        db, _STRAT, _decision(outcome="BLOCK", level=3, reason="reason_b")
    )
    assert perf.top_block_reasons_json["reason_a"] == 2
    assert perf.top_block_reasons_json["reason_b"] == 1


@pytest.mark.asyncio
async def test_avg_score_running_average(db: AsyncSession) -> None:
    tracker = PerformanceTracker()
    await tracker.update(db, _STRAT, _decision(score=100))
    perf = await tracker.update(db, _STRAT, _decision(score=80))
    # (100 + 80) / 2 = 90
    assert float(perf.avg_score) == 90.0


@pytest.mark.asyncio
async def test_block_reasons_capped_at_10(db: AsyncSession) -> None:
    tracker = PerformanceTracker()
    for i in range(15):
        await tracker.update(
            db, _STRAT, _decision(outcome="BLOCK", level=1, reason=f"reason_{i}")
        )
    perf = await tracker.update(
        db, _STRAT, _decision(outcome="BLOCK", level=1, reason="reason_final")
    )
    assert len(perf.top_block_reasons_json) <= 10
