"""Lote P-A — Módulo de Riesgo de Portafolio: PortfolioGuard (regla 1, L3).

Adversariales (rojo sin el guard):
- 2ª estrategia sobre el MISMO ACTIVO (distinto símbolo del mismo raíz) → BLOCK
  con motivo visible "ES ya tiene posición" (symbol_busy NO lo atrapa: es otro
  símbolo). MES/ES→ES en ambas direcciones (micro↔padre).
- El mismo símbolo sigue siendo `symbol_busy` (precedencia + intacto).
- Regla APAGADA = decisión idéntica al comportamiento anterior (no escanea).
- Las legs de la escalera NO se bloquean (viajan en el despacho multi-leg de su
  propia señal; el guard evalúa entradas nuevas, no legs).
- FAIL-CLOSED: estado de posiciones no legible → BLOCK; activo de una posición
  abierta indeterminado → BLOCK.
- Activo distinto no colisiona (participación intacta salvo por riesgo visible).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.webhooks_luxalgo import process_signal
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.portfolio_config import PortfolioConfig
from app.models.raw_signal import RawSignal
from app.models.strategy import Strategy
from app.models.strategy_profile import StrategyProfile
from app.models.symbol_map import SymbolMap
from app.models.webhook_delivery import WebhookDelivery
from app.services import portfolio_guard as pg
from app.services.filter_pipeline import FilterPipeline
from app.services.market_data_service import MarketDataService

UTC = timezone.utc

_SESSION_OK = patch(
    "app.services.session_validator.SessionValidator.is_within_session_config",
    return_value=True,
)


class _MockMD:
    async def get_bars(self, *a, **kw):
        return []

    async def get_atr(self, *a, **kw):
        return 8.0

    async def is_active(self, symbol: str = "") -> bool:
        return True


_MD = MarketDataService(_MockMD())


def _map(tv, mapped, data=None, **kw):
    return SymbolMap(
        tv_symbol=tv, mapped_symbol=mapped, market_data_symbol=data,
        exchange="CME", contract_type="futures_micro",
        pine_script_config=f'"ticker": "{tv}"', active=True, **kw)


async def _seed_index_maps(db: AsyncSession) -> None:
    """Catálogo raíz: ES↔MES→ES, NQ→NQ."""
    db.add(_map("ES", "ESU2026", data=None))          # padre → raíz ES
    db.add(_map("MES", "MESU2026", data="ES"))        # micro → raíz ES
    db.add(_map("NQ", "NQU2026", data=None))          # raíz NQ
    await db.flush()


def _signal(strategy_id="B", ticker="MES", mapped="MESU2026",
            action="buy", sentiment="long", role="entry_long",
            price=5500.0) -> NormalizedSignal:
    return NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id=strategy_id,
        ticker_received=ticker, mapped_symbol=mapped,
        action=action, sentiment=sentiment, price=price,
        signal_ts=datetime.now(UTC), signal_role=role,
        dedupe_key=uuid.uuid4().hex,
    )


def _strategy(strategy_id="B", asset="MES", status="paper") -> Strategy:
    return Strategy(strategy_id=strategy_id, name=strategy_id,
                    asset_symbol=asset, status=status, enabled=True)


def _position(symbol="ESU2026", strategy_id="A", state="LONG") -> PositionState:
    return PositionState(
        strategy_id=strategy_id, account_id="paper_default", symbol=symbol,
        state=state, state_source="estimated",
        direction="long" if "LONG" in state else "short" if "SHORT" in state else None,
        quantity=1)


# ---------------------------------------------------------------------------
# Regla 1 — no apilar el mismo ACTIVO (ADVERSARIAL)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_second_strategy_same_asset_blocked(db: AsyncSession, market_data_service):
    """A tiene ES abierto (ESU2026); B entra al MISMO activo por MES (MESU2026,
    otro símbolo). symbol_busy NO lo ve (símbolo distinto) → el PortfolioGuard
    bloquea con motivo visible."""
    await _seed_index_maps(db)
    db.add(_position(symbol="ESU2026", strategy_id="A", state="LONG"))
    await db.flush()

    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="MES", mapped="MESU2026"),
            _strategy("B", asset="MES"), {"global_mode": "normal"})

    assert result.outcome == "BLOCK", (
        f"2ª estrategia sobre el mismo activo salió {result.outcome} — "
        "el PortfolioGuard debía bloquear")
    assert result.block_reason == "portfolio_asset_busy"
    assert result.block_level == 3
    l3 = result.pipeline_execution_json["level_3"]
    assert l3["message"] == "ES ya tiene posición"
    assert l3["asset"] == "ES"
    assert l3["holder_strategy"] == "A"


@pytest.mark.asyncio
async def test_micro_and_parent_collide_both_ways(db: AsyncSession, market_data_service):
    """Simétrico: A tiene MES (micro) abierto; B entra por ES (padre). Mismo
    activo raíz → BLOCK (MES/ES→ES en ambas direcciones)."""
    await _seed_index_maps(db)
    db.add(_position(symbol="MESU2026", strategy_id="A", state="SHORT"))
    await db.flush()

    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="ES", mapped="ESU2026"),
            _strategy("B", asset="ES"), {"global_mode": "normal"})

    assert result.outcome == "BLOCK"
    assert result.block_reason == "portfolio_asset_busy"
    # Sin importar dirección: la posición previa es SHORT, la nueva LONG.
    assert result.pipeline_execution_json["level_3"]["asset"] == "ES"


@pytest.mark.asyncio
async def test_same_symbol_still_symbol_busy(db: AsyncSession, market_data_service):
    """Mismo símbolo → sigue siendo symbol_busy (precedencia intacta), NUNCA
    portfolio_asset_busy: el guard solo mira OTROS símbolos del activo."""
    await _seed_index_maps(db)
    db.add(_position(symbol="MESU2026", strategy_id="A", state="LONG"))
    await db.flush()

    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="MES", mapped="MESU2026"),
            _strategy("B", asset="MES"), {"global_mode": "normal"})

    assert result.outcome == "BLOCK"
    assert result.block_reason == "symbol_busy"


@pytest.mark.asyncio
async def test_different_asset_passes(db: AsyncSession, market_data_service):
    """A tiene NQ abierto; B entra por MES (activo ES). Activos distintos → no
    colisiona: la entrada aprueba (participación intacta)."""
    await _seed_index_maps(db)
    db.add(_position(symbol="NQU2026", strategy_id="A", state="LONG"))
    await db.flush()

    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="MES", mapped="MESU2026"),
            _strategy("B", asset="MES"), {"global_mode": "normal"})

    assert result.outcome == "APPROVE"


# ---------------------------------------------------------------------------
# Regla APAGADA = decisión idéntica (no escanea)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rule_off_identical_behavior(db: AsyncSession, market_data_service):
    """Con la regla 1 APAGADA, el mismo escenario del primer test APRUEBA —
    decisión idéntica al comportamiento anterior al guard."""
    await _seed_index_maps(db)
    db.add(PortfolioConfig(rules_json={pg.RULE_NO_STACK_ASSET: False},
                           params_json={}, active=True))
    db.add(_position(symbol="ESU2026", strategy_id="A", state="LONG"))
    await db.flush()

    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="MES", mapped="MESU2026"),
            _strategy("B", asset="MES"), {"global_mode": "normal"})

    assert result.outcome == "APPROVE", (
        f"regla apagada debía dejar la decisión idéntica, salió "
        f"{result.outcome}/{result.block_reason}")


@pytest.mark.asyncio
async def test_rule_off_skips_failclosed(db: AsyncSession, market_data_service):
    """Regla apagada NO escanea: ni siquiera un estado que dispararía el
    fail-closed altera la decisión (activo indeterminado abierto + regla off →
    APRUEBA igual)."""
    db.add(_map("ES", "ESU2026", data=None))
    db.add(PortfolioConfig(rules_json={pg.RULE_NO_STACK_ASSET: False},
                           params_json={}, active=True))
    db.add(_position(symbol="ZZZ9999", strategy_id="ghost", state="LONG"))
    await db.flush()

    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="ES", mapped="ESU2026"),
            _strategy("B", asset="ES"), {"global_mode": "normal"})

    assert result.outcome == "APPROVE"


# ---------------------------------------------------------------------------
# FAIL-CLOSED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fail_closed_positions_unreadable(db: AsyncSession, monkeypatch):
    """Estado de posiciones no legible → BLOCK (agregado no computable)."""
    async def _boom(*a, **kw):
        raise RuntimeError("db down")
    monkeypatch.setattr(db, "execute", _boom)

    res = await pg.PortfolioGuard()._rule_no_stack_asset(
        db, _signal("B", ticker="ES", mapped="ESU2026"), {})

    assert res["failed"] is True
    assert res["reason"] == "portfolio_state_unreadable"


@pytest.mark.asyncio
async def test_fail_closed_unknown_asset(db: AsyncSession, market_data_service):
    """Posición abierta cuyo activo no se puede determinar (sin símbolo mapeado
    ni estrategia con activo) → no se puede descartar colisión → BLOCK."""
    db.add(_map("ES", "ESU2026", data=None))
    db.add(_position(symbol="ZZZ9999", strategy_id="ghost", state="LONG"))
    await db.flush()

    pipeline = FilterPipeline(market_data_service)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="ES", mapped="ESU2026"),
            _strategy("B", asset="ES"), {"global_mode": "normal"})

    assert result.outcome == "BLOCK"
    assert result.block_reason == "portfolio_exposure_unknown"
    assert result.pipeline_execution_json["level_3"]["unknown_symbol"] == "ZZZ9999"


# ---------------------------------------------------------------------------
# Las legs de la escalera NO se bloquean
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ladder_legs_not_blocked(db: AsyncSession):
    """Una entrada con escalera despacha varias legs bajo UNA sola señal
    aprobada — el guard corre una vez (para la entrada) y las legs viajan en el
    despacho multi-leg; ninguna leg se auto-bloquea."""
    db.add(_map("MES", "MESU2026", data=None, tick_size=0.25, tick_value=1.25))
    db.add(_strategy("A", asset="MES", status="paper"))
    db.add(StrategyProfile(
        strategy_id="A", mode="paper",
        traderspost_webhook_url="https://tp/base",
        pipeline_config_json={"scale_entry": {
            "mode": "execute",
            "quantities": [1, 1, 1],   # 1 a mercado + 2 límite
            "levels": [1, 2],
            "max_micro_contracts": 5,
        }}))
    await db.commit()

    raw = RawSignal(source="luxalgo", strategy_id="A", payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    buy = {"ticker": "MES", "action": "buy", "sentiment": "long",
           "quantity": "1", "price": "5500.00", "interval": "5"}
    with _SESSION_OK:
        decision = await process_signal(db, "A", raw.id, buy, _MD)
    await db.flush()

    assert decision.outcome == "APPROVE", (
        f"la entrada con escalera salió {decision.outcome} "
        f"({decision.block_reason}) — el guard no debe tocar las legs")
    delivs = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.strategy_id == "A"))).scalars().all()
    assert len(delivs) == 3, f"esperaba 3 legs despachadas, hubo {len(delivs)}"


@pytest.mark.asyncio
async def test_ladder_then_second_strategy_blocked(db: AsyncSession):
    """Tras abrir la escalera de A (activo MES), una entrada NUEVA e
    independiente de B sobre el mismo activo (por ES) sí se bloquea: legs libres,
    entradas nuevas gobernadas."""
    db.add(_map("ES", "ESU2026", data=None))
    db.add(_map("MES", "MESU2026", data="ES", tick_size=0.25, tick_value=1.25))
    db.add(_strategy("A", asset="MES", status="paper"))
    db.add(StrategyProfile(
        strategy_id="A", mode="paper",
        traderspost_webhook_url="https://tp/base",
        pipeline_config_json={"scale_entry": {
            "mode": "execute", "quantities": [1, 1], "levels": [1],
            "max_micro_contracts": 5}}))
    await db.commit()

    raw = RawSignal(source="luxalgo", strategy_id="A", payload_json={},
                    token_valid=True)
    db.add(raw)
    await db.flush()
    buy = {"ticker": "MES", "action": "buy", "sentiment": "long",
           "quantity": "1", "price": "5500.00", "interval": "5"}
    with _SESSION_OK:
        dec_a = await process_signal(db, "A", raw.id, buy, _MD)
    assert dec_a.outcome == "APPROVE"

    # B — entrada independiente sobre el mismo activo raíz (ES), otro símbolo.
    pipeline = FilterPipeline(_MD)
    with _SESSION_OK:
        result = await pipeline.evaluate(
            db, _signal("B", ticker="ES", mapped="ESU2026"),
            _strategy("B", asset="ES"), {"global_mode": "normal"})
    assert result.outcome == "BLOCK"
    assert result.block_reason == "portfolio_asset_busy"


# ---------------------------------------------------------------------------
# compute_exposure — vista en vivo
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compute_exposure_groups_by_asset(db: AsyncSession):
    await _seed_index_maps(db)
    db.add(_position(symbol="MESU2026", strategy_id="A", state="LONG"))
    db.add(_position(symbol="NQU2026", strategy_id="C", state="SHORT"))
    db.add(_position(symbol="FLATONE", strategy_id="D", state="FLAT"))
    await db.flush()

    exp = await pg.compute_exposure(db)
    assert exp["occupied_assets"] == 2          # ES y NQ (FLAT no cuenta)
    assert exp["total_micros"] == 2
    es = next(a for a in exp["assets"] if a["asset"] == "ES")
    assert es["positions"][0]["symbol"] == "MESU2026"
