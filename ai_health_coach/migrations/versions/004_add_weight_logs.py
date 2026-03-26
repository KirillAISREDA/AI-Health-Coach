"""add weight_logs table

Revision ID: 004
Revises: 003
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weight_logs",
        sa.Column("id",           sa.Integer(),    autoincrement=True, nullable=False),
        sa.Column("user_id",      sa.BigInteger(), nullable=False),
        sa.Column("log_date",     sa.Date(),       nullable=False),
        sa.Column("logged_at",    sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("weight_kg",    sa.Float(),      nullable=False),
        sa.Column("note",         sa.Text(),       nullable=True),
        sa.Column("tdee_updated", sa.Boolean(),    nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_weight_logs_user_date", "weight_logs", ["user_id", "log_date"])


def downgrade() -> None:
    op.drop_index("ix_weight_logs_user_date", table_name="weight_logs")
    op.drop_table("weight_logs")
