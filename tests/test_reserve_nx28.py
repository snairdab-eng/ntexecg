"""NX-28 — liberar la reserva de symbol_busy cuando la entrada no se llena.

Con NX-09, una entrada despachada reserva el símbolo (symbol_busy). En los
diseños pullback (todas las piernas límite: ES/GC/YM/6J/CL/NQ), si el precio
no retrocede TradersPost cancela las órdenes tras cancel_after y la "posición"
estimada queda LONG/SHORT fantasma bloqueando el símbolo para siempre.

Regla: el sweep resetea a FLAT una reserva SIN fill confirmable tras un
timeout ≈ cancel_after (default 3600 s; override por estrategia con
`entry_reserve_timeout_seconds`):
  - PENDING_* viejos → liberar (nunca hubo envío confirmado).
  - LONG/SHORT con entry_style == "limit_only" viejos → liberar (las límite
    ya fueron canceladas por TradersPost o LuxAlgo mandará su exit igual).
  - LONG/SHORT de entrada a MERCADO (o sin marca, legacy) → NO liberar
    (fill casi seguro; el exit/reconciliación los gobierna).
  - EXITING → NO liberar (dominio de NX-08).

Para eso el dispatch registra `entry_style` ("market" | "limit_only") en
risk_plan_json al aprobar la entrada.

Adversariales: fallan sin el fix.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal
from app.models.audit_log import AuditLog
from app.models.position_state import PositionState
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.services.forced_exit import release_unfilled_reservations
from app.services.market_data_service import MarketDataService

UTC = timezone.utc


class _MockMD:
    async def get_bars(self, *a, **kw):
        return []

    async def get_atr(self, *a, **kw):
        return 8.0

    async def is_active(self, symbol: str) -> bool:
        return True


_MD = MarketDataService(_MockMD())


def _position(state="LONG", strategy_id="rs", symbol="MESU2026",
              opened_minutes_ago=120, entry_style=None) -> PositionState:
    plan = {"opened_at": (datetime.now(UTC) -
                          timedelta(minutes=opened_minutes_ago)).isoformat()}
    if entry_style is not None:
        plan["entry_style"] = entry_style
    return PositionState(
        strategy_id=strategy_id, account_id="paper_default", symbol=symbol,
        state=state, state_source="estimated",
        direction="long" if "LONG" in state else None,
        quantity=1, risk_plan_json=plan,
    )


async def _seed_strategy(db: AsyncSession, sid="rs", pipeline_config=None):
    db.add(Strategy(strategy_id=sid, name=sid, asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    db.add(StrategyProfile(strategy_id=sid, mode="paper",
                           traderspost_webhook_url="https://tp/base",
                           pipeline_config_json=pipeline_config))


async def _state(db: AsyncSession, symbol="MESU2026") -> str:
    db.expire_all()
    p = (await db.execute(select(PositionState).where(
        PositionState.symbol == symbol))).scalar_one()
    return p.state


# ---------------------------------------------------------------------------
# Liberación de reservas (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stale_limit_only_reservation_is_released(db: AsyncSession):
    await _seed_strategy(db)
    db.add(_position(state="LONG", entry_style="limit_only",
                     opened_minutes_ago=120))
    await db.commit()

    n = await release_unfilled_reservations(db)
    assert n == 1
    state = await _state(db)
    assert state == "FLAT", (
        f"reserva límite sin fill quedó {state} tras el timeout (bug NX-28: "
        "el símbolo quedaba bloqueado para siempre)")
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "RESERVE_RELEASED"))).scalars().first()
    assert audit is not None


@pytest.mark.asyncio
async def test_stale_pending_is_released(db: AsyncSession):
    await _seed_strategy(db)
    db.add(_position(state="PENDING_LONG", opened_minutes_ago=120))
    await db.commit()
    assert await release_unfilled_reservations(db) == 1
    assert await _state(db) == "FLAT"


@pytest.mark.asyncio
async def test_market_entry_is_never_released(db: AsyncSession):
    """Entrada a mercado = fill casi seguro: NO se libera por tiempo."""
    await _seed_strategy(db)
    db.add(_position(state="LONG", entry_style="market",
                     opened_minutes_ago=600))
    await db.commit()
    assert await release_unfilled_reservations(db) == 0
    assert await _state(db) == "LONG"


@pytest.mark.asyncio
async def test_legacy_position_without_style_is_not_released(db: AsyncSession):
    """Posiciones previas al fix (sin entry_style): conservador, no tocar."""
    await _seed_strategy(db)
    db.add(_position(state="LONG", opened_minutes_ago=600))
    await db.commit()
    assert await release_unfilled_reservations(db) == 0
    assert await _state(db) == "LONG"


@pytest.mark.asyncio
async def test_fresh_reservation_is_kept(db: AsyncSession):
    await _seed_strategy(db)
    db.add(_position(state="LONG", entry_style="limit_only",
                     opened_minutes_ago=10))
    await db.commit()
    assert await release_unfilled_reservations(db) == 0
    assert await _state(db) == "LONG"


@pytest.mark.asyncio
async def test_exiting_is_not_released(db: AsyncSession):
    """EXITING es dominio de NX-08 (cierre en vuelo), no de la reserva."""
    await _seed_strategy(db)
    db.add(_position(state="EXITING", entry_style="limit_only",
                     opened_minutes_ago=600))
    await db.commit()
    assert await release_unfilled_reservations(db) == 0
    assert await _state(db) == "EXITING"


@pytest.mark.asyncio
async def test_per_strategy_timeout_override(db: AsyncSession):
    """entry_reserve_timeout_seconds=120 → una reserva de 5 min se libera
    aunque el default sea 3600."""
    await _seed_strategy(db, pipeline_config={
        "entry_reserve_timeout_seconds": 120})
    db.add(_position(state="LONG", entry_style="limit_only",
                     opened_minutes_ago=5))
    await db.commit()
    assert await release_unfilled_reservations(db) == 1
    assert await _state(db) == "FLAT"


# ---------------------------------------------------------------------------
# Registro de entry_style en el dispatch (ADVERSARIAL)
# ---------------------------------------------------------------------------

async def _fire_buy(db: AsyncSession, sid: str):
    raw = RawSignal(source="luxalgo", strategy_id=sid, payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    buy = {"ticker": "MES", "action": "buy", "sentiment": "long",
           "quantity": "1", "price": "5500.00", "interval": "5"}
    decision = await process_signal(db, sid, raw.id, buy, _MD)
    await db.flush()
    return decision


def _symbol_map():
    return SymbolMap(tv_symbol="MES", mapped_symbol="MESU2026", exchange="CME",
                     contract_type="futures_micro",
                     pine_script_config='"ticker": "MES"', active=True)


@pytest.mark.asyncio
async def test_market_entry_records_style(db: AsyncSession):
    db.add(_symbol_map())
    await _seed_strategy(db, sid="mk")
    await db.commit()
    decision = await _fire_buy(db, "mk")
    assert decision.outcome == "APPROVE"
    db.expire_all()
    pos = (await db.execute(select(PositionState).where(
        PositionState.symbol == "MESU2026"))).scalar_one()
    assert (pos.risk_plan_json or {}).get("entry_style") == "market", (
        "el dispatch no registró entry_style (bug NX-28)")


@pytest.mark.asyncio
async def test_all_limit_scaled_entry_records_limit_only(db: AsyncSession):
    db.add(_symbol_map())
    await _seed_strategy(db, sid="lm", pipeline_config={
        "scale_entry": {"mode": "execute", "levels": [0.75, 1.25],
                        "quantities": [0, 1, 2], "max_micro_contracts": 5},
    })
    await db.commit()
    decision = await _fire_buy(db, "lm")
    assert decision.outcome == "APPROVE"
    db.expire_all()
    pos = (await db.execute(select(PositionState).where(
        PositionState.symbol == "MESU2026"))).scalar_one()
    assert (pos.risk_plan_json or {}).get("entry_style") == "limit_only"
