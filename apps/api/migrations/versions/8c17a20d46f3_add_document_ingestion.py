"""add document ingestion

Revision ID: 8c17a20d46f3
Revises: de2b6f45a890
Create Date: 2026-07-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8c17a20d46f3"
down_revision: str | None = "de2b6f45a890"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingestion_runs",
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("run_number", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="pending",
            nullable=False,
        ),
        sa.Column(
            "stage",
            sa.String(length=24),
            server_default="queued",
            nullable=False,
        ),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "retryable",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.String(length=255), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("tokenizer", sa.String(length=64), nullable=False),
        sa.Column("chunk_size", sa.Integer(), nullable=False),
        sa.Column("chunk_overlap", sa.Integer(), nullable=False),
        sa.Column("chunking_config_version", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_ingestion_runs_ingestion_attempt_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "chunk_overlap >= 0 AND chunk_overlap < chunk_size",
            name=op.f("ck_ingestion_runs_ingestion_chunk_overlap_valid"),
        ),
        sa.CheckConstraint(
            "chunk_size > 0",
            name=op.f("ck_ingestion_runs_ingestion_chunk_size_positive"),
        ),
        sa.CheckConstraint(
            "run_number > 0",
            name=op.f("ck_ingestion_runs_ingestion_run_number_positive"),
        ),
        sa.CheckConstraint(
            "stage IN ('queued', 'parsing', 'chunking', 'chunked', 'completed', 'failed')",
            name=op.f("ck_ingestion_runs_ingestion_stage_valid"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'chunked', 'completed', 'failed')",
            name=op.f("ck_ingestion_runs_ingestion_status_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_ingestion_runs_document_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_runs")),
        sa.UniqueConstraint(
            "document_version_id",
            "run_number",
            name="uq_ingestion_runs_version_sequence",
        ),
    )
    op.create_table(
        "chunks",
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("ingestion_run_id", sa.UUID(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("chunking_config_version", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name=op.f("ck_chunks_chunk_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "page_chunk_index >= 0",
            name=op.f("ck_chunks_chunk_page_index_nonnegative"),
        ),
        sa.CheckConstraint(
            "length(text) > 0",
            name=op.f("ck_chunks_chunk_text_not_empty"),
        ),
        sa.CheckConstraint(
            "page_number > 0",
            name=op.f("ck_chunks_chunk_page_number_positive"),
        ),
        sa.CheckConstraint(
            "token_count > 0",
            name=op.f("ck_chunks_chunk_token_count_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["document_version_id"],
            ["document_versions.id"],
            name=op.f("fk_chunks_document_version_id_document_versions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id"],
            ["ingestion_runs.id"],
            name=op.f("fk_chunks_ingestion_run_id_ingestion_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "document_version_id",
            "chunking_config_version",
            "chunk_index",
            name="uq_chunks_stable_order",
        ),
        sa.UniqueConstraint(
            "document_version_id",
            "chunking_config_version",
            "page_number",
            "page_chunk_index",
            name="uq_chunks_stable_position",
        ),
    )
    op.create_index(
        "ix_chunks_document_version_order",
        "chunks",
        ["document_version_id", "chunk_index"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_document_version_order", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("ingestion_runs")
