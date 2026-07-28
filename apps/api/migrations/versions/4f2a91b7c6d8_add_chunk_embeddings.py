"""add chunk embeddings

Revision ID: 4f2a91b7c6d8
Revises: 8c17a20d46f3
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "4f2a91b7c6d8"
down_revision: str | None = "8c17a20d46f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "ingestion_runs",
        sa.Column(
            "embedding_attempt_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("embedding_provider", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("embedding_model_revision", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("embedding_dimension", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ingestion_runs",
        sa.Column("embedding_config_version", sa.String(length=64), nullable=True),
    )
    op.create_check_constraint(
        op.f("ck_ingestion_runs_ingestion_embedding_attempt_count_nonnegative"),
        "ingestion_runs",
        "embedding_attempt_count >= 0",
    )
    op.drop_constraint(
        op.f("ck_ingestion_runs_ingestion_stage_valid"),
        "ingestion_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ingestion_runs_ingestion_stage_valid"),
        "ingestion_runs",
        "stage IN ('queued', 'parsing', 'chunking', 'chunked', 'embedding', "
        "'indexing', 'completed', 'failed')",
    )
    op.add_column("chunks", sa.Column("embedding", Vector(dim=1024), nullable=True))
    op.create_index(
        "ix_chunks_embedding_cosine",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_chunks_embedding_cosine", table_name="chunks")
    op.drop_column("chunks", "embedding")
    op.drop_constraint(
        op.f("ck_ingestion_runs_ingestion_stage_valid"),
        "ingestion_runs",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_ingestion_runs_ingestion_stage_valid"),
        "ingestion_runs",
        "stage IN ('queued', 'parsing', 'chunking', 'chunked', 'completed', 'failed')",
    )
    op.drop_constraint(
        op.f("ck_ingestion_runs_ingestion_embedding_attempt_count_nonnegative"),
        "ingestion_runs",
        type_="check",
    )
    op.drop_column("ingestion_runs", "embedding_config_version")
    op.drop_column("ingestion_runs", "embedding_dimension")
    op.drop_column("ingestion_runs", "embedding_model_revision")
    op.drop_column("ingestion_runs", "embedding_model")
    op.drop_column("ingestion_runs", "embedding_provider")
    op.drop_column("ingestion_runs", "embedding_attempt_count")
