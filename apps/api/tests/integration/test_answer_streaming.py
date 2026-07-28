import json
from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.api.dependencies import (
    get_answer_generator,
    get_document_source_storage,
    get_query_embedding_provider,
)
from sourcetrace.db.session import get_session
from sourcetrace.main import create_app
from sourcetrace.modules.documents.models import Chunk
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.service import DocumentService
from sourcetrace.modules.documents.storage import LocalDocumentStorage
from sourcetrace.rag.ports import RetrievalCandidate


class QueryEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        assert texts == ["How are vectors stored?"]
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

    app = create_app()
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_query_embedding_provider] = QueryEmbeddingProvider
    app.dependency_overrides[get_answer_generator] = GroundedAnswerGenerator
    app.dependency_overrides[get_document_source_storage] = lambda: LocalDocumentStorage(
        tmp_path
    )
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
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers",
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
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/questions"
        )
        assert [item["content"] for item in questions.json()["items"]] == [
            "How are vectors stored?"
        ]
        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers"
        )
        persisted = history_response.json()["items"][0]
        assert persisted["id"] == final["run_id"]
        assert persisted["status"] == "completed"
        assert persisted["outcome"] == "answered"
        assert persisted["answer"] == final["answer"]
        assert persisted["llm_provider"] == "openai-compatible"
        assert persisted["llm_model"] == "gpt-5.6-luna"
        assert persisted["prompt_version"] == "grounded-answer-v1"
        assert persisted["retrieval_version"] == "pgvector-cosine-v1"
        assert persisted["workflow_version"] == "linear-grounded-v1"
        assert persisted["citations"] == citations


async def test_insufficient_evidence_is_refused_and_persisted(
    session: AsyncSession,
) -> None:
    async def override_session() -> AsyncIterator[AsyncSession]:
        yield session

    app = create_app()
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
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers",
            json={"content": "How are vectors stored?"},
        )

        events = _events(response.text)
        assert [name for name, _data in events] == ["status", "refusal"]
        assert events[-1][1]["code"] == "INSUFFICIENT_EVIDENCE"

        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers"
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

    app = create_app()
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
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers",
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
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers"
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

    app = create_app()
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
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers",
            json={"content": "How are vectors stored?"},
        )

        events = _events(response.text)
        assert events[-1][0] == "refusal"
        assert events[-1][1]["code"] == "CITATION_VALIDATION_FAILED"

        history_response = await client.get(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations/"
            f"{conversation_id}/answers"
        )
        item = history_response.json()["items"][0]
        assert item["outcome"] == "refused"
        assert item["answer"] is None
        assert item["citations"] == []
