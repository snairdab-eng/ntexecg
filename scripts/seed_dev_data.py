"""Seed development data into local PostgreSQL.
Run from project root:
    python scripts/seed_dev_data.py
Idempotent — safe to run multiple times.
"""
import asyncio
import sys
from datetime import date
from pathlib import Path

# Allow running from project root without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.global_profile import GlobalProfile
from app.models.symbol_map import SymbolMap
from app.models.asset_profile import AssetProfile
from app.models.strategy_template import StrategyTemplate
from app.models.market_data_status import MarketDataStatus


# All 16 tradeable symbols with their CURRENT (front) contract month as of
# mid-2026: equity index & FX → U2026 (Sep), gold → Q2026 (Aug), crude → Q2026.
# The 8 PARENTS the operator charts keep market_data_symbol=None (read their own
# bridge files). The 8 MICROS alias to their parent's bridge data via
# market_data_symbol (Anexo A.9.1; reglas 36, 38) — micro and parent share the
# same underlying/price/contract month; only the USD multiplier differs and it
# does NOT enter the data path.
SYMBOL_MAPS = [
    # ── Parents (charted; own bridge data; market_data_symbol=None) ───────────
    dict(tv_symbol="ES",  mapped_symbol="ESU2026",  exchange="CME",
         contract_type="futures_large", pine_script_config='"ticker": "ES"',
         expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="NQ",  mapped_symbol="NQU2026",  exchange="CME",
         contract_type="futures_large", pine_script_config='"ticker": "NQ"',
         expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="RTY", mapped_symbol="RTYU2026", exchange="CME",
         contract_type="futures_large", pine_script_config='"ticker": "RTY"',
         expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="YM",  mapped_symbol="YMU2026",  exchange="CBOT",
         contract_type="futures_large", pine_script_config='"ticker": "YM"',
         expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="GC",  mapped_symbol="GCQ2026",  exchange="COMEX",
         contract_type="futures_large", pine_script_config='"ticker": "GC"',
         expiry_date=date(2026, 8, 27)),
    dict(tv_symbol="CL",  mapped_symbol="CLQ2026",  exchange="NYMEX",
         contract_type="futures_large", pine_script_config='"ticker": "CL"',
         expiry_date=date(2026, 7, 22)),
    dict(tv_symbol="6J",  mapped_symbol="6JU2026",  exchange="CME",
         contract_type="futures_large", pine_script_config='"ticker": "6J"',
         expiry_date=date(2026, 9, 14)),
    dict(tv_symbol="6E",  mapped_symbol="6EU2026",  exchange="CME",
         contract_type="futures_large", pine_script_config='"ticker": "6E"',
         expiry_date=date(2026, 9, 14)),
    # ── Micros (aliased to parent bridge data via market_data_symbol) ─────────
    dict(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
         contract_type="futures_micro", pine_script_config='"ticker": "MES"',
         market_data_symbol="ES", expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="MNQ", mapped_symbol="MNQU2026", exchange="CME",
         contract_type="futures_micro", pine_script_config='"ticker": "MNQ"',
         market_data_symbol="NQ", expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="M2K", mapped_symbol="M2KU2026", exchange="CME",
         contract_type="futures_micro", pine_script_config='"ticker": "M2K"',
         market_data_symbol="RTY", expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="MYM", mapped_symbol="MYMU2026", exchange="CBOT",
         contract_type="futures_micro", pine_script_config='"ticker": "MYM"',
         market_data_symbol="YM", expiry_date=date(2026, 9, 18)),
    dict(tv_symbol="MGC", mapped_symbol="MGCQ2026", exchange="COMEX",
         contract_type="futures_micro", pine_script_config='"ticker": "MGC"',
         market_data_symbol="GC", expiry_date=date(2026, 8, 27)),
    dict(tv_symbol="MCL", mapped_symbol="MCLQ2026", exchange="NYMEX",
         contract_type="futures_micro", pine_script_config='"ticker": "MCL"',
         market_data_symbol="CL", expiry_date=date(2026, 7, 22)),
    dict(tv_symbol="MJY", mapped_symbol="MJYU2026", exchange="CME",
         contract_type="futures_micro", pine_script_config='"ticker": "MJY"',
         market_data_symbol="6J", expiry_date=date(2026, 9, 14)),
    dict(tv_symbol="M6E", mapped_symbol="M6EU2026", exchange="CME",
         contract_type="futures_micro", pine_script_config='"ticker": "M6E"',
         market_data_symbol="6E", expiry_date=date(2026, 9, 14)),
]

# Pit session config (Mon-Fri)
_PIT_09_30 = {
    "timezone": "America/New_York",
    "days_enabled": [1, 2, 3, 4, 5],
    "entry_start": "09:30",
    "entry_end": "15:45",
    "next_day_end": False,
    "avoid_open_minutes": 30,
    "avoid_close_minutes": 15,
    "force_flat_time": "15:55",
    "allow_overnight": False,
    "allow_exits_outside_window": True,
}
_MGC_SESSION = {
    **_PIT_09_30,
    "entry_start": "08:20",
    "entry_end": "13:30",
    "force_flat_time": "13:40",
}
# WTI crude RTH pit session (09:00–14:30 ET)
_CL_SESSION = {
    **_PIT_09_30,
    "entry_start": "09:00",
    "entry_end": "14:00",
    "force_flat_time": "14:25",
}
# 24h forex futures session (Sun 18:00 – Fri 17:00 ET)
_FX_24H = {
    "timezone": "America/New_York",
    "days_enabled": [0, 1, 2, 3, 4, 5],  # 0=Sunday
    "entry_start": "18:00",
    "entry_end": "17:00",
    "next_day_end": True,
    "avoid_open_minutes": 30,
    "allow_overnight": True,
    "allow_exits_outside_window": True,
}

ASSET_PROFILES = [
    # ── Parents ───────────────────────────────────────────────────────────────
    dict(symbol="ES",   name="E-mini S&P 500",
         pine_script_config='"ticker": "ES"', contract_type="futures_large",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="NQ",   name="E-mini Nasdaq-100",
         pine_script_config='"ticker": "NQ"', contract_type="futures_large",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="RTY",  name="E-mini Russell 2000",
         pine_script_config='"ticker": "RTY"', contract_type="futures_large",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="YM",   name="E-mini Dow Jones",
         pine_script_config='"ticker": "YM"', contract_type="futures_large",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="GC",   name="Gold",
         pine_script_config='"ticker": "GC"', contract_type="futures_large",
         session_config_json=_MGC_SESSION, sl_atr_multiplier=2.0),
    dict(symbol="CL",   name="Crude Oil WTI",
         pine_script_config='"ticker": "CL"', contract_type="futures_large",
         session_config_json=_CL_SESSION, sl_atr_multiplier=2.0),
    # ── Micros ────────────────────────────────────────────────────────────────
    dict(symbol="MES",  name="Micro E-mini S&P 500",
         pine_script_config='"ticker": "MES"', contract_type="futures_micro",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="MNQ",  name="Micro E-mini Nasdaq-100",
         pine_script_config='"ticker": "MNQ"', contract_type="futures_micro",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="MYM",  name="Micro E-mini Dow Jones",
         pine_script_config='"ticker": "MYM"', contract_type="futures_micro",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="M2K",  name="Micro E-mini Russell 2000",
         pine_script_config='"ticker": "M2K"', contract_type="futures_micro",
         session_config_json=_PIT_09_30, sl_atr_multiplier=2.0),
    dict(symbol="MGC",  name="Micro Gold",
         pine_script_config='"ticker": "MGC"', contract_type="futures_micro",
         session_config_json=_MGC_SESSION, sl_atr_multiplier=2.0),
    dict(symbol="MCL",  name="Micro Crude Oil WTI",
         pine_script_config='"ticker": "MCL"', contract_type="futures_micro",
         session_config_json=_CL_SESSION, sl_atr_multiplier=2.0),
    dict(symbol="MJY",  name="Micro JPY/USD Futures — CME",
         pine_script_config='"ticker": "MJY"', contract_type="futures_micro",
         session_config_json=_FX_24H, sl_atr_multiplier=2.0),
    dict(symbol="M6E",  name="Micro EUR/USD Futures — CME",
         pine_script_config='"ticker": "M6E"', contract_type="futures_micro",
         session_config_json=_FX_24H, sl_atr_multiplier=2.0),
    dict(symbol="6J",   name="JPY/USD Futures — CME",
         pine_script_config='"ticker": "6J"', contract_type="futures_large",
         session_config_json=_FX_24H, sl_atr_multiplier=2.0),
    dict(symbol="6E",   name="EUR/USD Futures — CME",
         pine_script_config='"ticker": "6E"', contract_type="futures_large",
         session_config_json=_FX_24H, sl_atr_multiplier=2.0),
]

SYMBOLS = [s["tv_symbol"] for s in SYMBOL_MAPS]


async def seed(session: AsyncSession) -> None:
    # GlobalProfile (single row)
    existing = await session.execute(select(GlobalProfile).limit(1))
    if existing.scalar_one_or_none() is None:
        session.add(GlobalProfile(
            profile_name="default",
            mode="normal",
            dry_run=True,
            traderspost_enabled=False,
            max_open_positions=5,
            daily_loss_stop=500.0,
            score_minimum=70,
        ))
        print("✓ GlobalProfile created")
    else:
        print("  GlobalProfile already exists")

    # SymbolMaps — upsert so re-runs ENSURE the current contract month and the
    # market-data alias on rows seeded before this column existed.
    # Instrument catalog tick values (Anexo 08 #4): USD/tick, tick size.
    TICK_VALUES = {
        "ES": (12.50, 0.25), "NQ": (5.00, 0.25), "RTY": (5.00, 0.10),
        "YM": (5.00, 1.00), "GC": (10.00, 0.10), "CL": (10.00, 0.01),
        "6J": (6.25, 0.0000005), "6E": (6.25, 0.00005),
        "MES": (1.25, 0.25), "MNQ": (0.50, 0.25), "M2K": (0.50, 0.10),
        "MYM": (0.50, 1.00), "MGC": (1.00, 0.10), "MCL": (1.00, 0.01),
        "M6E": (1.25, 0.0001), "MJY": (1.25, 0.000001), "M6J": (1.25, 0.000001),
    }
    for sm in SYMBOL_MAPS:
        _tv, _ts = TICK_VALUES.get(sm["tv_symbol"], (None, None))
        sm.setdefault("tick_value", _tv)
        sm.setdefault("tick_size", _ts)
        existing = await session.execute(
            select(SymbolMap).where(SymbolMap.tv_symbol == sm["tv_symbol"])
        )
        row = existing.scalar_one_or_none()
        alias = sm.get("market_data_symbol")
        if row is None:
            session.add(SymbolMap(**sm))
            suffix = f"  (datos: {alias})" if alias else ""
            print(f"  ✓ SymbolMap {sm['tv_symbol']} → {sm['mapped_symbol']}{suffix}")
        else:
            for key, value in sm.items():
                setattr(row, key, value)
            # Parents omit the key → explicitly clear any stale alias.
            row.market_data_symbol = alias
            suffix = f"  (datos: {alias})" if alias else ""
            print(f"  ↻ SymbolMap {sm['tv_symbol']} → {sm['mapped_symbol']}{suffix}")

    # AssetProfiles
    for ap in ASSET_PROFILES:
        existing = await session.execute(
            select(AssetProfile).where(AssetProfile.symbol == ap["symbol"])
        )
        if existing.scalar_one_or_none() is None:
            session.add(AssetProfile(**ap))
            print(f"  ✓ AssetProfile {ap['symbol']}")

    # StrategyTemplate
    existing = await session.execute(
        select(StrategyTemplate).where(StrategyTemplate.name == "LuxAlgo Confirmation Normal")
    )
    if existing.scalar_one_or_none() is None:
        session.add(StrategyTemplate(
            name="LuxAlgo Confirmation Normal",
            description="Standard LuxAlgo Backtesting AI confirmation strategy template",
            source="luxalgo",
            strategy_type="trend_following",
            default_config_json={
                "sl_atr_multiplier": 1.5,
                "score_minimum": 70,
                "mode": "paper",
            },
            typical_metrics_json={
                "win_rate_range": "70-85%",
                "pf_range": "2.0-4.0",
                "min_trades": 50,
            },
        ))
        print("  ✓ StrategyTemplate 'LuxAlgo Confirmation Normal'")

    # MarketDataStatus — one row per symbol, inactive initially
    for symbol in SYMBOLS:
        existing = await session.execute(
            select(MarketDataStatus).where(MarketDataStatus.symbol == symbol)
        )
        if existing.scalar_one_or_none() is None:
            session.add(MarketDataStatus(
                symbol=symbol,
                provider=settings.MARKET_DATA_PROVIDER,
                is_active=False,
            ))
            print(f"  ✓ MarketDataStatus {symbol}")

    await session.commit()
    print("\nSeed complete.")


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await seed(session)
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
