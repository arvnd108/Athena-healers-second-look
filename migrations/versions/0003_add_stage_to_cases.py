"""Add stage column to cases

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-23

Closes issue #51: cancer stage was never in IMPLEMENTATION_PLAN.md SS2.2's
original cases schema, even though patient-schema-mvp.md SS1 lists it as a
P0 field. Nullable, free text -- see case/models.py's Case.stage docstring
comment for why.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("stage", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("cases", "stage")
