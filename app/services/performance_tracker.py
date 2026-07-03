"""PerformanceTracker — accumulates real metrics per strategy.

Updated after every StrategyDecision. Upserts a StrategyPerformance row:
  - total_signals_received (always +1)
  - total_approved / total_blocked + blocks_level_{N}
  - top_block_reasons_json (top 10 by frequency)
  - filter_pass_rate = total_approved / total_signals_received
  - avg_score (running average over received signals)
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.decision import StrategyDecision
from app.models.strategy_performance import StrategyPerformance


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PerformanceTracker:
    async def update(
        self, db: AsyncSession, strategy_id: str, decision: StrategyDecision
    ) -> StrategyPerformance:
        """Upsert StrategyPerformance for strategy_id given a decision."""
        perf = await self._get_or_create(db, strategy_id)

        # total received (running count BEFORE this signal is added)
        prev_total = perf.total_signals_received
        perf.total_signals_received = prev_total + 1

        if perf.first_signal_at is None:
            perf.first_signal_at = _utcnow()
        perf.last_signal_at = _utcnow()

        outcome = decision.outcome
        if outcome == "APPROVE":
            perf.total_approved += 1
        elif outcome == "BLOCK":
            perf.total_blocked += 1
            self._increment_block_level(perf, decision.block_level)
            self._track_block_reason(perf, decision.block_reason)

        # filter_pass_rate = approved / received
        if perf.total_signals_received > 0:
            perf.filter_pass_rate = round(
                perf.total_approved / perf.total_signals_received, 4
            )

        # NX-26 — avg_score promedia SOLO señales con score medido (el N4
        # corrió). Salidas y blocks tempranos (score None) no diluyen la media.
        if decision.score is not None:
            prev_scored = perf.scored_signals or 0
            old_avg = float(perf.avg_score) if perf.avg_score is not None else 0.0
            perf.avg_score = round(
                (old_avg * prev_scored + decision.score) / (prev_scored + 1), 2
            )
            perf.scored_signals = prev_scored + 1

        perf.updated_at = _utcnow()
        await db.flush()
        return perf

    @staticmethod
    def _increment_block_level(perf: StrategyPerformance, level: int | None) -> None:
        mapping = {
            1: "blocks_level_1", 2: "blocks_level_2", 3: "blocks_level_3",
            4: "blocks_level_4", 5: "blocks_level_5",
        }
        attr = mapping.get(level)
        if attr:
            setattr(perf, attr, getattr(perf, attr) + 1)

    @staticmethod
    def _track_block_reason(perf: StrategyPerformance, reason: str | None) -> None:
        if not reason:
            return
        reasons = dict(perf.top_block_reasons_json or {})
        reasons[reason] = reasons.get(reason, 0) + 1
        # Keep only the top 10 by frequency
        top = dict(sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)[:10])
        perf.top_block_reasons_json = top

    @staticmethod
    async def _get_or_create(
        db: AsyncSession, strategy_id: str
    ) -> StrategyPerformance:
        result = await db.execute(
            select(StrategyPerformance).where(
                StrategyPerformance.strategy_id == strategy_id
            )
        )
        perf = result.scalar_one_or_none()
        if perf is None:
            perf = StrategyPerformance(strategy_id=strategy_id)
            db.add(perf)
            await db.flush()
        return perf
