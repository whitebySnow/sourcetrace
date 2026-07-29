"""add answer workflow trace

Revision ID: e9f6c1a4b320
Revises: d8a41c6e2f90
Create Date: 2026-07-29 00:00:01.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e9f6c1a4b320"
down_revision: str | None = "d8a41c6e2f90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "answer_runs",
        sa.Column("workflow_trace", sa.JSON(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE answer_runs
            SET workflow_trace = json_build_object(
                'retrieval_queries', json_build_array(retrieval_query),
                'assessments', json_build_array(),
                'supplemental_retrieval_attempts', 0,
                'citation_repair_attempts', 0
            )
            """
        )
    )
    op.alter_column("answer_runs", "workflow_trace", nullable=False)


def downgrade() -> None:
    op.drop_column("answer_runs", "workflow_trace")
