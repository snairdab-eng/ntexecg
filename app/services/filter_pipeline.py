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
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy
from app.services.hmm_service import HMMService
from app.services.market_data_service import MarketDataService
from app.services.quality_scorer import (
    DEFAULT_HIGH_THRESHOLD,
    QualityScorer,
    filters_active as _quality_measured,
    quality_label,
)
from app.services.session_validator import SessionValidator
from app.services.sl_tp_calculator import SLTPCalculator
from app.services.symbol_mapper import SymbolMapper

# Outcomes that terminate the pipeline at Level 1 without being a "BLOCK"
_CONTINUE = "CONTINUE"


def _normalize_tf(value: object) -> str | None:
    """Normalize a timeframe to a comparable canonical string (minutes).

    "5" -> "5", "5m" -> "5", "15M" -> "15", "1h" -> "60", "4h" -> "240".
    Used by the per-strategy interval guardrail (Anexo 08 #2) so that
    "5" from LuxAlgo and "5m" declared on the strategy compare equal.
    Returns None if the value is empty/unparseable.
    """
    if value is None:
        return None
    s = str(value).strip().lower()
    if not s:
        return None
    if s.endswith("m") and s[:-1].isdigit():
        return s[:-1]
    if s.endswith("h") and s[:-1].isdigit():
        return str(int(s[:-1]) * 60)
    if s.endswith("d") and s[:-1].isdigit():
        return str(int(s[:-1]) * 1440)
    if s.isdigit():
        return s
    return s


@dataclass
class PipelineResult:
    outcome: str  # APPROVE, BLOCK, IGNORE_DUPLICATE, QUEUE_FOR_REVIEW
    block_reason: str | None = None
    block_level: int | None = None  # 1-5, None if APPROVE
    # NX-04: el score ya NO parte en 100 — solo existe cuando el Nivel 4 corrió
    # (entradas). Salidas y blocks tempranos → None (calidad no medida).
    score: int | None = None
    sl_price: float | None = None
    tp_price: float | None = None
    atr_value: float | None = None
    market_data_provider: str | None = None
    # NX-04 (Anexo 25 §1-bis): etiqueta de calidad + si hubo medición real.
    quality: str | None = None  # UNKNOWN / LOW / MEDIUM / HIGH (None si N4 no corrió)
    filters_active: bool = False
    pipeline_execution_json: dict = field(default_factory=dict)


class FilterPipeline:
    """5-level fail-fast filter evaluation."""

    def __init__(self, market_data: MarketDataService) -> None:
        self.market_data = market_data
        self._session_validator = SessionValidator()
        self._quality_scorer = QualityScorer()
        self._sl_tp_calc = SLTPCalculator()
        self._symbol_mapper = SymbolMapper()
        self._regime = HMMService(market_data)

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
        stale = self._check_staleness(signal, config, is_exit)
        execution["staleness"] = stale
        if stale["failed"]:
            return PipelineResult(
                outcome="BLOCK",
                block_reason=stale["reason"],
                block_level=2,
                pipeline_execution_json=execution,
            )

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
        # NX-04: score/quality solo existen si este nivel corre. Sin filtros
        # reales activos la calidad es UNKNOWN (nunca HIGH) aunque el score
        # passthrough sea 100. El gate numérico no cambia.
        # ─────────────────────────────────────────────────────────────────
        score: int | None = None
        quality: str | None = None
        measured = False
        if is_exit:
            execution["level_4"] = {"skipped": True, "reason": "exit_signal"}
        else:
            # Fase 6 — market-regime gate (opt-in). Regime is a higher-level
            # state read on a slower timeframe (default 1h), independent of the
            # entry timeframe. Blocks only when the regime is KNOWN and not in
            # allowed_regimes; "unknown" (insufficient data) fails open.
            regime_cfg = config.get("regime") or {}
            if regime_cfg.get("enabled"):
                rtf = regime_cfg.get("timeframe") or "1h"
                regime = await self._regime.get_regime(data_symbol, rtf)
                allowed = regime_cfg.get("allowed_regimes") or []
                execution["regime"] = {
                    "regime": regime, "timeframe": rtf, "allowed": allowed,
                }
                # NX-26 (P2-12): enabled sin regímenes permitidos = no-op — el
                # gate corre y nunca bloquea. Se deja rastro para el operador.
                if not allowed:
                    execution["regime"]["warning"] = "no_allowed_regimes"
                if allowed and regime != "unknown" and regime not in allowed:
                    return PipelineResult(
                        outcome="BLOCK",
                        block_reason="regime_not_allowed",
                        block_level=4,
                        pipeline_execution_json=execution,
                    )

            # Quality bars come from the resolved data symbol too — a micro reuses
            # its parent's bridge bars (MES → ES), consistent with get_atr (L5).
            bars = await self.market_data.get_bars(
                data_symbol, signal.timeframe or "5m", limit=100
            )
            score = await self._quality_scorer.score(signal, bars, config)
            score_minimum = config.get("score_minimum", 70)
            passed = score >= score_minimum

            # NX-04 — medición real = filtros de score activos O gate de régimen.
            measured = _quality_measured(config)
            high_thr = config.get("quality_high_threshold")
            if not (isinstance(high_thr, (int, float)) and 1 <= high_thr <= 100):
                high_thr = DEFAULT_HIGH_THRESHOLD
            quality = quality_label(score, measured, score_minimum, int(high_thr))
            execution["level_4"] = {
                "score": score, "passed": passed,
                "filters_active": measured, "quality": quality,
            }
            if not passed:
                return PipelineResult(
                    outcome="BLOCK",
                    block_reason="score_below_minimum",
                    block_level=4,
                    score=score,
                    quality=quality,
                    filters_active=measured,
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
            # NX-14: el timeframe del ATR es el calibrado (atr_timeframe) si la
            # estrategia/activo lo define; si no, el timeframe de la señal.
            atr_tf = config.get("atr_timeframe") or signal.timeframe or "5m"
            atr_value = await self.market_data.get_atr(
                data_symbol, atr_tf,
                period=config.get("atr_period", 14),
            )
            # NX-05: pasar None (no 0.0) si falta el precio — el calculador
            # bloquea con entry_price_missing en vez de fabricar un SL absurdo.
            calc = await self._sl_tp_calc.calculate(
                signal, atr_value,
                float(signal.price) if signal.price is not None else None,
                config,
            )
            execution["level_5"] = {
                "atr": atr_value,
                "atr_timeframe": atr_tf,
                "passed": calc["passed"],
                "reason": calc["reason"],
                "sl_price": calc["sl_price"],
                "tp_price": calc["tp_price"],
                # MR-5a: "backstop_fixed" (stop de precio fijo desde la
                # señal) o "atr" (k×ATR de siempre) — visible en el audit.
                "sl_mode": calc.get("sl_mode"),
            }
            if not calc["passed"]:
                return PipelineResult(
                    outcome="BLOCK",
                    block_reason=calc["reason"],
                    block_level=5,
                    score=score,
                    quality=quality,
                    filters_active=measured,
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
            quality=quality,
            filters_active=measured,
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
        # Reads "global_mode" (GlobalProfile.mode), NOT "mode": the StrategyProfile
        # merge overwrites "mode" with the strategy's maturity mode (paper/micro/...),
        # which silently disabled this brake for every strategy (NX-01).
        mode = config.get("global_mode", "normal")
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

        # 1.4 Symbol mapping - applies to entries and exits
        if signal.mapped_symbol is None:
            return {"outcome": "BLOCK", "reason": "symbol_not_mapped",
                    "check": "1.4_symbol_map"}

        # 1.7 Per-strategy symbol guardrail (Anexo 08) - opt-in.
        if config.get("enforce_symbol_match") and config.get("expected_symbol"):
            if signal.ticker_received != config["expected_symbol"]:
                return {"outcome": "BLOCK", "reason": "symbol_mismatch",
                        "check": "1.7_symbol_expected",
                        "expected": config["expected_symbol"],
                        "received": signal.ticker_received}

        # 1.8 Per-strategy timeframe guardrail (Anexo 08) - opt-in.
        if config.get("enforce_timeframe_match") and config.get("expected_timeframe"):
            if _normalize_tf(signal.timeframe) != _normalize_tf(
                config["expected_timeframe"]
            ):
                return {"outcome": "BLOCK", "reason": "interval_mismatch",
                        "check": "1.8_interval_expected",
                        "expected": config["expected_timeframe"],
                        "received": signal.timeframe}

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

    def _check_staleness(
        self, signal: NormalizedSignal, config: dict, is_exit: bool
    ) -> dict:
        """Reject signals older than the configured threshold (Anexo 08)."""
        max_age = (
            config.get("signal_max_age_exit_seconds")
            if is_exit
            else config.get("signal_max_age_entry_seconds")
        )
        if not max_age or signal.signal_ts is None:
            return {"failed": False, "skipped": True}
        now = config.get("now") or datetime.now(timezone.utc)
        ts = signal.signal_ts
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
        if age > float(max_age):
            return {"failed": True, "reason": "signal_stale",
                    "check": "2.0_staleness",
                    "age_seconds": age, "max_age": float(max_age)}
        return {"failed": False, "age_seconds": age}

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

        # 3.4 symbol_busy (NX-09) — una posición por símbolo/cuenta: con el
        # símbolo ocupado (abierta o en tránsito) se bloquea CUALQUIER entrada,
        # de la misma estrategia (re-entrada/piramidación) o de otra (caso dos
        # ES sobre MES). Opt-out por estrategia: allow_stacking. Los reversals
        # están exentos: su cierre lo despachó este mismo flujo justo antes.
        # Las salidas nunca llegan aquí (L3 se salta para exits).
        busy_states = ("PENDING_LONG", "PENDING_SHORT", "LONG", "SHORT",
                       "EXITING")
        if (
            position.state in busy_states
            and not config.get("allow_stacking")
            and signal.signal_role not in ("reversal_to_long",
                                           "reversal_to_short")
        ):
            return {"failed": True, "reason": "symbol_busy",
                    "check": "3.4_symbol_busy",
                    "state": position.state,
                    "holder_strategy": position.strategy_id}

        # 3.1 daily_loss_stop / 3.2 max_positions — Phase 1 stubs
        return {"failed": False}
