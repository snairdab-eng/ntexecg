"""NX-24 — alias de strategy_id legacy (renames).

`scripts/rename_strategy.py` recrea la estrategia con el id nuevo y retira o
borra la vieja, dejando su huella en AuditLog:
    object_type="Strategy", object_id=<id_nuevo>,
    old_value={"renamed_from": <id_viejo>, ...}

Ese registro ES el mapa de alias (sin migración ni archivo aparte, respaldado
con la DB). Este módulo lo lee y resuelve cadenas (a→b→c ⇒ a→c) para que la
analítica agrupe por el id canónico y los renames no partan las series.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog


async def get_alias_map(db: AsyncSession) -> dict[str, str]:
    """Mapa {id_viejo → id_canónico} con cadenas resueltas y guard de ciclos."""
    rows = await db.execute(
        select(AuditLog.object_id, AuditLog.old_value_json).where(
            AuditLog.object_type == "Strategy",
            AuditLog.old_value_json.is_not(None),
        )
    )
    direct: dict[str, str] = {}
    for new_id, old_value in rows.all():
        old_id = (old_value or {}).get("renamed_from")
        if old_id and new_id and old_id != new_id:
            direct[old_id] = new_id

    def _canon(x: str) -> str:
        seen: set[str] = set()
        while x in direct and x not in seen:
            seen.add(x)
            x = direct[x]
        return x

    return {old: _canon(old) for old in direct}


def canonical_id(strategy_id: str | None, alias_map: dict[str, str]) -> str:
    """Id canónico de una estrategia (él mismo si no fue renombrada)."""
    sid = strategy_id or "—"
    return alias_map.get(sid, sid)
