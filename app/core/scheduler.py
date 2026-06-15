"""APScheduler-based background jobs.

HeartbeatMonitor: every 30 seconds, checks is_active() for each active
SymbolMap and upserts MarketDataStatus in the DB. Logs a warning when a
symbol goes inactive (NinjaTrader down → BLOCK entries in FilterPipeline).

The scheduler is started in app lifespan and stopped on shutdown.
It creates its own DB sessions — independent of the request lifecycle.
"""
from __future__ import annotations

from loguru import logger
from apscheduler.schedulers.asyncio import AsyncIOScheduler


class HeartbeatMonitor:
    """Polls provider activity and persists results to market_data_status table."""

    def __init__(self, market_data_service: object) -> None:
        self._svc = market_data_service
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        self._scheduler.add_job(
            self._check,
            trigger="interval",
            seconds=30,
            id="heartbeat_monitor",
            replace_existing=True,
        )
        self._scheduler.start()
        logger.info("heartbeat_monitor_started interval=30s")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("heartbeat_monitor_stopped")

    async def _check(self) -> None:
        from sqlalchemy import select
        from app.db.session import AsyncSessionLocal
        from app.models.symbol_map import SymbolMap
        from app.services.repositories import upsert_market_data_status

        provider_name = type(self._svc.provider).__name__

        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(
                    select(SymbolMap).where(SymbolMap.active.is_(True))
                )
                symbol_maps = result.scalars().all()

                for sm in symbol_maps:
                    try:
                        active = await self._svc.is_active(sm.mapped_symbol)
                    except Exception as exc:
                        logger.error(
                            "heartbeat_is_active_failed symbol={} error={}",
                            sm.tv_symbol, exc,
                        )
                        active = False

                    await upsert_market_data_status(
                        db,
                        sm.tv_symbol,
                        provider=provider_name,
                        is_active=active,
                    )

                    if not active:
                        logger.warning(
                            "market_data_inactive symbol={} provider={}",
                            sm.tv_symbol, provider_name,
                        )

                await db.commit()
            except Exception as exc:
                logger.error("heartbeat_check_failed error={}", exc)
                await db.rollback()
