"""add chunk english text search index

Revision ID: a4d1c9e7b205
Revises: e9f6c1a4b320
Create Date: 2026-08-08 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d1c9e7b205"
down_revision: str | None = "e9f6c1a4b320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_chunks_text_search_english"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(
            sa.text(
                f"""
                CREATE INDEX CONCURRENTLY {_INDEX_NAME}
                ON chunks
                USING gin (to_tsvector('english'::regconfig, text))
                """
            )
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(sa.text(f"DROP INDEX CONCURRENTLY {_INDEX_NAME}"))
