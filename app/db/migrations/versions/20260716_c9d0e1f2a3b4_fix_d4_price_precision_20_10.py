"""FIX-D4 — price columns Numeric(18,6) → Numeric(20,10)

Widen the decimal scale of the ORDER-PRICE columns in strategy_decisions,
position_states and execution_results so the 7th significant decimal of FX
instruments is not truncated on write (6J tick = 5e-7 = 0.0000005 needs 7
decimals; Numeric(18,6) only kept 6). Non-destructive: widening the scale (6→10)
preserves every stored value, and 10 integer digits (20−10) still dwarf any
futures price (≤6 integer digits). batch_alter_table for SQLite/PostgreSQL parity.

Deploy: alembic upgrade head.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-16 00:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (table, column) tuples whose price precision is widened.
_COLS = [
    ("strategy_decisions", "sl_price"),
    ("strategy_decisions", "tp_price"),
    ("strategy_decisions", "atr_value"),
    ("position_states", "entry_price"),
    ("execution_results", "entry_price"),
    ("execution_results", "exit_price"),
]


def _alter(table: str, col: str, *, from_type: sa.types.TypeEngine,
           to_type: sa.types.TypeEngine) -> None:
    with op.batch_alter_table(table) as b:
        b.alter_column(col, existing_type=from_type, type_=to_type,
                       existing_nullable=True)


def upgrade() -> None:
    for table, col in _COLS:
        _alter(table, col, from_type=sa.Numeric(18, 6), to_type=sa.Numeric(20, 10))


def downgrade() -> None:
    # Reverting NARROWS the scale (10→6): the 7th–10th decimals are lost by design.
    for table, col in _COLS:
        _alter(table, col, from_type=sa.Numeric(20, 10), to_type=sa.Numeric(18, 6))
