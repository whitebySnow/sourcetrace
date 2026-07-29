from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.documents.models import Chunk, Document, DocumentVersion
from sourcetrace.modules.retrieval.service import RetrievedEvidence


class PgVectorRetrievalRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        document_version_ids: Sequence[UUID] | None = None,
    ) -> None:
        self._session = session
        self._document_version_ids = (
            tuple(document_version_ids) if document_version_ids is not None else None
        )
        if self._document_version_ids is not None and not self._document_version_ids:
            raise ValueError("document version snapshot must not be empty")

    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[RetrievedEvidence]:
        latest_completed = (
            select(
                DocumentVersion.document_id,
                func.max(DocumentVersion.version_number).label("version_number"),
            )
            .where(
                DocumentVersion.knowledge_base_id == knowledge_base_id,
                DocumentVersion.status == "completed",
            )
            .group_by(DocumentVersion.document_id)
            .subquery()
        )
        distance = Chunk.embedding.cosine_distance(list(query_embedding)).label("distance")
        statement = (
            select(
                Chunk.id.label("chunk_id"),
                Document.id.label("document_id"),
                DocumentVersion.id.label("document_version_id"),
                Document.name.label("document_name"),
                DocumentVersion.storage_key,
                Chunk.page_number,
                Chunk.text,
                distance,
            )
            .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
        )
        if self._document_version_ids is None:
            statement = statement.join(
                latest_completed,
                and_(
                    latest_completed.c.document_id == DocumentVersion.document_id,
                    latest_completed.c.version_number == DocumentVersion.version_number,
                ),
            )
        else:
            statement = statement.where(
                DocumentVersion.id.in_(self._document_version_ids),
            )
        statement = (
            statement.where(
                DocumentVersion.knowledge_base_id == knowledge_base_id,
                DocumentVersion.status == "completed",
                Chunk.embedding.is_not(None),
                DocumentVersion.storage_key.is_not(None),
            )
            .order_by(distance, Chunk.id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            RetrievedEvidence(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                document_version_id=row.document_version_id,
                document_name=row.document_name,
                storage_key=row.storage_key,
                page_number=row.page_number,
                text=row.text,
                score=1.0 - float(row.distance),
            )
            for row in rows
        ]
