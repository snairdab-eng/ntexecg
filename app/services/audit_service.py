"""AuditService — immutable audit trail. NEVER raises (REQ-0801).

If a write fails, the error is logged and execution continues. An audit
failure must never break signal processing or a UI action.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


class AuditService:
    """Writes AuditLog entries. Swallows all exceptions by design."""

    async def log(
        self,
        db: AsyncSession,
        actor: str,
        action: str,
        object_type: str,
        object_id: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        reason: str | None = None,
        ip: str | None = None,
    ) -> AuditLog | None:
        """Create an AuditLog entry. Returns the entry, or None if it failed.

        Never raises — audit must not break the calling flow.
        """
        try:
            entry = AuditLog(
                actor=actor,
                action=action,
                object_type=object_type,
                object_id=object_id,
                old_value_json=old_value,
                new_value_json=new_value,
                reason=reason,
                ip_address=ip,
            )
            db.add(entry)
            await db.flush()
            return entry
        except Exception as exc:
            # Log and continue — do NOT propagate
            from loguru import logger
            logger.error(
                "audit_log_failed action={} object_type={} object_id={} error={}",
                action, object_type, object_id, exc,
            )
            return None

    async def log_strategy_change(
        self,
        db: AsyncSession,
        actor: str,
        strategy_id: str,
        old_data: dict | None,
        new_data: dict | None,
        action: str = "STATUS_CHANGE",
        reason: str | None = None,
    ) -> AuditLog | None:
        """Convenience wrapper for strategy status/config changes."""
        return await self.log(
            db,
            actor=actor,
            action=action,
            object_type="Strategy",
            object_id=strategy_id,
            old_value=old_data,
            new_value=new_data,
            reason=reason,
        )

    async def log_system_event(
        self,
        db: AsyncSession,
        action: str,
        object_type: str,
        object_id: str | None = None,
        details: dict | None = None,
    ) -> AuditLog | None:
        """Convenience wrapper for automated system events (actor='system')."""
        return await self.log(
            db,
            actor="system",
            action=action,
            object_type=object_type,
            object_id=object_id,
            new_value=details,
        )
