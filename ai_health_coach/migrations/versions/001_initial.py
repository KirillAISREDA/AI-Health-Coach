"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-26
"""

from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("first_name", sa.String(64), nullable=True),
        sa.Column("gender", sa.String(10), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("height_cm", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("goal", sa.String(20), nullable=True),
        sa.Column("activity_level", sa.String(20), nullable=True),
        sa.Column("allergies", sa.Text(), nullable=True),
        sa.Column("timezone", sa.String(50), nullable=False, server_default="Europe/Moscow"),
        sa.Column("onboarding_step", sa.String(20), nullable=False, server_default="start"),
        sa.Column("onboarding_done", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("tdee_kcal", sa.Float(), nullable=True),
        sa.Column("water_goal_ml", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_id", "users", ["id"])

    # ── food_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "food_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("logged_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("meal_date", sa.Date(), nullable=False),
        sa.Column("raw_input", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("calories", sa.Float(), nullable=True),
        sa.Column("protein_g", sa.Float(), nullable=True),
        sa.Column("fat_g", sa.Float(), nullable=True),
        sa.Column("carbs_g", sa.Float(), nullable=True),
        sa.Column("is_photo", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("photo_file_id", sa.String(256), nullable=True),
        sa.Column("weight_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("weight_g", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_food_logs_user_date", "food_logs", ["user_id", "meal_date"])

    # ── water_logs ───────────────────────────────────────────────────────────
    op.create_table(
        "water_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("logged_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("amount_ml", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_water_logs_user_date", "water_logs", ["user_id", "log_date"])

    # ── supplements ──────────────────────────────────────────────────────────
    op.create_table(
        "supplements",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("dose", sa.String(64), nullable=True),
        sa.Column("schedule_time", sa.String(10), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ── supplement_logs ───────────────────────────────────────────────────────
    op.create_table(
        "supplement_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("supplement_id", sa.Integer(), nullable=False),
        sa.Column("logged_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("log_date", sa.Date(), nullable=False),
        sa.Column("taken", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplement_id"], ["supplements.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_sup_logs_user_date", "supplement_logs", ["user_id", "log_date"])

    # ── reminders ────────────────────────────────────────────────────────────
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("reminder_type", sa.String(20), nullable=False),
        sa.Column("time_utc", sa.String(10), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("supplement_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["supplement_id"], ["supplements.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("reminders")
    op.drop_table("supplement_logs")
    op.drop_table("supplements")
    op.drop_table("water_logs")
    op.drop_table("food_logs")
    op.drop_table("users")
