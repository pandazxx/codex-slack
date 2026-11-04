"""Add staffs.agent_config — per-staff mr-deed YAML (ADR-0018).

Nullable TEXT, no default: existing staffs are unaffected.

Revision ID: 000000000002
Revises: 000000000001
Create Date: 2026-11-02
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "000000000002"
down_revision = "000000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("staffs", sa.Column("agent_config", sa.Text(), nullable=True))


def downgrade() -> None:
    # batch mode so SQLite builds without native DROP COLUMN still work
    with op.batch_alter_table("staffs") as batch:
        batch.drop_column("agent_config")
