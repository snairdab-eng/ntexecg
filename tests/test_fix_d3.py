"""FIX-D3 — cancelación explícita de piernas al cierre.

TradersPost soporta cancelar órdenes de trabajo por webhook (webhook-spec.json:
`cancel` boolean top-level). El exit lleva cancel:true → cancela las C2/C3 no
llenadas ANTES de aplanar (sin pierna huérfana, R-RA6). Tests adversariales:
  · el exit (simple y escalonado) lleva cancel:true; se audita;
  · NINGUNA entrada (simple ni C1..Cn) lo lleva (cancelaría su bracket);
  · residual: exit con entrega FALLIDA → cancel no tomó → posición UNKNOWN visible.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.audit_log import AuditLog
from app.models.normalized_signal import NormalizedSignal
from app.models.position_state import PositionState
from app.models.strategy import Strategy
from app.models.webhook_delivery import WebhookDelivery
from app.services.filter_pipeline import PipelineResult
from app.services.payload_builder import PayloadBuilder

UTC = timezone.utc


def _signal(action="buy", sentiment="long", role="entry_long",
            mapped="MESU2025", price=5500.0, qty=1) -> NormalizedSignal:
    s = NormalizedSignal(
        raw_signal_id=uuid.uuid4(), strategy_id="mes", ticker_received="MES",
        mapped_symbol=mapped, action=action, sentiment=sentiment, signal_role=role,
        price=price, quantity=qty, signal_ts=datetime.now(UTC),
        dedupe_key=uuid.uuid4().hex)
    s.id = uuid.uuid4()
    return s


def _result(sl=5484.0, tp=None, atr=8.0) -> PipelineResult:
    return PipelineResult(outcome="APPROVE", score=100, sl_price=sl, tp_price=tp,
                          atr_value=atr, market_data_provider="Mock")


# ---------------------------------------------------------------------------
# 1) EXIT lleva cancel:true (ambos lados) — camino soportado
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action,role", [("exit", "exit_long"), ("exit", "exit_short")])
def test_exit_payload_carries_cancel(action, role):
    p = PayloadBuilder().build(
        _signal(action=action, sentiment="flat", role=role), None, {}, _result(sl=None))
    assert p["action"] == "exit"
    assert p["cancel"] is True
    assert "stopLoss" not in p and "sentiment" not in p


def test_exit_via_signal_role_carries_cancel():
    # is_exit también se detecta por signal_role (action puede venir "sell")
    p = PayloadBuilder().build(
        _signal(action="sell", sentiment="flat", role="exit_short"),
        None, {}, _result(sl=None))
    assert p["cancel"] is True


# ---------------------------------------------------------------------------
# 2) Adversarial — NINGUNA entrada lleva cancel (cancelaría su propio bracket)
# ---------------------------------------------------------------------------

def test_entry_never_carries_cancel():
    p = PayloadBuilder().build(_signal(), None, {}, _result(sl=5484.0, tp=5520.0))
    assert "cancel" not in p


def test_scaled_entry_legs_never_carry_cancel():
    se = {"mode": "execute", "quantities": [2, 2, 1], "levels": [1.0, 2.0],
          "max_micro_contracts": 10}
    legs = PayloadBuilder().build_scaled(
        _signal(), None, {"scale_entry": se, "tick_size": 0.25},
        _result(sl=5484.0, tp=5520.0))
    assert len(legs) == 3
    assert all("cancel" not in leg for leg in legs)      # ni C1 ni C2/C3


def test_scaled_exit_returns_single_cancel_payload():
    # build_scaled sobre un exit → un solo payload de cierre con cancel:true
    se = {"mode": "execute", "quantities": [2, 2], "levels": [1.0]}
    legs = PayloadBuilder().build_scaled(
        _signal(action="exit", sentiment="flat", role="exit_long"),
        None, {"scale_entry": se}, _result(sl=None))
    assert len(legs) == 1 and legs[0]["cancel"] is True


# ---------------------------------------------------------------------------
# 3) Integración — forced_exit: payload con cancel + audit cancel_requested
# ---------------------------------------------------------------------------

def _long_position(strategy_id="fx", symbol="MESU2026", opened_min=120, now=None):
    now = now or datetime.now(UTC)
    return PositionState(
        strategy_id=strategy_id, account_id="paper_default", symbol=symbol,
        state="LONG", state_source="estimated", direction="long", quantity=1,
        risk_plan_json={"opened_at": (now - timedelta(minutes=opened_min)).isoformat()})


@pytest.mark.asyncio
async def test_forced_exit_requests_cancel_and_audits(db: AsyncSession):
    from app.services.forced_exit import dispatch_forced_exit
    strat = Strategy(strategy_id="fx", name="FX", asset_symbol="MES",
                     timeframe="5m", status="paper", enabled=True)
    pos = _long_position()
    db.add_all([strat, pos])
    await db.flush()
    config = {"traderspost_webhook_url": None, "dry_run": True,
              "traderspost_enabled": False, "timezone": "America/New_York"}

    await dispatch_forced_exit(db, pos, strat, config, "max_holding", settings)

    deliv = (await db.execute(select(WebhookDelivery).where(
        WebhookDelivery.strategy_id == "fx"))).scalars().first()
    assert deliv.payload_json["action"] == "exit"
    assert deliv.payload_json["cancel"] is True          # FIX-D3
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "FORCED_EXIT"))).scalars().first()
    assert audit.new_value_json["cancel_requested"] is True


# ---------------------------------------------------------------------------
# 4) Adversarial residual — exit con entrega FALLIDA → cancel no tomó → UNKNOWN
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failed_exit_leaves_position_unknown_and_audits(db: AsyncSession, monkeypatch):
    import app.services.forced_exit as fe
    from app.services.traderspost_client import TradersPostClient, WebhookDeliveryResult

    strat = Strategy(strategy_id="fx", name="FX", asset_symbol="MES",
                     timeframe="5m", status="live", enabled=True)
    pos = _long_position()
    db.add_all([strat, pos])
    await db.flush()

    async def _failed_send(self, url, payload, **kw):
        # cancel:true viaja en el payload, pero la ENTREGA falla (broker/red)
        assert payload.get("cancel") is True
        return WebhookDeliveryResult(
            status="FAILED", payload_json=payload, url_masked="masked",
            response_status_code=500, response_body="boom", attempts=3,
            latency_ms=1, error_message="http_500")
    monkeypatch.setattr(TradersPostClient, "send", _failed_send)

    config = {"traderspost_webhook_url": "https://x/y/tok", "dry_run": False,
              "traderspost_enabled": True, "timezone": "America/New_York"}
    await fe.dispatch_forced_exit(db, pos, strat, config, "max_holding", settings)

    # NX-08 — entrega fallida en todos los destinos → estado incierto VISIBLE
    p = (await db.execute(select(PositionState).where(
        PositionState.account_id == "paper_default",
        PositionState.symbol == "MESU2026"))).scalar_one()
    assert p.state == "UNKNOWN"
    # el intento de cancelación quedó registrado, con any_sent False (residual visible)
    audit = (await db.execute(select(AuditLog).where(
        AuditLog.action == "FORCED_EXIT"))).scalars().first()
    assert audit.new_value_json["cancel_requested"] is True
    assert audit.new_value_json["any_sent"] is False
