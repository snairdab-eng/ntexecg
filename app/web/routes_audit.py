"""Audit log UI — paginated, filterable by action/actor."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.web.common import render, flash_messages

router = APIRouter()

_PAGE_SIZE = 50


@router.get("/ui/audit", response_class=HTMLResponse)
async def audit_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    action: str = "",
    actor: str = "",
    page: int = 1,
) -> HTMLResponse:
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor:
        stmt = stmt.where(AuditLog.actor == actor)

    page = max(1, page)
    stmt = stmt.limit(_PAGE_SIZE).offset((page - 1) * _PAGE_SIZE)
    result = await db.execute(stmt)
    logs = list(result.scalars().all())

    # Distinct actions/actors for filter dropdowns
    actions_res = await db.execute(select(AuditLog.action).distinct())
    actors_res = await db.execute(select(AuditLog.actor).distinct())

    return await render(
        request, "audit.html",
        {
            "logs": logs,
            "actions": sorted({a[0] for a in actions_res.all()}),
            "actors": sorted({a[0] for a in actors_res.all()}),
            "filter_action": action, "filter_actor": actor, "page": page,
            "messages": flash_messages(request),
        }, db=db,
    )
