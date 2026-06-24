"""PayloadBuilder — constructs the exact payload sent to TradersPost.

Contract (doc 00 §8, REQ-0602):
  - ticker = mapped_symbol ("MESU2025"), NEVER ticker_received ("MES")
  - Entries ALWAYS include stopLoss. No exceptions.
  - Exits NEVER include stopLoss or takeProfit.
  - Entry without sl_price → ValueError (the pipeline must never let this happen).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.models.normalized_signal import NormalizedSignal
from app.models.strategy import Strategy

if TYPE_CHECKING:
    from app.services.filter_pipeline import PipelineResult

# signal_role values that represent an entry (need a stopLoss)
_ENTRY_ROLES = {"entry_long", "entry_short", "reversal_to_long", "reversal_to_short"}
_EXIT_ROLES = {"exit_long", "exit_short"}


class PayloadBuilder:
    """Builds TradersPost-ready payload dicts from a signal + pipeline result."""

    def build(
        self,
        signal: NormalizedSignal,
        strategy: Strategy | None,
        config: dict,
        pipeline_result: "PipelineResult",
    ) -> dict:
        """Return a dict ready for JSON serialization to TradersPost.

        Raises:
            ValueError: if an entry signal has no sl_price (forbidden).
        """
        # Canonical exit detection — consistent with FilterPipeline (action == "exit").
        # signal_role is used as a secondary signal for reversal/entry classification.
        is_exit = signal.action == "exit" or signal.signal_role in _EXIT_ROLES

        payload: dict = {
            "ticker": signal.mapped_symbol,      # mapped contract, not ticker_received
            "action": signal.action,
            "sentiment": signal.sentiment,
            "signalPrice": float(signal.price) if signal.price is not None else None,
            "quantity": signal.quantity,
        }

        if not is_exit:
            # ENTRY — stopLoss is MANDATORY
            if pipeline_result.sl_price is None:
                raise ValueError(
                    "Entry signal without sl_price is forbidden "
                    f"(strategy={signal.strategy_id}, role={signal.signal_role})"
                )
            # TradersPost expects the ABSOLUTE stop under "stopPrice" (NOT "price").
            # Wrong key → 400 invalid-stop-loss-value-required.
            payload["stopLoss"] = {
                "type": "stop",
                "stopPrice": float(pipeline_result.sl_price),
            }
            # takeProfit is optional — only when tp_price was calculated.
            # Absolute limit target goes under "limitPrice".
            if pipeline_result.tp_price is not None:
                payload["takeProfit"] = {
                    "type": "limit",
                    "limitPrice": float(pipeline_result.tp_price),
                }

        # extras — always included, useful for cross-referencing in TradersPost
        payload["extras"] = {
            "strategy_id": signal.strategy_id,
            "signal_id": str(signal.id),
            "ntexecg_score": pipeline_result.score,
            "atr_value": (
                float(pipeline_result.atr_value)
                if pipeline_result.atr_value is not None else None
            ),
            "sl_multiplier": config.get("sl_atr_multiplier"),
            "provider": pipeline_result.market_data_provider,
        }

        return payload
