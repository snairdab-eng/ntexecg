"""add execution_results (real trade results + reconciliation)

Revision ID: c3d4e5f6a7b8
Revises: b2f1c3d4e5f6
Create Date: 2026-06-23 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c3d4e5f6a7b8'
down_revision: Union[str, None] = 'b2f1c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'execution_results',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('row_hash', sa.String(length=64), nullable=False),
        sa.Column('signal_id', sa.String(length=64), nullable=True),
        sa.Column('strategy_id', sa.String(length=100), nullable=True),
        sa.Column('symbol', sa.String(length=50), nullable=False),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('quantity', sa.Integer(), nullable=True),
        sa.Column('entry_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('entry_price', sa.Numeric(18, 6), nullable=True),
        sa.Column('exit_time', sa.DateTime(timezone=True), nullable=True),
        sa.Column('exit_price', sa.Numeric(18, 6), nullable=True),
        sa.Column('pnl', sa.Numeric(18, 2), nullable=True),
        sa.Column('pnl_calc', sa.Numeric(18, 2), nullable=True),
        sa.Column('exit_reason', sa.String(length=20), nullable=True),
        sa.Column('fees', sa.Numeric(18, 2), nullable=True),
        sa.Column('matched_decision_id', sa.Uuid(), nullable=True),
        sa.Column('match_method', sa.String(length=20), nullable=False, server_default='unmatched'),
        sa.Column('imported_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('row_hash', name='uq_execution_result_row_hash'),
    )
    op.create_index('ix_execution_results_strategy', 'execution_results', ['strategy_id'])
    op.create_index(
        'ix_execution_results_symbol_entry', 'execution_results', ['symbol', 'entry_time']
    )


def downgrade() -> None:
    op.drop_index('ix_execution_results_symbol_entry', table_name='execution_results')
    op.drop_index('ix_execution_results_strategy', table_name='execution_results')
    op.drop_table('execution_results')
