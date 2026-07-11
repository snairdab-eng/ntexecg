"""Portafolio UI — config GLOBAL del Módulo de Riesgo de Portafolio (P-A).

Muestra los interruptores de las 8 reglas (solo la 1 encendida al nacer) + la
vista de exposición en vivo (posiciones por activo, micros totales). El toggle
persiste en `PortfolioConfig`; la aplicación real de la regla vive en el
guardarraíl L3 (`app/services/portfolio_guard.py`).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.portfolio_config import PortfolioConfig
from app.services.audit_service import AuditService
from app.services.portfolio_guard import RULE_META, compute_exposure, merge_rules
from app.web.common import render, redirect, flash_messages

router = APIRouter()

_VALID_KEYS = {m["key"] for m in RULE_META}


async def _get_or_create_config(db: AsyncSession) -> PortfolioConfig:
    result = await db.execute(
        select(PortfolioConfig).where(PortfolioConfig.active.is_(True)).limit(1)
    )
    cfg = result.scalar_one_or_none()
    if cfg is None:
        # Nace con los defaults (solo la regla 1 ON) materializados.
        cfg = PortfolioConfig(rules_json=dict(merge_rules(None)), params_json={})
        db.add(cfg)
        await db.flush()
    return cfg


@router.get("/ui/portfolio", response_class=HTMLResponse)
async def portfolio_page(
    request: Request, db: AsyncSession = Depends(get_db)
) -> HTMLResponse:
    cfg = await _get_or_create_config(db)
    rules = merge_rules(cfg.rules_json)
    exposure = await compute_exposure(db)
    await db.commit()

    rows = [{**m, "enabled": rules.get(m["key"], False)} for m in RULE_META]
    return await render(
        request, "portfolio.html",
        {
            "rules": rows,
            "exposure": exposure,
            "messages": flash_messages(request),
        }, db=db,
    )


@router.post("/ui/portfolio/rules")
async def toggle_rule(
    request: Request,
    db: AsyncSession = Depends(get_db),
    rule_key: str = Form(...),
    enabled: str = Form(...),
) -> RedirectResponse:
    """Enciende/apaga una regla del portafolio (persiste en PortfolioConfig)."""
    if rule_key not in _VALID_KEYS:
        return redirect("/ui/portfolio", flash="Regla desconocida",
                        category="error")

    cfg = await _get_or_create_config(db)
    rules = merge_rules(cfg.rules_json)
    new_value = enabled == "on"
    old_value = rules.get(rule_key, False)
    rules[rule_key] = new_value
    # Reasignar el dict entero para que SQLAlchemy detecte el cambio del JSON.
    cfg.rules_json = dict(rules)

    await AuditService().log(
        db, actor="admin", action="PORTFOLIO_RULE_CHANGE",
        object_type="PortfolioConfig", object_id=rule_key,
        old_value={rule_key: old_value}, new_value={rule_key: new_value},
        reason="portfolio rule toggled via UI",
    )
    await db.commit()
    estado = "encendida" if new_value else "apagada"
    return redirect("/ui/portfolio", flash=f"Regla {estado}")
