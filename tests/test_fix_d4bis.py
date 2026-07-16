"""FIX-D4-bis — la FUENTE no trunca: normalized_signals.price Numeric(20,10).

El precio de la señal (fuente que alimenta el payload) conserva el 7º decimal de
FX (6J tick 5e-7). Tipo de columna (backend-independiente) + round-trip real de 6J
forzando recarga desde DB.
"""
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import Numeric, select

from app.models.normalized_signal import NormalizedSignal

# 6J real, 7 decimales significativos (múltiplo del tick 5e-7). Numeric(18,6) trunca.
_6J = 0.0067895


def test_normalized_price_is_numeric_20_10():
    t = NormalizedSignal.__table__.c["price"].type
    assert isinstance(t, Numeric)
    assert (t.precision, t.scale) == (20, 10), (t.precision, t.scale)


@pytest.mark.asyncio
async def test_6j_signal_price_survives_roundtrip(db):
    ns = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), source="luxalgo", strategy_id="6j",
        ticker_received="6J", mapped_symbol="6JU2025", action="buy",
        sentiment="long", quantity=1, price=_6J,
        signal_ts=datetime.now(timezone.utc), dedupe_key=uuid.uuid4().hex,
        status="pending")
    db.add(ns)
    await db.commit()
    db.expunge_all()                       # fuerza recarga desde DB (no identity-map)

    n2 = (await db.execute(select(NormalizedSignal).where(
        NormalizedSignal.id == ns.id))).scalar_one()
    # el 7º decimal sobrevive (tolerancia ≪ Δ de truncar a 6 decimales, 5e-7)
    assert abs(float(n2.price) - _6J) < 1e-12
