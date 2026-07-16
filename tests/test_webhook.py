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
from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.services.market_data_service import MarketDataService
from app.services.repositories import get_strategy_by_id


# Injected MarketDataService for direct process_signal tests — NEVER real yfinance.
# get_atr→8.0, is_active→True (matches conftest.MockMarketDataProvider).
class _MockMD:
    async def get_bars(self, *a, **kw) -> list:
        return []

    async def get_atr(self, *a, **kw) -> float:
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return True


_MD = MarketDataService(_MockMD())


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
async def test_invalid_token_within_cap_is_quarantined(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIX-D1 (migrado) — un rechazado DENTRO de la cota se cuarentena: se persiste
    la traza forense (RawSignal token_valid=False + AuditLog WEBHOOK_BLOCKED)."""
    async def _noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr("app.api.webhooks_luxalgo._background_process_signal", _noop)

    response = await client.post(
        f"/webhooks/luxalgo/bad_token_strat?token={_INVALID_TOKEN}",
        json=_PAYLOAD,
    )
    assert response.status_code == 401

    raw = (await db.execute(
        select(RawSignal).where(RawSignal.strategy_id == "bad_token_strat")
    )).scalar_one_or_none()
    assert raw is not None
    assert raw.token_valid is False
    # traza forense: AuditLog WEBHOOK_BLOCKED con quién/por qué
    audit = (await db.execute(
        select(AuditLog).where(AuditLog.action == "WEBHOOK_BLOCKED",
                               AuditLog.object_id == "bad_token_strat")
    )).scalars().first()
    assert audit is not None and audit.reason == "invalid_token"


@pytest.mark.asyncio
async def test_invalid_token_flood_capped_no_db_growth(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIX-D1 — pasada la cota por IP/ventana, los rechazos siguen dando 401 pero NO
    escriben fila (RawSignal ni AuditLog): el flood a DB queda tapado."""
    from app.core import quarantine_guard as qg

    async def _noop(*args, **kwargs) -> None:
        pass

    monkeypatch.setattr("app.api.webhooks_luxalgo._background_process_signal", _noop)

    sid = "flood_strat"
    cap = qg.QUARANTINE_MAX_PER_WINDOW
    for _ in range(cap + 5):                       # cap + exceso, misma IP (test client)
        r = await client.post(
            f"/webhooks/luxalgo/{sid}?token={_INVALID_TOKEN}", json=_PAYLOAD)
        assert r.status_code == 401                # 401 SIEMPRE, con o sin cota

    raws = (await db.execute(
        select(RawSignal).where(RawSignal.strategy_id == sid))).scalars().all()
    audits = (await db.execute(
        select(AuditLog).where(AuditLog.action == "WEBHOOK_BLOCKED",
                               AuditLog.object_id == sid))).scalars().all()
    assert len(raws) == cap                        # exactamente la cota, ni una más
    assert len(audits) == cap                      # audit gobernado por la misma cota


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
    from app.models.symbol_map import SymbolMap

    # A live strategy can only APPROVE if the ticker maps to an active contract.
    # Without this SymbolMap the pipeline correctly BLOCKs at level 1.4
    # (symbol_not_mapped) — that is the contract behavior, not an APPROVE.
    db.add(SymbolMap(
        tv_symbol="MES",
        mapped_symbol="MESU2025",
        exchange="CME",
        contract_type="futures_micro",
        pine_script_config='"ticker": "MES"',
        active=True,
    ))
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
    decision = await process_signal(db, "live_strat", raw.id, _PAYLOAD, _MD)
    assert decision.outcome == "APPROVE"
    assert decision.score == 100
    # Approved entries MUST carry a calculated SL (contract rule 6)
    assert decision.sl_price is not None


@pytest.mark.asyncio
async def test_approved_entry_dispatches_dry_run_and_advances_position(
    db: AsyncSession,
) -> None:
    """End-to-end: APPROVE → DRY_RUN WebhookDelivery + position → PENDING_LONG.

    dry_run defaults True (no GlobalProfile in test DB), so no HTTP is attempted.
    """
    from app.models.strategy import Strategy
    from app.models.symbol_map import SymbolMap
    from app.models.webhook_delivery import WebhookDelivery
    from app.models.position_state import PositionState

    db.add(SymbolMap(
        tv_symbol="MES", mapped_symbol="MESU2025", exchange="CME",
        contract_type="futures_micro", pine_script_config='"ticker": "MES"',
        active=True,
    ))
    db.add(Strategy(
        strategy_id="disp_strat", name="Dispatch Strat", asset_symbol="MES",
        status="live", enabled=True,
    ))
    await db.flush()

    raw = await _make_raw(db, "disp_strat")
    decision = await process_signal(db, "disp_strat", raw.id, _PAYLOAD, _MD)
    assert decision.outcome == "APPROVE"

    # WebhookDelivery recorded as DRY_RUN, token never stored raw
    result = await db.execute(
        select(WebhookDelivery).where(WebhookDelivery.strategy_id == "disp_strat")
    )
    delivery = result.scalar_one_or_none()
    assert delivery is not None
    assert delivery.status == "DRY_RUN"
    assert delivery.decision_id == decision.id

    # Estimated position advanced to PENDING_LONG (buy entry)
    result = await db.execute(
        select(PositionState).where(PositionState.symbol == "MESU2025")
    )
    position = result.scalar_one_or_none()
    assert position is not None
    assert position.state == "PENDING_LONG"
    assert position.state_source == "estimated"


@pytest.mark.asyncio
async def test_asset_profile_config_applied_via_ticker_received(
    db: AsyncSession,
) -> None:
    """Regression: ConfigResolver must look up AssetProfile by ticker_received
    ("MES"), not mapped_symbol ("MESU2025"). Otherwise asset-level config
    (here sl_atr_multiplier=2.0) is silently dropped and the default 1.5 is used.
    """
    from app.models.strategy import Strategy
    from app.models.symbol_map import SymbolMap
    from app.models.asset_profile import AssetProfile

    db.add(SymbolMap(
        tv_symbol="MES", mapped_symbol="MESU2025", exchange="CME",
        contract_type="futures_micro", pine_script_config='"ticker": "MES"',
        active=True,
    ))
    db.add(AssetProfile(
        symbol="MES", name="Micro S&P", contract_type="futures_micro",
        sl_atr_multiplier=2.0,  # asset-level override; default would be 1.5
    ))
    db.add(Strategy(
        strategy_id="asset_strat", name="Asset Strat", asset_symbol="MES",
        status="live", enabled=True,
    ))
    await db.flush()

    raw = await _make_raw(db, "asset_strat")
    decision = await process_signal(db, "asset_strat", raw.id, _PAYLOAD, _MD)

    # ATR mock=8.0, multiplier must be the asset's 2.0 → SL = 5500 - 8*2 = 5484
    # If the bug regressed (default 1.5), SL would be 5488.
    assert decision.outcome == "APPROVE"
    assert float(decision.sl_price) == 5484.0


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
    decision = await process_signal(db, "cand_strat", raw.id, _PAYLOAD, _MD)
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
    decision = await process_signal(db, "ret_strat", raw.id, _PAYLOAD, _MD)
    assert decision.outcome == "BLOCK"
    assert decision.block_reason == "strategy_retired"


@pytest.mark.asyncio
async def test_unknown_strategy_auto_created_as_candidate(db: AsyncSession) -> None:
    """When strategy_id is not in the DB, it should be auto-created as candidate."""
    raw = await _make_raw(db, "brand_new_strat")
    decision = await process_signal(db, "brand_new_strat", raw.id, _PAYLOAD, _MD)

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
    decision1 = await process_signal(db, "dedup_strat", raw1.id, _PAYLOAD, _MD)
    # First signal should not be a duplicate
    assert decision1.outcome != "IGNORE_DUPLICATE"

    # Same payload + strategy → same dedupe_key
    raw2 = await _make_raw(db, "dedup_strat")
    decision2 = await process_signal(db, "dedup_strat", raw2.id, _PAYLOAD, _MD)
    assert decision2.outcome == "IGNORE_DUPLICATE"
    assert decision2.block_reason == "duplicate_signal"


@pytest.mark.asyncio
async def test_different_ticker_not_duplicate(db: AsyncSession) -> None:
    """Different ticker → different dedupe_key → not a duplicate."""
    raw1 = await _make_raw(db, "nd_strat")
    payload_mes = {**_PAYLOAD, "ticker": "MES"}
    await process_signal(db, "nd_strat", raw1.id, payload_mes, _MD)

    raw2 = await _make_raw(db, "nd_strat")
    payload_mnq = {**_PAYLOAD, "ticker": "MNQ"}
    decision2 = await process_signal(db, "nd_strat", raw2.id, payload_mnq, _MD)
    assert decision2.outcome != "IGNORE_DUPLICATE"


@pytest.mark.asyncio
async def test_legit_repeat_outside_window_rekeyed_not_crash(
    db: AsyncSession,
) -> None:
    """Regression: a signal whose content dedupe_key already exists in the DB
    but OUTSIDE the 60s dedup window is a LEGITIMATE new signal, not a
    duplicate.

    make_dedupe_key is content-only (no timestamp) while the dedupe_key UNIQUE
    constraint is permanent. Before the fix, the second occurrence of the same
    strategy/ticker/action/price/interval hours later passed is_duplicate()
    (old row outside the window) and then crashed on the UNIQUE constraint with
    no decision written. It must now be stored under a fresh "rk:" key and
    produce a real decision.
    """
    from datetime import datetime, timedelta, timezone
    from app.services.signal_normalizer import make_dedupe_key

    content_key = make_dedupe_key(
        "stale_strat", "MES", "buy", "long", "5500.00", "5",
    )
    seed_raw = await _make_raw(db, "stale_strat")
    stale = NormalizedSignal(
        raw_signal_id=seed_raw.id,
        source="luxalgo",
        strategy_id="stale_strat",
        ticker_received="MES",
        action="buy",
        sentiment="long",
        quantity=1,
        price=5500.0,
        timeframe="5m",
        signal_ts=datetime.now(timezone.utc),
        signal_role="entry_long",
        dedupe_key=content_key,
        status="processed",
    )
    stale.created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.add(stale)
    await db.flush()

    raw = await _make_raw(db, "stale_strat")
    # Would previously raise UniqueViolationError → no decision.
    decision = await process_signal(db, "stale_strat", raw.id, _PAYLOAD, _MD)

    assert decision.outcome != "IGNORE_DUPLICATE"
    stored = await db.get(NormalizedSignal, decision.normalized_signal_id)
    assert stored is not None
    assert stored.dedupe_key.startswith("rk:")


@pytest.mark.asyncio
async def test_normalized_signal_created(db: AsyncSession) -> None:
    """process_signal always creates a NormalizedSignal."""
    raw = await _make_raw(db, "norm_strat")
    await process_signal(db, "norm_strat", raw.id, _PAYLOAD, _MD)

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
    decision = await process_signal(db, "link_strat", raw.id, _PAYLOAD, _MD)

    result = await db.execute(
        select(NormalizedSignal).where(
            NormalizedSignal.id == decision.normalized_signal_id
        )
    )
    norm = result.scalar_one_or_none()
    assert norm is not None


@pytest.mark.asyncio
async def test_per_strategy_token(
    client: AsyncClient, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A strategy with its own webhook_token accepts only that token."""
    async def _noop(*args, **kwargs) -> None:
        pass
    monkeypatch.setattr("app.api.webhooks_luxalgo._background_process_signal", _noop)

    db.add(Strategy(strategy_id="tok_strat", name="Tok",
                    webhook_token="sekret_abc_123"))
    await db.commit()

    ok = await client.post(
        "/webhooks/luxalgo/tok_strat?token=sekret_abc_123", json=_PAYLOAD)
    assert ok.status_code == 200

    bad = await client.post(
        "/webhooks/luxalgo/tok_strat?token=wrong", json=_PAYLOAD)
    assert bad.status_code == 401

    # The global secret must NOT work once a per-strategy token exists.
    glob = await client.post(
        "/webhooks/luxalgo/tok_strat?token=dev_global_token", json=_PAYLOAD)
    assert glob.status_code == 401


# ---------------------------------------------------------------------------
# Fase 3 — reversals + position-aware role classifier
# ---------------------------------------------------------------------------

from app.api.webhooks_luxalgo import _classify_role, _effective_direction

_SHORT_PAYLOAD = {
    "ticker": "MES", "action": "sell", "sentiment": "short",
    "quantity": "1", "price": "5500.00", "interval": "5",
}


def test_classify_role_table():
    assert _effective_direction("LONG") == "long"
    assert _effective_direction("PENDING_SHORT") == "short"
    assert _effective_direction("FLAT") is None
    # exit role follows current direction
    assert _classify_role("exit", "short") == "exit_short"
    assert _classify_role("exit", "long") == "exit_long"
    # opposite signal while in position → reversal
    assert _classify_role("sell", "long") == "reversal_to_short"
    assert _classify_role("buy", "short") == "reversal_to_long"
    # same/flat → plain entry
    assert _classify_role("buy", None) == "entry_long"
    assert _classify_role("sell", None) == "entry_short"


async def _setup_long(db, strategy_id, *, allow_reversal):
    from app.models.strategy import Strategy
    from app.models.symbol_map import SymbolMap
    from app.models.strategy_profile import StrategyProfile
    from app.models.position_state import PositionState
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2025", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True))
    db.add(Strategy(strategy_id=strategy_id, name="Rev", asset_symbol="MES",
                    status="live", enabled=True))
    db.add(StrategyProfile(strategy_id=strategy_id, mode="paper",
                           allow_reversal=allow_reversal))
    db.add(PositionState(strategy_id=strategy_id, account_id="paper_default",
                         symbol="MESU2025", state="LONG", direction="long",
                         quantity=1, state_source="estimated"))
    await db.flush()


@pytest.mark.asyncio
async def test_reversal_allowed_closes_then_opens(db: AsyncSession) -> None:
    from app.models.decision import StrategyDecision
    from app.models.position_state import PositionState
    await _setup_long(db, "rev_on", allow_reversal=True)
    raw = RawSignal(strategy_id="rev_on", payload_json=_SHORT_PAYLOAD, token_valid=True)
    db.add(raw)
    await db.flush()

    decision = await process_signal(db, "rev_on", raw.id, _SHORT_PAYLOAD, _MD)
    # The returned decision is the OPPOSITE entry, approved
    assert decision.outcome == "APPROVE"
    # A close (EXIT_ONLY) decision was created too
    exits = (await db.execute(select(StrategyDecision).where(
        StrategyDecision.strategy_id == "rev_on",
        StrategyDecision.outcome == "EXIT_ONLY"))).scalars().all()
    assert len(exits) == 1
    # Position flipped to PENDING_SHORT
    pos = (await db.execute(select(PositionState).where(
        PositionState.symbol == "MESU2025"))).scalar_one()
    assert pos.state == "PENDING_SHORT"


@pytest.mark.asyncio
async def test_reversal_not_allowed_closes_only(db: AsyncSession) -> None:
    from app.models.decision import StrategyDecision
    from app.models.position_state import PositionState
    await _setup_long(db, "rev_off", allow_reversal=False)
    raw = RawSignal(strategy_id="rev_off", payload_json=_SHORT_PAYLOAD, token_valid=True)
    db.add(raw)
    await db.flush()

    decision = await process_signal(db, "rev_off", raw.id, _SHORT_PAYLOAD, _MD)
    # Entry blocked: only the close happened
    assert decision.outcome == "BLOCK"
    assert decision.block_reason == "reversal_not_allowed"
    exits = (await db.execute(select(StrategyDecision).where(
        StrategyDecision.strategy_id == "rev_off",
        StrategyDecision.outcome == "EXIT_ONLY"))).scalars().all()
    assert len(exits) == 1
    # Position is EXITING (closed, not reopened)
    pos = (await db.execute(select(PositionState).where(
        PositionState.symbol == "MESU2025"))).scalar_one()
    assert pos.state == "EXITING"
