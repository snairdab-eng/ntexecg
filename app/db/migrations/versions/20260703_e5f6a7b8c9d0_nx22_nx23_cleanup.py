"""NX-22/NX-23/NX-26 — hash de tokens, limpieza de columnas muertas, scored_signals.

ADDS (nuevas, nullables/con default — sin impacto en filas existentes):
  - strategies.webhook_token_hash (NX-22)
  - strategy_performance.scored_signals (NX-26)

DROPS (verificado por grep exhaustivo: cero lectores fuera de los modelos):
  - strategies.pine_script_ticker_note
  - strategy_profiles: profile_name, routing_mode, allowed_accounts_json,
    allowed_symbols_json, timezone, days_enabled_json, entry_start_time,
    entry_end_time, cooldown_minutes, daily_profit_lock
  - global_profile: days_enabled_json, entry_start_time, entry_end_time,
    entry_cutoff_time, global_daily_profit_lock, default_quantity,
    news_impact_levels_json
  - asset_profiles.cooldown_minutes
  - tabla economic_events (modelo eliminado)

SE CONSERVAN a propósito: conflict_logs (NX-18 Fase C la usará),
global_profile.retry_* (NX-15 los consume), Strategy.enabled y
Strategy.traderspost_webhook_url (tienen lectores en UI — consolidación futura).

Reversible: downgrade recrea columnas/tabla con los tipos del esquema inicial
(NOT NULL con server_default donde aplica). Los DATOS de las columnas dropeadas
no se recuperan (eran basura sin lectores); los tokens hasheados no vuelven a
claro (regenerar si se downgradea NX-22).

Revision ID: e5f6a7b8c9d0
Revises: c3d4e5f6a7b8
"""
from alembic import op
import sqlalchemy as sa

revision = "e5f6a7b8c9d0"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ADDS ────────────────────────────────────────────────────────────
    with op.batch_alter_table("strategies") as b:
        b.add_column(sa.Column("webhook_token_hash", sa.String(length=64),
                               nullable=True))
        b.drop_column("pine_script_ticker_note")

    with op.batch_alter_table("strategy_performance") as b:
        b.add_column(sa.Column("scored_signals", sa.Integer(), nullable=False,
                               server_default="0"))

    # ── DROPS ───────────────────────────────────────────────────────────
    with op.batch_alter_table("strategy_profiles") as b:
        for col in ("profile_name", "routing_mode", "allowed_accounts_json",
                    "allowed_symbols_json", "timezone", "days_enabled_json",
                    "entry_start_time", "entry_end_time", "cooldown_minutes",
                    "daily_profit_lock"):
            b.drop_column(col)

    with op.batch_alter_table("global_profile") as b:
        for col in ("days_enabled_json", "entry_start_time", "entry_end_time",
                    "entry_cutoff_time", "global_daily_profit_lock",
                    "default_quantity", "news_impact_levels_json"):
            b.drop_column(col)

    with op.batch_alter_table("asset_profiles") as b:
        b.drop_column("cooldown_minutes")

    op.drop_table("economic_events")


def downgrade() -> None:
    op.create_table(
        "economic_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("impact", sa.String(length=20), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    with op.batch_alter_table("asset_profiles") as b:
        b.add_column(sa.Column("cooldown_minutes", sa.Integer(), nullable=True))

    with op.batch_alter_table("global_profile") as b:
        b.add_column(sa.Column("days_enabled_json", sa.JSON(), nullable=False,
                               server_default='[1, 2, 3, 4, 5]'))
        b.add_column(sa.Column("entry_start_time", sa.Time(), nullable=True))
        b.add_column(sa.Column("entry_end_time", sa.Time(), nullable=True))
        b.add_column(sa.Column("entry_cutoff_time", sa.Time(), nullable=True))
        b.add_column(sa.Column("global_daily_profit_lock",
                               sa.Numeric(precision=12, scale=2), nullable=True))
        b.add_column(sa.Column("default_quantity", sa.Integer(), nullable=False,
                               server_default="1"))
        b.add_column(sa.Column("news_impact_levels_json", sa.JSON(),
                               nullable=False, server_default='["high"]'))

    with op.batch_alter_table("strategy_profiles") as b:
        b.add_column(sa.Column("profile_name", sa.String(length=100), nullable=True))
        b.add_column(sa.Column("routing_mode", sa.String(length=30),
                               nullable=False, server_default="specific_accounts"))
        b.add_column(sa.Column("allowed_accounts_json", sa.JSON(), nullable=True))
        b.add_column(sa.Column("allowed_symbols_json", sa.JSON(), nullable=True))
        b.add_column(sa.Column("timezone", sa.String(length=50), nullable=True))
        b.add_column(sa.Column("days_enabled_json", sa.JSON(), nullable=True))
        b.add_column(sa.Column("entry_start_time", sa.Time(), nullable=True))
        b.add_column(sa.Column("entry_end_time", sa.Time(), nullable=True))
        b.add_column(sa.Column("cooldown_minutes", sa.Integer(), nullable=True))
        b.add_column(sa.Column("daily_profit_lock",
                               sa.Numeric(precision=12, scale=2), nullable=True))

    with op.batch_alter_table("strategy_performance") as b:
        b.drop_column("scored_signals")

    with op.batch_alter_table("strategies") as b:
        b.add_column(sa.Column("pine_script_ticker_note", sa.Text(), nullable=True))
        b.drop_column("webhook_token_hash")
