from datetime import datetime
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DDL,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    literal_column,
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
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        server_default="pending",
        nullable=False,
    )
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
        CheckConstraint(
            "file_size_bytes IS NULL OR file_size_bytes > 0",
            name="document_version_file_size_positive",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="document_version_page_count_positive",
        ),
        Index(
            "ix_document_versions_knowledge_base_created_id",
            "knowledge_base_id",
            "created_at",
            "id",
        ),
    )


class IngestionRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "ingestion_runs"

    document_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="pending")
    stage: Mapped[str] = mapped_column(String(24), nullable=False, server_default="queued")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    retryable: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    tokenizer: Mapped[str] = mapped_column(String(64), nullable=False)
    chunk_size: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_overlap: Mapped[int] = mapped_column(Integer, nullable=False)
    chunking_config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_attempt_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
    )
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    embedding_model_revision: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_dimension: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_config_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "run_number",
            name="uq_ingestion_runs_version_sequence",
        ),
        CheckConstraint("run_number > 0", name="ingestion_run_number_positive"),
        CheckConstraint("attempt_count >= 0", name="ingestion_attempt_count_nonnegative"),
        CheckConstraint(
            "embedding_attempt_count >= 0",
            name="ingestion_embedding_attempt_count_nonnegative",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'chunked', 'completed', 'failed')",
            name="ingestion_status_valid",
        ),
        CheckConstraint(
            "stage IN ('queued', 'parsing', 'chunking', 'chunked', 'embedding', "
            "'indexing', 'completed', 'failed')",
            name="ingestion_stage_valid",
        ),
        CheckConstraint("chunk_size > 0", name="ingestion_chunk_size_positive"),
        CheckConstraint(
            "chunk_overlap >= 0 AND chunk_overlap < chunk_size",
            name="ingestion_chunk_overlap_valid",
        ),
    )


class Chunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "chunks"

    document_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    ingestion_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunking_config_version: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "document_version_id",
            "chunking_config_version",
            "page_number",
            "page_chunk_index",
            name="uq_chunks_stable_position",
        ),
        UniqueConstraint(
            "document_version_id",
            "chunking_config_version",
            "chunk_index",
            name="uq_chunks_stable_order",
        ),
        CheckConstraint("page_number > 0", name="chunk_page_number_positive"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_nonnegative"),
        CheckConstraint("page_chunk_index >= 0", name="chunk_page_index_nonnegative"),
        CheckConstraint("token_count > 0", name="chunk_token_count_positive"),
        CheckConstraint("length(text) > 0", name="chunk_text_not_empty"),
        Index("ix_chunks_document_version_order", "document_version_id", "chunk_index"),
        Index(
            "ix_chunks_text_search_english",
            func.to_tsvector(literal_column("'english'::regconfig"), text),
            postgresql_using="gin",
        ),
        Index(
            "ix_chunks_embedding_cosine",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
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
               OR NEW.storage_key IS DISTINCT FROM OLD.storage_key
               OR NEW.file_size_bytes IS DISTINCT FROM OLD.file_size_bytes
               OR NEW.page_count IS DISTINCT FROM OLD.page_count
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
