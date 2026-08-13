"""add answer provider timeout provenance

Revision ID: 3b9e7d2a1c64
Revises: a4d1c9e7b205
Create Date: 2026-08-14 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3b9e7d2a1c64"
down_revision: str | None = "a4d1c9e7b205"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    "provider_connect_timeout_seconds",
    "provider_read_timeout_seconds",
    "provider_request_timeout_seconds",
    "provider_operation_deadline_seconds",
)


def upgrade() -> None:
    for column_name in _COLUMNS:
        op.add_column(
            "answer_runs",
            sa.Column(
                column_name,
                sa.Float(),
                nullable=False,
                server_default="60",
            ),
        )
        op.alter_column("answer_runs", column_name, server_default=None)


def downgrade() -> None:
    for column_name in reversed(_COLUMNS):
        op.drop_column("answer_runs", column_name)
