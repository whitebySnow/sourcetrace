"""add document upload metadata

Revision ID: de2b6f45a890
Revises: c76df281aa31
Create Date: 2026-07-27 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "de2b6f45a890"
down_revision: str | None = "c76df281aa31"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "document_versions",
        sa.Column("storage_key", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column("page_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "document_versions",
        sa.Column(
            "status",
            sa.String(length=24),
            server_default="pending",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_document_versions_document_version_file_size_positive"),
        "document_versions",
        "file_size_bytes IS NULL OR file_size_bytes > 0",
    )
    op.create_check_constraint(
        op.f("ck_document_versions_document_version_page_count_positive"),
        "document_versions",
        "page_count IS NULL OR page_count > 0",
    )
    op.create_index(
        "ix_document_versions_knowledge_base_created_id",
        "document_versions",
        ["knowledge_base_id", "created_at", "id"],
        unique=False,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_document_version_identity_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.document_id IS DISTINCT FROM OLD.document_id
               OR NEW.knowledge_base_id IS DISTINCT FROM OLD.knowledge_base_id
               OR NEW.version_number IS DISTINCT FROM OLD.version_number
               OR NEW.checksum_sha256 IS DISTINCT FROM OLD.checksum_sha256
               OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
               OR NEW.file_size_bytes IS DISTINCT FROM OLD.file_size_bytes
               OR NEW.page_count IS DISTINCT FROM OLD.page_count
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'document version identity is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_document_version_identity_update()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.document_id IS DISTINCT FROM OLD.document_id
               OR NEW.knowledge_base_id IS DISTINCT FROM OLD.knowledge_base_id
               OR NEW.version_number IS DISTINCT FROM OLD.version_number
               OR NEW.checksum_sha256 IS DISTINCT FROM OLD.checksum_sha256
               OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'document version identity is immutable'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.drop_index(
        "ix_document_versions_knowledge_base_created_id",
        table_name="document_versions",
    )
    op.drop_constraint(
        op.f("ck_document_versions_document_version_page_count_positive"),
        "document_versions",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_document_versions_document_version_file_size_positive"),
        "document_versions",
        type_="check",
    )
    op.drop_column("document_versions", "status")
    op.drop_column("document_versions", "page_count")
    op.drop_column("document_versions", "file_size_bytes")
    op.drop_column("document_versions", "storage_key")
