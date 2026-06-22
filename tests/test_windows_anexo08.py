"""Anexo 08 §5 — repeatable operation windows (each window has its own days).

Resolves cases like "Mon-Thu 09:00-15:45 / Fri 09:00-12:00". Backward compatible:
a session_config without "windows" uses the legacy single-window logic.
"""
from contextlib import contextmanager
from datetime import datetime as real_dt
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.services.session_validator import SessionValidator

ET = "America/New_York"


def _multi_window() -> dict:
    return {
        "timezone": ET,
        "windows": [
            {"days": [1, 2, 3, 4], "start": "09:00", "end": "15:45"},  # Mon-Thu (Sun=0)
            {"days": [5], "start": "09:00", "end": "12:00"},            # Fri short
        ],
    }


@contextmanager
def _at(year, month, day, hour, minute):
    """Patch session_validator.datetime so now() is deterministic in ET."""
    with patch("app.services.session_validator.datetime") as mock_dt:
        mock_dt.now.return_value = real_dt(year, month, day, hour, minute,
                                           tzinfo=ZoneInfo(ET))
        mock_dt.side_effect = lambda *a, **k: real_dt(*a, **k)
        yield


# 2026-06-15 is a Monday; 18 = Thursday; 19 = Friday; 20 = Saturday; 21 = Sunday.
# Day numbers below use the Sunday=0 convention (%w): Mon=1 .. Fri=5, Sat=6, Sun=0.

def test_thursday_within_long_window():
    with _at(2026, 6, 18, 10, 0):
        assert SessionValidator().is_within_session_config(_multi_window()) is True


def test_thursday_after_1545_outside():
    with _at(2026, 6, 18, 16, 0):
        assert SessionValidator().is_within_session_config(_multi_window()) is False


def test_friday_morning_within_short_window():
    with _at(2026, 6, 19, 11, 0):
        assert SessionValidator().is_within_session_config(_multi_window()) is True


def test_friday_afternoon_outside_short_window():
    with _at(2026, 6, 19, 13, 0):
        assert SessionValidator().is_within_session_config(_multi_window()) is False


def test_saturday_no_window():
    with _at(2026, 6, 20, 11, 0):
        assert SessionValidator().is_within_session_config(_multi_window()) is False


def test_window_with_next_day_end():
    cfg = {"timezone": ET,
           "windows": [{"days": [0, 1, 2, 3, 4, 5], "start": "18:00",
                        "end": "17:00", "next_day_end": True}]}  # Sun-Fri
    # Sunday (%w=0) 20:00 → within the overnight window
    with _at(2026, 6, 21, 20, 0):  # 2026-06-21 is a Sunday
        assert SessionValidator().is_within_session_config(cfg) is True


def test_legacy_single_window_still_works():
    cfg = {"timezone": ET, "days_enabled": [1, 2, 3, 4, 5],
           "entry_start": "09:30", "entry_end": "15:45"}
    with _at(2026, 6, 18, 10, 0):  # Thursday 10:00
        assert SessionValidator().is_within_session_config(cfg) is True
    with _at(2026, 6, 20, 10, 0):  # Saturday
        assert SessionValidator().is_within_session_config(cfg) is False
