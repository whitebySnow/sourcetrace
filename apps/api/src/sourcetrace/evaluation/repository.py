from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.documents.models import Chunk, DocumentVersion, IngestionRun


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
