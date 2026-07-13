import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LuxyExploracion(Base):
    """LX-10 — Snapshot server-side de la EXPLORACIÓN Luxy (diagnóstico), una
    fila por estrategia. Sobrevive cambios de navegador/equipo; complementa el
    auto-guardado de sesión en localStorage (LX-9).

    ALMACÉN PROPIO, JAMÁS `pipeline_config_json`: esto es diagnóstico con otra
    vida, NO config de producción. Los únicos puentes a producción siguen siendo
    'Aplicar' (SL/TP) y 'Proponer ventanas' (L2). Guardar/borrar aquí no cambia
    ninguna decisión que corra el motor.

    `estado_json` = {S: palancas, dir: str, ZON: [bool], DON: {dow: bool}}.
    `estudio_id` = `<fecha>:<sha_master[:12]>` — el front descarta el snapshot
    si el estudio vigente cambió (misma invalidación que LX-9). La restauración
    SIEMPRE cae en "estimación · aprox": VLAST (validados del motor) jamás viaja.
    """

    __tablename__ = "luxy_exploracion"

    # Uno por estrategia: strategy_id es PK y FK (sobreescribe al guardar).
    strategy_id: Mapped[str] = mapped_column(
        String(100), ForeignKey("strategies.strategy_id", ondelete="CASCADE"),
        primary_key=True,
    )

    estado_json: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    estudio_id: Mapped[Optional[str]] = mapped_column(String(80))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
