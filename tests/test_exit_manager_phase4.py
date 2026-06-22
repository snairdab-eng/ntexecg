"""Fase 4 — Exit Manager decision logic (pure) + foundations."""
import uuid
from datetime import datetime, time, timezone, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.exit_manager import ExitManager, _to_time
from app.services.position_service import PositionService
from app.services.config_resolver import ConfigResolver
from app.models.global_profile import GlobalProfile
from app.models.strategy import Strategy

UTC = timezone.utc


def _pos(state="LONG", opened=None):
    plan = {"opened_at": opened.isoformat()} if opened else {}
    return SimpleNamespace(state=state, risk_plan_json=plan)


# ---------------------------------------------------------------------------
# due_exit — pure logic
# ---------------------------------------------------------------------------

def test_not_open_never_exits():
    em = ExitManager()
    for st in ("FLAT", "PENDING_LONG", "EXITING", "LOCKED"):
        assert em.due_exit(_pos(state=st), {"max_holding_minutes": 1},
                           now=datetime(2026, 6, 22, 20, 0, tzinfo=UTC)) is None


def test_max_holding_triggers():
    em = ExitManager()
    now = datetime(2026, 6, 22, 18, 0, tzinfo=UTC)
    opened = now - timedelta(minutes=120)
    r = em.due_exit(_pos(opened=opened),
                    {"max_holding_minutes": 60, "timezone": "America/New_York"},
                    now=now)
    assert r == "max_holding"


def test_max_holding_not_exceeded():
    em = ExitManager()
    now = datetime(2026, 6, 22, 18, 0, tzinfo=UTC)  # 14:00 ET, before any EOD
    opened = now - timedelta(minutes=30)
    r = em.due_exit(_pos(opened=opened),
                    {"max_holding_minutes": 60, "timezone": "America/New_York"},
                    now=now)
    assert r is None


def test_forced_close_eod_triggers():
    em = ExitManager()
    # 20:00 UTC = 16:00 ET (EDT), force_flat at 15:55 → due
    now = datetime(2026, 6, 22, 20, 0, tzinfo=UTC)
    r = em.due_exit(_pos(),
                    {"force_flat_time": time(15, 55), "timezone": "America/New_York"},
                    now=now)
    assert r == "forced_close_eod"


def test_forced_close_eod_before_time():
    em = ExitManager()
    now = datetime(2026, 6, 22, 17, 0, tzinfo=UTC)  # 13:00 ET
    r = em.due_exit(_pos(),
                    {"force_flat_time": "15:55", "timezone": "America/New_York"},
                    now=now)
    assert r is None


def test_overnight_close_when_outside_session():
    stub = SimpleNamespace(is_within_session_config=lambda sc: False)
    em = ExitManager(session_validator=stub)
    r = em.due_exit(_pos(),
                    {"allow_overnight": False, "session_config_json": {"x": 1},
                     "timezone": "America/New_York"},
                    now=datetime(2026, 6, 22, 23, 0, tzinfo=UTC))
    assert r == "overnight_close"


def test_overnight_allowed_does_not_exit():
    stub = SimpleNamespace(is_within_session_config=lambda sc: False)
    em = ExitManager(session_validator=stub)
    r = em.due_exit(_pos(),
                    {"allow_overnight": True, "session_config_json": {"x": 1}},
                    now=datetime(2026, 6, 22, 23, 0, tzinfo=UTC))
    assert r is None


def test_to_time_parsing():
    assert _to_time(time(16, 0)) == time(16, 0)
    assert _to_time("16:00") == time(16, 0)
    assert _to_time("bad") is None
    assert _to_time(None) is None


# ---------------------------------------------------------------------------
# opened_at recorded on entry
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_entry_records_opened_at(db: AsyncSession):
    pos = await PositionService().on_entry_approved(
        db, "s1", "paper_default", "MESU2026", "long", 1, 5500.0, uuid.uuid4())
    assert pos.risk_plan_json and "opened_at" in pos.risk_plan_json
    # parseable ISO
    datetime.fromisoformat(pos.risk_plan_json["opened_at"])


# ---------------------------------------------------------------------------
# ConfigResolver merges force_flat_time / max_holding_minutes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_config_resolves_exit_fields(db: AsyncSession):
    db.add(GlobalProfile(mode="normal", score_minimum=70, active=True,
                         force_flat_time=time(15, 55), max_holding_minutes=90))
    db.add(Strategy(strategy_id="ex1", name="EX", asset_symbol="MES",
                    timeframe="5m", status="paper", enabled=True))
    await db.commit()
    c = await ConfigResolver().resolve(db, "ex1", "MES")
    assert c["force_flat_time"] == time(15, 55)
    assert c["max_holding_minutes"] == 90
