"""FIX-D4-bis — normalized_signals.price Numeric(18,6) → Numeric(20,10)

Widen the SOURCE signal-price column so the 7th significant decimal of FX
instruments is not truncated when the normalized signal is persisted (6J tick 5e-7
needs 7 decimals). Downstream price columns were widened in c9d0e1f2a3b4 (FIX-D4);
this closes the source so a future db.refresh()/re-query of the NormalizedSignal
cannot feed a truncated 6J price into a payload. Non-destructive (scale 6→10 keeps
every value; 10 integer digits dwarf any futures price). batch_alter_table for
SQLite/PostgreSQL parity.

Deploy: alembic upgrade head.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-16 00:00:00.000000+00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("normalized_signals") as b:
        b.alter_column("price", existing_type=sa.Numeric(18, 6),
                       type_=sa.Numeric(20, 10), existing_nullable=True)


def downgrade() -> None:
    # Reverting NARROWS the scale (10→6): the 7th–10th decimals are lost by design.
    with op.batch_alter_table("normalized_signals") as b:
        b.alter_column("price", existing_type=sa.Numeric(20, 10),
                       type_=sa.Numeric(18, 6), existing_nullable=True)
