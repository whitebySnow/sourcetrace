from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, or_, select
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

    async def list_searchable_document_titles(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        if limit <= 0:
            raise ValueError("document title limit must be positive")
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
        statement = (
            select(Document.name)
            .join(DocumentVersion, DocumentVersion.document_id == Document.id)
            .join(Chunk, Chunk.document_version_id == DocumentVersion.id)
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
            .group_by(Document.name)
            .order_by(func.lower(Document.name), Document.name)
            .limit(limit)
        )
        return tuple((await self._session.scalars(statement)).all())

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
                Chunk.page_chunk_index,
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
                page_chunk_index=row.page_chunk_index,
            )
            for row in rows
        ]

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]:
        if neighbor_count <= 0 or not evidence:
            return []
        page_scores: dict[tuple[UUID, int], float] = {}
        page_indexes: dict[tuple[UUID, int], set[int]] = {}
        for item in evidence:
            key = (item.document_version_id, item.page_number)
            page_scores[key] = max(page_scores.get(key, item.score), item.score)
            indexes = page_indexes.setdefault(key, set())
            indexes.update(
                range(
                    max(0, item.page_chunk_index - neighbor_count),
                    item.page_chunk_index + neighbor_count + 1,
                )
            )
        page_conditions = [
            and_(
                Chunk.document_version_id == document_version_id,
                Chunk.page_number == page_number,
                Chunk.page_chunk_index.in_(sorted(indexes)),
            )
            for (document_version_id, page_number), indexes in page_indexes.items()
        ]
        statement = (
            select(
                Chunk.id.label("chunk_id"),
                Document.id.label("document_id"),
                DocumentVersion.id.label("document_version_id"),
                Document.name.label("document_name"),
                DocumentVersion.storage_key,
                Chunk.page_number,
                Chunk.text,
                Chunk.page_chunk_index,
            )
            .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentVersion.knowledge_base_id == knowledge_base_id,
                DocumentVersion.status == "completed",
                Chunk.id.not_in([item.chunk_id for item in evidence]),
                or_(*page_conditions),
            )
            .order_by(
                DocumentVersion.id,
                Chunk.page_number,
                Chunk.page_chunk_index,
                Chunk.id,
            )
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
                score=page_scores[(row.document_version_id, row.page_number)],
                page_chunk_index=row.page_chunk_index,
            )
            for row in rows
        ]
