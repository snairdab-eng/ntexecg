"""ExitManager — autonomous forced exits (Fase 4).

Pure decision logic here: given an OPEN position, its merged config and the
current time, decide whether a forced exit is due and WHY. Triggers (in order):
  - max_holding     : held longer than max_holding_minutes.
  - forced_close_eod: local time reached force_flat_time (EOD flat).
  - overnight_close : allow_overnight is False and we're outside the session.

The actual dispatch of the exit lives in the scheduler job; this service has no
side effects so it is trivially testable with an injected `now`.
"""
from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from app.services.session_validator import SessionValidator

# Only act on CONFIRMED open positions. PENDING_*/EXITING/LOCKED/FLAT are left
# alone (nothing real to close, or already exiting / explicitly held).
OPEN_STATES = {"LONG", "SHORT"}


def _to_time(value: object) -> time | None:
    """Accept a datetime.time or an 'HH:MM' string."""
    if isinstance(value, time):
        return value
    if isinstance(value, str) and ":" in value:
        try:
            parts = value.split(":")
            return time(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return None
    return None


class ExitManager:
    def __init__(self, session_validator: SessionValidator | None = None) -> None:
        self._sv = session_validator or SessionValidator()

    def due_exit(self, position: object, config: dict, now: datetime | None = None) -> str | None:
        """Return the trigger reason if a forced exit is due, else None."""
        if getattr(position, "state", None) not in OPEN_STATES:
            return None

        now = now or datetime.now(timezone.utc)
        tz = ZoneInfo(config.get("timezone") or "America/New_York")
        now_local = now.astimezone(tz)

        # 1. max_holding
        max_holding = config.get("max_holding_minutes")
        opened = self._opened_at(position)
        if max_holding and opened is not None:
            if (now - opened).total_seconds() > float(max_holding) * 60.0:
                return "max_holding"

        # 2. forced_close_eod
        fct = _to_time(config.get("force_flat_time"))
        if fct is not None and now_local.time() >= fct:
            return "forced_close_eod"

        # 3. overnight_close (only when overnight is explicitly disallowed)
        if config.get("allow_overnight") is False:
            session_config = config.get("session_config_json")
            if session_config and not self._sv.is_within_session_config(session_config):
                return "overnight_close"

        return None

    @staticmethod
    def _opened_at(position: object) -> datetime | None:
        plan = getattr(position, "risk_plan_json", None) or {}
        raw = plan.get("opened_at")
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw)
        except (ValueError, TypeError):
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
