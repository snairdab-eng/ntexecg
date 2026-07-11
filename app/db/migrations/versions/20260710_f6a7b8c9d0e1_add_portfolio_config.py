"""add portfolio_config (Módulo de Riesgo de Portafolio — P-A)

Config global del marco de reglas con interruptor (una sola fila activa):
banderas ON/OFF de las 8 reglas + parámetros. Al nacer solo la regla 1 está
encendida (la aplicación real vive en el guardarraíl L3; ver
app/services/portfolio_guard.py). No guarda estado de posiciones — eso lo
lee PositionState.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-10 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'portfolio_config',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('rules_json', sa.JSON(), nullable=True),
        sa.Column('params_json', sa.JSON(), nullable=True),
        sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_by', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade() -> None:
    op.drop_table('portfolio_config')
