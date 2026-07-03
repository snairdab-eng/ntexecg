"""Lote 6 — Config que cambia comportamiento (NX-13, NX-14, NX-15, NX-16).

NX-13: `force_flat_off` por estrategia — "sin cierre EOD" explícito (antes
       None = heredar y una estrategia 24h no podía escapar del 15:55 global).
NX-14: el pipeline CONSUME `atr_timeframe` en el Nivel 5 (antes era un knob
       decorativo: siempre se usaba el timeframe de la señal).
NX-15: reintentos/backoff/timeout de TradersPost desde GlobalProfile (antes
       hardcode 3 / 1-2-4 / 30 s; los exits conservan 10 intentos SIEMPRE).
NX-16: `signal_ts` viene del payload `time` de TradingView (antes = hora de
       recepción, así que staleness solo medía latencia interna).

Adversariales: fallan sin el fix.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.models.global_profile import GlobalProfile
from app.models.normalized_signal import NormalizedSignal
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.config_resolver import ConfigResolver
from app.services.exit_manager import ExitManager
from app.services.filter_pipeline import FilterPipeline
from app.services.market_data_service import MarketDataService
from app.services.signal_normalizer import SignalNormalizer
from app.services.traderspost_client import TradersPostClient

UTC = timezone.utc

_SESSION_OK = patch(
    "app.services.session_validator.SessionValidator.is_within_session_config",
    return_value=True,
)


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lote6")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


# ---------------------------------------------------------------------------
# NX-13 — force_flat_off (ADVERSARIAL)
# ---------------------------------------------------------------------------

async def _seed_eod(db: AsyncSession, *, off: bool) -> None:
    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True))
    # el default de columna pone force_flat_time=15:55 en la fila global
    db.add(Strategy(strategy_id="eod", name="EOD", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(
        strategy_id="eod", mode="paper",
        pipeline_config_json={"force_flat_off": True} if off else None,
    ))
    await db.commit()


@pytest.mark.asyncio
async def test_force_flat_off_disables_inherited_eod(db: AsyncSession):
    await _seed_eod(db, off=True)
    cfg = await ConfigResolver().resolve(db, "eod", "MES")
    assert cfg["force_flat_time"] is None, (
        f"force_flat_off ignorado: hereda {cfg['force_flat_time']} (bug NX-13 "
        "— una 24h no puede escapar del EOD global)")
    # y el ExitManager ya no dispara forced_close_eod a las 16:10 ET
    pos = type("P", (), {"state": "LONG", "risk_plan_json": None})()
    now = datetime(2026, 6, 17, 20, 10, tzinfo=UTC)   # 16:10 ET
    assert ExitManager().due_exit(pos, cfg, now=now) is None


@pytest.mark.asyncio
async def test_without_off_flag_inherits_global_eod(db: AsyncSession):
    await _seed_eod(db, off=False)
    cfg = await ConfigResolver().resolve(db, "eod", "MES")
    assert cfg["force_flat_time"] is not None   # hereda 15:55 (default global)


@pytest.mark.asyncio
async def test_ficha_saves_force_flat_off(client: AsyncClient, db: AsyncSession):
    await _seed_eod(db, off=False)
    r = await client.post("/ui/strategies/eod/ficha",
                          data={"force_flat_off": "1"})
    assert r.status_code in (200, 303)
    db.expire_all()
    p = (await db.execute(select(StrategyProfile).where(
        StrategyProfile.strategy_id == "eod"))).scalar_one()
    assert (p.pipeline_config_json or {}).get("force_flat_off") is True
    assert p.force_flat_time is None


# ---------------------------------------------------------------------------
# NX-14 — atr_timeframe consumido en L5 (ADVERSARIAL)
# ---------------------------------------------------------------------------

class _RecordingMD:
    def __init__(self):
        self.atr_calls: list[tuple] = []

    async def get_bars(self, *a, **kw):
        return []

    async def get_atr(self, symbol, timeframe="5m", period=14):
        self.atr_calls.append((symbol, timeframe, period))
        return 8.0

    async def is_active(self, symbol):
        return True


def _signal(timeframe="5m") -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="atf",
        ticker_received="MES", mapped_symbol="MESU2026",
        action="buy", sentiment="long", price=5500.0, timeframe=timeframe,
        signal_ts=datetime.now(UTC), signal_role="entry_long",
        dedupe_key=uuid.uuid4().hex,
    )


def _strategy() -> Strategy:
    return Strategy(strategy_id="atf", name="ATF", asset_symbol="MES",
                    status="paper", enabled=True)


@pytest.mark.asyncio
async def test_level5_uses_configured_atr_timeframe(db: AsyncSession):
    rec = _RecordingMD()
    pipeline = FilterPipeline(MarketDataService(rec))
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal(timeframe="5m"), _strategy(),
            {"global_mode": "normal", "atr_timeframe": "15m"})
    assert result.outcome == "APPROVE"
    assert rec.atr_calls[-1][1] == "15m", (
        f"L5 leyó ATR en {rec.atr_calls[-1][1]} (bug NX-14: atr_timeframe "
        "era decorativo)")
    assert result.pipeline_execution_json["level_5"]["atr_timeframe"] == "15m"


@pytest.mark.asyncio
async def test_level5_falls_back_to_signal_timeframe(db: AsyncSession):
    rec = _RecordingMD()
    pipeline = FilterPipeline(MarketDataService(rec))
    with _SESSION_OK:
        await pipeline.evaluate(
            db, _signal(timeframe="15m"), _strategy(),
            {"global_mode": "normal", "atr_timeframe": None})
    assert rec.atr_calls[-1][1] == "15m"


@pytest.mark.asyncio
async def test_resolver_atr_timeframe_default_is_none(db: AsyncSession):
    """Sin calibración explícita, atr_timeframe NO fuerza 5m: queda None y el
    L5 usa el timeframe de la señal (una 15m ya no leería ATR de 5m)."""
    db.add(Strategy(strategy_id="ntf", name="N", asset_symbol="ZZZ",
                    timeframe="15m", status="paper", enabled=True))
    await db.commit()
    cfg = await ConfigResolver().resolve(db, "ntf", "ZZZ")
    assert cfg["atr_timeframe"] is None


# ---------------------------------------------------------------------------
# NX-15 — retries/backoff/timeout configurables (ADVERSARIAL)
# ---------------------------------------------------------------------------

class _FakeAsyncClient:
    """httpx.AsyncClient falso: siempre 500."""
    calls = 0

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **kw):
        _FakeAsyncClient.calls += 1
        return type("R", (), {"status_code": 500, "text": "err"})()


@pytest.fixture()
def _http_500(monkeypatch):
    async def _fast_sleep(*a, **kw):
        return None
    monkeypatch.setattr("app.services.traderspost_client.asyncio.sleep",
                        _fast_sleep)
    _FakeAsyncClient.calls = 0
    monkeypatch.setattr(
        "app.services.traderspost_client.httpx.AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_entry_retry_attempts_configurable(_http_500):
    client = TradersPostClient(settings)
    result = await client.send(
        "https://tp/x", {"action": "buy"}, signal_role="entry_long",
        dry_run=False, retry_attempts=5)
    assert result.status == "FAILED"
    assert result.attempts == 5, (
        f"attempts={result.attempts} (bug NX-15: retry_attempts era hardcode 3)")


@pytest.mark.asyncio
async def test_exit_keeps_10_attempts_regardless(_http_500):
    """Los exits son críticos: la config NO puede bajar sus 10 intentos."""
    client = TradersPostClient(settings)
    result = await client.send(
        "https://tp/x", {"action": "exit"}, signal_role="exit_long",
        dry_run=False, retry_attempts=2)
    assert result.attempts == 10


@pytest.mark.asyncio
async def test_entry_timeout_configurable(_http_500):
    """Señal más vieja que entry_timeout_secs → un solo intento sin reintentos."""
    client = TradersPostClient(settings)
    old_ts = datetime.now(UTC) - timedelta(seconds=120)
    result = await client.send(
        "https://tp/x", {"action": "buy"}, signal_role="entry_long",
        dry_run=False, signal_ts=old_ts, entry_timeout_secs=60)
    assert result.attempts == 1


@pytest.mark.asyncio
async def test_resolver_exposes_retry_config(db: AsyncSession):
    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True,
                         retry_attempts=5, retry_backoff_seconds=2,
                         entry_signal_timeout_secs=45))
    db.add(Strategy(strategy_id="rt", name="RT", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    await db.commit()
    cfg = await ConfigResolver().resolve(db, "rt", "MES")
    assert cfg["retry_attempts"] == 5
    assert cfg["retry_backoff_seconds"] == 2
    assert cfg["entry_signal_timeout_secs"] == 45


@pytest.mark.asyncio
async def test_dispatch_passes_retry_config_to_client(db: AsyncSession, monkeypatch):
    """_dispatch_approved pasa la config de reintentos al cliente."""
    from app.api.webhooks_luxalgo import process_signal
    from app.models.symbol_map import SymbolMap

    seen: list[dict] = []

    async def _rec(self, webhook_url, payload, signal_role, dry_run,
                   signal_ts=None, **kw):
        from app.services.traderspost_client import WebhookDeliveryResult
        seen.append(dict(kw))
        return WebhookDeliveryResult(status="DRY_RUN", payload_json=payload,
                                     url_masked=webhook_url, attempts=0)

    monkeypatch.setattr(TradersPostClient, "send", _rec)

    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True,
                         retry_attempts=5, retry_backoff_seconds=2,
                         entry_signal_timeout_secs=45))
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True))
    db.add(Strategy(strategy_id="rp", name="RP", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id="rp", mode="paper",
                           traderspost_webhook_url="https://tp/base"))
    await db.commit()

    class _MD:
        async def get_bars(self, *a, **kw):
            return []

        async def get_atr(self, *a, **kw):
            return 8.0

        async def is_active(self, s):
            return True

    raw = RawSignal(source="luxalgo", strategy_id="rp", payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    buy = {"ticker": "MES", "action": "buy", "sentiment": "long",
           "quantity": "1", "price": "5500.00", "interval": "5"}
    with _SESSION_OK:
        decision = await process_signal(db, "rp", raw.id, buy,
                                        MarketDataService(_MD()))
    assert decision.outcome == "APPROVE"
    assert seen and seen[-1].get("retry_attempts") == 5, (
        f"el dispatch no pasó retry_attempts al cliente (kwargs={seen[-1]})")
    assert seen[-1].get("entry_timeout_secs") == 45


# ---------------------------------------------------------------------------
# NX-16 — signal_ts desde payload["time"] (ADVERSARIAL)
# ---------------------------------------------------------------------------

async def _normalize(db: AsyncSession, payload: dict) -> NormalizedSignal:
    raw = RawSignal(source="luxalgo", strategy_id="ts", payload_json=payload,
                    token_valid=True)
    db.add(raw)
    await db.flush()
    return await SignalNormalizer().normalize(db, raw.id, "ts", payload)


_BASE = {"ticker": "MES", "action": "buy", "sentiment": "long",
         "quantity": "1", "price": "5500.0", "interval": "5"}


@pytest.mark.asyncio
async def test_signal_ts_from_payload_time_iso_z(db: AsyncSession):
    norm = await _normalize(db, {**_BASE, "time": "2026-07-02T14:30:00Z"})
    assert norm.signal_ts == datetime(2026, 7, 2, 14, 30, tzinfo=UTC), (
        f"signal_ts={norm.signal_ts} (bug NX-16: usaba la hora de recepción y "
        "la frescura solo medía latencia interna)")


@pytest.mark.asyncio
async def test_signal_ts_naive_time_assumed_utc(db: AsyncSession):
    norm = await _normalize(db, {**_BASE, "time": "2026-07-02 14:30:00"})
    assert norm.signal_ts == datetime(2026, 7, 2, 14, 30, tzinfo=UTC)


@pytest.mark.asyncio
async def test_signal_ts_falls_back_to_reception(db: AsyncSession):
    before = datetime.now(UTC)
    norm = await _normalize(db, {**_BASE, "time": "garbage"})
    assert norm.signal_ts is not None
    assert abs((norm.signal_ts - before).total_seconds()) < 5
    norm2 = await _normalize(db, dict(_BASE))
    assert abs((norm2.signal_ts - before).total_seconds()) < 5


@pytest.mark.asyncio
async def test_stale_tv_signal_blocks(db: AsyncSession, market_data_service):
    """Una señal retenida 10 min por TV/red ahora sí cae por staleness."""
    old = (datetime.now(UTC) - timedelta(minutes=10)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    norm = await _normalize(db, {**_BASE, "time": old})
    norm.mapped_symbol = "MESU2026"   # sin SymbolMap en este test; L1.4 aparte
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, norm, _strategy(),
            {"global_mode": "normal", "signal_max_age_entry_seconds": 60})
    assert result.outcome == "BLOCK"
    assert result.block_reason == "signal_stale"
