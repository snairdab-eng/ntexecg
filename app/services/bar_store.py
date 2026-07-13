"""bar_store — persist OHLCV bars into the ohlcv_bars table (idempotent).

⚠ JUBILADO (CSV-only, retiro NO destructivo estilo P3). `ohlcv_bars` ya no tiene
consumidores de producción: el estudio lee el CSV master directo (sin costura),
el entrenamiento HMM lee el CSV, y el precio en vivo usa el bridge. El
MarketBarsUpdater ya no se arranca (app/main.py). Estas funciones se conservan
(no se borra la tabla en este lote) pero NO se invocan en el flujo vivo. Ver el
informe del lote CSV-only y scripts/audit_ohlcv_tz.py para el diagnóstico de la
corrupción de TZ que motivó el retiro.

Source of truth for HMM training (Fase 6) and future backtests. Two writers,
one table:
  - scripts/backfill_market_bars.py : one-time load of the NinjaTrader HOLC CSVs.
  - MarketBarsUpdater (scheduler)    : keeps it current from the live bridge feed.

Both write with provider="ninjatrader" so the unique constraint
(symbol, timeframe, bar_time, provider) deduplicates across history and the live
feed — re-running either writer never creates duplicates.

bar_time is stored NAIVE (exchange wall-clock as exported by NinjaTrader, ET).
Both writers parse identically so dedup is exact on SQLite (tests) and Postgres.

LX-6 — CONVENCIÓN CANÓNICA: ET-naive wall-clock (la del CSV) en TODO el ciclo.
  · backfill (scripts/backfill_market_bars.py): lee el DateTime del CSV de
    NinjaTrader, que YA es ET-naive → correcto.
  · MarketBarsUpdater: guarda el campo `time` de los JSON del bridge .NET. El
    bridge DEBE entregar ET (hora del exchange, igual que el export CSV). Si un
    símbolo entra en UTC (verificar con scripts/audit_ohlcv_tz.py), el escritor
    debe convertir a ET ANTES de persistir — el desalineo envenena el intrabar
    de la costura (ver diagnóstico LX-6).
  · ALMACÉN: la columna original era `timestamptz` (DateTime(timezone=True)):
    guardar un naive ahí dejaba que Postgres impusiera un instante según el
    `TimeZone` de la sesión → corrupción heterogénea (mezcla ET/UTC por época de
    ingesta). La migración b8c9d0e1f2a3 (Pieza 1) la pasó a `timestamp` naive y
    YA ESTÁ APLICADA en producción — se CONSERVA en el repo (el alembic_version
    del server apunta a ella). Con la tabla ahora JUBILADA (CSV-only) el tipo de
    columna deja de importar: la patología queda inerte al no leerse la tabla.
  · LECTURA (retirada): _et_naive(bar_time) normalizaba aware→NY→naive.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ohlcv_bar import OhlcvBar

PROVIDER = "ninjatrader"
_CONFLICT_COLS = ["symbol", "timeframe", "bar_time", "provider"]
# asyncpg caps a single statement at 32767 bind parameters. Each row binds 11
# columns (id + 9 fields + created_at), so keep inserts well under that limit.
_MAX_ROWS_PER_INSERT = 2000


def parse_bar_time(raw: object) -> datetime | None:
    """Parse a bar timestamp into a NAIVE datetime.

    Accepts CSV form 'YYYY-MM-DD HH:MM:SS', bridge form 'YYYY-MM-DDTHH:MM:SS',
    or a datetime (tz stripped). Returns None if unparseable.
    """
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None)
    if raw is None:
        return None
    s = str(raw).strip().replace("T", " ")
    if not s:
        return None
    try:
        return datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _dialect_name(db: AsyncSession) -> str:
    """Resolve the bind's dialect name across sync/async engine shapes."""
    bind = db.get_bind()
    dialect = getattr(bind, "dialect", None)
    if dialect is None:
        sync = getattr(bind, "sync_engine", None)
        dialect = getattr(sync, "dialect", None)
    return dialect.name if dialect is not None else "sqlite"


def _rows_from_bars(symbol: str, timeframe: str, bars: list[dict], provider: str) -> list[dict]:
    """Build insert rows from canonical bar dicts {time,open,high,low,close,volume}."""
    rows: list[dict] = []
    for b in bars or []:
        ts = parse_bar_time(b.get("time"))
        if ts is None:
            continue
        try:
            rows.append({
                "symbol": symbol, "timeframe": timeframe, "bar_time": ts,
                "open": float(b["open"]), "high": float(b["high"]),
                "low": float(b["low"]), "close": float(b["close"]),
                "volume": float(b.get("volume", 0) or 0),
                "provider": provider,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return rows


async def persist_bars(
    db: AsyncSession,
    symbol: str,
    timeframe: str,
    bars: list[dict],
    provider: str = PROVIDER,
) -> int:
    """Idempotently insert OHLCV bars. Returns the number of NEW rows inserted.

    Uses INSERT ... ON CONFLICT DO NOTHING on the unique constraint, so it is
    safe to call repeatedly with overlapping bar windows (the live feed always
    overlaps the previous run).
    """
    rows = _rows_from_bars(symbol, timeframe, bars, provider)
    if not rows:
        return 0

    if _dialect_name(db) == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:
        from sqlalchemy.dialects.sqlite import insert as _insert

    inserted = 0
    for i in range(0, len(rows), _MAX_ROWS_PER_INSERT):
        batch = rows[i:i + _MAX_ROWS_PER_INSERT]
        stmt = _insert(OhlcvBar).values(batch).on_conflict_do_nothing(
            index_elements=_CONFLICT_COLS
        )
        result = await db.execute(stmt)
        rc = result.rowcount
        inserted += rc if rc and rc > 0 else 0
    return inserted


async def count_bars(
    db: AsyncSession, symbol: str, timeframe: str, provider: str = PROVIDER
) -> int:
    """Count stored bars for a symbol/timeframe (for verification / status)."""
    res = await db.execute(
        select(func.count()).select_from(OhlcvBar).where(
            OhlcvBar.symbol == symbol,
            OhlcvBar.timeframe == timeframe,
            OhlcvBar.provider == provider,
        )
    )
    return int(res.scalar_one())


async def get_training_bars(
    db: AsyncSession,
    symbol: str,
    timeframe: str,
    provider: str = PROVIDER,
    limit: int | None = None,
) -> tuple[list[float], list[float]]:
    """Return (closes, volumes) ordered oldest→newest for HMM training/inference.

    `limit` caps to the most recent N bars (None = full history).
    """
    stmt = (
        select(OhlcvBar.close, OhlcvBar.volume)
        .where(
            OhlcvBar.symbol == symbol,
            OhlcvBar.timeframe == timeframe,
            OhlcvBar.provider == provider,
        )
        .order_by(OhlcvBar.bar_time.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    res = await db.execute(stmt)
    rows = list(res.all())
    rows.reverse()  # back to oldest→newest
    closes = [float(r[0]) for r in rows if r[0] is not None]
    volumes = [float(r[1]) if r[1] is not None else 0.0 for r in rows]
    return closes, volumes


async def latest_bar_time(
    db: AsyncSession, symbol: str, timeframe: str, provider: str = PROVIDER
) -> datetime | None:
    """Most recent stored bar_time, or None — useful to report freshness."""
    res = await db.execute(
        select(func.max(OhlcvBar.bar_time)).where(
            OhlcvBar.symbol == symbol,
            OhlcvBar.timeframe == timeframe,
            OhlcvBar.provider == provider,
        )
    )
    return res.scalar_one_or_none()
