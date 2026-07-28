from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.documents.models import Chunk
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.service import DocumentService
from sourcetrace.modules.knowledge_bases.repository import KnowledgeBaseRepository
from sourcetrace.modules.knowledge_bases.service import KnowledgeBaseService
from sourcetrace.modules.retrieval.repository import PgVectorRetrievalRepository
from sourcetrace.modules.retrieval.service import RetrievalService


class QueryEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert texts == ["How are vectors normalized?"]
        return [_vector(1.0, 0.0)]


def _vector(first: float, second: float) -> list[float]:
    return [first, second, *([0.0] * 1022)]


async def _create_searchable_version(
    session: AsyncSession,
    *,
    knowledge_base_id: UUID,
    file_name: str,
    checksum: str,
    text: str,
    page_number: int,
    embedding: list[float],
) -> UUID:
    repository = DocumentRepository(session)
    registration = await DocumentService(repository).register_version(
        knowledge_base_id,
        file_name=file_name,
        checksum_sha256=checksum,
        storage_key=f"{knowledge_base_id}/{checksum}.pdf",
        file_size_bytes=1024,
        page_count=page_number,
    )
    run = await repository.create_ingestion_run(
        registration.version.id,
        parser_version="fake-parser-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_config_version="token-window-v1",
    )
    chunk = Chunk(
        id=uuid4(),
        document_version_id=registration.version.id,
        ingestion_run_id=run.id,
        page_number=page_number,
        chunk_index=0,
        page_chunk_index=0,
        text=text,
        token_count=4,
        chunking_config_version="token-window-v1",
    )
    await repository.create_chunks([chunk])
    await repository.set_chunk_embeddings([chunk], [embedding])
    registration.version.status = "completed"
    run.status = "completed"
    run.stage = "completed"
    await repository.commit()
    return registration.version.id


async def test_retrieval_is_scoped_to_latest_searchable_versions_and_ranked(
    session: AsyncSession,
) -> None:
    knowledge_bases = KnowledgeBaseService(KnowledgeBaseRepository(session))
    research = await knowledge_bases.create("Research")
    private = await knowledge_bases.create("Private")
    old_version_id = await _create_searchable_version(
        session,
        knowledge_base_id=research.id,
        file_name="vectors.pdf",
        checksum="1" * 64,
        text="Old normalization guidance",
        page_number=1,
        embedding=_vector(1.0, 0.0),
    )
    latest_version_id = await _create_searchable_version(
        session,
        knowledge_base_id=research.id,
        file_name="vectors.pdf",
        checksum="2" * 64,
        text="Current normalization guidance",
        page_number=3,
        embedding=_vector(0.8, 0.6),
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=research.id,
        file_name="distance.pdf",
        checksum="3" * 64,
        text="Cosine distance reference",
        page_number=7,
        embedding=_vector(0.6, 0.8),
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=private.id,
        file_name="secret.pdf",
        checksum="4" * 64,
        text="Private perfect match",
        page_number=9,
        embedding=_vector(1.0, 0.0),
    )
    service = RetrievalService(
        repository=PgVectorRetrievalRepository(session),
        embedding_provider=QueryEmbeddingProvider(),
        top_k=8,
    )

    evidence = await service.search(
        knowledge_base_id=research.id,
        query="How are vectors normalized?",
    )

    assert [item.text for item in evidence] == [
        "Current normalization guidance",
        "Cosine distance reference",
    ]
    assert evidence[0].document_version_id == latest_version_id
    assert all(item.document_version_id != old_version_id for item in evidence)
    assert [item.document_name for item in evidence] == [
        "vectors.pdf",
        "distance.pdf",
    ]
    assert [item.page_number for item in evidence] == [3, 7]
    assert evidence[0].score > evidence[1].score
