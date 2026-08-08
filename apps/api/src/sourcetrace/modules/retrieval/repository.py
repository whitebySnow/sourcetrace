from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from sourcetrace.modules.documents.models import Chunk, Document, DocumentVersion
from sourcetrace.modules.retrieval.hybrid import (
    FusedChannelCandidate,
    LexicalSearchQuery,
    RankedChannelCandidate,
    build_lexical_search_query,
    fuse_ranked_channels,
)
from sourcetrace.modules.retrieval.service import RetrievedEvidence

_TEXT_SEARCH_CONFIG: ColumnElement[object] = literal_column("'english'::regconfig")


class PgVectorRetrievalRepository:
    def __init__(
        self,
        session: AsyncSession,
        *,
        document_version_ids: Sequence[UUID] | None = None,
        channel_rrf_rank_constant: int = 60,
        lexical_phrase_weight: float = 2.0,
    ) -> None:
        self._session = session
        self._document_version_ids = (
            tuple(document_version_ids) if document_version_ids is not None else None
        )
        if self._document_version_ids is not None and not self._document_version_ids:
            raise ValueError("document version snapshot must not be empty")
        if channel_rrf_rank_constant <= 0:
            raise ValueError("channel RRF rank constant must be positive")
        if lexical_phrase_weight < 0:
            raise ValueError("lexical phrase weight must not be negative")
        self._channel_rrf_rank_constant = channel_rrf_rank_constant
        self._lexical_phrase_weight = lexical_phrase_weight

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
        query: str,
        limit: int,
    ) -> list[FusedChannelCandidate[RetrievedEvidence]]:
        dense = await self.search_dense(
            knowledge_base_id,
            query_embedding,
            limit=limit,
        )
        dense_channel = tuple(
            RankedChannelCandidate(
                channel="dense",
                rank=rank,
                channel_score=item.score,
                evidence=item,
            )
            for rank, item in enumerate(dense, start=1)
        )
        lexical_query = build_lexical_search_query(query)
        lexical = (
            await self._search_lexical(
                knowledge_base_id,
                query_embedding,
                query=lexical_query,
                limit=limit,
            )
            if lexical_query is not None
            else ()
        )
        return list(
            fuse_ranked_channels(
                (*dense_channel, *lexical),
                rank_constant=self._channel_rrf_rank_constant,
                limit=limit,
            )
        )

    async def search_dense(
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

    async def _search_lexical(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        query: LexicalSearchQuery,
        limit: int,
    ) -> tuple[RankedChannelCandidate[RetrievedEvidence], ...]:
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
        document_vector = func.to_tsvector(_TEXT_SEARCH_CONFIG, Chunk.text)
        term_query = func.websearch_to_tsquery(_TEXT_SEARCH_CONFIG, query.disjunction)
        phrase_query = func.websearch_to_tsquery(
            _TEXT_SEARCH_CONFIG,
            query.phrase_disjunction,
        )
        lexical_score = (
            func.ts_rank_cd(document_vector, term_query, 32)
            + self._lexical_phrase_weight
            * func.ts_rank_cd(document_vector, phrase_query, 32)
        ).label("lexical_score")
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
                lexical_score,
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
                DocumentVersion.storage_key.is_not(None),
                Chunk.embedding.is_not(None),
                document_vector.op("@@")(term_query),
            )
            .order_by(lexical_score.desc(), Chunk.id)
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).all()
        return tuple(
            RankedChannelCandidate(
                channel="lexical",
                rank=rank,
                channel_score=float(row.lexical_score),
                evidence=RetrievedEvidence(
                    chunk_id=row.chunk_id,
                    document_id=row.document_id,
                    document_version_id=row.document_version_id,
                    document_name=row.document_name,
                    storage_key=row.storage_key,
                    page_number=row.page_number,
                    text=row.text,
                    score=1.0 - float(row.distance),
                    page_chunk_index=row.page_chunk_index,
                ),
            )
            for rank, row in enumerate(rows, start=1)
        )

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
