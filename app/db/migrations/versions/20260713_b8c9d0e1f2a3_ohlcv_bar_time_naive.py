"""ohlcv_bars.bar_time → timestamp WITHOUT time zone (LX-6, fix de convención)

Cambia `bar_time` de `timestamptz` (DateTime(timezone=True)) a `timestamp`
naive (DateTime(timezone=False)) para que el wall-clock ET del exchange se
guarde LITERAL y determinista, sin que Postgres imponga un instante según el
`TimeZone` de la sesión. Ese acoplamiento a la sesión fue la causa de la
corrupción heterogénea de `ohlcv_bars` (mezcla ET/UTC por época de ingesta;
diagnóstico LX-6). Con la columna naive, backfill y updater escriben la MISMA
hora que el CSV/trades, y `_et_naive` (lectura) queda como passthrough.

⚠️⚠️ ESTA MIGRACIÓN **NO CONSERVA LOS DATOS EXISTENTES DE FORMA FIABLE** ⚠️⚠️
El cast `timestamptz → timestamp` en Postgres reinterpreta cada valor aplicando
el `TimeZone` de la sesión (`bar_time AT TIME ZONE session_tz`): CORRE LOS
INSTANTES de todas las filas ya almacenadas. Además el histórico previo ya
estaba mal-etiquetado de forma heterogénea (ver scripts/audit_ohlcv_tz.py), así
que NINGÚN cast global lo deja sano.

→ Las filas viejas quedan INVÁLIDAS tras esta migración. El plan operativo es:
   1) aplicar esta migración (código nuevo desplegado),
   2) BACKUP de la tabla (pg_dump),
   3) TRUNCATE ohlcv_bars   ← BLOQUEADO hasta verificar cobertura CSV+bridge,
   4) re-backfill desde los HOLC CSV + re-pull del bridge (ya con el escritor
      corregido), y
   5) audit_ohlcv_tz debe dar "homogéneo ET (sano)".
El TRUNCATE/rebuild es un paso operativo SEPARADO (no lo hace esta migración).

`downgrade` vuelve la columna a timestamptz — igual de destructivo para los
instantes (cast inverso); se documenta por simetría, no porque preserve nada.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-13 00:00:00.000000+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8c9d0e1f2a3'
down_revision: Union[str, None] = 'a7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table: en SQLite (tests) el cambio de tipo requiere recrear la
    # tabla; en Postgres emite un ALTER COLUMN directo. El cast implícito de
    # timestamptz→timestamp usa el TimeZone de sesión y CORRE los instantes de
    # las filas existentes (ver docstring: los datos viejos quedan inválidos y se
    # reconstruyen aparte).
    with op.batch_alter_table('ohlcv_bars') as batch:
        batch.alter_column(
            'bar_time',
            existing_type=sa.DateTime(timezone=True),
            type_=sa.DateTime(timezone=False),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('ohlcv_bars') as batch:
        batch.alter_column(
            'bar_time',
            existing_type=sa.DateTime(timezone=False),
            type_=sa.DateTime(timezone=True),
            existing_nullable=False,
        )
