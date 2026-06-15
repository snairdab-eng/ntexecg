"""Webhook endpoint and process_signal integration tests.

HTTP tests (client fixture):
  - Invalid token → 401
  - Valid token → 200
  - RawSignal saved after valid webhook

Background-logic tests (db fixture, call process_signal directly):
  - Duplicate signal within 60s → IGNORE_DUPLICATE
  - Unknown strategy_id → strategy auto-created as candidate + QUEUE_FOR_REVIEW

All tests use SQLite in-memory. Background task (_background_process_signal)
is patched to a no-op in HTTP tests to avoid PostgreSQL connection attempts.
"""
import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.services.repositories import get_strategy_by_id


# ---------------------------------------------------------------------------
# Shared test payload
# ---------------------------------------------------------------------------

_PAYLOAD = {
    "ticker": "MES",
    "action": "buy",
    "sentiment": "long",
    "quantity": "1",
    "price": "5500.00",
    "interval": "5",
}

_VALID_TOKEN = "dev_global_token"   # matches settings.LUXALGO_WEBHOOK_SECRET
_INVALID_TOKEN = "wrong_token_xyz"
_STRATEGY_ID = "test_strat_webhook"


# ---------------------------------------------------------------------------
# HTTP-level tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_token_returns_401(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr("app.api.webhooks_luxalgo._background_process_signal", _noop)

    response = await client.post(
        f"/webhooks/luxalgo/{_STRATEGY_ID}?token={_INVALID_TOKEN}",
        json=_PAYLOAD,
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_returns_200(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr("app.api.webhooks_luxalgo._background_process_signal", _noop)

    response = await client.post(
        f"/webhooks/luxalgo/{_STRATEGY_ID}?token={_VALID_TOKEN}",
        json=_PAYLOAD,
    )
    assert response.status_code == 200
    data = response.json()
    assert data["received"] is True
    assert "signal_id" in data
    # signal_id must be a valid UUID
    uuid.UUID(data["signal_id"])


@pytest.mark.asyncio
async def test_raw_signal_saved_after_valid_webhook(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr("app.api.webhooks_luxalgo._background_process_signal", _noop)

    response = await client.post(
        f"/webhooks/luxalgo/{_STRATEGY_ID}?token={_VALID_TOKEN}",
        json=_PAYLOAD,
    )
    assert response.status_code == 200

    # The webhook handler commits to the shared test DB session
    result = await db.execute(
        select(RawSignal).where(RawSignal.strategy_id == _STRATEGY_ID)
    )
    raw = result.scalar_one_or_none()
    assert raw is not None
    assert raw.ticker_received == "MES"
    assert raw.token_valid is True


@pytest.mark.asyncio
async def test_raw_signal_saved_on_invalid_token(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RawSignal is persisted even when the token is invalid (audit trail)."""
    async def _noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr("app.api.webhooks_luxalgo._background_process_signal", _noop)

    response = await client.post(
        f"/webhooks/luxalgo/bad_token_strat?token={_INVALID_TOKEN}",
        json=_PAYLOAD,
    )
    assert response.status_code == 401

    result = await db.execute(
        select(RawSignal).where(RawSignal.strategy_id == "bad_token_strat")
    )
    raw = result.scalar_one_or_none()
    assert raw is not None
    assert raw.token_valid is False


# ---------------------------------------------------------------------------
# process_signal direct tests (no HTTP)
# ---------------------------------------------------------------------------

async def _make_raw(db: AsyncSession, strategy_id: str) -> RawSignal:
    raw = RawSignal(
        strategy_id=strategy_id,
        payload_json=_PAYLOAD,
        token_valid=True,
    )
    db.add(raw)
    await db.flush()
    return raw


@pytest.mark.asyncio
async def test_process_signal_approve_for_live_strategy(db: AsyncSession) -> None:
    from app.models.strategy import Strategy

    strategy = Strategy(
        strategy_id="live_strat",
        name="Live Strategy",
        asset_symbol="MES",
        status="live",
        enabled=True,
    )
    db.add(strategy)
    await db.flush()

    raw = await _make_raw(db, "live_strat")
    decision = await process_signal(db, "live_strat", raw.id, _PAYLOAD)
    assert decision.outcome == "APPROVE"
    assert decision.score == 100


@pytest.mark.asyncio
async def test_process_signal_queue_for_candidate_strategy(db: AsyncSession) -> None:
    from app.models.strategy import Strategy

    strategy = Strategy(
        strategy_id="cand_strat",
        name="Candidate",
        asset_symbol="MES",
        status="candidate",
        enabled=False,
    )
    db.add(strategy)
    await db.flush()

    raw = await _make_raw(db, "cand_strat")
    decision = await process_signal(db, "cand_strat", raw.id, _PAYLOAD)
    assert decision.outcome == "QUEUE_FOR_REVIEW"
    assert decision.block_reason == "strategy_candidate"


@pytest.mark.asyncio
async def test_process_signal_block_for_retired_strategy(db: AsyncSession) -> None:
    from app.models.strategy import Strategy

    strategy = Strategy(
        strategy_id="ret_strat",
        name="Retired",
        asset_symbol="MES",
        status="retired",
        enabled=False,
    )
    db.add(strategy)
    await db.flush()

    raw = await _make_raw(db, "ret_strat")
    decision = await process_signal(db, "ret_strat", raw.id, _PAYLOAD)
    assert decision.outcome == "BLOCK"
    assert decision.block_reason == "strategy_retired"


@pytest.mark.asyncio
async def test_unknown_strategy_auto_created_as_candidate(db: AsyncSession) -> None:
    """When strategy_id is not in the DB, it should be auto-created as candidate."""
    raw = await _make_raw(db, "brand_new_strat")
    decision = await process_signal(db, "brand_new_strat", raw.id, _PAYLOAD)

    assert decision.outcome == "QUEUE_FOR_REVIEW"
    assert decision.block_reason == "strategy_candidate"

    created = await get_strategy_by_id(db, "brand_new_strat")
    assert created is not None
    assert created.status == "candidate"
    assert created.enabled is False


@pytest.mark.asyncio
async def test_duplicate_signal_within_60s(db: AsyncSession) -> None:
    """Second signal with identical dedupe_key within the window → IGNORE_DUPLICATE."""
    raw1 = await _make_raw(db, "dedup_strat")
    decision1 = await process_signal(db, "dedup_strat", raw1.id, _PAYLOAD)
    # First signal should not be a duplicate
    assert decision1.outcome != "IGNORE_DUPLICATE"

    # Same payload + strategy → same dedupe_key
    raw2 = await _make_raw(db, "dedup_strat")
    decision2 = await process_signal(db, "dedup_strat", raw2.id, _PAYLOAD)
    assert decision2.outcome == "IGNORE_DUPLICATE"
    assert decision2.block_reason == "duplicate_signal"


@pytest.mark.asyncio
async def test_different_ticker_not_duplicate(db: AsyncSession) -> None:
    """Different ticker → different dedupe_key → not a duplicate."""
    raw1 = await _make_raw(db, "nd_strat")
    payload_mes = {**_PAYLOAD, "ticker": "MES"}
    await process_signal(db, "nd_strat", raw1.id, payload_mes)

    raw2 = await _make_raw(db, "nd_strat")
    payload_mnq = {**_PAYLOAD, "ticker": "MNQ"}
    decision2 = await process_signal(db, "nd_strat", raw2.id, payload_mnq)
    assert decision2.outcome != "IGNORE_DUPLICATE"


@pytest.mark.asyncio
async def test_normalized_signal_created(db: AsyncSession) -> None:
    """process_signal always creates a NormalizedSignal."""
    raw = await _make_raw(db, "norm_strat")
    await process_signal(db, "norm_strat", raw.id, _PAYLOAD)

    result = await db.execute(
        select(NormalizedSignal).where(NormalizedSignal.strategy_id == "norm_strat")
    )
    norm = result.scalar_one_or_none()
    assert norm is not None
    assert norm.ticker_received == "MES"


@pytest.mark.asyncio
async def test_decision_linked_to_normalized_signal(db: AsyncSession) -> None:
    """StrategyDecision.normalized_signal_id must reference the NormalizedSignal."""
    raw = await _make_raw(db, "link_strat")
    decision = await process_signal(db, "link_strat", raw.id, _PAYLOAD)

    result = await db.execute(
        select(NormalizedSignal).where(
            NormalizedSignal.id == decision.normalized_signal_id
        )
    )
    norm = result.scalar_one_or_none()
    assert norm is not None
