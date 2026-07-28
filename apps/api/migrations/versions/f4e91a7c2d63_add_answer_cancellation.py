"""add answer cancellation state and active-run constraint

Revision ID: f4e91a7c2d63
Revises: c3f812a9e4b1
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4e91a7c2d63"
down_revision: str | None = "c3f812a9e4b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CONSTRAINT = "ck_answer_runs_answer_run_status_valid"
_TERMINAL_CONSTRAINT = "ck_answer_runs_answer_run_terminal_state_consistent"
_ACTIVE_INDEX = "uq_answer_runs_one_active_per_conversation"


def upgrade() -> None:
    op.drop_constraint(op.f(_STATUS_CONSTRAINT), "answer_runs", type_="check")
    op.drop_constraint(op.f(_TERMINAL_CONSTRAINT), "answer_runs", type_="check")
    op.alter_column("answer_runs", "status", server_default="pending")

    op.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (
                           PARTITION BY conversation_id
                           ORDER BY created_at DESC, id DESC
                       ) AS active_rank
                FROM answer_runs
                WHERE status = 'running'
            )
            UPDATE answer_runs AS runs
            SET status = 'failed',
                failure_code = 'SUPERSEDED_DURING_MIGRATION',
                failure_message = 'A newer answer run was kept active during migration',
                completed_at = now()
            FROM ranked
            WHERE runs.id = ranked.id AND ranked.active_rank > 1
            """
        )
    )

    op.create_check_constraint(
        op.f(_STATUS_CONSTRAINT),
        "answer_runs",
        "status IN ('pending', 'running', 'cancel_requested', 'cancelled', "
        "'completed', 'failed')",
    )
    op.create_check_constraint(
        op.f(_TERMINAL_CONSTRAINT),
        "answer_runs",
        "(status = 'completed' AND outcome IS NOT NULL AND completed_at IS NOT NULL) "
        "OR (status = 'failed' AND outcome IS NULL AND failure_code IS NOT NULL "
        "AND completed_at IS NOT NULL) "
        "OR (status = 'cancelled' AND outcome IS NULL AND completed_at IS NOT NULL) "
        "OR (status IN ('pending', 'running', 'cancel_requested') "
        "AND outcome IS NULL AND completed_at IS NULL)",
    )
    op.create_index(
        _ACTIVE_INDEX,
        "answer_runs",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('pending', 'running', 'cancel_requested')"
        ),
    )


def downgrade() -> None:
    op.drop_index(_ACTIVE_INDEX, table_name="answer_runs")
    op.drop_constraint(op.f(_STATUS_CONSTRAINT), "answer_runs", type_="check")
    op.drop_constraint(op.f(_TERMINAL_CONSTRAINT), "answer_runs", type_="check")
    op.execute(
        sa.text(
            """
            UPDATE answer_runs
            SET status = CASE
                    WHEN status = 'cancelled' THEN 'failed'
                    ELSE 'running'
                END,
                failure_code = CASE
                    WHEN status = 'cancelled' THEN 'CANCELLED_BEFORE_DOWNGRADE'
                    ELSE failure_code
                END,
                failure_message = CASE
                    WHEN status = 'cancelled' THEN 'Run was cancelled before schema downgrade'
                    ELSE failure_message
                END,
                completed_at = CASE
                    WHEN status = 'cancelled' THEN COALESCE(completed_at, now())
                    ELSE NULL
                END
            WHERE status IN ('pending', 'cancel_requested', 'cancelled')
            """
        )
    )
    op.alter_column("answer_runs", "status", server_default="running")
    op.create_check_constraint(
        op.f(_STATUS_CONSTRAINT),
        "answer_runs",
        "status IN ('running', 'completed', 'failed')",
    )
    op.create_check_constraint(
        op.f(_TERMINAL_CONSTRAINT),
        "answer_runs",
        "(status = 'completed' AND outcome IS NOT NULL AND completed_at IS NOT NULL) "
        "OR (status = 'failed' AND outcome IS NULL AND failure_code IS NOT NULL "
        "AND completed_at IS NOT NULL) "
        "OR (status = 'running' AND outcome IS NULL AND completed_at IS NULL)",
    )
