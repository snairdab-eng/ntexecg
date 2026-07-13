"""add luxy_exploracion (LX-10 — snapshot server-side de la exploración Luxy)

Almacén PROPIO del diagnóstico Luxy (una fila por estrategia), separado de
pipeline_config_json: guardar/borrar aquí NO toca config de producción. El front
descarta el snapshot si `estudio_id` no corresponde al estudio vigente (misma
invalidación que LX-9). Precedencia de carga: localStorage → este snapshot.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-12 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'luxy_exploracion',
        sa.Column('strategy_id', sa.String(length=100), nullable=False),
        sa.Column('estado_json', sa.JSON(), nullable=True),
        sa.Column('estudio_id', sa.String(length=80), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['strategy_id'], ['strategies.strategy_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('strategy_id'),
    )


def downgrade() -> None:
    op.drop_table('luxy_exploracion')
