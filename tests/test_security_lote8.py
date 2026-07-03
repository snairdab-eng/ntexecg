"""Lote 8 — seguridad + limpieza (NX-21, NX-22, NX-23, NX-26).

NX-21: ALLOW_STRATEGY_AUTOCREATE=false → una señal con strategy_id desconocido
       NO crea la estrategia: BLOCK `unknown_strategy` (RawSignal se guarda
       igual, auditoría intacta). Default true = comportamiento histórico.
NX-22: tokens de webhook hasheados (SHA-256 + WEBHOOK_TOKEN_SALT) con
       dual-read: filas legacy en claro siguen validando; filas hasheadas
       validan el MISMO token que ya tienen las alertas de LuxAlgo (no hay
       re-alta). Alta/clone/regenerar guardan SOLO el hash y muestran el
       token una única vez.
NX-23: columnas/modelos muertos fuera de los modelos (la migración los dropea;
       ConflictLog se CONSERVA para NX-18 Fase C).
NX-26: avg_score promedia SOLO señales con score medido (columna
       scored_signals); régimen enabled con allowed=[] deja warning en traza.

Adversariales: fallan sin el fix.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal
from app.core.auth import SESSION_COOKIE_NAME, create_session_token
from app.core.config import settings
from app.core.security import hash_token
from app.models.decision import StrategyDecision
from app.models.global_profile import GlobalProfile
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.services.filter_pipeline import FilterPipeline
from app.services.market_data_service import MarketDataService
from app.services.performance_tracker import PerformanceTracker

UTC = timezone.utc


@pytest.fixture(autouse=True)
def _auth(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    if not settings.SESSION_SECRET:
        monkeypatch.setattr(settings, "SESSION_SECRET", "test_secret_lote8")
    client.cookies.set(SESSION_COOKIE_NAME, create_session_token("admin"))


class _MockMD:
    async def get_bars(self, *a, **kw):
        return []

    async def get_atr(self, *a, **kw):
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return True


_MD = MarketDataService(_MockMD())

_PAYLOAD = {"ticker": "MES", "action": "buy", "sentiment": "long",
            "quantity": "1", "price": "5500.00", "interval": "5"}


# ---------------------------------------------------------------------------
# NX-21 — flag de auto-creación (ADVERSARIAL)
# ---------------------------------------------------------------------------

async def _fire_unknown(db: AsyncSession, sid: str) -> StrategyDecision:
    raw = RawSignal(source="luxalgo", strategy_id=sid, payload_json=_PAYLOAD,
                    token_valid=True)
    db.add(raw)
    await db.flush()
    decision = await process_signal(db, sid, raw.id, dict(_PAYLOAD), _MD)
    await db.flush()
    return decision


@pytest.mark.asyncio
async def test_autocreate_off_blocks_unknown_strategy(
    db: AsyncSession, monkeypatch
):
    monkeypatch.setattr(settings, "ALLOW_STRATEGY_AUTOCREATE", False,
                        raising=False)
    decision = await _fire_unknown(db, "intrusa")

    assert decision.outcome == "BLOCK", (
        f"con el flag apagado salió {decision.outcome} (bug NX-21: cualquier "
        "id desconocido creaba una Strategy)")
    assert decision.block_reason == "unknown_strategy"
    row = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "intrusa"))).scalar_one_or_none()
    assert row is None


@pytest.mark.asyncio
async def test_autocreate_on_preserved(db: AsyncSession, monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_STRATEGY_AUTOCREATE", True,
                        raising=False)
    decision = await _fire_unknown(db, "nueva_auto")
    assert decision.outcome == "QUEUE_FOR_REVIEW"
    row = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "nueva_auto"))).scalar_one_or_none()
    assert row is not None and row.status == "candidate"


# ---------------------------------------------------------------------------
# NX-22 — tokens hasheados con dual-read (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hashed_token_validates(client: AsyncClient, db: AsyncSession):
    """Fila con SOLO hash: el mismo token que ya tiene la alerta valida."""
    db.add(Strategy(
        strategy_id="hsh", name="H", asset_symbol="MES", timeframe="5m",
        status="paper", enabled=True,
        webhook_token=None,
        webhook_token_hash=hash_token("tok_hasheado_123",
                                      settings.WEBHOOK_TOKEN_SALT),
    ))
    await db.commit()

    r = await client.post("/webhooks/luxalgo/hsh?token=tok_hasheado_123",
                          json=_PAYLOAD)
    assert r.status_code == 200, (
        f"HTTP {r.status_code}: el hash no valida (bug NX-22 dual-read)")
    r2 = await client.post("/webhooks/luxalgo/hsh?token=tok_malo",
                           json=_PAYLOAD)
    assert r2.status_code == 401


@pytest.mark.asyncio
async def test_legacy_plaintext_token_still_validates(
    client: AsyncClient, db: AsyncSession
):
    db.add(Strategy(strategy_id="pln", name="P", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True,
                    webhook_token="tok_plano_456"))
    await db.commit()
    r = await client.post("/webhooks/luxalgo/pln?token=tok_plano_456",
                          json=_PAYLOAD)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_regenerate_stores_hash_only(client: AsyncClient, db: AsyncSession):
    db.add(Strategy(strategy_id="rg8", name="R", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True,
                    webhook_token="viejo_plano"))
    await db.commit()

    r = await client.post("/ui/strategies/rg8/regenerate-token")
    assert r.status_code in (200, 303)
    db.expire_all()
    s = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "rg8"))).scalar_one()
    assert s.webhook_token is None, (
        "regenerar dejó el token en claro en la DB (bug NX-22)")
    assert s.webhook_token_hash


@pytest.mark.asyncio
async def test_create_form_stores_hash_only(client: AsyncClient, db: AsyncSession):
    r = await client.post("/ui/strategies/new", data={
        "strategy_id": "NUEVAH", "name": "NH", "asset_symbol": "MES",
        "timeframe": "5m",
    })
    assert r.status_code in (200, 303)
    db.expire_all()
    s = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "NUEVAH"))).scalar_one()
    assert s.webhook_token is None
    assert s.webhook_token_hash


@pytest.mark.asyncio
async def test_hash_script_migrates_and_alert_keeps_working(
    client: AsyncClient, db: AsyncSession
):
    """El script hashea in-place y la MISMA alerta (mismo token) sigue OK."""
    from scripts.hash_webhook_tokens import hash_existing_tokens

    db.add(Strategy(strategy_id="mig", name="M", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True,
                    webhook_token="token_de_la_alerta"))
    await db.commit()

    n = await hash_existing_tokens(db, settings.WEBHOOK_TOKEN_SALT)
    await db.commit()
    assert n == 1
    db.expire_all()
    s = (await db.execute(select(Strategy).where(
        Strategy.strategy_id == "mig"))).scalar_one()
    assert s.webhook_token is None and s.webhook_token_hash

    r = await client.post("/webhooks/luxalgo/mig?token=token_de_la_alerta",
                          json=_PAYLOAD)
    assert r.status_code == 200   # sin re-alta en LuxAlgo


# ---------------------------------------------------------------------------
# NX-23 — columnas/modelos muertos fuera (ADVERSARIAL en los modelos)
# ---------------------------------------------------------------------------

def test_dead_columns_removed_from_models():
    for col in ("profile_name", "routing_mode", "allowed_accounts_json",
                "allowed_symbols_json", "timezone", "days_enabled_json",
                "entry_start_time", "entry_end_time", "cooldown_minutes",
                "daily_profit_lock"):
        assert col not in StrategyProfile.__table__.columns, f"SP.{col} sigue"
    for col in ("days_enabled_json", "entry_start_time", "entry_end_time",
                "entry_cutoff_time", "global_daily_profit_lock",
                "default_quantity", "news_impact_levels_json"):
        assert col not in GlobalProfile.__table__.columns, f"GP.{col} sigue"
    assert "pine_script_ticker_note" not in Strategy.__table__.columns
    # conservados a propósito
    assert "profile_name" in GlobalProfile.__table__.columns
    assert "retry_attempts" in GlobalProfile.__table__.columns   # NX-15 lo usa


def test_conflictlog_preserved_economic_event_gone():
    import app.models as m

    assert hasattr(m, "ConflictLog")            # NX-18 Fase C lo usará
    assert not hasattr(m, "EconomicEvent")


def test_dead_env_settings_removed():
    for attr in ("MAX_RETRY_ATTEMPTS", "RETRY_BACKOFF_SECONDS",
                 "DEFAULT_TIMEZONE", "MARKET_DATA_FALLBACK_ENABLED",
                 "NEWS_CACHE_TTL_MINUTES"):
        assert not hasattr(settings, attr), f"settings.{attr} sigue"
    assert hasattr(settings, "WEBHOOK_TOKEN_SALT")   # NX-22 lo usa
    assert hasattr(settings, "DRY_RUN")              # NX-03 lo usa


# ---------------------------------------------------------------------------
# NX-26 — avg_score solo medidos + warning de régimen vacío (ADVERSARIAL)
# ---------------------------------------------------------------------------

def _decision(score) -> StrategyDecision:
    return StrategyDecision(normalized_signal_id=uuid.uuid4(),
                            strategy_id="avg", outcome="APPROVE", score=score)


@pytest.mark.asyncio
async def test_avg_score_ignores_unscored_decisions(db: AsyncSession):
    tracker = PerformanceTracker()
    for _ in range(3):
        await tracker.update(db, "avg", _decision(None))   # salidas/blocks
    perf = await tracker.update(db, "avg", _decision(80))

    assert float(perf.avg_score) == 80.0, (
        f"avg_score={perf.avg_score} (bug NX-26: los None diluían como 0)")
    assert perf.scored_signals == 1
    perf = await tracker.update(db, "avg", _decision(60))
    assert float(perf.avg_score) == 70.0
    assert perf.scored_signals == 2


@pytest.mark.asyncio
async def test_regime_enabled_without_allowed_warns(
    db: AsyncSession, market_data_service
):
    from app.models.normalized_signal import NormalizedSignal

    signal = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="rw", ticker_received="MES",
        mapped_symbol="MESU2026", action="buy", sentiment="long",
        price=5500.0, signal_ts=datetime.now(UTC), signal_role="entry_long",
        dedupe_key=uuid.uuid4().hex,
    )
    strategy = Strategy(strategy_id="rw", name="RW", asset_symbol="MES",
                        status="paper", enabled=True)
    pipeline = FilterPipeline(market_data_service)
    with patch(
        "app.services.session_validator.SessionValidator.is_within_session_config",
        return_value=True,
    ):
        result = await pipeline.evaluate(
            db, signal, strategy,
            {"global_mode": "normal",
             "regime": {"enabled": True, "allowed_regimes": []}})

    assert result.outcome == "APPROVE"   # sigue siendo no-op (documentado)
    rg = result.pipeline_execution_json.get("regime") or {}
    assert rg.get("warning") == "no_allowed_regimes", (
        "régimen enabled con allowed=[] pasa en silencio (bug P2-12/NX-26)")
