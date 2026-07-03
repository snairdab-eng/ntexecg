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
            "signalPrice": float(signal.price) if signal.price is not None else None,
            "quantity": signal.quantity,
        }
        # "sentiment" is only valid for entries (buy/sell). TradersPost rejects it
        # with action == "exit" (invalid-sentiment-action), so omit it on exits.
        if not is_exit:
            payload["sentiment"] = signal.sentiment

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
        # NX-04: ntexecg_quality + filters_active viajan junto al score para que
        # un 100 sin medición sea distinguible (UNKNOWN) del 100 medido (HIGH).
        # getattr con default: forced_exit / dispatch por perfil pasan
        # SimpleNamespace sin estos campos.
        payload["extras"] = {
            "strategy_id": signal.strategy_id,
            "signal_id": str(signal.id),
            "ntexecg_score": pipeline_result.score,
            "ntexecg_quality": getattr(pipeline_result, "quality", None),
            "filters_active": getattr(pipeline_result, "filters_active", None),
            "atr_value": (
                float(pipeline_result.atr_value)
                if pipeline_result.atr_value is not None else None
            ),
            "sl_multiplier": config.get("sl_atr_multiplier"),
            "provider": pipeline_result.market_data_provider,
        }

        return payload

    # ------------------------------------------------------------------ #
    # Scaled entry (motor de ejecución escalonada — Anexo 14 §8)          #
    # ------------------------------------------------------------------ #
    def build_scaled(
        self,
        signal: NormalizedSignal,
        strategy: Strategy | None,
        config: dict,
        pipeline_result: "PipelineResult",
    ) -> list[dict]:
        """Devuelve una LISTA de payloads para una entrada escalonada.

        - C1 = entrada base a mercado (qty quantities[0]).
        - C2..Cn = ordenes LIMITE (orderType=limit, limitPrice) en
          precio_senal ∓ levels[i-1]×ATR (− para long, + para short).
        - TODAS comparten el mismo stopLoss (stop comun) y, si existe, takeProfit.
        Solo aplica a ENTRADAS con scale_entry mode in {execute, live}; en cualquier
        otro caso devuelve [self.build(...)] (entrada unica, comportamiento normal).
        Salidas nunca escalan. Si la config es inconsistente (sin qty, total<=0 o
        total>max_micro_contracts, o falta precio/ATR para un add) cae a entrada unica.
        """
        se = config.get("scale_entry") or {}
        is_exit = signal.action == "exit" or signal.signal_role in _EXIT_ROLES
        mode = se.get("mode")
        quantities = [int(q or 0) for q in (se.get("quantities") or [])]
        levels = [float(x) for x in (se.get("levels") or [])]

        if is_exit or mode not in ("execute", "live") or not quantities:
            return [self.build(signal, strategy, config, pipeline_result)]

        total = sum(q for q in quantities if q > 0)
        maxm = se.get("max_micro_contracts")
        if total <= 0 or (maxm and total > int(maxm)):
            return [self.build(signal, strategy, config, pipeline_result)]
        if pipeline_result.sl_price is None:
            raise ValueError(
                "Scaled entry without sl_price is forbidden "
                f"(strategy={signal.strategy_id})"
            )

        base_price = float(signal.price) if signal.price is not None else None
        atr = (float(pipeline_result.atr_value)
               if pipeline_result.atr_value is not None else None)
        is_long = signal.action == "buy"
        sl_block = {"type": "stop", "stopPrice": float(pipeline_result.sl_price)}
        tp_block = (
            {"type": "limit", "limitPrice": float(pipeline_result.tp_price)}
            if pipeline_result.tp_price is not None else None
        )

        payloads: list[dict] = []
        n = len(quantities)
        for i in range(n):
            q = quantities[i]
            if q <= 0:
                continue
            leg: dict = {
                "ticker": signal.mapped_symbol,
                "action": signal.action,
                "quantity": q,
                "sentiment": signal.sentiment,
                "signalPrice": base_price,
            }
            level_atr = 0.0
            if i == 0:
                # C1 a mercado (sin orderType → market)
                pass
            else:
                # add: requiere precio base, ATR y un nivel definido
                if base_price is None or atr is None or (i - 1) >= len(levels):
                    continue
                level_atr = levels[i - 1]
                off = level_atr * atr
                limit_price = base_price - off if is_long else base_price + off
                leg["orderType"] = "limit"
                leg["limitPrice"] = round(limit_price, 6)
            leg["stopLoss"] = dict(sl_block)
            if tp_block is not None:
                leg["takeProfit"] = dict(tp_block)
            leg["extras"] = {
                "strategy_id": signal.strategy_id,
                "signal_id": str(signal.id),
                "leg_index": len(payloads) + 1,
                "leg_quantity": q,
                "level_atr": level_atr,
                "ntexecg_score": pipeline_result.score,
                "ntexecg_quality": getattr(pipeline_result, "quality", None),
                "filters_active": getattr(pipeline_result, "filters_active", None),
                "atr_value": atr,
                "sl_multiplier": config.get("sl_atr_multiplier"),
                "provider": pipeline_result.market_data_provider,
            }
            payloads.append(leg)

        if not payloads:
            return [self.build(signal, strategy, config, pipeline_result)]
        return payloads
