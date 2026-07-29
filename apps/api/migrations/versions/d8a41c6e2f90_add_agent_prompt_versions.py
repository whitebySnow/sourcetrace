"""add agent prompt versions

Revision ID: d8a41c6e2f90
Revises: b7d3e4f1a209
Create Date: 2026-07-29 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d8a41c6e2f90"
down_revision: str | None = "b7d3e4f1a209"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "answer_runs",
        sa.Column(
            "evidence_assessment_prompt_version",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "answer_runs",
        sa.Column(
            "citation_repair_prompt_version",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE answer_runs
            SET evidence_assessment_prompt_version = 'legacy-no-evidence-assessment',
                citation_repair_prompt_version = 'legacy-no-citation-repair'
            """
        )
    )
    op.alter_column(
        "answer_runs",
        "evidence_assessment_prompt_version",
        nullable=False,
    )
    op.alter_column(
        "answer_runs",
        "citation_repair_prompt_version",
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("answer_runs", "citation_repair_prompt_version")
    op.drop_column("answer_runs", "evidence_assessment_prompt_version")
