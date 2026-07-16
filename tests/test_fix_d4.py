"""FIX-D4 — precisión decimal en el registro: Numeric(18,6) → Numeric(20,10).

El 7º decimal de FX (6J tick 5e-7) ya no se trunca en las columnas de precio de
decision/posición/execution. Dos pruebas: (1) el TIPO de columna es Numeric(20,10)
en el modelo (backend-independiente); (2) round-trip real de un precio 6J con 7
decimales forzando recarga desde DB.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import Numeric, select

from app.models.decision import StrategyDecision
from app.models.execution_result import ExecutionResult
from app.models.position_state import PositionState

# (modelo, columna) de precio ampliadas por FIX-D4.
_PRICE_COLS = [
    (StrategyDecision, "sl_price"), (StrategyDecision, "tp_price"),
    (StrategyDecision, "atr_value"), (PositionState, "entry_price"),
    (ExecutionResult, "entry_price"), (ExecutionResult, "exit_price"),
]

# Precio 6J real, 7 decimales significativos (múltiplo del tick 5e-7). Numeric(18,6)
# lo truncaría al 6º decimal (Δ ≈ 5e-7); Numeric(20,10) lo conserva.
_6J = 0.0067895
_6J_ATR = 0.0000015


def test_price_columns_are_numeric_20_10():
    for model, col in _PRICE_COLS:
        t = model.__table__.c[col].type
        assert isinstance(t, Numeric), (model.__name__, col)
        assert (t.precision, t.scale) == (20, 10), (
            model.__name__, col, t.precision, t.scale)


@pytest.mark.asyncio
async def test_6j_price_survives_roundtrip(db):
    # FK no se aplica en el SQLite de test → id sintético para normalized_signal_id.
    dec = StrategyDecision(
        normalized_signal_id=uuid.uuid4(), strategy_id="6j", outcome="APPROVE",
        sl_price=_6J - _6J_ATR, tp_price=_6J + _6J_ATR, atr_value=_6J_ATR)
    pos = PositionState(
        strategy_id="6j", account_id="paper_default", symbol="6JU2025",
        state="LONG", direction="long", quantity=1, entry_price=_6J)
    ex = ExecutionResult(
        row_hash=uuid.uuid4().hex, symbol="6JU2025", direction="long", quantity=1,
        entry_time=datetime.now(timezone.utc), entry_price=_6J,
        exit_time=datetime.now(timezone.utc), exit_price=_6J + _6J_ATR)
    db.add_all([dec, pos, ex])
    await db.commit()
    db.expunge_all()                       # fuerza recarga desde DB (no identity-map)

    d2 = (await db.execute(select(StrategyDecision).where(
        StrategyDecision.id == dec.id))).scalar_one()
    p2 = (await db.execute(select(PositionState).where(
        PositionState.id == pos.id))).scalar_one()
    e2 = (await db.execute(select(ExecutionResult).where(
        ExecutionResult.id == ex.id))).scalar_one()

    # el 7º decimal sobrevive (tolerancia ≪ Δ de truncar a 6 decimales, 5e-7)
    assert abs(float(d2.atr_value) - _6J_ATR) < 1e-12
    assert abs(float(d2.sl_price) - (_6J - _6J_ATR)) < 1e-12
    assert abs(float(d2.tp_price) - (_6J + _6J_ATR)) < 1e-12
    assert abs(float(p2.entry_price) - _6J) < 1e-12
    assert abs(float(e2.entry_price) - _6J) < 1e-12
    assert abs(float(e2.exit_price) - (_6J + _6J_ATR)) < 1e-12
