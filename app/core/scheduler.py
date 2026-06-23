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
        from app.services.symbol_mapper import SymbolMapper

        provider_name = type(self._svc.provider).__name__
        mapper = SymbolMapper()

        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(
                    select(SymbolMap).where(SymbolMap.active.is_(True))
                )
                symbol_maps = result.scalars().all()

                # Probe each distinct DATA symbol once per cycle: micro and parent
                # share bridge files (MES → ES), so we never hit the bridge twice
                # for the same data symbol within a single cycle.
                probe_cache: dict[str, tuple[bool, float | None]] = {}

                for sm in symbol_maps:
                    # Resolve which bridge symbol backs this tradeable symbol.
                    data_symbol = await mapper.resolve_market_data_symbol(
                        db, sm.tv_symbol
                    )

                    if data_symbol not in probe_cache:
                        try:
                            active = await self._svc.is_active(data_symbol)
                        except Exception as exc:
                            logger.error(
                                "heartbeat_is_active_failed data_symbol={} error={}",
                                data_symbol, exc,
                            )
                            active = False

                        atr_5m: float | None = None
                        if active:
                            try:
                                atr_5m = await self._svc.get_atr(data_symbol, "5m", 14)
                            except Exception as exc:
                                logger.error(
                                    "heartbeat_atr_failed data_symbol={} error={}",
                                    data_symbol, exc,
                                )
                                atr_5m = None

                        probe_cache[data_symbol] = (active, atr_5m)

                    active, atr_5m = probe_cache[data_symbol]

                    # Persist status keyed by the TRADEABLE symbol (tv_symbol),
                    # not the data symbol — the operator monitors what they trade.
                    await upsert_market_data_status(
                        db,
                        sm.tv_symbol,
                        provider=provider_name,
                        is_active=active,
                        last_atr_5m=atr_5m,
                    )

                    if not active:
                        logger.warning(
                            "market_data_inactive symbol={} data_symbol={} provider={}",
                            sm.tv_symbol, data_symbol, provider_name,
                        )

                await db.commit()
            except Exception as exc:
                logger.error("heartbeat_check_failed error={}", exc)
                await db.rollback()


class ExitManagerJob:
    """Fase 4 — runs the Exit Manager sweep every 60s.

    Scans confirmed-open positions and dispatches forced exits (EOD /
    max-holding / overnight) through the Fase-2 gate. Own DB session per cycle.
    """

    def __init__(self, settings_obj: object) -> None:
        self._settings = settings_obj
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        self._scheduler.add_job(
            self._run, trigger="interval", seconds=60,
            id="exit_manager", replace_existing=True,
        )
        self._scheduler.start()
        logger.info("exit_manager_started interval=60s")

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("exit_manager_stopped")

    async def _run(self) -> None:
        from app.db.session import AsyncSessionLocal
        from app.services.forced_exit import exit_manager_sweep

        async with AsyncSessionLocal() as db:
            try:
                n = await exit_manager_sweep(db, self._settings)
                if n:
                    logger.info("exit_manager_sweep dispatched={}", n)
                await db.commit()
            except Exception as exc:
                logger.error("exit_manager_sweep_failed error={}", exc)
                await db.rollback()


class MarketBarsUpdater:
    """Keeps the ohlcv_bars history current from the live bridge feed.

    Every `interval_minutes`, reads the latest bridge bars for each active data
    symbol and timeframe and upserts them into ohlcv_bars (idempotent). The HOLC
    CSV backfill seeds the long history; this job appends new bars going forward
    so the store is always up to date for HMM training / backtests.
    """

    _TIMEFRAMES = ("5m", "15m", "1h", "4h")

    def __init__(self, market_data_service: object, interval_minutes: int = 15) -> None:
        self._svc = market_data_service
        self._interval = interval_minutes
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        self._scheduler.add_job(
            self._run, trigger="interval", minutes=self._interval,
            id="market_bars_updater", replace_existing=True,
        )
        self._scheduler.start()
        logger.info("market_bars_updater_started interval={}m", self._interval)

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("market_bars_updater_stopped")

    async def _run(self) -> None:
        from sqlalchemy import select
        from app.db.session import AsyncSessionLocal
        from app.models.symbol_map import SymbolMap
        from app.services.bar_store import persist_bars
        from app.services.symbol_mapper import SymbolMapper

        mapper = SymbolMapper()
        async with AsyncSessionLocal() as db:
            try:
                result = await db.execute(
                    select(SymbolMap).where(SymbolMap.active.is_(True))
                )
                # Distinct DATA symbols (micro + parent share bridge files).
                data_symbols: set[str] = set()
                for sm in result.scalars().all():
                    ds = await mapper.resolve_market_data_symbol(db, sm.tv_symbol)
                    if ds:
                        data_symbols.add(ds)

                total = 0
                for ds in sorted(data_symbols):
                    for tf in self._TIMEFRAMES:
                        try:
                            bars = await self._svc.get_bars(ds, tf, limit=500)
                        except Exception as exc:
                            logger.error(
                                "market_bars_fetch_failed symbol={} tf={} error={}",
                                ds, tf, exc,
                            )
                            continue
                        total += await persist_bars(db, ds, tf, bars)

                await db.commit()
                if total:
                    logger.info("market_bars_updated inserted={}", total)
            except Exception as exc:
                logger.error("market_bars_update_failed error={}", exc)
                await db.rollback()


class HMMTrainerJob:
    """Fase 6 — weekly HMM regime training per symbol from ohlcv_bars.

    Trains a GaussianHMM for each active data symbol on the configured regime
    timeframe and saves it to MODELS_DIR. get_regime() picks up the new model
    automatically (mtime-cached). Disabled via HMM_TRAIN_ENABLED.
    """

    def __init__(self, settings_obj: object) -> None:
        self._settings = settings_obj
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    def start(self) -> None:
        if not getattr(self._settings, "HMM_TRAIN_ENABLED", True):
            logger.info("hmm_trainer_disabled")
            return
        self._scheduler.add_job(
            self._run, trigger="cron",
            day_of_week=self._settings.HMM_TRAIN_DAY_OF_WEEK,
            hour=self._settings.HMM_TRAIN_HOUR,
            id="hmm_trainer", replace_existing=True,
        )
        self._scheduler.start()
        logger.info(
            "hmm_trainer_started day={} hour={}",
            self._settings.HMM_TRAIN_DAY_OF_WEEK, self._settings.HMM_TRAIN_HOUR,
        )

    def stop(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("hmm_trainer_stopped")

    async def _run(self) -> None:
        from app.db.session import AsyncSessionLocal
        from app.services.hmm_trainer import train_active_symbols

        async with AsyncSessionLocal() as db:
            try:
                results = await train_active_symbols(db)
                logger.info("hmm_training_done results={}", results)
            except Exception as exc:
                logger.error("hmm_training_failed error={}", exc)
