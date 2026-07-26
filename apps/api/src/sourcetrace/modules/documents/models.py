from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from sourcetrace.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Document(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    knowledge_base_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    __table_args__ = (
        UniqueConstraint("knowledge_base_id", "name", name="uq_documents_knowledge_base_name"),
        UniqueConstraint("id", "knowledge_base_id", name="uq_documents_id_knowledge_base"),
        CheckConstraint("length(btrim(name)) > 0", name="document_name_not_blank"),
    )


class DocumentVersion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "document_versions"

    document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "knowledge_base_id"],
            ["documents.id", "documents.knowledge_base_id"],
            ondelete="CASCADE",
            name="fk_document_versions_document_knowledge_base",
        ),
        UniqueConstraint("document_id", "version_number", name="uq_document_versions_sequence"),
        UniqueConstraint(
            "knowledge_base_id",
            "checksum_sha256",
            name="uq_document_versions_knowledge_base_checksum",
        ),
        CheckConstraint("version_number > 0", name="document_version_number_positive"),
        CheckConstraint(
            "checksum_sha256 ~ '^[0-9a-f]{64}$'",
            name="document_version_checksum_sha256",
        ),
    )


IMMUTABLE_VERSION_FUNCTION = "prevent_document_version_identity_update"

event.listen(
    DocumentVersion.__table__,
    "after_create",
    DDL(
        f"""
        CREATE FUNCTION {IMMUTABLE_VERSION_FUNCTION}() RETURNS trigger AS $$
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
        $$ LANGUAGE plpgsql;
        """
    ).execute_if(dialect="postgresql"),
)
event.listen(
    DocumentVersion.__table__,
    "after_create",
    DDL(
        f"""
        CREATE TRIGGER trg_document_versions_immutable_identity
        BEFORE UPDATE ON document_versions
        FOR EACH ROW EXECUTE FUNCTION {IMMUTABLE_VERSION_FUNCTION}();
        """
    ).execute_if(dialect="postgresql"),
)
