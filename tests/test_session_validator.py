"""SessionValidator tests.

Tests day-of-week and session hours per asset.
MES/MNQ/etc: Mon-Fri 09:30-15:45 ET
MGC: Mon-Fri 08:20-13:30 ET
MJY/M6E/6J/6E: Sun-Fri 18:00-17:00 ET next day (crosses midnight)
"""
from datetime import datetime, time
from unittest.mock import patch

import pytest

from app.models.asset_profile import AssetProfile
from app.services.session_validator import SessionValidator


def _make_asset(symbol: str, session_config: dict) -> AssetProfile:
    return AssetProfile(
        symbol=symbol,
        name=f"{symbol} Test",
        contract_type="futures_micro",
        session_config_json=session_config,
    )


def _pit_session() -> dict:
    """Mon-Fri 09:30-15:45 ET pit session."""
    return {
        "timezone": "America/New_York",
        "days_enabled": [1, 2, 3, 4, 5],  # Mon-Fri (Sunday=0)
        "entry_start": "09:30",
        "entry_end": "15:45",
        "next_day_end": False,
    }


def _fx_24h_session() -> dict:
    """Sun 18:00 – Fri 17:00 ET (crosses midnight)."""
    return {
        "timezone": "America/New_York",
        "days_enabled": [0, 1, 2, 3, 4, 5],  # Sun-Fri
        "entry_start": "18:00",
        "entry_end": "17:00",
        "next_day_end": True,
    }


def test_mes_within_session_morning() -> None:
    """MES at 10:00 ET Monday → within session."""
    asset = _make_asset("MES", _pit_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt, timezone as real_tz
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 15, 10, 0, 0, tzinfo=et
        )  # Monday 10:00 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is True


def test_mes_outside_session_before_open() -> None:
    """MES at 09:29 ET Monday → outside session."""
    asset = _make_asset("MES", _pit_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 15, 9, 29, 0, tzinfo=et
        )  # Monday 09:29 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is False


def test_mes_outside_session_after_close() -> None:
    """MES at 15:46 ET Monday → outside session."""
    asset = _make_asset("MES", _pit_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 15, 15, 46, 0, tzinfo=et
        )  # Monday 15:46 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is False


def test_mes_weekend_not_allowed() -> None:
    """MES at 10:00 ET Saturday → outside session (weekend)."""
    asset = _make_asset("MES", _pit_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 20, 10, 0, 0, tzinfo=et
        )  # Saturday 10:00 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is False


def test_mjy_within_24h_session_afternoon() -> None:
    """MJY at 14:00 ET Tuesday → within 24h session."""
    asset = _make_asset("MJY", _fx_24h_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 16, 14, 0, 0, tzinfo=et
        )  # Tuesday 14:00 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is True


def test_mjy_within_24h_session_morning() -> None:
    """MJY at 02:00 ET Tuesday → within 24h session (after Sunday 18:00 open)."""
    asset = _make_asset("MJY", _fx_24h_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 16, 2, 0, 0, tzinfo=et
        )  # Tuesday 02:00 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is True


def test_mjy_outside_24h_session_gap() -> None:
    """MJY at 17:30 ET Friday → outside session (gap 17:00-18:00 ET Fri-Sun)."""
    asset = _make_asset("MJY", _fx_24h_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 19, 17, 30, 0, tzinfo=et
        )  # Friday 17:30 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is False


def test_mjy_saturday_not_allowed() -> None:
    """MJY at 14:00 ET Saturday → outside session (only Sun-Fri).

    June 20, 2026 is the actual Saturday (Sunday=0 convention: %w=6).
    """
    asset = _make_asset("MJY", _fx_24h_session())
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 20, 14, 0, 0, tzinfo=et
        )  # Saturday 14:00 ET (June 20 is Saturday)
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is False


def test_mgc_within_pit_session() -> None:
    """MGC at 10:00 ET Monday (08:20-13:30 ET pit) → within session."""
    mgc_session = {
        "timezone": "America/New_York",
        "days_enabled": [1, 2, 3, 4, 5],
        "entry_start": "08:20",
        "entry_end": "13:30",
        "next_day_end": False,
    }
    asset = _make_asset("MGC", mgc_session)
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 15, 10, 0, 0, tzinfo=et
        )  # Monday 10:00 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is True


def test_mgc_outside_pit_session() -> None:
    """MGC at 14:00 ET Monday (pit closes 13:30) → outside session."""
    mgc_session = {
        "timezone": "America/New_York",
        "days_enabled": [1, 2, 3, 4, 5],
        "entry_start": "08:20",
        "entry_end": "13:30",
        "next_day_end": False,
    }
    asset = _make_asset("MGC", mgc_session)
    validator = SessionValidator()

    with patch(
        "app.services.session_validator.datetime"
    ) as mock_dt:
        from datetime import datetime as real_dt
        from zoneinfo import ZoneInfo

        et = ZoneInfo("America/New_York")
        mock_dt.now.return_value = real_dt(
            2026, 6, 15, 14, 0, 0, tzinfo=et
        )  # Monday 14:00 ET
        mock_dt.side_effect = lambda *args, **kwargs: real_dt(*args, **kwargs)

        assert validator.is_within_session(asset) is False


def test_no_session_config_always_allowed() -> None:
    """Asset with no session_config_json → always allowed."""
    asset = _make_asset("XYZ", None)
    validator = SessionValidator()

    assert validator.is_within_session(asset) is True
