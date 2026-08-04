from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.evaluation.repository import EvaluationCorpusRepository
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


class RecordingQuestionRewriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    async def rewrite(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> str:
        self.calls.append((question, list(recent_questions)))
        return "How does cosine normalization work?"


class UnusedQuestionRewriter:
    async def rewrite(self, **kwargs: object) -> str:
        raise AssertionError("question rewriting must not start")


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
    embedding_config_version: str = "bge-m3-dense-v1",
    additional_page_chunks: Sequence[tuple[str, list[float]]] = (),
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
    chunks = [chunk]
    embeddings = [embedding]
    for page_chunk_index, (additional_text, additional_embedding) in enumerate(
        additional_page_chunks,
        start=1,
    ):
        chunks.append(
            Chunk(
                id=uuid4(),
                document_version_id=registration.version.id,
                ingestion_run_id=run.id,
                page_number=page_number,
                chunk_index=page_chunk_index,
                page_chunk_index=page_chunk_index,
                text=additional_text,
                token_count=4,
                chunking_config_version="token-window-v1",
            )
        )
        embeddings.append(additional_embedding)
    await repository.create_chunks(chunks)
    await repository.set_chunk_embeddings(chunks, embeddings)
    run.embedding_provider = "sentence-transformers"
    run.embedding_model = "BAAI/bge-m3"
    run.embedding_model_revision = "test-revision"
    run.embedding_dimension = 1024
    run.embedding_config_version = embedding_config_version
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
        question_rewriter=UnusedQuestionRewriter(),
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


async def test_retrieval_service_owns_follow_up_query_resolution(
    session: AsyncSession,
) -> None:
    rewriter = RecordingQuestionRewriter()
    service = RetrievalService(
        repository=PgVectorRetrievalRepository(session),
        embedding_provider=QueryEmbeddingProvider(),
        question_rewriter=rewriter,
        top_k=8,
    )

    direct_query = await service.resolve_query(
        question="How are vectors normalized?",
        recent_questions=[],
    )
    follow_up_query = await service.resolve_query(
        question="How does that work?",
        recent_questions=["What is cosine similarity?"],
    )

    assert direct_query == "How are vectors normalized?"
    assert follow_up_query == "How does cosine normalization work?"
    assert rewriter.calls == [
        ("How does that work?", ["What is cosine similarity?"]),
    ]


async def test_retrieval_can_be_pinned_to_an_exact_document_version_snapshot(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Evaluation snapshot"
    )
    old_version_id = await _create_searchable_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="snapshot.pdf",
        checksum="a" * 64,
        text="Reviewed snapshot evidence",
        page_number=1,
        embedding=_vector(1.0, 0.0),
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="snapshot.pdf",
        checksum="b" * 64,
        text="Newer evidence outside the reviewed snapshot",
        page_number=2,
        embedding=_vector(1.0, 0.0),
    )
    service = RetrievalService(
        repository=PgVectorRetrievalRepository(
            session,
            document_version_ids=(old_version_id,),
        ),
        embedding_provider=QueryEmbeddingProvider(),
        question_rewriter=UnusedQuestionRewriter(),
        top_k=8,
    )

    evidence = await service.search(
        knowledge_base_id=knowledge_base.id,
        query="How are vectors normalized?",
    )

    assert [item.document_version_id for item in evidence] == [old_version_id]
    assert [item.text for item in evidence] == ["Reviewed snapshot evidence"]


async def test_retrieval_expands_a_top_ranked_chunk_with_its_page_neighbor(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Page context"
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="target.pdf",
        checksum="e" * 64,
        text="Top ranked context",
        page_number=4,
        embedding=_vector(1.0, 0.0),
        additional_page_chunks=(("Adjacent expected evidence", _vector(0.1, 0.995)),),
    )
    for index in range(7):
        await _create_searchable_version(
            session,
            knowledge_base_id=knowledge_base.id,
            file_name=f"distractor-{index}.pdf",
            checksum=f"{index}" * 64,
            text=f"Distractor {index}",
            page_number=1,
            embedding=_vector(0.8, 0.6),
        )
    service = RetrievalService(
        repository=PgVectorRetrievalRepository(session),
        embedding_provider=QueryEmbeddingProvider(),
        question_rewriter=UnusedQuestionRewriter(),
        top_k=8,
        page_neighbor_count=1,
    )

    evidence = await service.search(
        knowledge_base_id=knowledge_base.id,
        query="How are vectors normalized?",
    )

    assert len(evidence) == 9
    assert evidence[0].text == "Top ranked context"
    assert evidence[-1].text == "Adjacent expected evidence"
    assert evidence[-1].page_number == 4
    assert evidence[-1].page_chunk_index == 1
    assert evidence[-1].score == evidence[0].score


async def test_evaluation_snapshot_rejects_mixed_ingestion_provenance(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Mixed provenance"
    )
    first_version_id = await _create_searchable_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="first.pdf",
        checksum="c" * 64,
        text="First evidence",
        page_number=1,
        embedding=_vector(1.0, 0.0),
    )
    second_version_id = await _create_searchable_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="second.pdf",
        checksum="d" * 64,
        text="Second evidence",
        page_number=1,
        embedding=_vector(1.0, 0.0),
        embedding_config_version="different-embedding-v2",
    )

    with pytest.raises(ValueError, match="same ingestion configuration"):
        await EvaluationCorpusRepository(session).get_provenance(
            knowledge_base.id,
            (first_version_id, second_version_id),
        )
