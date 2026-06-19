"""add_tick_value_size_to_symbol_map (Anexo 08 #4 instrument catalog)

Revision ID: b2f1c3d4e5f6
Revises: a1029493ee79
Create Date: 2026-06-19 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b2f1c3d4e5f6'
down_revision: Union[str, None] = 'a1029493ee79'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('symbol_maps', sa.Column('tick_value', sa.Numeric(12, 4), nullable=True))
    op.add_column('symbol_maps', sa.Column('tick_size', sa.Numeric(14, 8), nullable=True))


def downgrade() -> None:
    op.drop_column('symbol_maps', 'tick_size')
    op.drop_column('symbol_maps', 'tick_value')
