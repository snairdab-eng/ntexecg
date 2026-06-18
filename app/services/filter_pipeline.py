"""FilterPipeline — 5-level fail-fast evaluation of signals.

Architecture (doc 00 §8):
  Level 1 — System validation (binary)
  Level 2 — Temporal context (binary)
  Level 3 — Risk management (binary, exits exempt)
  Level 4 — Quality score (entries only)
  Level 5 — SL/TP calculation (entries only, after APPROVE)

Fail-fast: stop at first failing level. pipeline_execution_json records
every evaluated level for the audit trail.

Exit handling (contract rules 7, 11, 25):
  - Exits bypass levels 3, 4, 5 (no risk/score/SL needed to close).
  - Exits permitted under global paused/flatten_only mode.
  - Exits permitted when market data (NinjaTrader bridge) is inactive.
  - Exits outside session window honored if allow_exits_outside_window.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.services.market_data_service import MarketDataService
from app.services.quality_scorer import QualityScorer
from app.services.session_validator import SessionValidator
from app.services.sl_tp_calculator import SLTPCalculator
from app.services.symbol_mapper import SymbolMapper

# Outcomes that terminate the pipeline at Level 1 without being a "BLOCK"
_CONTINUE = "CONTINUE"


@dataclass
class PipelineResult:
    outcome: str  # APPROVE, BLOCK, IGNORE_DUPLICATE, QUEUE_FOR_REVIEW
    block_reason: str | None = None
    block_level: int | None = None  # 1-5, None if APPROVE
    score: int = 100
    sl_price: float | None = None
    tp_price: float | None = None
    atr_value: float | None = None
    market_data_provider: str | None = None
    pipeline_execution_json: dict = field(default_factory=dict)


class FilterPipeline:
    """5-level fail-fast filter evaluation."""

    def __init__(self, market_data: MarketDataService) -> None:
        self.market_data = market_data
        self._session_validator = SessionValidator()
        self._quality_scorer = QualityScorer()
        self._sl_tp_calc = SLTPCalculator()
        self._symbol_mapper = SymbolMapper()

    async def evaluate(
        self,
        db: AsyncSession,
        signal: NormalizedSignal,
        strategy: Strategy | None,
        config: dict,
    ) -> PipelineResult:
        """Evaluate signal through 5-level pipeline. Fail-fast on any block."""
        is_exit = signal.action == "exit"
        symbol = signal.mapped_symbol
        execution: dict = {}

        # Market-data alias (Anexo A.9.1; reglas 36, 38): resolve WHICH bridge
        # symbol to read from the ticker_received (micro → parent, e.g. MES → ES).
        # Read-only symbol substitution — used for EVERY bridge market-data read:
        # is_active (1.6), quality bars (4) and get_atr (5). Decisions, position
        # state and the payload keep using the mapped contract symbol (`symbol`).
        # Never transforms prices.
        data_symbol = await self._symbol_mapper.resolve_market_data_symbol(
            db, signal.ticker_received
        )

        # ─────────────────────────────────────────────────────────────────
        # LEVEL 1 — SYSTEM VALIDATION
        # ─────────────────────────────────────────────────────────────────
        l1 = await self._level_1_validation(
            signal, strategy, config, is_exit, data_symbol
        )
        execution["level_1"] = l1
        if l1["outcome"] != _CONTINUE:
            return PipelineResult(
                outcome=l1["outcome"],
                block_reason=l1.get("reason"),
                block_level=1 if l1["outcome"] == "BLOCK" else None,
                pipeline_execution_json=execution,
            )

        # ─────────────────────────────────────────────────────────────────
        # LEVEL 2 — TEMPORAL CONTEXT
        # Single source of truth: config["session_config_json"]
        # (already merged from AssetProfile/StrategyProfile by ConfigResolver)
        # ─────────────────────────────────────────────────────────────────
        l2 = self._level_2_temporal(config)
        execution["level_2"] = l2
        if l2["failed"]:
            if not is_exit:
                # Entry outside window → BLOCK
                return PipelineResult(
                    outcome="BLOCK",
                    block_reason=l2.get("reason"),
                    block_level=2,
                    pipeline_execution_json=execution,
                )
            elif not config.get("allow_exits_outside_window", True):
                # Exit outside window AND exits-outside disabled → BLOCK
                return PipelineResult(
                    outcome="BLOCK",
                    block_reason=l2.get("reason"),
                    block_level=2,
                    pipeline_execution_json=execution,
                )
            # else: exit permitted outside window (default) → continue

        # ─────────────────────────────────────────────────────────────────
        # LEVEL 3 — RISK MANAGEMENT (exits exempt)
        # ─────────────────────────────────────────────────────────────────
        if is_exit:
            execution["level_3"] = {"skipped": True, "reason": "exit_signal"}
        else:
            l3 = await self._level_3_risk(db, signal, config)
            execution["level_3"] = l3
            if l3["failed"]:
                return PipelineResult(
                    outcome="BLOCK",
                    block_reason=l3.get("reason"),
                    block_level=3,
                    pipeline_execution_json=execution,
                )

        # ─────────────────────────────────────────────────────────────────
        # LEVEL 4 — QUALITY SCORE (entries only)
        # ─────────────────────────────────────────────────────────────────
        score = 100
        if is_exit:
            execution["level_4"] = {"skipped": True, "reason": "exit_signal"}
        else:
            # Quality bars come from the resolved data symbol too — a micro reuses
            # its parent's bridge bars (MES → ES), consistent with get_atr (L5).
            bars = await self.market_data.get_bars(
                data_symbol, signal.timeframe or "5m", limit=100
            )
            score = await self._quality_scorer.score(signal, bars, config)
            score_minimum = config.get("score_minimum", 70)
            passed = score >= score_minimum
            execution["level_4"] = {"score": score, "passed": passed}
            if not passed:
                return PipelineResult(
                    outcome="BLOCK",
                    block_reason="score_below_minimum",
                    block_level=4,
                    score=score,
                    pipeline_execution_json=execution,
                )

        # ─────────────────────────────────────────────────────────────────
        # LEVEL 5 — SL/TP CALCULATION (entries only)
        # ─────────────────────────────────────────────────────────────────
        sl_price = tp_price = atr_value = None
        if is_exit:
            execution["level_5"] = {"skipped": True, "reason": "exit_signal"}
        else:
            # Read ATR from the resolved data symbol (micro reuses parent data).
            atr_value = await self.market_data.get_atr(
                data_symbol, signal.timeframe or "5m",
                period=config.get("atr_period", 14),
            )
            calc = await self._sl_tp_calc.calculate(
                signal, atr_value, signal.price or 0.0, config
            )
            execution["level_5"] = {
                "atr": atr_value,
                "passed": calc["passed"],
                "reason": calc["reason"],
                "sl_price": calc["sl_price"],
                "tp_price": calc["tp_price"],
            }
            if not calc["passed"]:
                return PipelineResult(
                    outcome="BLOCK",
                    block_reason=calc["reason"],
                    block_level=5,
                    score=score,
                    pipeline_execution_json=execution,
                )
            sl_price = calc["sl_price"]
            tp_price = calc["tp_price"]

        # ─────────────────────────────────────────────────────────────────
        # APPROVED
        # ─────────────────────────────────────────────────────────────────
        return PipelineResult(
            outcome="APPROVE",
            score=score,
            sl_price=sl_price,
            tp_price=tp_price,
            atr_value=atr_value,
            market_data_provider=type(self.market_data.provider).__name__,
            pipeline_execution_json=execution,
        )

    # ───────────────────────────────────────────────────────────────────────
    # LEVEL 1 — System validation
    # ───────────────────────────────────────────────────────────────────────
    async def _level_1_validation(
        self,
        signal: NormalizedSignal,
        strategy: Strategy | None,
        config: dict,
        is_exit: bool,
        data_symbol: str,
    ) -> dict:
        """Returns dict with 'outcome' = CONTINUE | BLOCK | QUEUE_FOR_REVIEW.

        data_symbol is the resolved bridge symbol (micro → parent) used for the
        1.6 heartbeat check only. Symbol mapping (1.4) still uses mapped_symbol.
        """

        # 1.1 Global mode — exits always pass (contract rule: exits prioritized)
        mode = config.get("mode", "normal")
        if mode in ("paused", "flatten_only") and not is_exit:
            return {"outcome": "BLOCK", "reason": f"global_{mode}",
                    "check": "1.1_global_mode"}

        # 1.2 Strategy status
        if strategy is None or strategy.status == "candidate":
            # Unknown/candidate strategies never execute → queue for operator review
            return {"outcome": "QUEUE_FOR_REVIEW", "reason": "strategy_candidate",
                    "check": "1.2_strategy_status"}

        if strategy.status in ("quarantined", "retired"):
            return {"outcome": "BLOCK", "reason": f"strategy_{strategy.status}",
                    "check": "1.2_strategy_status"}

        if strategy.status == "paused" and not is_exit:
            # paused + entry → BLOCK; paused + exit → continue (exits prioritized)
            return {"outcome": "BLOCK", "reason": "strategy_paused",
                    "check": "1.2_strategy_status"}

        # 1.4 Symbol mapping — applies to entries and exits
        if signal.mapped_symbol is None:
            return {"outcome": "BLOCK", "reason": "symbol_not_mapped",
                    "check": "1.4_symbol_map"}

        # 1.6 Market data active (NinjaTrader bridge heartbeat)
        #     Entry + inactive → BLOCK. Exit → always permitted (contract rule 25).
        #     Checked on the resolved data_symbol (micro reuses parent heartbeat).
        if not is_exit:
            try:
                active = await self.market_data.is_active(data_symbol)
            except Exception as exc:
                logger.error("is_active_check_failed symbol={} error={}",
                             data_symbol, exc)
                active = False
            if not active:
                return {"outcome": "BLOCK", "reason": "market_data_not_active",
                        "check": "1.6_bridge_active"}

        return {"outcome": _CONTINUE}

    # ───────────────────────────────────────────────────────────────────────
    # LEVEL 2 — Temporal context
    # ───────────────────────────────────────────────────────────────────────
    def _level_2_temporal(self, config: dict) -> dict:
        """Day-of-week + session-hours check using config['session_config_json']."""
        session_config = config.get("session_config_json")

        # 2.1 & 2.2 — day of week and session window
        within = self._session_validator.is_within_session_config(session_config)
        if not within:
            return {"failed": True, "reason": "outside_session_hours",
                    "check": "2.2_session_hours"}

        # 2.3 News filter — Phase 1 stub (always passes)
        return {"failed": False}

    # ───────────────────────────────────────────────────────────────────────
    # LEVEL 3 — Risk management (entries only — caller skips for exits)
    # ───────────────────────────────────────────────────────────────────────
    async def _level_3_risk(
        self, db: AsyncSession, signal: NormalizedSignal, config: dict
    ) -> dict:
        """Risk checks. daily_loss_stop and max_positions are Phase 1 stubs.

        3.3 Position state — contract rule 11: "if state uncertain, block entries".
        UNKNOWN or LOCKED state → BLOCK entry.
        """
        from app.services.repositories import get_position_state

        account_id = config.get("account_id", "paper_default")
        position = await get_position_state(
            db, signal.strategy_id, account_id, signal.mapped_symbol
        )

        if position.state == "UNKNOWN":
            return {"failed": True, "reason": "unknown_position_state",
                    "check": "3.3_position_state"}
        if position.state == "LOCKED":
            return {"failed": True, "reason": "position_locked",
                    "check": "3.3_position_state"}

        # 3.1 daily_loss_stop / 3.2 max_positions — Phase 1 stubs
        return {"failed": False}
