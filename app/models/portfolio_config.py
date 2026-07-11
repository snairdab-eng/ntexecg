import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PortfolioConfig(Base):
    """Config GLOBAL del Módulo de Riesgo de Portafolio (una sola fila activa).

    Marco de reglas con interruptor (Parte B del diseño): las 8 reglas viven
    aquí como banderas ON/OFF + parámetros. Al nacer SOLO la regla 1 está
    encendida (una posición por ACTIVO entre todas las estrategias); el resto
    son inertes hasta que el operador las active.

    NO guarda estado de posiciones — eso lo lee `PositionState` (P-A reusa el
    estado existente, no inventa uno nuevo). Aquí solo viven los interruptores
    y sus parámetros.
    """

    __tablename__ = "portfolio_config"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    # {rule_key: bool} — banderas de las 8 reglas. Merge sobre DEFAULT_RULES
    # (portfolio_guard.DEFAULT_RULES) para tolerar reglas nuevas sin migración.
    rules_json: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)

    # {param_key: value} — parámetros de las reglas (topes $, nº micros…).
    # Inertes en P-A (solo la regla 1 corre y no toma parámetros).
    params_json: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)

    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    updated_by: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
