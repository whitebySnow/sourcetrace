"""add answer runs and citations

Revision ID: c3f812a9e4b1
Revises: a91e6c2d4f70
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3f812a9e4b1"
down_revision: str | None = "a91e6c2d4f70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "answer_runs",
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status", sa.String(length=24), server_default="running", nullable=False
        ),
        sa.Column("outcome", sa.String(length=24), nullable=True),
        sa.Column("answer_text", sa.Text(), nullable=True),
        sa.Column("refusal_code", sa.String(length=64), nullable=True),
        sa.Column("refusal_message", sa.String(length=255), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=255), nullable=True),
        sa.Column("llm_provider", sa.String(length=64), nullable=False),
        sa.Column("llm_model", sa.String(length=255), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("retrieval_version", sa.String(length=64), nullable=False),
        sa.Column("workflow_version", sa.String(length=64), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')",
            name=op.f("ck_answer_runs_answer_run_status_valid"),
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('answered', 'refused')",
            name=op.f("ck_answer_runs_answer_run_outcome_valid"),
        ),
        sa.CheckConstraint(
            "(status = 'completed' AND outcome IS NOT NULL AND completed_at IS NOT NULL) "
            "OR (status = 'failed' AND outcome IS NULL AND failure_code IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status = 'running' AND outcome IS NULL AND completed_at IS NULL)",
            name=op.f("ck_answer_runs_answer_run_terminal_state_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "knowledge_base_id"],
            ["conversations.id", "conversations.knowledge_base_id"],
            name="fk_answer_runs_conversation_knowledge_base",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            name=op.f("fk_answer_runs_question_id_questions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answer_runs")),
    )
    op.create_index(
        "ix_answer_runs_conversation_created_id",
        "answer_runs",
        ["conversation_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "citations",
        sa.Column("answer_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_name", sa.String(length=255), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.CheckConstraint(
            "length(excerpt) > 0",
            name=op.f("ck_citations_citation_excerpt_not_empty"),
        ),
        sa.CheckConstraint(
            "page_number > 0",
            name=op.f("ck_citations_citation_page_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["answer_run_id"],
            ["answer_runs.id"],
            name=op.f("fk_citations_answer_run_id_answer_runs"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["chunks.id"],
            name=op.f("fk_citations_chunk_id_chunks"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_citations_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_citations_document_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_citations")),
        sa.UniqueConstraint(
            "answer_run_id", "chunk_id", name="uq_citations_run_chunk"
        ),
    )
    op.create_index(
        "ix_citations_answer_run",
        "citations",
        ["answer_run_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_citations_answer_run", table_name="citations")
    op.drop_table("citations")
    op.drop_index("ix_answer_runs_conversation_created_id", table_name="answer_runs")
    op.drop_table("answer_runs")
