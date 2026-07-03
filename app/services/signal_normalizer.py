"""SignalNormalizer — converts raw LuxAlgo payload into a NormalizedSignal.

ticker_received is stored EXACTLY as payload["ticker"] — no strip, no upper,
no prefix logic. Any transformation here would break the symbol mapper lookup.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal
from app.services.symbol_mapper import SymbolMapper

_TF_MAP: dict[str, str] = {
    "1": "1m",
    "3": "3m",
    "5": "5m",
    "10": "10m",
    "15": "15m",
    "30": "30m",
    "45": "45m",
    "60": "1h",
    "120": "2h",
    "180": "3h",
    "240": "4h",
    "480": "8h",
    "720": "12h",
    "D": "1d",
    "W": "1w",
    "M": "1M",
}


def _normalize_timeframe(interval: str) -> str:
    return _TF_MAP.get(str(interval), interval)


def _parse_signal_time(raw: object) -> datetime | None:
    """NX-16 — parsea el `time` del payload de TradingView a un datetime UTC.

    Acepta ISO 8601 ("2026-07-02T14:30:00Z", con offset, o naive → UTC),
    la forma con espacio ("2026-07-02 14:30:00") y epoch (s o ms).
    Devuelve None si no es parseable (el caller cae a la hora de recepción).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.isdigit():
        try:
            v = int(s)
            if v > 10**12:      # epoch en milisegundos
                v = v / 1000.0
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (ValueError, OverflowError, OSError):
            return None
    iso = s.replace("Z", "+00:00")
    if " " in iso and "T" not in iso:
        iso = iso.replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def make_dedupe_key(
    strategy_id: str,
    ticker_received: str,
    action: str,
    sentiment: str,
    price_raw: str,
    interval_raw: str,
) -> str:
    """SHA256 over the signal's identity fields.

    Deterministic: same payload + strategy_id always produces the same key.
    Used by Deduplicator to detect repeated signals within the time window.
    """
    raw = f"{strategy_id}:{ticker_received}:{action}:{sentiment}:{price_raw}:{interval_raw}"
    return hashlib.sha256(raw.encode()).hexdigest()


class SignalNormalizer:
    def __init__(self, symbol_mapper: SymbolMapper | None = None) -> None:
        self._mapper = symbol_mapper or SymbolMapper()

    async def normalize(
        self,
        db: AsyncSession,
        raw_signal_id: uuid.UUID,
        strategy_id: str,
        payload: dict,
    ) -> NormalizedSignal:
        """Return an unsaved NormalizedSignal ready to add to the session.

        strategy_id comes from the URL path — NEVER from the payload.
        ticker_received is copied verbatim from payload["ticker"].
        """
        # CRITICAL: zero-modification copy
        ticker_received: str = payload.get("ticker", "")

        raw_sentiment: str = payload.get("sentiment", "")
        raw_action: str = payload.get("action", "")

        # Sentiment → canonical action + signal_role
        if raw_sentiment == "flat":
            action = "exit"
            # Phase 1 default; refined when position state lookup is available
            signal_role = "exit_long"
        elif raw_sentiment == "long":
            action = "buy"
            signal_role = "entry_long"
        elif raw_sentiment == "short":
            action = "sell"
            signal_role = "entry_short"
        else:
            action = raw_action or "unknown"
            signal_role = "unknown"

        # quantity: payload sends strings, model expects int
        try:
            quantity = int(payload.get("quantity", "1"))
        except (ValueError, TypeError):
            quantity = 1

        # price: payload sends strings, model expects float.
        # NX-05 (fail-closed): ausente / no parseable / <= 0 → None, NUNCA 0.0.
        # Un 0.0 aquí producía un SL absurdo (0 − ATR×k) con passed=True en N5.
        price_raw_str = str(payload.get("price", "0"))
        try:
            price = float(price_raw_str)
        except (ValueError, TypeError):
            price = None
        if price is not None and price <= 0:
            price = None

        interval_raw = str(payload.get("interval", ""))
        timeframe = _normalize_timeframe(interval_raw)

        # Symbol mapper: direct DB lookup, exact ticker_received as key
        mapped_symbol = await self._mapper.map_symbol(db, ticker_received)

        dedupe_key = make_dedupe_key(
            strategy_id, ticker_received, action, raw_sentiment,
            price_raw_str, interval_raw,
        )

        # NX-16 — signal_ts es la hora REAL de la señal (payload["time"] de
        # TradingView); solo si falta o no parsea se usa la recepción. Así la
        # frescura (L2) y el timeout de entrada del cliente miden la edad
        # verdadera, no la latencia interna. normalized_at conserva la
        # recepción para diagnóstico.
        signal_ts = _parse_signal_time(payload.get("time")) or datetime.now(
            timezone.utc
        )

        return NormalizedSignal(
            raw_signal_id=raw_signal_id,
            source="luxalgo",
            strategy_id=strategy_id,
            ticker_received=ticker_received,
            mapped_symbol=mapped_symbol,
            action=action,
            sentiment=raw_sentiment,
            quantity=quantity,
            price=price,
            timeframe=timeframe,
            signal_ts=signal_ts,
            signal_role=signal_role,
            dedupe_key=dedupe_key,
            status="pending",
        )
