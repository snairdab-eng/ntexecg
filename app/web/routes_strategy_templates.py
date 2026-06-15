"""Strategy Templates UI — list + create."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.strategy_template import StrategyTemplate
from app.services.audit_service import AuditService
from app.web.common import render, redirect, flash_messages

router = APIRouter()


@router.get("/ui/strategy-templates", response_class=HTMLResponse)
async def list_templates(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    result = await db.execute(select(StrategyTemplate).order_by(StrategyTemplate.name))
    templates_list = list(result.scalars().all())
    return await render(
        request, "strategy_templates.html",
        {"templates_list": templates_list, "messages": flash_messages(request)}, db=db,
    )


@router.post("/ui/strategy-templates/new")
async def create_template(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
    strategy_type: str = Form("trend_following"),
    sl_atr_multiplier: str = Form("1.5"),
    score_minimum: str = Form("70"),
) -> RedirectResponse:
    dup = await db.execute(select(StrategyTemplate).where(StrategyTemplate.name == name))
    if dup.scalar_one_or_none() is not None:
        return redirect(
            "/ui/strategy-templates", flash=f"Template '{name}' ya existe", category="error"
        )

    default_config: dict = {"mode": "paper"}
    try:
        default_config["sl_atr_multiplier"] = float(sl_atr_multiplier)
    except ValueError:
        pass
    try:
        default_config["score_minimum"] = int(score_minimum)
    except ValueError:
        pass

    tpl = StrategyTemplate(
        name=name,
        description=description or None,
        source="luxalgo",
        strategy_type=strategy_type,
        default_config_json=default_config,
    )
    db.add(tpl)
    await AuditService().log(
        db, actor="admin", action="CREATE", object_type="StrategyTemplate",
        object_id=name, new_value=default_config,
    )
    await db.commit()
    return redirect("/ui/strategy-templates", flash=f"Template '{name}' creado")
