"""add knowledge-base-bound conversations

Revision ID: a91e6c2d4f70
Revises: 4f2a91b7c6d8
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a91e6c2d4f70"
down_revision: str | None = "4f2a91b7c6d8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
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
            "length(btrim(title)) > 0",
            name=op.f("ck_conversations_conversation_title_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_base_id"],
            ["knowledge_bases.id"],
            name=op.f("fk_conversations_knowledge_base_id_knowledge_bases"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint(
            "id",
            "knowledge_base_id",
            name="uq_conversations_id_knowledge_base",
        ),
    )
    op.create_index(
        "ix_conversations_knowledge_base_created_id",
        "conversations",
        ["knowledge_base_id", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "questions",
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "length(btrim(content)) > 0",
            name=op.f("ck_questions_question_content_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id", "knowledge_base_id"],
            ["conversations.id", "conversations.knowledge_base_id"],
            name="fk_questions_conversation_knowledge_base",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_questions")),
    )
    op.create_index(
        "ix_questions_conversation_created_id",
        "questions",
        ["conversation_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_questions_conversation_created_id", table_name="questions")
    op.drop_table("questions")
    op.drop_index(
        "ix_conversations_knowledge_base_created_id",
        table_name="conversations",
    )
    op.drop_table("conversations")
