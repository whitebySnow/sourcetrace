import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import Message, Scope

from sourcetrace.api.dependencies import (
    get_answer_generator,
    get_citation_repairer,
    get_document_source_storage,
    get_evidence_assessor,
    get_query_embedding_provider,
    get_question_planner,
    get_reranker,
)
from sourcetrace.core.config import get_settings
from sourcetrace.db.session import get_session
from sourcetrace.main import create_app
from sourcetrace.modules.answers.models import AnswerRun
from sourcetrace.modules.answers.repository import AnswerRepository
from sourcetrace.modules.conversations.models import Question
from sourcetrace.modules.documents.models import Chunk
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.service import DocumentService
from sourcetrace.modules.documents.storage import LocalDocumentStorage
from sourcetrace.rag.ports import (
    EvidenceDecision,
    RetrievalCandidate,
    RetrievalPlanProposal,
)
from tests.helpers import PreserveOrderReranker


class QueryEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert list(texts) == ["How are vectors stored?"]
        return [[1.0, *([0.0] * 1023)]]


class GroundedAnswerGenerator:
    def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        assert question == "How are vectors stored?"
        assert len(evidence) == 1
        assert evidence[0].content == "Vectors are normalized before storage."
        return self._stream(evidence[0].citation_id)

    async def _stream(self, citation_id: str) -> AsyncIterator[str]:
        yield "Vectors are normalized "
        yield f"before storage [normalized]. [{citation_id}]"


class NeverAnswerGenerator:
    def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        raise AssertionError("the model must not run without sufficient evidence")


class UncitedAnswerGenerator:
    def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[str]:
        yield "Vectors are normalized, but this response omits its citation label."


class MixedCitationAnswerGenerator:
    def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        return self._stream(evidence[0].citation_id)

    async def _stream(self, citation_id: str) -> AsyncIterator[str]:
        yield f"Supported claim [{citation_id}]. "
        yield f"Fabricated claim [{uuid4()}]."


class BlockingAnswerGenerator:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.second_started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.call_count = 0

    def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        self.call_count += 1
        if self.call_count == 2:
            self.second_started.set()
        return self._stream(evidence[0].citation_id)

    async def _stream(self, citation_id: str) -> AsyncIterator[str]:
        try:
            self.started.set()
            yield "This partial answer "
            await self.release.wait()
            yield f"is grounded [{citation_id}]."
        finally:
            self.closed = True


class NoAdditionalQueryPlanner:
    async def plan(self, **kwargs: object) -> RetrievalPlanProposal:
        return RetrievalPlanProposal(additional_queries=())


class SelectingAllEvidenceAssessor:
    async def assess(
        self,
        *,
        evidence: Sequence[RetrievalCandidate],
        **kwargs: object,
    ) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=bool(evidence),
            selected_chunk_ids=tuple(item.chunk_id for item in evidence),
            supplemental_query=None,
        )


class NoOpCitationRepairer:
    async def repair(self, *, answer: str, **kwargs: object) -> str:
        return answer


def _answer_app():
    app = create_app()
    app.dependency_overrides[get_question_planner] = NoAdditionalQueryPlanner
    app.dependency_overrides[get_evidence_assessor] = SelectingAllEvidenceAssessor
    app.dependency_overrides[get_citation_repairer] = NoOpCitationRepairer
    app.dependency_overrides[get_reranker] = PreserveOrderReranker
    return app


class RecordingQuestionPlanner:
    def __init__(
        self,
        retrieval_query: str = "Why are BGE-M3 dense vectors normalized before storage?",
    ) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self.retrieval_query = retrieval_query

    async def plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
        document_titles: Sequence[str],
    ) -> RetrievalPlanProposal:
        self.calls.append((question, list(recent_questions)))
        return RetrievalPlanProposal(additional_queries=(self.retrieval_query,))


class RewrittenQueryEmbeddingProvider:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.queries.extend(texts)
        assert list(texts) == [
            "Why is it normalized?",
            "Why are BGE-M3 dense vectors normalized before storage?",
        ]
        return [
            [0.0, 1.0, *([0.0] * 1022)],
            [1.0, *([0.0] * 1023)],
        ]


class NoMatchingEvidenceEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert list(texts) == ["What color is it?", "What color is the Moon?"]
        return [[0.0, 1.0, *([0.0] * 1022)]] * 2


class ChineseRewrittenQueryEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert list(texts) == [
            "它为什么需要归一化?",
            "BGE-M3 稠密向量为什么要在存储前归一化?",
        ]
        return [
            [0.0, 1.0, *([0.0] * 1022)],
            [1.0, *([0.0] * 1023)],
        ]


class FollowUpAnswerGenerator:
    def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        assert question == "Why is it normalized?"
        assert evidence[0].content == "Vectors are normalized before storage."
        return self._stream(evidence[0].citation_id)

    async def _stream(self, citation_id: str) -> AsyncIterator[str]:
        yield f"It is normalized for cosine retrieval. [{citation_id}]"


class ChineseFollowUpAnswerGenerator:
    def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        assert question == "它为什么需要归一化?"
        assert evidence[0].content == "Vectors are normalized before storage."
        return self._stream(evidence[0].citation_id)

    async def _stream(self, citation_id: str) -> AsyncIterator[str]:
        yield f"归一化后可以稳定进行余弦相似度检索。 [{citation_id}]"


async def _create_searchable_evidence(
    session: AsyncSession,
    knowledge_base_id: UUID,
) -> UUID:
    repository = DocumentRepository(session)
    registration = await DocumentService(repository).register_version(
        knowledge_base_id,
        file_name="vectors.pdf",
        checksum_sha256="a" * 64,
        storage_key=f"{knowledge_base_id}/vectors.pdf",
        file_size_bytes=1024,
        page_count=5,
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
        page_number=4,
        chunk_index=0,
        page_chunk_index=0,
        text="Vectors are normalized before storage.",
        token_count=6,
        chunking_config_version="token-window-v1",
        embedding=[1.0, *([0.0] * 1023)],
    )
    await repository.create_chunks([chunk])
    registration.version.status = "completed"
    run.status = "completed"
    run.stage = "completed"
    await repository.commit()
    return registration.version.id


def _events(body: str) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    event_name = ""
    for line in body.splitlines():
        if line.startswith("event: "):
            event_name = line.removeprefix("event: ")
        elif line.startswith("data: "):
            parsed.append((event_name, json.loads(line.removeprefix("data: "))))
    return parsed


async def test_user_receives_a_streamed_answer_with_validated_citations(
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = GroundedAnswerGenerator
    app.dependency_overrides[get_document_source_storage] = lambda: LocalDocumentStorage(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Research"},
        )
        knowledge_base_id = knowledge_base_response.json()["id"]
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Vector storage"},
        )
        conversation_id = conversation_response.json()["id"]
        version_id = await _create_searchable_evidence(
            session,
            UUID(knowledge_base_id),
        )
        source_file = tmp_path / knowledge_base_id / "vectors.pdf"
        source_file.parent.mkdir(parents=True)
        source_file.write_bytes(b"%PDF-1.7\nsource evidence")

        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
            json={"content": "How are vectors stored?"},
        )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(response.text)
        assert [name for name, _data in events] == [
            "status",
            "status",
            "delta",
            "delta",
            "final",
        ]
        assert [data["status"] for name, data in events if name == "status"] == [
            "retrieving",
            "generating",
        ]
        final = events[-1][1]
        assert final["version"] == "1"
        assert final["answer"].startswith("Vectors are normalized")
        citations = final["citations"]
        assert isinstance(citations, list)
        assert len(citations) == 1
        assert citations[0]["document_name"] == "vectors.pdf"
        assert citations[0]["document_version_id"] == str(version_id)
        assert citations[0]["page_number"] == 4
        assert citations[0]["excerpt"] == "Vectors are normalized before storage."
        assert citations[0]["source_url"].endswith(f"/{version_id}/source#page=4")

        source_url = citations[0]["source_url"].split("#", maxsplit=1)[0]
        source_response = await client.get(source_url)
        assert source_response.status_code == 200
        assert source_response.headers["content-type"] == "application/pdf"
        assert source_response.headers["content-disposition"].startswith("inline;")
        assert source_response.content.startswith(b"%PDF-1.7")

        other_knowledge_base = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Other"},
        )
        out_of_scope_url = source_url.replace(
            knowledge_base_id,
            other_knowledge_base.json()["id"],
            1,
        )
        out_of_scope_response = await client.get(out_of_scope_url)
        assert out_of_scope_response.status_code == 404

        questions = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/questions"
        )
        assert [item["content"] for item in questions.json()["items"]] == [
            "How are vectors stored?"
        ]
        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )
        persisted = history_response.json()["items"][0]
        assert persisted["id"] == final["run_id"]
        assert persisted["status"] == "completed"
        assert persisted["outcome"] == "answered"
        assert persisted["answer"] == final["answer"]
        assert persisted["llm_provider"] == "openai-compatible"
        assert persisted["llm_model"] == get_settings().llm_model
        assert persisted["prompt_version"] == "grounded-answer-v2"
        assert persisted["retrieval_version"] == get_settings().retrieval_config_version
        assert persisted["evidence_assessment_prompt_version"] == ("evidence-assessment-v1")
        assert persisted["citation_repair_prompt_version"] == "citation-repair-v2"
        assert persisted["workflow_version"] == "langgraph-bounded-multi-query-v2"
        trace = persisted["workflow_trace"]
        assert trace["retrieval_queries"] == ["How are vectors stored?"]
        assert trace["retrieval_plan_version"] == "bounded-counterexample-v3"
        assert len(trace["retrieval_rounds"]) == 1
        candidate_trace = trace["retrieval_rounds"][0]["query_results"][0][
            "candidates"
        ][0]
        assert "dense_rank" in candidate_trace
        assert "lexical_rank" in candidate_trace
        assert "dense_score" in candidate_trace
        assert "lexical_score" in candidate_trace
        assert candidate_trace["channel_fused_rank"] >= 1
        assert candidate_trace["channel_fused_score"] > 0
        assert trace["supplemental_retrieval_attempts"] == 0
        assert trace["citation_repair_attempts"] == 0
        assert len(trace["assessments"]) == 1
        assert trace["assessments"][0]["sufficient"] is True
        assert trace["assessments"][0]["supplemental_query"] is None
        assert len(trace["assessments"][0]["selected_chunk_ids"]) == 1
        assert persisted["citations"] == citations


async def test_follow_up_uses_bounded_questions_for_fresh_retrieval(
    session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    rewriter = RecordingQuestionPlanner()
    embedding = RewrittenQueryEmbeddingProvider()
    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = lambda: embedding
    app.dependency_overrides[get_question_planner] = lambda: rewriter
    app.dependency_overrides[get_answer_generator] = FollowUpAnswerGenerator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Follow-up research"},
        )
        knowledge_base_id = UUID(knowledge_base_response.json()["id"])
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "BGE-M3 discussion"},
        )
        conversation_id = UUID(conversation_response.json()["id"])
        started_at = datetime(2026, 7, 28, 8, 0, tzinfo=UTC)
        session.add_all(
            [
                Question(
                    id=uuid4(),
                    knowledge_base_id=knowledge_base_id,
                    conversation_id=conversation_id,
                    content=f"History question {index}",
                    created_at=started_at + timedelta(minutes=index),
                )
                for index in range(1, 6)
            ]
        )
        await session.commit()
        await _create_searchable_evidence(session, knowledge_base_id)

        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
            json={"content": "Why is it normalized?"},
        )
        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )

    assert response.status_code == 200
    assert _events(response.text)[-1][0] == "final"
    assert rewriter.calls == [
        (
            "Why is it normalized?",
            [
                "History question 2",
                "History question 3",
                "History question 4",
                "History question 5",
            ],
        )
    ]
    assert embedding.queries == [
        "Why is it normalized?",
        "Why are BGE-M3 dense vectors normalized before storage?",
    ]
    persisted = history_response.json()["items"][0]
    assert persisted["retrieval_query"] == "Why is it normalized?"
    assert persisted["query_rewrite_version"] == "bounded-counterexample-v3"


async def test_follow_up_refuses_when_only_a_prior_answer_contains_the_claim(
    session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    rewriter = RecordingQuestionPlanner("What color is the Moon?")
    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = NoMatchingEvidenceEmbeddingProvider
    app.dependency_overrides[get_question_planner] = lambda: rewriter
    app.dependency_overrides[get_answer_generator] = NeverAnswerGenerator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Untrusted history"},
        )
        knowledge_base_id = UUID(knowledge_base_response.json()["id"])
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Unsupported prior answer"},
        )
        conversation_id = UUID(conversation_response.json()["id"])
        prior_question = Question(
            id=uuid4(),
            knowledge_base_id=knowledge_base_id,
            conversation_id=conversation_id,
            content="What is the Moon made of?",
            created_at=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
        )
        session.add(prior_question)
        await session.flush()
        session.add(
            AnswerRun(
                id=uuid4(),
                question_id=prior_question.id,
                knowledge_base_id=knowledge_base_id,
                conversation_id=conversation_id,
                status="completed",
                outcome="answered",
                answer_text="UNSUPPORTED PRIOR ANSWER: The Moon is green.",
                llm_provider="openai-compatible",
                llm_model="gpt-5.6-luna",
                prompt_version="grounded-answer-v1",
                retrieval_version="pgvector-cosine-v1",
                retrieval_query="What is the Moon made of?",
                query_rewrite_version="legacy-direct-query-v1",
                evidence_assessment_prompt_version=("legacy-no-evidence-assessment"),
                citation_repair_prompt_version="legacy-no-citation-repair",
                workflow_version="linear-grounded-v1",
                workflow_trace={
                    "retrieval_queries": ["What is the Moon made of?"],
                    "assessments": [],
                    "supplemental_retrieval_attempts": 0,
                    "citation_repair_attempts": 0,
                },
                completed_at=datetime(2026, 7, 28, 8, 1, tzinfo=UTC),
            )
        )
        await session.commit()
        await _create_searchable_evidence(session, knowledge_base_id)

        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
            json={"content": "What color is it?"},
        )
        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )

    events = _events(response.text)
    assert events[-1][0] == "refusal"
    assert events[-1][1]["code"] == "INSUFFICIENT_EVIDENCE"
    assert rewriter.calls == [("What color is it?", ["What is the Moon made of?"])]
    assert "UNSUPPORTED PRIOR ANSWER" not in json.dumps(rewriter.calls)
    current = next(
        item
        for item in history_response.json()["items"]
        if item["question_content"] == "What color is it?"
    )
    assert current["outcome"] == "refused"
    assert current["answer"] is None


async def test_follow_up_answer_uses_question_language_and_preserves_source_text(
    session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    rewriter = RecordingQuestionPlanner("BGE-M3 稠密向量为什么要在存储前归一化?")
    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = ChineseRewrittenQueryEmbeddingProvider
    app.dependency_overrides[get_question_planner] = lambda: rewriter
    app.dependency_overrides[get_answer_generator] = ChineseFollowUpAnswerGenerator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "多语言追问"},
        )
        knowledge_base_id = UUID(knowledge_base_response.json()["id"])
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "BGE-M3 讨论"},
        )
        conversation_id = UUID(conversation_response.json()["id"])
        session.add(
            Question(
                id=uuid4(),
                knowledge_base_id=knowledge_base_id,
                conversation_id=conversation_id,
                content="BGE-M3 向量如何存储?",
                created_at=datetime(2026, 7, 28, 8, 0, tzinfo=UTC),
            )
        )
        await session.commit()
        await _create_searchable_evidence(session, knowledge_base_id)

        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
            json={"content": "它为什么需要归一化?"},
        )

    final = _events(response.text)[-1]
    assert final[0] == "final"
    assert str(final[1]["answer"]).startswith("归一化后")
    citations = final[1]["citations"]
    assert isinstance(citations, list)
    assert citations[0]["excerpt"] == "Vectors are normalized before storage."


async def test_insufficient_evidence_is_refused_and_persisted(
    session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = NeverAnswerGenerator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Empty research"},
        )
        knowledge_base_id = knowledge_base_response.json()["id"]
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Unknown topic"},
        )
        conversation_id = conversation_response.json()["id"]

        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
            json={"content": "How are vectors stored?"},
        )

        events = _events(response.text)
        assert [name for name, _data in events] == ["status", "refusal"]
        assert events[-1][1]["code"] == "INSUFFICIENT_EVIDENCE"

        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )
        assert history_response.status_code == 200
        history = history_response.json()
        assert history["next_cursor"] is None
        assert len(history["items"]) == 1
        assert history["items"][0]["question_content"] == "How are vectors stored?"
        assert history["items"][0]["status"] == "completed"
        assert history["items"][0]["outcome"] == "refused"
        assert history["items"][0]["answer"] is None
        assert history["items"][0]["refusal_code"] == "INSUFFICIENT_EVIDENCE"
        assert history["items"][0]["citations"] == []


async def test_uncited_model_output_is_refused_instead_of_completed(
    session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = UncitedAnswerGenerator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Citation validation"},
        )
        knowledge_base_id = knowledge_base_response.json()["id"]
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Uncited output"},
        )
        conversation_id = conversation_response.json()["id"]
        await _create_searchable_evidence(session, UUID(knowledge_base_id))

        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
            json={"content": "How are vectors stored?"},
        )

        events = _events(response.text)
        assert [name for name, _data in events] == [
            "status",
            "status",
            "delta",
            "refusal",
        ]
        assert events[-1][1]["code"] == "CITATION_VALIDATION_FAILED"

        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )
        item = history_response.json()["items"][0]
        assert item["outcome"] == "refused"
        assert item["answer"] is None
        assert item["citations"] == []


async def test_answer_with_any_unretrieved_citation_is_refused(
    session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = MixedCitationAnswerGenerator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Mixed citation validation"},
        )
        knowledge_base_id = knowledge_base_response.json()["id"]
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Fabricated citation"},
        )
        conversation_id = conversation_response.json()["id"]
        await _create_searchable_evidence(session, UUID(knowledge_base_id))

        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
            json={"content": "How are vectors stored?"},
        )

        events = _events(response.text)
        assert events[-1][0] == "refusal"
        assert events[-1][1]["code"] == "CITATION_VALIDATION_FAILED"

        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )
        item = history_response.json()["items"][0]
        assert item["outcome"] == "refused"
        assert item["answer"] is None
        assert item["citations"] == []


async def test_active_answer_can_be_cancelled_without_persisting_partial_text(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as request_session:
            yield request_session

    generator = BlockingAnswerGenerator()
    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = lambda: generator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Cancellation"},
        )
        knowledge_base_id = knowledge_base_response.json()["id"]
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Cancelled answer"},
        )
        conversation_id = conversation_response.json()["id"]
        async with session_factory() as setup_session:
            await _create_searchable_evidence(
                setup_session,
                UUID(knowledge_base_id),
            )

        answer_url = (
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )
        first_request = asyncio.create_task(
            client.post(
                answer_url,
                json={"content": "How are vectors stored?"},
            )
        )
        await asyncio.wait_for(generator.started.wait(), timeout=2)
        safety_release = asyncio.get_running_loop().call_later(5, generator.release.set)

        history_response = await asyncio.wait_for(client.get(answer_url), timeout=2)
        run = history_response.json()["items"][0]
        assert run["status"] == "running"

        conflict_response = await asyncio.wait_for(
            client.post(
                answer_url,
                json={"content": "How are vectors stored?"},
            ),
            timeout=2,
        )
        assert conflict_response.status_code == 409
        assert conflict_response.json()["code"] == "ANSWER_RUN_ALREADY_ACTIVE"

        cancel_url = f"{answer_url}/{run['id']}/cancel"
        first_cancel = await asyncio.wait_for(client.post(cancel_url), timeout=2)
        assert first_cancel.status_code == 200
        assert first_cancel.json() == {
            "run_id": run["id"],
            "status": "cancel_requested",
        }
        second_cancel = await asyncio.wait_for(client.post(cancel_url), timeout=2)
        assert second_cancel.status_code == 200
        assert second_cancel.json()["run_id"] == run["id"]
        assert second_cancel.json()["status"] in {"cancel_requested", "cancelled"}

        generator.release.set()
        safety_release.cancel()
        response = await asyncio.wait_for(first_request, timeout=2)
        events = _events(response.text)
        assert events[-1] == (
            "cancelled",
            {"version": "1", "type": "cancelled", "run_id": run["id"]},
        )
        assert all(name not in {"final", "refusal", "error"} for name, _ in events)

        persisted_response = await asyncio.wait_for(client.get(answer_url), timeout=2)
        persisted = persisted_response.json()["items"][0]
        assert persisted["status"] == "cancelled"
        assert persisted["outcome"] is None
        assert persisted["answer"] is None
        assert persisted["citations"] == []
        assert generator.closed is True


async def test_different_conversations_can_generate_answers_concurrently(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as request_session:
            yield request_session

    generator = BlockingAnswerGenerator()
    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = lambda: generator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Concurrent conversations"},
        )
        knowledge_base_id = knowledge_base_response.json()["id"]
        conversation_ids: list[str] = []
        for title in ("First conversation", "Second conversation"):
            response = await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
                json={"title": title},
            )
            conversation_ids.append(response.json()["id"])
        async with session_factory() as setup_session:
            await _create_searchable_evidence(
                setup_session,
                UUID(knowledge_base_id),
            )

        requests = [
            asyncio.create_task(
                client.post(
                    f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
                    f"{conversation_id}/answers",
                    json={"content": "How are vectors stored?"},
                )
            )
            for conversation_id in conversation_ids
        ]
        safety_release = asyncio.get_running_loop().call_later(
            5,
            generator.release.set,
        )
        try:
            await asyncio.wait_for(generator.started.wait(), timeout=2)
            await asyncio.wait_for(generator.second_started.wait(), timeout=2)
            assert all(not request.done() for request in requests)

            generator.release.set()
            responses = await asyncio.wait_for(asyncio.gather(*requests), timeout=2)
        finally:
            safety_release.cancel()
            generator.release.set()

        assert generator.call_count == 2
        assert [response.status_code for response in responses] == [200, 200]
        assert [_events(response.text)[-1][0] for response in responses] == ["final", "final"]


async def test_asgi_disconnect_persists_cancellation(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as request_session:
            yield request_session

    generator = BlockingAnswerGenerator()
    app = _answer_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = lambda: generator
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Disconnected client"},
        )
        knowledge_base_id = knowledge_base_response.json()["id"]
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Interrupted stream"},
        )
        conversation_id = conversation_response.json()["id"]
        async with session_factory() as setup_session:
            await _create_searchable_evidence(setup_session, UUID(knowledge_base_id))

        path = (
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers"
        )
        body = json.dumps({"content": "How are vectors stored?"}).encode()
        request_delivered = False

        async def receive() -> Message:
            nonlocal request_delivered
            if not request_delivered:
                request_delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            await generator.started.wait()
            return {"type": "http.disconnect"}

        sent: list[Message] = []

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"testserver"),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "state": {},
        }
        await asyncio.wait_for(
            app(scope, receive, send),
            timeout=2,
        )

        history_response = await client.get(path)
        persisted = history_response.json()["items"][0]
        assert persisted["status"] == "cancelled"
        assert persisted["answer"] is None
        assert generator.closed is True
        assert any(message["type"] == "http.response.start" for message in sent)


async def test_startup_recovery_fails_interrupted_answer_runs(
    session: AsyncSession,
) -> None:
    app = _answer_app()

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        knowledge_base_response = await client.post(
            "/api/v1/knowledge-bases",
            json={"name": "Recovery"},
        )
        knowledge_base_id = UUID(knowledge_base_response.json()["id"])
        conversation_response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "Interrupted answer"},
        )
        conversation_id = UUID(conversation_response.json()["id"])

    question = Question(
        id=uuid4(),
        knowledge_base_id=knowledge_base_id,
        conversation_id=conversation_id,
        content="How are vectors stored?",
    )
    session.add(question)
    await session.flush()
    repository = AnswerRepository(session)
    run = await repository.create_run(
        question,
        llm_provider="openai-compatible",
        llm_model="gpt-5.6-luna",
        prompt_version="grounded-answer-v1",
        retrieval_version="pgvector-cosine-v1",
        query_rewrite_version="follow-up-query-v1",
        evidence_assessment_prompt_version="evidence-assessment-v1",
        citation_repair_prompt_version="citation-repair-v1",
        workflow_version="linear-grounded-v1",
    )
    await repository.commit()

    recovered = await repository.fail_interrupted_runs()
    await repository.commit()
    await session.refresh(run)

    assert recovered == 1
    assert run.status == "failed"
    assert run.failure_code == "ANSWER_RUN_INTERRUPTED"
    assert run.completed_at is not None
