"""TradersPostClient — sends approved payloads via httpx with retry logic.

Contract (REQ-0601):
  - httpx async, timeout 10s
  - Entries: max 3 attempts, backoff 1s/2s/4s; do NOT retry if signal older
    than entry_signal_timeout_secs (default 30s).
  - Exits: up to 10 attempts (exits are critical); always retry regardless of age.
  - dry_run=True → DRY_RUN result, no HTTP call.
  - URL token always masked in logs/storage.
  - Never raises — always returns a WebhookDeliveryResult.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from loguru import logger

from app.services.tp_format import dumps as tp_dumps

_EXIT_ROLES = {"exit_long", "exit_short"}

# Mask any token=... query value (keeps the key, hides the secret)
_TOKEN_RE = re.compile(r"(token=)[^&\s]+", re.IGNORECASE)

_HTTP_TIMEOUT = 10.0


@dataclass
class WebhookDeliveryResult:
    status: str  # DRY_RUN / SENT / FAILED
    payload_json: dict
    url_masked: str
    response_status_code: int | None = None
    response_body: str | None = None
    attempts: int = 0
    latency_ms: int | None = None
    error_message: str | None = None


def mask_token(url: str) -> str:
    """Replace the token query value with *** so secrets never hit logs/DB."""
    if not url:
        return ""
    return _TOKEN_RE.sub(r"\1***", url)


class TradersPostClient:
    def __init__(self, settings: object) -> None:
        self.settings = settings

    async def send(
        self,
        webhook_url: str,
        payload: dict,
        signal_role: str,
        dry_run: bool,
        signal_ts: datetime | None = None,
        retry_attempts: int | None = None,
        backoff_seconds: float | None = None,
        entry_timeout_secs: float | None = None,
    ) -> WebhookDeliveryResult:
        """Send payload to TradersPost. Never raises.

        Args:
            webhook_url: Full TradersPost webhook URL (with token).
            payload: JSON-serializable dict from PayloadBuilder.
            signal_role: Used to classify exit vs entry retry policy.
            dry_run: If True, no HTTP call is made.
            signal_ts: Signal timestamp, used for entry staleness check.
            retry_attempts: NX-15 — max attempts for ENTRIES (default 3).
                EXITS always keep 10 attempts (critical, config can't lower).
            backoff_seconds: NX-15 — backoff base (default 1 → 1s/2s/4s,
                capped at 4×base).
            entry_timeout_secs: NX-15 — entry staleness threshold (default 30,
                era el env inexistente ENTRY_SIGNAL_TIMEOUT_SECS).
        """
        url_masked = mask_token(webhook_url)
        is_exit = signal_role in _EXIT_ROLES

        # DRY_RUN short-circuit — no HTTP
        if dry_run:
            logger.info("traderspost_dry_run role={} url={}", signal_role, url_masked)
            return WebhookDeliveryResult(
                status="DRY_RUN",
                payload_json=payload,
                url_masked=url_masked,
                attempts=0,
            )

        if not webhook_url:
            return WebhookDeliveryResult(
                status="FAILED",
                payload_json=payload,
                url_masked=url_masked,
                attempts=0,
                error_message="no_webhook_url_configured",
            )

        if is_exit:
            max_attempts = 10   # exits are critical — config can't lower this
        else:
            max_attempts = max(1, int(retry_attempts)) if retry_attempts else 3
        timeout_secs = entry_timeout_secs \
            or getattr(self.settings, "entry_signal_timeout_secs", None) \
            or getattr(self.settings, "ENTRY_SIGNAL_TIMEOUT_SECS", 30)
        backoff_base = float(backoff_seconds) if backoff_seconds else 1.0

        # Entry staleness: if too old, allow a single attempt but no retries.
        if not is_exit and signal_ts is not None:
            age = (datetime.now(timezone.utc) - _as_utc(signal_ts)).total_seconds()
            if age > timeout_secs:
                logger.warning(
                    "entry_signal_stale age={:.1f}s timeout={}s — single attempt, no retry",
                    age, timeout_secs,
                )
                max_attempts = 1

        headers = {"Content-Type": "application/json"}
        last_status: int | None = None
        last_body: str | None = None
        last_error: str | None = None
        start = time.monotonic()

        # FIX-D2 — serialize with tp_format.dumps (fixed-decimal, NEVER scientific
        # notation) via content=, not json=payload. httpx's json= uses stdlib
        # json.dumps, which renders deep-decimal FX prices/atr as e.g. "5e-07" and
        # TradersPost misparses those. For payloads without such floats the bytes are
        # identical, so ES/GC never regress. Content-Type is set in `headers`.
        body = tp_dumps(payload)

        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    resp = await client.post(webhook_url, content=body, headers=headers)
                    last_status = resp.status_code
                    last_body = resp.text[:2000] if resp.text else None
                    if 200 <= resp.status_code < 300:
                        latency_ms = int((time.monotonic() - start) * 1000)
                        logger.info(
                            "traderspost_sent role={} status={} attempts={} url={}",
                            signal_role, resp.status_code, attempt, url_masked,
                        )
                        return WebhookDeliveryResult(
                            status="SENT",
                            payload_json=payload,
                            url_masked=url_masked,
                            response_status_code=resp.status_code,
                            response_body=last_body,
                            attempts=attempt,
                            latency_ms=latency_ms,
                        )
                    # Non-2xx → will retry if attempts remain
                    last_error = f"http_{resp.status_code}"
                except Exception as exc:
                    last_status = None
                    last_error = type(exc).__name__
                    logger.warning(
                        "traderspost_attempt_failed role={} attempt={} error={}",
                        signal_role, attempt, exc,
                    )

                # Backoff before next attempt (capped at 4×base; exits keep
                # retrying at the cap)
                if attempt < max_attempts:
                    await asyncio.sleep(self._backoff(attempt, backoff_base))

        latency_ms = int((time.monotonic() - start) * 1000)
        logger.error(
            "traderspost_failed role={} attempts={} last_error={} url={}",
            signal_role, max_attempts, last_error, url_masked,
        )
        return WebhookDeliveryResult(
            status="FAILED",
            payload_json=payload,
            url_masked=url_masked,
            response_status_code=last_status,
            response_body=last_body,
            attempts=max_attempts,
            latency_ms=latency_ms,
            error_message=last_error,
        )

    @staticmethod
    def _backoff(attempt: int, base: float = 1.0) -> float:
        """Exponential backoff: base·2^(n−1), capped at 4×base.

        Con base=1 (default): 1s, 2s, 4s, 4s, ... (comportamiento histórico).
        """
        return float(min(base * (2 ** (attempt - 1)), 4 * base))


def _as_utc(dt: datetime) -> datetime:
    """Treat naive datetimes as UTC (SQLite drops tzinfo on round-trip)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
