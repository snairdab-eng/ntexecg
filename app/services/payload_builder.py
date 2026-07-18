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
from app.services.tp_format import round_to_tick

if TYPE_CHECKING:
    from app.services.filter_pipeline import PipelineResult

# signal_role values that represent an entry (need a stopLoss)
_ENTRY_ROLES = {"entry_long", "entry_short", "reversal_to_long", "reversal_to_short"}
_EXIT_ROLES = {"exit_long", "exit_short"}

# RA-2a — TradersPost `cancelAfter` (segundos, 1..3600): TTL de una orden límite
# de trabajo. TradersPost la caduca sola al vencer. Es el reloj del ciclo que el
# re-armado (RA-2b) usa para la ventana SIN SOLAPE (re-envía DESPUÉS de que el
# cancelAfter ejecutó con certeza), y elimina la config manual de "Cancel entry
# after" por cuenta. Cap duro 3600 = máximo que TradersPost acepta (verificado RA-1).
_CANCEL_AFTER_MAX_S = 3600
_CANCEL_AFTER_MIN_S = 1


def _cancel_after_seconds(config: dict) -> int:
    """TTL en segundos para una pierna LÍMITE: el `entry_reserve_timeout_seconds`
    del config (la MISMA fuente que la reserva NX-28, así el fantasma symbol_busy
    y el corte de TradersPost caducan juntos), acotado a [1, 3600]. Ausente o
    inválido → 3600 (el techo, comportamiento de despacho vigente)."""
    v = config.get("entry_reserve_timeout_seconds")
    try:
        s = int(v) if v is not None else _CANCEL_AFTER_MAX_S
    except (TypeError, ValueError):
        s = _CANCEL_AFTER_MAX_S
    return max(_CANCEL_AFTER_MIN_S, min(s, _CANCEL_AFTER_MAX_S))


def _short_size_factor(config: dict) -> float | None:
    """Factor de tamaño para ENTRADAS CORTAS (MR-5c, opt-in): motor de
    largos → cortos reducidos, no eliminados. Válido: 0 < f ≤ 1 (bool
    excluido — True es int). Ausente/inválido → None (simétrico)."""
    v = config.get("short_size_factor")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1:
        return None
    return float(v)


def _apportion(quantities: list[int], factor: float) -> list[int]:
    """Reparte el factor sobre el vector de piernas CONSERVANDO el total
    objetivo (round(total·f), mínimo 1) — método del mayor resto. A empate
    ganan las piernas de MENOR índice (las someras: la participación es la
    prioridad declarada del estudio)."""
    total = sum(q for q in quantities if q > 0)
    target = max(1, int(round(total * factor)))
    raw = [q * factor if q > 0 else 0.0 for q in quantities]
    floors = [int(x) for x in raw]
    resto = max(0, target - sum(floors))
    orden = sorted(range(len(raw)),
                   key=lambda i: (-(raw[i] - floors[i]), i))
    out = list(floors)
    for i in orden[:resto]:
        out[i] += 1
    return out


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

        # MR-5c — asimetría de lado: cortos reducidos (mínimo 1 — reducidos,
        # NO eliminados). Solo entradas; las salidas cierran completo.
        quantity = signal.quantity
        factor = _short_size_factor(config)
        if (factor is not None and not is_exit and signal.action == "sell"
                and quantity):
            quantity = max(1, int(round(quantity * factor)))

        payload: dict = {
            "ticker": signal.mapped_symbol,      # mapped contract, not ticker_received
            "action": signal.action,
            "signalPrice": float(signal.price) if signal.price is not None else None,
            "quantity": quantity,
        }
        # "sentiment" is only valid for entries (buy/sell). TradersPost rejects it
        # with action == "exit" (invalid-sentiment-action), so omit it on exits.
        if not is_exit:
            payload["sentiment"] = signal.sentiment
        else:
            # FIX-D3 — cancel any still-WORKING orders BEFORE flattening. TradersPost's
            # documented top-level boolean "cancel" (webhook-spec.json) cancels open
            # orders for the ticker before submitting new ones, so an unfilled pullback
            # leg (C2/C3) — or a re-armed leg once RA-2 exists — cannot fill into an
            # already-closed position (orphan leg, R-RA6). The cancel happens atomically
            # WITH the exit (cancel-then-flatten in one message), closing the residual
            # exposure window at the source rather than in a post-close race. Best-effort
            # by nature (broker-dependent + delivery can FAIL) — the residual risk when
            # it does not take is bounded and made visible: a failed exit → position
            # UNKNOWN (L3 blocks entries) and NX-28 release_unfilled_reservations frees
            # the phantom within ≤ cancel_after remaining. See CONTRATO/FIX_D3_*.md.
            payload["cancel"] = True

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
        if factor is not None and not is_exit and signal.action == "sell":
            payload["extras"]["short_size_factor"] = factor

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

        # MR-5c — asimetría de lado en la escalera: el reparto por mayor
        # resto conserva el total objetivo y el pareo pierna↔nivel (las
        # piernas en 0 se saltan). Solo cortos; tras las validaciones (la
        # reducción nunca viola max_micro_contracts).
        factor = _short_size_factor(config)
        if factor is not None and signal.action == "sell":
            quantities = _apportion(quantities, factor)

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
                # LX-15 — C1 puede ser MÓVIL: con c1_depth_atr>0 se despacha como
                # LÍMITE a P0∓depth (igual que C2/C3, absoluto y al tick). c1_depth==0
                # → mercado (comportamiento actual). FAIL-HONEST TOTAL: un C1>0 sin
                # precio base/ATR NUNCA cae a mercado en silencio — bloquea la entrada
                # (el operador prometió C1 a un precio, no al mercado). Nada de utilería.
                c1_depth = float(se.get("c1_depth_atr") or 0.0)
                if c1_depth > 0:
                    if base_price is None or atr is None:
                        raise ValueError(
                            "C1 móvil (c1_depth_atr>0) sin precio base/ATR — no se "
                            "puede despachar como límite; jamás a mercado en silencio "
                            "(LX-15 fail-honest)")
                    level_atr = c1_depth
                    off = level_atr * atr
                    limit_price = base_price - off if is_long else base_price + off
                    leg["orderType"] = "limit"
                    leg["limitPrice"] = round_to_tick(
                        limit_price, config.get("tick_size"))
                # else: C1 a mercado (sin orderType → market)
            else:
                # add: requiere precio base, ATR y un nivel definido
                if base_price is None or atr is None or (i - 1) >= len(levels):
                    continue
                level_atr = levels[i - 1]
                off = level_atr * atr
                limit_price = base_price - off if is_long else base_price + off
                leg["orderType"] = "limit"
                # FIX-D2 — al tick del catálogo (múltiplo más cercano), nunca un
                # round(x,6) fijo que desalinea FX (6J tick 5e-7). Sin tick → intacto.
                leg["limitPrice"] = round_to_tick(limit_price, config.get("tick_size"))
            # RA-2a — TTL del ciclo en TODA pierna LÍMITE (C1 móvil incluida): le da
            # a la orden de trabajo un vencimiento cierto en TradersPost. El mercado
            # (C1 a mercado) llena al instante → jamás lleva cancelAfter.
            if leg.get("orderType") == "limit":
                leg["cancelAfter"] = _cancel_after_seconds(config)
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
            if factor is not None and signal.action == "sell":
                leg["extras"]["short_size_factor"] = factor
            payloads.append(leg)

        if not payloads:
            return [self.build(signal, strategy, config, pipeline_result)]
        return payloads
