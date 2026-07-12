"""ConfigResolver — merges GlobalProfile → AssetProfile → StrategyProfile.

Later sources override earlier ones. Returns a flat dict with all
config values needed by FilterPipeline, SLTPCalculator, and dispatch.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.repositories import (
    get_active_symbol_map,
    get_asset_profile,
    get_global_profile,
    get_strategy_by_id,
)


class ConfigResolver:
    """Merge config hierarchy: GlobalProfile < AssetProfile < StrategyProfile."""

    async def resolve(
        self, db: AsyncSession, strategy_id: str, asset_symbol: str | None
    ) -> dict:
        """Return merged config dict for FilterPipeline evaluation.

        Args:
            db: Database session
            strategy_id: Strategy identifier (required)
            asset_symbol: Asset/symbol for the signal (e.g., "MESU2025")

        Returns:
            Flat dict with all config keys merged and overridden.
            Falls back to safe defaults if profiles are missing.
        """
        config: dict = {
            # System defaults (fallbacks)
            "mode": "normal",
            # System-level brake (GlobalProfile.mode: normal/defensive/
            # flatten_only/paused). Kept SEPARATE from "mode", which the
            # StrategyProfile overwrites with its maturity mode (paper/micro/...).
            # L1.1 reads THIS key — never "mode" (NX-01).
            "global_mode": "normal",
            "dry_run": True,
            "traderspost_enabled": False,
            "sl_atr_multiplier": 1.5,
            "tp_atr_multiplier": None,
            "score_minimum": 70,
            "max_open_positions": 5,
            "daily_loss_stop": None,
            "max_trades_day": None,
            "max_quantity": 1,
            "max_open_positions_symbol": 1,
            "atr_period": 14,
            # NX-14: None = sin calibración explícita → el L5 usa el timeframe
            # de la señal. Un default "5m" forzaba ATR de 5m en estrategias 15m.
            "atr_timeframe": None,
            "allow_exits_outside_window": True,
            "allow_overnight": False,
            "force_flat_time": None,
            "max_holding_minutes": None,
            "allow_reversal": False,
            "traderspost_webhook_url": None,
            "session_config_json": None,
            "news_filter_enabled": True,
            "news_window_minutes": 30,
            "timezone": "America/New_York",
            # NX-15 — reintentos TradersPost (GlobalProfile los sobreescribe).
            # Entradas: retry_attempts; los EXITS siempre reintentan 10 veces.
            "retry_attempts": 3,
            "retry_backoff_seconds": 1,
            "entry_signal_timeout_secs": 30,
            # NX-28 — timeout para liberar reservas de entrada sin fill
            # (≈ cancel_after de TradersPost; override por estrategia).
            "entry_reserve_timeout_seconds": 3600,
            # Per-strategy guardrails (Anexo 08) - opt-in, disabled by default.
            "expected_symbol": None,
            "expected_timeframe": None,
            "enforce_symbol_match": False,
            "enforce_timeframe_match": False,
            "signal_max_age_entry_seconds": None,
            "signal_max_age_exit_seconds": None,
            # NX-10 — ventana de dedupe por estrategia (la consume el webhook
            # ANTES de resolver esta config; aquí solo para visibilidad en la
            # config efectiva: UI/show_strategy_configs).
            "dedup_seconds": 60,
            # NX-09 — opt-out de la regla symbol_busy (L3.4): permitir apilar
            # posiciones sobre un símbolo ocupado. Default: una posición por
            # símbolo/cuenta.
            "allow_stacking": False,
            # Instrument catalog (Anexo 08 #4) - reference data for the UI ficha
            # (e.g. show tick value when registering a strategy). NOT a risk gate:
            # NTEXECG's only monetary-risk responsibility is the ATR SL (Level 5).
            "tick_value": None,
            "tick_size": None,
            "contract_type": None,
            # Quality filters (Fase 5) - opt-in; empty → score 100.
            "filters": {},
            # Market regime (Fase 6) - opt-in; empty → gate disabled.
            "regime": {},
            # Scaled entry (Anexo 14 §8) - design/execution metadata; empty → entrada única.
            "scale_entry": {},
            # Perfiles de riesgo (tiers) - lista de deltas sobre la base; [] → un solo
            # destino (el webhook base). Cada perfil overridea cantidades/levels/webhook/etc.
            "profiles": [],
        }

        # Merge GlobalProfile (base)
        global_profile = await get_global_profile(db)
        if global_profile:
            config.update({
                "mode": global_profile.mode,
                # System brake — survives the StrategyProfile merge (NX-01).
                "global_mode": global_profile.mode,
                "dry_run": global_profile.dry_run,
                "traderspost_enabled": global_profile.traderspost_enabled,
                "score_minimum": global_profile.score_minimum,
                "max_open_positions": global_profile.max_open_positions,
                "daily_loss_stop": float(global_profile.daily_loss_stop)
                    if global_profile.daily_loss_stop else None,
                "allow_exits_outside_window": global_profile.allow_exits_outside_window,
                "allow_overnight": global_profile.allow_overnight,
                "force_flat_time": global_profile.force_flat_time,
                "max_holding_minutes": global_profile.max_holding_minutes,
                "news_filter_enabled": global_profile.news_filter_enabled,
                "news_window_minutes": global_profile.news_window_minutes,
                "timezone": global_profile.timezone,
                # NX-15 — antes se guardaban en Settings/DB y nadie los leía.
                "retry_attempts": global_profile.retry_attempts or 3,
                "retry_backoff_seconds": global_profile.retry_backoff_seconds or 1,
                "entry_signal_timeout_secs":
                    global_profile.entry_signal_timeout_secs or 30,
            })

        # Merge AssetProfile (overrides global)
        if asset_symbol:
            asset_profile = await get_asset_profile(db, asset_symbol)
            if asset_profile:
                config.update({
                    "sl_atr_multiplier": float(asset_profile.sl_atr_multiplier)
                        if asset_profile.sl_atr_multiplier else config["sl_atr_multiplier"],
                    "tp_atr_multiplier": float(asset_profile.tp_atr_multiplier)
                        if asset_profile.tp_atr_multiplier else None,
                    "atr_period": asset_profile.atr_period,
                    # NX-14: solo override si el activo lo define; None se
                    # queda None (→ timeframe de la señal en L5).
                    "atr_timeframe": asset_profile.atr_timeframe
                        or config["atr_timeframe"],
                    "max_trades_day": asset_profile.max_trades_day,
                    "daily_loss_stop": float(asset_profile.daily_loss_stop)
                        if asset_profile.daily_loss_stop else config["daily_loss_stop"],
                    "max_quantity": asset_profile.max_quantity or 1,
                    "max_open_positions_symbol": asset_profile.max_open_positions_symbol,
                    "allow_reversal": asset_profile.allow_reversal,
                    "score_minimum": asset_profile.score_minimum or config["score_minimum"],
                    "session_config_json": asset_profile.session_config_json,
                })

        # Merge StrategyProfile (overrides both)
        strategy = await get_strategy_by_id(db, strategy_id)
        if strategy:
            from app.models.strategy_profile import StrategyProfile
            from sqlalchemy import select

            # Per-strategy guardrails (Anexo 08): expected symbol/timeframe come
            # from the strategy record; enforcement stays OFF unless turned on
            # via the profile's pipeline_config_json["guardrails"].
            config["expected_symbol"] = strategy.asset_symbol
            config["expected_timeframe"] = strategy.timeframe

            # Instrument catalog (Anexo 08 #4): tick value/size from symbol_maps.
            if strategy.asset_symbol:
                sm = await get_active_symbol_map(db, strategy.asset_symbol)
                if sm is not None:
                    config["contract_type"] = sm.contract_type
                    if sm.tick_value is not None:
                        config["tick_value"] = float(sm.tick_value)
                    if sm.tick_size is not None:
                        config["tick_size"] = float(sm.tick_size)

            result = await db.execute(
                select(StrategyProfile).where(
                    StrategyProfile.strategy_id == strategy_id
                )
            )
            strategy_profile = result.scalar_one_or_none()

            # Parte C — the routing-safety guardrails (symbol_mismatch /
            # interval_mismatch) are SIEMPRE-ON. A persisted enforce_*=False in
            # pipeline_config_json is deliberately ignored: the UI toggle was
            # removed and the check can no longer be disabled. This lives in the
            # resolver (not the pipeline), so the opt-in filter_pipeline unit
            # tests that pass config explicitly stay intact.
            config["enforce_symbol_match"] = True
            config["enforce_timeframe_match"] = True

            if strategy_profile:
                guardrails = (strategy_profile.pipeline_config_json or {}).get(
                    "guardrails", {})
                if isinstance(guardrails, dict):
                    # Staleness thresholds remain per-strategy configurable; the
                    # enforce_* toggles above are forced and no longer read here.
                    for _k in ("signal_max_age_entry_seconds",
                               "signal_max_age_exit_seconds"):
                        if guardrails.get(_k) is not None:
                            config[_k] = guardrails[_k]
                # Anexo 08 #5 — per-strategy repeatable windows override the
                # asset session config (SessionValidator reads "windows").
                windows = (strategy_profile.pipeline_config_json or {}).get(
                    "windows")
                if windows:
                    base = dict(config.get("session_config_json") or {})
                    base["windows"] = windows
                    config["session_config_json"] = base
                # Quality filters (Fase 5) — Level-4 scorer config (opt-in).
                _filters = (strategy_profile.pipeline_config_json or {}).get(
                    "filters")
                if isinstance(_filters, dict):
                    config["filters"] = _filters
                # Market regime (Fase 6) — Level-4 opt-in gate config.
                _regime = (strategy_profile.pipeline_config_json or {}).get(
                    "regime")
                if isinstance(_regime, dict):
                    config["regime"] = _regime
                # Quality threshold (Fase 5) — per-strategy override of the
                # score_minimum used at Level 4 (pipeline_config_json["score_minimum"]).
                # Documented intent of StrategyProfile; lets a strategy require a
                # stricter/looser quality bar than the global/asset default.
                _score_min = (strategy_profile.pipeline_config_json or {}).get(
                    "score_minimum")
                if isinstance(_score_min, (int, float)) and _score_min > 0:
                    config["score_minimum"] = int(_score_min)
                # Scaled entry (Anexo 14 §8) — diseño/ejecución escalonada.
                # Necesario para que PayloadBuilder.build_scaled lo vea; sin esto
                # el motor cae siempre a entrada única.
                _scale = (strategy_profile.pipeline_config_json or {}).get(
                    "scale_entry")
                if isinstance(_scale, dict):
                    config["scale_entry"] = _scale
                # Perfiles de riesgo (tiers) — deltas sobre la base, usados por el
                # dispatch multi-perfil (app/services/dispatch_profiles.py).
                _profiles = (strategy_profile.pipeline_config_json or {}).get(
                    "profiles")
                if isinstance(_profiles, list):
                    config["profiles"] = _profiles
                # NX-10 — dedup_seconds de la ficha (ver default arriba).
                _dedup = (strategy_profile.pipeline_config_json or {}).get(
                    "dedup_seconds")
                if isinstance(_dedup, (int, float)) and _dedup > 0:
                    config["dedup_seconds"] = int(_dedup)
                # NX-09 — opt-out de symbol_busy (bool explícito).
                _stack = (strategy_profile.pipeline_config_json or {}).get(
                    "allow_stacking")
                if isinstance(_stack, bool):
                    config["allow_stacking"] = _stack
                # NX-28 — timeout de reserva por estrategia (≈ cancel_after).
                _rt = (strategy_profile.pipeline_config_json or {}).get(
                    "entry_reserve_timeout_seconds")
                if isinstance(_rt, (int, float)) and _rt > 0:
                    config["entry_reserve_timeout_seconds"] = int(_rt)
                # MR-5a — backstop: stop de PRECIO FIJO anclado a la señal
                # (puntos en la unidad de precio del activo; viene del
                # recomendacion.json del Motor de Riesgo). Reemplaza el SL
                # k×ATR en L5 para esta estrategia. Ausente/ inválido → la
                # lógica ATR actual (retrocompat; bool excluido — True es
                # int en Python).
                _bk = (strategy_profile.pipeline_config_json or {}).get(
                    "backstop_points")
                if (isinstance(_bk, (int, float))
                        and not isinstance(_bk, bool) and _bk > 0):
                    config["backstop_points"] = float(_bk)
                # MR-5b — TP NOMINAL por lado (×ATR sobre el p99 del cierre
                # de LuxAlgo, del recomendacion.json). Que cierre LuxAlgo:
                # el TP casi nunca dispara, solo satisface el bracket de
                # TradersPost. Misma validación opt-in que el backstop.
                for _tk in ("tp_nominal_long", "tp_nominal_short"):
                    _tv = (strategy_profile.pipeline_config_json or {}).get(
                        _tk)
                    if (isinstance(_tv, (int, float))
                            and not isinstance(_tv, bool) and _tv > 0):
                        config[_tk] = float(_tv)
                # MR-5c — asimetría de lado (motor de largos): factor de
                # tamaño de ENTRADAS CORTAS, 0 < f ≤ 1, opt-in. Ausente =
                # simétrico. Las salidas nunca se reducen.
                _ssf = (strategy_profile.pipeline_config_json or {}).get(
                    "short_size_factor")
                if (isinstance(_ssf, (int, float))
                        and not isinstance(_ssf, bool) and 0 < _ssf <= 1):
                    config["short_size_factor"] = float(_ssf)
                updates = {
                    "mode": strategy_profile.mode,
                    # Kill-switch semantics (Fase 2): any level that says dry_run
                    # wins; traderspost must be enabled at BOTH global and
                    # strategy level. A strategy can only restrict further, never
                    # escalate above the global setting.
                    "dry_run": config["dry_run"] or strategy_profile.dry_run,
                    "traderspost_enabled": (
                        config["traderspost_enabled"]
                        and strategy_profile.traderspost_enabled
                    ),
                    "traderspost_webhook_url": strategy_profile.traderspost_webhook_url,
                }
                if strategy_profile.sl_atr_multiplier:
                    updates["sl_atr_multiplier"] = float(
                        strategy_profile.sl_atr_multiplier
                    )
                if strategy_profile.tp_atr_multiplier:
                    updates["tp_atr_multiplier"] = float(
                        strategy_profile.tp_atr_multiplier
                    )
                if strategy_profile.atr_period:
                    updates["atr_period"] = strategy_profile.atr_period
                if strategy_profile.atr_timeframe:
                    updates["atr_timeframe"] = strategy_profile.atr_timeframe
                if strategy_profile.max_trades_day:
                    updates["max_trades_day"] = strategy_profile.max_trades_day
                if strategy_profile.daily_loss_stop:
                    updates["daily_loss_stop"] = float(
                        strategy_profile.daily_loss_stop
                    )
                if strategy_profile.max_quantity:
                    updates["max_quantity"] = strategy_profile.max_quantity
                if strategy_profile.max_open_positions_symbol:
                    updates["max_open_positions_symbol"] = (
                        strategy_profile.max_open_positions_symbol
                    )
                if strategy_profile.allow_exits_outside_window is not None:
                    updates["allow_exits_outside_window"] = (
                        strategy_profile.allow_exits_outside_window
                    )
                if strategy_profile.allow_overnight is not None:
                    updates["allow_overnight"] = strategy_profile.allow_overnight
                if strategy_profile.force_flat_time is not None:
                    updates["force_flat_time"] = strategy_profile.force_flat_time
                if strategy_profile.max_holding_minutes is not None:
                    updates["max_holding_minutes"] = strategy_profile.max_holding_minutes
                if strategy_profile.allow_reversal is not None:
                    updates["allow_reversal"] = strategy_profile.allow_reversal

                config.update(updates)

                # NX-13 — "sin cierre EOD" explícito por estrategia: gana
                # sobre cualquier force_flat_time heredado (global/columna).
                # None en la columna sigue significando "heredar".
                if (strategy_profile.pipeline_config_json or {}).get(
                    "force_flat_off"
                ) is True:
                    config["force_flat_time"] = None

        return config
