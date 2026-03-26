"""add sleep_logs table

Revision ID: 002
Revises: 001
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sleep_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("logged_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("sleep_hours", sa.Float(), nullable=True),
        sa.Column("quality_score", sa.Integer(), nullable=True),
        sa.Column("bedtime", sa.Text(), nullable=True),
        sa.Column("wakeup_time", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("affected_workout", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sleep_logs_user_date", "sleep_logs", ["user_id", "log_date"])


def downgrade() -> None:
    op.drop_index("ix_sleep_logs_user_date", table_name="sleep_logs")
    op.drop_table("sleep_logs")
