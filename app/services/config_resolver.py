"""ConfigResolver — merges GlobalProfile → AssetProfile → StrategyProfile.

Later sources override earlier ones. Returns a flat dict with all
config values needed by FilterPipeline, SLTPCalculator, and dispatch.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.repositories import (
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
            "atr_timeframe": "5m",
            "allow_exits_outside_window": True,
            "allow_overnight": False,
            "allow_reversal": False,
            "traderspost_webhook_url": None,
            "session_config_json": None,
            "news_filter_enabled": True,
            "news_window_minutes": 30,
            "timezone": "America/New_York",
        }

        # Merge GlobalProfile (base)
        global_profile = await get_global_profile(db)
        if global_profile:
            config.update({
                "mode": global_profile.mode,
                "dry_run": global_profile.dry_run,
                "traderspost_enabled": global_profile.traderspost_enabled,
                "score_minimum": global_profile.score_minimum,
                "max_open_positions": global_profile.max_open_positions,
                "daily_loss_stop": float(global_profile.daily_loss_stop)
                    if global_profile.daily_loss_stop else None,
                "allow_exits_outside_window": global_profile.allow_exits_outside_window,
                "allow_overnight": global_profile.allow_overnight,
                "news_filter_enabled": global_profile.news_filter_enabled,
                "news_window_minutes": global_profile.news_window_minutes,
                "timezone": global_profile.timezone,
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
                    "atr_timeframe": asset_profile.atr_timeframe or "5m",
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

            result = await db.execute(
                select(StrategyProfile).where(
                    StrategyProfile.strategy_id == strategy_id
                )
            )
            strategy_profile = result.scalar_one_or_none()

            if strategy_profile:
                updates = {
                    "mode": strategy_profile.mode,
                    "dry_run": strategy_profile.dry_run,
                    "traderspost_enabled": strategy_profile.traderspost_enabled,
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
                if strategy_profile.allow_reversal is not None:
                    updates["allow_reversal"] = strategy_profile.allow_reversal

                config.update(updates)

        return config
