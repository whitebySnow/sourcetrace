from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.evaluation.hybrid_retrieval import (
    LexicalSearchQuery,
    RankedChannelCandidate,
)
from sourcetrace.modules.documents.models import Chunk, Document, DocumentVersion, IngestionRun
from sourcetrace.modules.retrieval.service import RetrievedEvidence


@dataclass(frozen=True, slots=True)
class CorpusProvenance:
    parser_version: str
    tokenizer: str
    chunk_size: int
    chunk_overlap: int
    chunking_version: str
    embedding_provider: str
    embedding_model: str
    embedding_revision: str
    embedding_dimension: int
    embedding_version: str


class EvaluationCorpusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_provenance(
        self,
        knowledge_base_id: UUID,
        document_version_ids: Sequence[UUID],
    ) -> CorpusProvenance:
        requested_ids = set(document_version_ids)
        if not requested_ids:
            raise ValueError("evaluation document snapshot must not be empty")
        rows = (
            await self._session.execute(
                select(
                    DocumentVersion.id,
                    IngestionRun.parser_version,
                    IngestionRun.tokenizer,
                    IngestionRun.chunk_size,
                    IngestionRun.chunk_overlap,
                    IngestionRun.chunking_config_version,
                    IngestionRun.embedding_provider,
                    IngestionRun.embedding_model,
                    IngestionRun.embedding_model_revision,
                    IngestionRun.embedding_dimension,
                    IngestionRun.embedding_config_version,
                )
                .join(Chunk, Chunk.document_version_id == DocumentVersion.id)
                .join(IngestionRun, IngestionRun.id == Chunk.ingestion_run_id)
                .where(
                    DocumentVersion.knowledge_base_id == knowledge_base_id,
                    DocumentVersion.id.in_(requested_ids),
                    DocumentVersion.status == "completed",
                    IngestionRun.status == "completed",
                    Chunk.embedding.is_not(None),
                )
                .distinct()
            )
        ).all()
        if {row.id for row in rows} != requested_ids:
            raise ValueError("every evaluation document version must be searchable")

        configurations: set[CorpusProvenance] = set()
        for row in rows:
            if (
                row.embedding_provider is None
                or row.embedding_model is None
                or row.embedding_model_revision is None
                or row.embedding_dimension is None
                or row.embedding_config_version is None
            ):
                raise ValueError("evaluation snapshot has incomplete embedding provenance")
            configurations.add(
                CorpusProvenance(
                    parser_version=row.parser_version,
                    tokenizer=row.tokenizer,
                    chunk_size=row.chunk_size,
                    chunk_overlap=row.chunk_overlap,
                    chunking_version=row.chunking_config_version,
                    embedding_provider=row.embedding_provider,
                    embedding_model=row.embedding_model,
                    embedding_revision=row.embedding_model_revision,
                    embedding_dimension=row.embedding_dimension,
                    embedding_version=row.embedding_config_version,
                )
            )
        if len(configurations) != 1:
            raise ValueError("evaluation snapshot must use the same ingestion configuration")
        return next(iter(configurations))

    async def get_chunks(
        self,
        knowledge_base_id: UUID,
        document_version_ids: Sequence[UUID],
        chunk_ids: Sequence[UUID],
    ) -> dict[UUID, RetrievedEvidence]:
        requested_ids = set(chunk_ids)
        if not requested_ids:
            return {}
        rows = (
            await self._session.execute(
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
                    Chunk.id.in_(requested_ids),
                    DocumentVersion.id.in_(tuple(document_version_ids)),
                    DocumentVersion.knowledge_base_id == knowledge_base_id,
                    DocumentVersion.status == "completed",
                    DocumentVersion.storage_key.is_not(None),
                )
            )
        ).all()
        chunks = {
            row.chunk_id: RetrievedEvidence(
                chunk_id=row.chunk_id,
                document_id=row.document_id,
                document_version_id=row.document_version_id,
                document_name=row.document_name,
                storage_key=row.storage_key,
                page_number=row.page_number,
                text=row.text,
                score=0.0,
                page_chunk_index=row.page_chunk_index,
            )
            for row in rows
        }
        if set(chunks) != requested_ids:
            raise ValueError(
                "every recorded reranker candidate must belong to the dataset snapshot"
            )
        return chunks

    async def search_lexical(
        self,
        knowledge_base_id: UUID,
        document_version_ids: Sequence[UUID],
        *,
        query: LexicalSearchQuery,
        query_embedding: Sequence[float],
        limit: int,
        phrase_weight: float,
    ) -> tuple[RankedChannelCandidate, ...]:
        if not document_version_ids:
            raise ValueError("evaluation document snapshot must not be empty")
        if not query_embedding:
            raise ValueError("lexical search query embedding must not be empty")
        if limit <= 0:
            raise ValueError("lexical candidate limit must be positive")
        if phrase_weight < 0:
            raise ValueError("lexical phrase weight must not be negative")

        document_vector = func.to_tsvector("english", Chunk.text)
        term_query = func.websearch_to_tsquery("english", query.disjunction)
        phrase_query = func.websearch_to_tsquery(
            "english",
            query.phrase_disjunction,
        )
        lexical_score = (
            func.ts_rank_cd(document_vector, term_query, 32)
            + phrase_weight * func.ts_rank_cd(document_vector, phrase_query, 32)
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
            .where(
                DocumentVersion.knowledge_base_id == knowledge_base_id,
                DocumentVersion.id.in_(tuple(document_version_ids)),
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
