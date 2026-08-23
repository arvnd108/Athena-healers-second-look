"""Alerts table for the Emerging Evidence Radar.

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-23

Hand-written to match case/alerts.py exactly. down_revision chains onto
0001_initial_case_schema (still the head at time of writing).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "case_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("cases.id"), nullable=False
        ),
        sa.Column(
            "finding_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("findings.id"),
            nullable=False,
        ),
        sa.Column("evidence_change_summary", sa.Text, nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("acknowledged", sa.Boolean, nullable=False),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("dedup_key", sa.Text, nullable=False, unique=True),
        sa.Column("evidence_node_type", sa.Text, nullable=False),
        sa.Column("evidence_node_id", sa.Text, nullable=False),
        sa.Column("evidence_source", sa.Text, nullable=False),
    )
    op.create_index("ix_alerts_case_id_acknowledged", "alerts", ["case_id", "acknowledged"])


def downgrade() -> None:
    op.drop_index("ix_alerts_case_id_acknowledged", table_name="alerts")
    op.drop_table("alerts")
