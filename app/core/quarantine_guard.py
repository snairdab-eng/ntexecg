"""FIX-D1 — bounded quarantine for rejected (unauthenticated) webhook signals.

An invalid-token webhook still deserves a forensic record (who/when + the payload),
but persisting a full RawSignal for EVERY rejected request lets an attacker flood the
DB with arbitrary payloads (RawSignal.payload_json is the raw, unbounded body). This
in-memory sliding-window cap PER IP bounds how many rejected signals are persisted per
window; beyond the cap the request is still 401'd but NOTHING is written to the DB —
the flood is tapped and the who/when stays in the application log (loguru), which is
not the DoS surface.

Best-effort in memory (same posture as login_guard): lost on restart, not shared
across workers — enough to bound a flood, not to account perfectly. The per-IP list
never grows past the cap, so the guard itself cannot be flooded.
"""
from __future__ import annotations

import time

# Cap: at most QUARANTINE_MAX_PER_WINDOW rejected signals PERSISTED per IP per window.
QUARANTINE_WINDOW_S = 60
QUARANTINE_MAX_PER_WINDOW = 20

# {"ip:1.2.3.4": [timestamps within the window]}
_hits: dict[str, list[float]] = {}


def _prune(key: str, now: float) -> list[float]:
    xs = [t for t in _hits.get(key, []) if now - t < QUARANTINE_WINDOW_S]
    if xs:
        _hits[key] = xs
    else:
        _hits.pop(key, None)
    return xs


def allow(ip: str | None) -> bool:
    """Record a rejected signal from `ip`; return True if it is WITHIN the cap (persist
    the forensic record), False if the cap for this IP/window is already reached (skip
    the DB write). The per-IP list never grows past the cap."""
    now = time.time()
    key = f"ip:{ip or 'unknown'}"
    xs = _prune(key, now)
    if len(xs) >= QUARANTINE_MAX_PER_WINDOW:
        return False
    xs.append(now)
    _hits[key] = xs
    return True


def reset() -> None:
    """Clear all state (tests / restart)."""
    _hits.clear()
