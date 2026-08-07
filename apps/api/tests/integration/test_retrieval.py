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
from sourcetrace.rag.ports import RetrievalPlanProposal
from tests.helpers import PreserveOrderReranker


class QueryEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert list(texts) == ["How are vectors normalized?"]
        return [_vector(1.0, 0.0)]


class MultiQueryEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert list(texts) == ["first concept", "second concept"]
        return [_vector(1.0, 0.0), _vector(0.0, 1.0)]


class RecordingQuestionPlanner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], list[str]]] = []

    async def plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
        document_titles: Sequence[str],
    ) -> RetrievalPlanProposal:
        self.calls.append(
            (question, list(recent_questions), list(document_titles))
        )
        if question == "How does that work?":
            return RetrievalPlanProposal(
                additional_queries=("How does cosine normalization work?",)
            )
        return RetrievalPlanProposal(additional_queries=())


class UnusedQuestionPlanner:
    async def plan(self, **kwargs: object) -> RetrievalPlanProposal:
        raise AssertionError("query planning must not start")


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
    additional_chunks: Sequence[tuple[int, str, list[float]]] = (),
) -> UUID:
    repository = DocumentRepository(session)
    registration = await DocumentService(repository).register_version(
        knowledge_base_id,
        file_name=file_name,
        checksum_sha256=checksum,
        storage_key=f"{knowledge_base_id}/{checksum}.pdf",
        file_size_bytes=1024,
        page_count=max(
            [page_number, *(item[0] for item in additional_chunks)],
        ),
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
    next_chunk_index = len(chunks)
    page_chunk_indexes: dict[int, int] = {}
    for additional_page, additional_text, additional_embedding in additional_chunks:
        page_chunk_index = page_chunk_indexes.get(additional_page, 0)
        chunks.append(
            Chunk(
                id=uuid4(),
                document_version_id=registration.version.id,
                ingestion_run_id=run.id,
                page_number=additional_page,
                chunk_index=next_chunk_index,
                page_chunk_index=page_chunk_index,
                text=additional_text,
                token_count=4,
                chunking_config_version="token-window-v1",
            )
        )
        embeddings.append(additional_embedding)
        next_chunk_index += 1
        page_chunk_indexes[additional_page] = page_chunk_index + 1
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
    repository = PgVectorRetrievalRepository(session)
    service = RetrievalService(
        repository=repository,
        embedding_provider=QueryEmbeddingProvider(),
        question_planner=UnusedQuestionPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=8,
    )

    result = await service.search(
        knowledge_base_id=research.id,
        queries=("How are vectors normalized?",),
    )
    evidence = result.evidence

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
    assert await repository.list_searchable_document_titles(
        research.id,
        limit=50,
    ) == ("distance.pdf", "vectors.pdf")


async def test_multi_query_rrf_uses_independent_pgvector_rankings_and_stable_ties(
    session: AsyncSession,
) -> None:
    knowledge_bases = KnowledgeBaseService(KnowledgeBaseRepository(session))
    target = await knowledge_bases.create("Multi-query target")
    private = await knowledge_bases.create("Multi-query private")
    await _create_searchable_version(
        session,
        knowledge_base_id=target.id,
        file_name="first.pdf",
        checksum="5" * 64,
        text="First concept evidence",
        page_number=1,
        embedding=_vector(1.0, 0.0),
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=target.id,
        file_name="second.pdf",
        checksum="6" * 64,
        text="Second concept evidence",
        page_number=2,
        embedding=_vector(0.0, 1.0),
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=private.id,
        file_name="private.pdf",
        checksum="7" * 64,
        text="Private evidence",
        page_number=1,
        embedding=_vector(1.0, 0.0),
    )
    service = RetrievalService(
        repository=PgVectorRetrievalRepository(session),
        embedding_provider=MultiQueryEmbeddingProvider(),
        question_planner=UnusedQuestionPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=8,
        rrf_rank_constant=60,
    )

    result = await service.search(
        knowledge_base_id=target.id,
        queries=("first concept", "second concept"),
    )

    assert result.query_results[0].candidates[0].evidence.text == ("First concept evidence")
    assert result.query_results[1].candidates[0].evidence.text == ("Second concept evidence")
    assert {item.text for item in result.primary_evidence} == {
        "First concept evidence",
        "Second concept evidence",
    }
    assert all(item.document_name != "private.pdf" for item in result.evidence)
    assert [item.chunk_id for item in result.primary_evidence] == sorted(
        (item.chunk_id for item in result.primary_evidence),
        key=str,
    )


async def test_retrieval_service_owns_bounded_query_plan_resolution(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Planning metadata"
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="BGE-M3.pdf",
        checksum="f" * 64,
        text="Embedding evidence",
        page_number=1,
        embedding=_vector(1.0, 0.0),
    )
    planner = RecordingQuestionPlanner()
    service = RetrievalService(
        repository=PgVectorRetrievalRepository(session),
        embedding_provider=QueryEmbeddingProvider(),
        question_planner=planner,
        reranker=PreserveOrderReranker(),
        top_k=8,
    )

    direct_plan = await service.resolve_plan(
        knowledge_base_id=knowledge_base.id,
        question="How are vectors normalized?",
        recent_questions=[],
    )
    follow_up_plan = await service.resolve_plan(
        knowledge_base_id=knowledge_base.id,
        question="How does that work?",
        recent_questions=["What is cosine similarity?"],
    )

    assert direct_plan.queries == ("How are vectors normalized?",)
    assert follow_up_plan.queries == (
        "How does that work?",
        "How does cosine normalization work?",
    )
    assert planner.calls == [
        ("How are vectors normalized?", [], ["BGE-M3.pdf"]),
        ("How does that work?", ["What is cosine similarity?"], ["BGE-M3.pdf"]),
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
        question_planner=UnusedQuestionPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=8,
    )

    result = await service.search(
        knowledge_base_id=knowledge_base.id,
        queries=("How are vectors normalized?",),
    )
    evidence = result.evidence

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
        question_planner=UnusedQuestionPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=8,
        page_neighbor_count=1,
    )

    result = await service.search(
        knowledge_base_id=knowledge_base.id,
        queries=("How are vectors normalized?",),
    )
    evidence = result.evidence

    assert len(evidence) == 9
    assert evidence[0].text == "Top ranked context"
    assert evidence[-1].text == "Adjacent expected evidence"
    assert evidence[-1].page_number == 4
    assert evidence[-1].page_chunk_index == 1
    assert evidence[-1].score == evidence[0].score


async def test_retrieval_primary_candidates_prefer_distinct_document_pages(
    session: AsyncSession,
) -> None:
    knowledge_base = await KnowledgeBaseService(KnowledgeBaseRepository(session)).create(
        "Page-diverse retrieval"
    )
    await _create_searchable_version(
        session,
        knowledge_base_id=knowledge_base.id,
        file_name="crowded.pdf",
        checksum="f" * 64,
        text="Highest scoring chunk",
        page_number=1,
        embedding=_vector(1.0, 0.0),
        additional_page_chunks=tuple(
            (f"Same-page candidate {index}", _vector(0.99, 0.1)) for index in range(7)
        ),
        additional_chunks=((2, "Relevant evidence on another page", _vector(0.9, 0.435)),),
    )
    service = RetrievalService(
        repository=PgVectorRetrievalRepository(session),
        embedding_provider=QueryEmbeddingProvider(),
        question_planner=UnusedQuestionPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=8,
    )

    result = await service.search(
        knowledge_base_id=knowledge_base.id,
        queries=("How are vectors normalized?",),
    )
    evidence = result.evidence

    assert len(evidence) == 8
    assert [item.page_number for item in evidence[:2]] == [1, 2]
    assert "Relevant evidence on another page" in {item.text for item in evidence}


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
