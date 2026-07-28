from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PostgreSQLUUID
from sqlalchemy.orm import Mapped, mapped_column

from sourcetrace.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Conversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "conversations"

    knowledge_base_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "id",
            "knowledge_base_id",
            name="uq_conversations_id_knowledge_base",
        ),
        CheckConstraint("length(btrim(title)) > 0", name="conversation_title_not_blank"),
        Index(
            "ix_conversations_knowledge_base_created_id",
            "knowledge_base_id",
            "created_at",
            "id",
        ),
    )


class Question(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "questions"

    conversation_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    knowledge_base_id: Mapped[UUID] = mapped_column(
        PostgreSQLUUID(as_uuid=True),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["conversation_id", "knowledge_base_id"],
            ["conversations.id", "conversations.knowledge_base_id"],
            ondelete="CASCADE",
            name="fk_questions_conversation_knowledge_base",
        ),
        CheckConstraint("length(btrim(content)) > 0", name="question_content_not_blank"),
        Index(
            "ix_questions_conversation_created_id",
            "conversation_id",
            "created_at",
            "id",
        ),
    )
