from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from sourcetrace.db.base import Base, UUIDPrimaryKeyMixin

type AnswerRunStatus = Literal[
    "pending",
    "running",
    "cancel_requested",
    "cancelled",
    "completed",
    "failed",
]
type AnswerOutcome = Literal["answered", "refused"]


class AnswerRun(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "answer_runs"

    question_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("questions.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    status: Mapped[AnswerRunStatus] = mapped_column(
        String(24), nullable=False, server_default="pending"
    )
    outcome: Mapped[AnswerOutcome | None] = mapped_column(String(24), nullable=True)
    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    refusal_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    refusal_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_provider: Mapped[str] = mapped_column(String(64), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_version: Mapped[str] = mapped_column(String(64), nullable=False)
    retrieval_query: Mapped[str] = mapped_column(Text, nullable=False)
    query_rewrite_version: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_assessment_prompt_version: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    citation_repair_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    workflow_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_connect_timeout_seconds: Mapped[float] = mapped_column(nullable=False)
    provider_read_timeout_seconds: Mapped[float] = mapped_column(nullable=False)
    provider_request_timeout_seconds: Mapped[float] = mapped_column(nullable=False)
    provider_operation_deadline_seconds: Mapped[float] = mapped_column(nullable=False)
    workflow_trace: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "knowledge_base_id"],
            ["conversations.id", "conversations.knowledge_base_id"],
            ondelete="CASCADE",
            name="fk_answer_runs_conversation_knowledge_base",
        ),
        CheckConstraint(
            "status IN ('pending', 'running', 'cancel_requested', 'cancelled', "
            "'completed', 'failed')",
            name="answer_run_status_valid",
        ),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('answered', 'refused')",
            name="answer_run_outcome_valid",
        ),
        CheckConstraint(
            "(status = 'completed' AND outcome IS NOT NULL AND completed_at IS NOT NULL) "
            "OR (status = 'failed' AND outcome IS NULL AND failure_code IS NOT NULL "
            "AND completed_at IS NOT NULL) "
            "OR (status = 'cancelled' AND outcome IS NULL AND completed_at IS NOT NULL) "
            "OR (status IN ('pending', 'running', 'cancel_requested') "
            "AND outcome IS NULL AND completed_at IS NULL)",
            name="answer_run_terminal_state_consistent",
        ),
        Index(
            "ix_answer_runs_conversation_created_id",
            "conversation_id",
            "created_at",
            "id",
        ),
        Index(
            "uq_answer_runs_one_active_per_conversation",
            "conversation_id",
            unique=True,
            postgresql_where=text(
                "status IN ('pending', 'running', 'cancel_requested')"
            ),
        ),
    )


class Citation(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "citations"

    answer_run_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("answer_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("chunks.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_version_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("document_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_name: Mapped[str] = mapped_column(String(255), nullable=False)
    page_number: Mapped[int] = mapped_column(nullable=False)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("answer_run_id", "chunk_id", name="uq_citations_run_chunk"),
        CheckConstraint("page_number > 0", name="citation_page_number_positive"),
        CheckConstraint("length(excerpt) > 0", name="citation_excerpt_not_empty"),
        Index("ix_citations_answer_run", "answer_run_id", "id"),
    )
