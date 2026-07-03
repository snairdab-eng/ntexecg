"""Lote 5 — portafolio + reconciliación (NX-09, NX-18 Fase A).

NX-09: regla L3.4 `symbol_busy` — con una posición del símbolo ocupada
(PENDING_*/LONG/SHORT/EXITING en la misma cuenta), las ENTRADAS se bloquean:
tanto la re-entrada de la misma estrategia como la de otra estrategia sobre el
mismo símbolo (caso dos ES sobre MES). Opt-out por estrategia con
`allow_stacking`. Las salidas siguen exentas (L3 no corre) y el reversal sigue
funcionando (el cierre lo gestiona el propio flujo antes de la entrada opuesta).

NX-18 Fase A: el import semanal de resultados pone FLAT una posición SOLO si
el trade cerrado concilió EXACTO por signal_id y esa posición fue abierta por
ese mismo signal (entry_signal_id). Nada especulativo: heurístico no toca
estado; señal distinta no toca estado.

Adversariales: fallan sin el fix.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal
from app.models.audit_log import AuditLog
from app.models.decision import StrategyDecision
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services.filter_pipeline import FilterPipeline
from app.services.market_data_service import MarketDataService
from app.services.results_import import import_results

UTC = timezone.utc

_SESSION_OK = patch(
    "app.services.session_validator.SessionValidator.is_within_session_config",
    return_value=True,
)


def _signal(strategy_id="A", action="buy", sentiment="long",
            signal_role="entry_long", price=5500.0) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id=strategy_id,
        ticker_received="MES", mapped_symbol="MESU2026",
        action=action, sentiment=sentiment, price=price,
        signal_ts=datetime.now(UTC), signal_role=signal_role,
        dedupe_key=uuid.uuid4().hex,
    )


def _strategy(strategy_id="A", status="paper") -> Strategy:
    return Strategy(strategy_id=strategy_id, name=strategy_id,
                    asset_symbol="MES", status=status, enabled=True)


def _position(state="LONG", strategy_id="A", symbol="MESU2026",
              entry_signal_id=None) -> PositionState:
    return PositionState(
        strategy_id=strategy_id, account_id="paper_default", symbol=symbol,
        state=state, state_source="estimated",
        direction="long" if "LONG" in state else None,
        quantity=1, entry_signal_id=entry_signal_id,
    )


# ---------------------------------------------------------------------------
# NX-09 — symbol_busy (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reentry_same_strategy_blocked(db: AsyncSession, market_data_service):
    """Estrategia A LONG en MESU2026: otra COMPRA de A → BLOCK symbol_busy
    (antes pasaba y piramidaba/overwriteaba el estimado)."""
    db.add(_position(state="LONG", strategy_id="A"))
    await db.flush()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("A"), _strategy("A"), {"global_mode": "normal"})

    assert result.outcome == "BLOCK", (
        f"re-entrada con posición abierta salió {result.outcome} (bug NX-09)")
    assert result.block_reason == "symbol_busy"
    assert result.block_level == 3


@pytest.mark.asyncio
async def test_other_strategy_same_symbol_blocked(db: AsyncSession, market_data_service):
    """Caso dos ES sobre MES: A tiene LONG; entrada de B al mismo símbolo →
    BLOCK symbol_busy, y la traza dice quién lo ocupa."""
    db.add(_position(state="LONG", strategy_id="A"))
    await db.flush()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B"), _strategy("B"), {"global_mode": "normal"})

    assert result.outcome == "BLOCK"
    assert result.block_reason == "symbol_busy"
    l3 = result.pipeline_execution_json["level_3"]
    assert l3.get("holder_strategy") == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("busy_state", ["PENDING_LONG", "PENDING_SHORT", "EXITING", "SHORT"])
async def test_transitional_states_also_block(db: AsyncSession, market_data_service, busy_state):
    db.add(_position(state=busy_state, strategy_id="A"))
    await db.flush()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B"), _strategy("B"), {"global_mode": "normal"})
    assert result.outcome == "BLOCK" and result.block_reason == "symbol_busy"


@pytest.mark.asyncio
async def test_exits_stay_exempt(db: AsyncSession, market_data_service):
    """Las salidas NO pasan por L3: con el símbolo ocupado, el exit se aprueba."""
    db.add(_position(state="LONG", strategy_id="A"))
    await db.flush()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("A", action="exit", sentiment="flat",
                        signal_role="exit_long"),
            _strategy("A"), {"global_mode": "normal"})
    assert result.outcome == "APPROVE"


@pytest.mark.asyncio
async def test_flat_symbol_passes(db: AsyncSession, market_data_service):
    """Sin posición (o FLAT) la entrada pasa igual que siempre."""
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("A"), _strategy("A"), {"global_mode": "normal"})
    assert result.outcome == "APPROVE"


@pytest.mark.asyncio
async def test_allow_stacking_opt_out(db: AsyncSession, market_data_service):
    """`allow_stacking: true` desactiva la regla para esa estrategia."""
    db.add(_position(state="LONG", strategy_id="A"))
    await db.flush()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("A"), _strategy("A"),
            {"global_mode": "normal", "allow_stacking": True})
    assert result.outcome == "APPROVE"


@pytest.mark.asyncio
async def test_unknown_keeps_its_own_reason(db: AsyncSession, market_data_service):
    """UNKNOWN/LOCKED conservan su motivo específico (no symbol_busy)."""
    db.add(_position(state="UNKNOWN", strategy_id="A"))
    await db.flush()
    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("A"), _strategy("A"), {"global_mode": "normal"})
    assert result.block_reason == "unknown_position_state"


class _MockMD:
    async def get_bars(self, *a, **kw):
        return []

    async def get_atr(self, *a, **kw):
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return True


_MD = MarketDataService(_MockMD())


@pytest.mark.asyncio
async def test_reversal_still_works_end_to_end(db: AsyncSession):
    """Reversal con allow_reversal=True: cierra y evalúa la entrada opuesta —
    symbol_busy NO la bloquea (el cierre es del propio flujo)."""
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True))
    db.add(_strategy("A"))
    db.add(StrategyProfile(strategy_id="A", mode="paper",
                           traderspost_webhook_url="https://tp/base",
                           allow_reversal=True))
    db.add(_position(state="LONG", strategy_id="A"))
    await db.commit()

    raw = RawSignal(source="luxalgo", strategy_id="A", payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    sell = {"ticker": "MES", "action": "sell", "sentiment": "short",
            "quantity": "1", "price": "5500.00", "interval": "5"}
    with _SESSION_OK:
        decision = await process_signal(db, "A", raw.id, sell, _MD)
    await db.flush()

    assert decision.outcome == "APPROVE", (
        f"la entrada opuesta del reversal salió {decision.outcome} "
        f"({decision.block_reason}) — symbol_busy no debe pisar el reversal")
    delivs = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.strategy_id == "A"))).scalars().all()
    # 1 cierre (forced exit) + 1 entrada opuesta
    assert len(delivs) >= 2


# ---------------------------------------------------------------------------
# NX-18 Fase A — reconciliación por signal_id exacto (ADVERSARIAL)
# ---------------------------------------------------------------------------

async def _seed_sent_entry(db: AsyncSession, sid="rc") -> str:
    """Señal de entrada + delivery SENT con extras.signal_id + posición LONG
    abierta por esa señal (entry_signal_id). Devuelve el signal_id (str)."""
    db.add(SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"',
                     tick_size=0.25, tick_value=1.25, active=True))
    raw = RawSignal(source="luxalgo", strategy_id=sid, payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    norm = NormalizedSignal(
        raw_signal_id=raw.id, strategy_id=sid, ticker_received="MES",
        mapped_symbol="MESU2026", action="buy", sentiment="long",
        price=5601.25, signal_ts=datetime(2026, 6, 22, 13, 35, tzinfo=UTC),
        signal_role="entry_long", dedupe_key=uuid.uuid4().hex,
        status="processed",
    )
    db.add(norm)
    await db.flush()
    decision = StrategyDecision(
        normalized_signal_id=norm.id, strategy_id=sid, outcome="APPROVE",
        score=100,
    )
    db.add(decision)
    await db.flush()
    db.add(WebhookDelivery(
        decision_id=decision.id,
        strategy_id=sid, destination="traderspost", url_masked="https://tp/***",
        payload_json={"ticker": "MESU2026", "action": "buy",
                      "extras": {"signal_id": str(norm.id)}},
        status="SENT", attempts=1,
        sent_at=datetime(2026, 6, 22, 13, 35, 5, tzinfo=UTC),
    ))
    db.add(_position(state="LONG", strategy_id=sid, entry_signal_id=norm.id))
    norm_id = str(norm.id)          # capturar antes del commit (expira el ORM)
    await db.commit()
    return norm_id


def _closed_row(signal_id: str | None, sid="rc") -> dict:
    return {
        "signal_id": signal_id or "", "strategy_id": sid, "symbol": "MESU2026",
        "direction": "long", "quantity": "1",
        "entry_time": "2026-06-22 09:35:00", "entry_price": "5601.25",
        "exit_time": "2026-06-22 10:10:00", "exit_price": "5610.50",
        "pnl": "46.25", "exit_reason": "target", "fees": "1.24",
    }


async def _state(db: AsyncSession, symbol="MESU2026") -> str:
    db.expire_all()
    p = (await db.execute(select(PositionState).where(
        PositionState.symbol == symbol))).scalar_one()
    return p.state


@pytest.mark.asyncio
async def test_exact_match_closes_position(db: AsyncSession):
    norm_id = await _seed_sent_entry(db)
    summary = await import_results(db, [_closed_row(norm_id)])
    await db.flush()

    assert summary["matched_signal_id"] == 1
    state = await _state(db)
    assert state == "FLAT", (
        f"trade cerrado y conciliado exacto dejó la posición en {state} "
        "(bug NX-18: el import no cerraba el lazo)")
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "RECONCILE"))).scalars().first()
    assert audit is not None


@pytest.mark.asyncio
async def test_heuristic_match_does_not_touch_state(db: AsyncSession):
    """Nada especulativo: match heurístico (sin signal_id) NO cambia estado."""
    await _seed_sent_entry(db)
    row = _closed_row(None)
    row["entry_time"] = "2026-06-22 13:35:00"   # dentro de la ventana heurística
    summary = await import_results(db, [row])
    await db.flush()

    assert summary["matched_heuristic"] == 1
    assert await _state(db) == "LONG"


@pytest.mark.asyncio
async def test_different_entry_signal_does_not_touch_state(db: AsyncSession):
    """La posición fue reabierta por OTRA señal: el cierre conciliado del trade
    viejo no la toca."""
    norm_id = await _seed_sent_entry(db)
    db.expire_all()
    pos = (await db.execute(select(PositionState).where(
        PositionState.symbol == "MESU2026"))).scalar_one()
    pos.entry_signal_id = uuid.uuid4()          # reabierta por otra señal
    await db.commit()

    summary = await import_results(db, [_closed_row(norm_id)])
    await db.flush()
    assert summary["matched_signal_id"] == 1
    assert await _state(db) == "LONG"


@pytest.mark.asyncio
async def test_open_trade_does_not_touch_state(db: AsyncSession):
    """Fila sin exit_time (trade aún abierto): no se toca el estado."""
    norm_id = await _seed_sent_entry(db)
    row = _closed_row(norm_id)
    row["exit_time"] = ""
    row["exit_price"] = ""
    await import_results(db, [row])
    await db.flush()
    assert await _state(db) == "LONG"
