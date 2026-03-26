"""add workout_logs table

Revision ID: 003
Revises: 002
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workout_logs",
        sa.Column("id",             sa.Integer(),   autoincrement=True, nullable=False),
        sa.Column("user_id",        sa.BigInteger(), nullable=False),
        sa.Column("log_date",       sa.Date(),       nullable=False),
        sa.Column("logged_at",      sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("feeling",        sa.String(20),   nullable=True),
        sa.Column("equipment",      sa.String(20),   nullable=True),
        sa.Column("duration_min",   sa.Integer(),    nullable=True),
        sa.Column("completed",      sa.String(20),   nullable=True),
        sa.Column("water_bonus_ml", sa.Integer(),    nullable=False, server_default="0"),
        sa.Column("plan_preview",   sa.Text(),       nullable=True),
        sa.Column("injury_zone",    sa.String(64),   nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workout_logs_user_date", "workout_logs", ["user_id", "log_date"])


def downgrade() -> None:
    op.drop_index("ix_workout_logs_user_date", table_name="workout_logs")
    op.drop_table("workout_logs")
