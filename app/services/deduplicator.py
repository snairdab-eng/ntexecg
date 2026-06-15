"""Deduplicator — detects repeated signals within a time window.

Queries normalized_signals WHERE dedupe_key = ? AND created_at >= cutoff.
A signal is a duplicate if the same dedupe_key was processed in the last
window_seconds (default 60). Signals with status='duplicate' have a modified
dedupe_key (prefixed "dup:") and are therefore excluded from future lookups.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.normalized_signal import NormalizedSignal


class Deduplicator:
    async def is_duplicate(
        self,
        db: AsyncSession,
        dedupe_key: str,
        window_seconds: int = 60,
    ) -> bool:
        """Return True if dedupe_key was seen in the last window_seconds."""
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
        result = await db.execute(
            select(NormalizedSignal.id)
            .where(
                NormalizedSignal.dedupe_key == dedupe_key,
                NormalizedSignal.created_at >= cutoff,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None
