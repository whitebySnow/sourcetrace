"""create document versions

Revision ID: c76df281aa31
Revises: 76b49e159f56
Create Date: 2026-07-26 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c76df281aa31"
down_revision: str | None = "76b49e159f56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("knowledge_base_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
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
            "length(btrim(name)) > 0",
            name=op.f("ck_documents_document_name_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_documents_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint(
            "id",
            "knowledge_base_id",
            name="uq_documents_id_knowledge_base",
        ),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "name",
            name="uq_documents_knowledge_base_name",
        ),
    )
    op.create_table(
        "document_versions",
        sa.Column("document_id", sa.UUID(), nullable=False),
        sa.Column("knowledge_base_id", sa.UUID(), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_document_versions_document_version_checksum_sha256"),
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name=op.f("ck_document_versions_document_version_number_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "knowledge_base_id"],
            ["documents.id", "documents.knowledge_base_id"],
            name="fk_document_versions_document_knowledge_base",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_versions")),
        sa.UniqueConstraint(
            "knowledge_base_id",
            "checksum_sha256",
            name="uq_document_versions_knowledge_base_checksum",
        ),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_versions_sequence",
        ),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_document_version_identity_update() RETURNS trigger AS $$
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
    op.execute(
        """
        CREATE TRIGGER trg_document_versions_immutable_identity
        BEFORE UPDATE ON document_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_document_version_identity_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_document_versions_immutable_identity ON document_versions")
    op.execute("DROP FUNCTION prevent_document_version_identity_update()")
    op.drop_table("document_versions")
    op.drop_table("documents")
