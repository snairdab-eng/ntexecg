"""TradersPostClient tests.

No real HTTP — httpx is mocked via monkeypatch. asyncio.sleep is patched
to avoid real backoff delays. URL masking is verified.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.traderspost_client import (
    TradersPostClient,
    WebhookDeliveryResult,
    mask_token,
)

_URL = "https://app.traderspost.io/trading/webhook/abc123?token=SECRET123"
_PAYLOAD = {"ticker": "MESU2025", "action": "buy"}


def _settings(timeout=30) -> SimpleNamespace:
    return SimpleNamespace(entry_signal_timeout_secs=timeout)


class _FakeResponse:
    def __init__(self, status_code: int, text: str = "ok") -> None:
        self.status_code = status_code
        self.text = text


class _FakeAsyncClient:
    """Stand-in for httpx.AsyncClient with a scripted sequence of responses."""

    _script: list = []
    calls: int = 0

    def __init__(self, *a, **kw) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, content=None, headers=None):
        _FakeAsyncClient.last_content = content        # FIX-D2: assert serialized body
        idx = _FakeAsyncClient.calls
        _FakeAsyncClient.calls += 1
        item = _FakeAsyncClient._script[min(idx, len(_FakeAsyncClient._script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Patch asyncio.sleep so retries don't actually wait."""
    async def _fast_sleep(*a, **kw):
        return None
    monkeypatch.setattr("app.services.traderspost_client.asyncio.sleep", _fast_sleep)


@pytest.fixture
def _patch_httpx(monkeypatch):
    def _apply(script: list):
        _FakeAsyncClient._script = script
        _FakeAsyncClient.calls = 0
        monkeypatch.setattr(
            "app.services.traderspost_client.httpx.AsyncClient", _FakeAsyncClient
        )
    return _apply


# ---------------------------------------------------------------------------
# URL masking
# ---------------------------------------------------------------------------

def test_mask_token_hides_secret() -> None:
    masked = mask_token(_URL)
    assert "SECRET123" not in masked
    assert "token=***" in masked
    assert "abc123" in masked  # path preserved


def test_mask_token_handles_empty() -> None:
    assert mask_token("") == ""


# ---------------------------------------------------------------------------
# DRY_RUN
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dry_run_no_http_call() -> None:
    client = TradersPostClient(_settings())
    result = await client.send(_URL, _PAYLOAD, "entry_long", dry_run=True)
    assert result.status == "DRY_RUN"
    assert result.attempts == 0
    assert "SECRET123" not in result.url_masked


# ---------------------------------------------------------------------------
# Success
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entry_sent_on_first_success(_patch_httpx) -> None:
    _patch_httpx([_FakeResponse(200)])
    client = TradersPostClient(_settings())
    result = await client.send(_URL, _PAYLOAD, "entry_long", dry_run=False)
    assert result.status == "SENT"
    assert result.attempts == 1
    assert result.response_status_code == 200


@pytest.mark.asyncio
async def test_entry_retries_then_succeeds(_patch_httpx) -> None:
    _patch_httpx([_FakeResponse(500), _FakeResponse(200)])
    client = TradersPostClient(_settings())
    result = await client.send(_URL, _PAYLOAD, "entry_long", dry_run=False)
    assert result.status == "SENT"
    assert result.attempts == 2


# ---------------------------------------------------------------------------
# Failure & retry limits
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entry_fails_after_3_attempts(_patch_httpx) -> None:
    _patch_httpx([_FakeResponse(500)])
    client = TradersPostClient(_settings())
    result = await client.send(_URL, _PAYLOAD, "entry_long", dry_run=False)
    assert result.status == "FAILED"
    assert result.attempts == 3  # entries: max 3


@pytest.mark.asyncio
async def test_exit_retries_up_to_10(_patch_httpx) -> None:
    _patch_httpx([_FakeResponse(503)])
    client = TradersPostClient(_settings())
    result = await client.send(_URL, _PAYLOAD, "exit_long", dry_run=False)
    assert result.status == "FAILED"
    assert result.attempts == 10  # exits: max 10 (critical)


@pytest.mark.asyncio
async def test_network_exception_is_retried_not_raised(_patch_httpx) -> None:
    _patch_httpx([RuntimeError("conn refused"), _FakeResponse(200)])
    client = TradersPostClient(_settings())
    result = await client.send(_URL, _PAYLOAD, "entry_long", dry_run=False)
    # Never raises; recovers on attempt 2
    assert result.status == "SENT"
    assert result.attempts == 2


# ---------------------------------------------------------------------------
# Entry staleness
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_entry_single_attempt_no_retry(_patch_httpx) -> None:
    _patch_httpx([_FakeResponse(500)])
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
    client = TradersPostClient(_settings(timeout=30))
    result = await client.send(
        _URL, _PAYLOAD, "entry_long", dry_run=False, signal_ts=old_ts
    )
    assert result.status == "FAILED"
    assert result.attempts == 1  # too old → no retries


@pytest.mark.asyncio
async def test_stale_exit_still_retries(_patch_httpx) -> None:
    """Exits are critical — age does not limit retries."""
    _patch_httpx([_FakeResponse(500)])
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=120)
    client = TradersPostClient(_settings(timeout=30))
    result = await client.send(
        _URL, _PAYLOAD, "exit_long", dry_run=False, signal_ts=old_ts
    )
    assert result.attempts == 10  # exit ignores staleness


# ---------------------------------------------------------------------------
# No URL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_url_fails_gracefully() -> None:
    client = TradersPostClient(_settings())
    result = await client.send("", _PAYLOAD, "entry_long", dry_run=False)
    assert result.status == "FAILED"
    assert result.error_message == "no_webhook_url_configured"
