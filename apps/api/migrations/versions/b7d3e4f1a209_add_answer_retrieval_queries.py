"""add replayable answer retrieval queries

Revision ID: b7d3e4f1a209
Revises: f4e91a7c2d63
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d3e4f1a209"
down_revision: str | None = "f4e91a7c2d63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "answer_runs",
        sa.Column("retrieval_query", sa.Text(), nullable=True),
    )
    op.add_column(
        "answer_runs",
        sa.Column("query_rewrite_version", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE answer_runs AS runs
            SET retrieval_query = questions.content,
                query_rewrite_version = 'legacy-direct-query-v1'
            FROM questions
            WHERE questions.id = runs.question_id
            """
        )
    )
    op.alter_column("answer_runs", "retrieval_query", nullable=False)
    op.alter_column("answer_runs", "query_rewrite_version", nullable=False)


def downgrade() -> None:
    op.drop_column("answer_runs", "query_rewrite_version")
    op.drop_column("answer_runs", "retrieval_query")
