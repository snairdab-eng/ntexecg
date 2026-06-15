"""SessionValidator — checks day of week and trading hours per asset.

MES/MNQ/MYM/M2K: Mon-Fri 09:30-15:45 ET
MGC: Mon-Fri 08:20-13:30 ET
MJY/M6E/6J/6E: Sun 18:00 ET – Fri 17:00 ET (next_day_end=true)
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.models.asset_profile import AssetProfile


class SessionValidator:
    """Validates day-of-week and session hours per asset."""

    def is_within_session(self, asset_profile: AssetProfile) -> bool:
        """Check session using an AssetProfile object (backward-compatible).

        Delegates to is_within_session_config using the asset's session_config_json.
        """
        return self.is_within_session_config(asset_profile.session_config_json)

    def is_within_session_config(self, session_config: dict | None) -> bool:
        """Check if current time (America/New_York) is within the trading session.

        This is the canonical method — the FilterPipeline passes
        config["session_config_json"] directly (already merged by ConfigResolver),
        so there is a single source of truth for session configuration.

        Returns True if:
          1. Day of week matches days_enabled
          2. Current time is within session window
        """
        session_config = session_config or {}
        if not session_config:
            return True  # No session config = always allowed

        # Get current time in America/New_York
        tz = ZoneInfo(session_config.get("timezone", "America/New_York"))
        now_dt = datetime.now(tz)
        current_time = now_dt.time()
        current_day = now_dt.weekday()  # 0=Mon, 6=Sun

        # Check day of week
        allowed_days: list = session_config.get("days_enabled", [0, 1, 2, 3, 4])
        if current_day not in allowed_days:
            return False

        # Check session hours
        entry_start = session_config.get("entry_start", "09:30")
        entry_end = session_config.get("entry_end", "15:45")
        next_day_end = session_config.get("next_day_end", False)

        # Parse times (format: "HH:MM")
        start_time = self._parse_time(entry_start)
        end_time = self._parse_time(entry_end)

        if next_day_end:
            # Session crosses midnight: 18:00 today to 17:00 next day
            # Within session if: time >= start OR time < end
            return current_time >= start_time or current_time < end_time

        # Regular session: within if start <= time <= end
        return start_time <= current_time <= end_time

    @staticmethod
    def _parse_time(time_str: str) -> time:
        """Parse HH:MM string to time object."""
        if isinstance(time_str, time):
            return time_str
        parts = time_str.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return time(hour, minute)
