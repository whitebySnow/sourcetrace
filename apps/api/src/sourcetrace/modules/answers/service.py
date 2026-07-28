import base64
import binascii
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from sourcetrace.modules.answers.models import AnswerRun, Citation
from sourcetrace.modules.answers.schemas import (
    AnswerDeltaEvent,
    AnswerErrorEvent,
    AnswerEvent,
    AnswerFinalEvent,
    AnswerHistoryItem,
    AnswerRefusalEvent,
    AnswerStatusEvent,
    CitationResponse,
)
from sourcetrace.modules.conversations.models import Question
from sourcetrace.modules.conversations.service import ConversationService
from sourcetrace.modules.retrieval.service import RetrievalService, RetrievedEvidence
from sourcetrace.rag.embeddings import EmbeddingProviderError
from sourcetrace.rag.llm import LlmProviderError
from sourcetrace.rag.ports import AnswerGenerator, RetrievalCandidate

_CITATION_LABEL = re.compile(
    r"\[([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\]"
)


class InvalidAnswerCursorError(ValueError):
    pass


def _encode_cursor(created_at: datetime, run_id: UUID) -> str:
    value = f"{created_at.isoformat()}|{run_id}"
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, UUID]:
    try:
        padded = value + "=" * (-len(value) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True).decode()
        created_at_value, run_id_value = decoded.split("|", maxsplit=1)
        created_at = datetime.fromisoformat(created_at_value)
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, UUID(run_id_value)
    except (binascii.Error, UnicodeDecodeError, ValueError) as error:
        raise InvalidAnswerCursorError from error


@dataclass(frozen=True, slots=True)
class AnswerExecutionMetadata:
    llm_provider: str
    llm_model: str
    prompt_version: str
    retrieval_version: str
    workflow_version: str


@dataclass(frozen=True, slots=True)
class CitationDraft:
    id: UUID
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    document_name: str
    page_number: int
    excerpt: str


@dataclass(frozen=True, slots=True)
class AnswerHistoryRow:
    run: AnswerRun
    question: Question
    citations: list[Citation]


@dataclass(frozen=True, slots=True)
class AnswerHistoryPage:
    items: list[AnswerHistoryItem]
    next_cursor: str | None


class AnswerRepositoryPort(Protocol):
    async def create_run(
        self,
        question: Question,
        *,
        llm_provider: str,
        llm_model: str,
        prompt_version: str,
        retrieval_version: str,
        workflow_version: str,
    ) -> AnswerRun: ...

    async def complete_refusal(
        self, run: AnswerRun, *, code: str, message: str
    ) -> None: ...

    async def complete_answer(
        self,
        run: AnswerRun,
        *,
        answer: str,
        citations: list[CitationDraft],
    ) -> None: ...

    async def fail(self, run: AnswerRun, *, code: str, message: str) -> None: ...

    async def list_page(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[AnswerHistoryRow]: ...

    async def commit(self) -> None: ...


class AnswerService:
    def __init__(
        self,
        *,
        repository: AnswerRepositoryPort,
        conversations: ConversationService,
        retrieval: RetrievalService,
        generator: AnswerGenerator,
        metadata: AnswerExecutionMetadata,
        minimum_score: float,
        minimum_evidence: int,
    ) -> None:
        if not -1.0 <= minimum_score <= 1.0:
            raise ValueError("minimum retrieval score must be between -1 and 1")
        if minimum_evidence <= 0:
            raise ValueError("minimum evidence count must be positive")
        self._repository = repository
        self._conversations = conversations
        self._retrieval = retrieval
        self._generator = generator
        self._metadata = metadata
        self._minimum_score = minimum_score
        self._minimum_evidence = minimum_evidence

    async def start(
        self,
        *,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> AsyncIterator[AnswerEvent]:
        question = await self._conversations.stage_question(
            knowledge_base_id,
            conversation_id,
            content,
        )
        run = await self._repository.create_run(
            question,
            llm_provider=self._metadata.llm_provider,
            llm_model=self._metadata.llm_model,
            prompt_version=self._metadata.prompt_version,
            retrieval_version=self._metadata.retrieval_version,
            workflow_version=self._metadata.workflow_version,
        )
        await self._repository.commit()
        return self._stream(run=run, knowledge_base_id=knowledge_base_id, content=content)

    async def list_history(
        self,
        *,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> AnswerHistoryPage:
        await self._conversations.get(knowledge_base_id, conversation_id)
        after = _decode_cursor(cursor) if cursor else None
        rows = await self._repository.list_page(
            knowledge_base_id,
            conversation_id,
            limit=limit + 1,
            after=after,
        )
        has_more = len(rows) > limit
        selected = rows[:limit]
        next_cursor = (
            _encode_cursor(selected[-1].run.created_at, selected[-1].run.id)
            if has_more
            else None
        )
        return AnswerHistoryPage(
            items=[self._history_item(row) for row in selected],
            next_cursor=next_cursor,
        )

    async def _stream(
        self,
        *,
        run: AnswerRun,
        knowledge_base_id: UUID,
        content: str,
    ) -> AsyncIterator[AnswerEvent]:
        yield AnswerStatusEvent(run_id=run.id, status="retrieving")
        try:
            retrieved = await self._retrieval.search(
                knowledge_base_id=knowledge_base_id,
                query=content,
            )
        except EmbeddingProviderError as error:
            await self._fail(run, error.code, error.safe_message)
            yield AnswerErrorEvent(
                run_id=run.id, code=error.code, message=error.safe_message
            )
            return
        evidence = [item for item in retrieved if item.score >= self._minimum_score]
        if len(evidence) < self._minimum_evidence:
            code = "INSUFFICIENT_EVIDENCE"
            message = "The knowledge base does not contain enough evidence to answer."
            await self._repository.complete_refusal(run, code=code, message=message)
            await self._repository.commit()
            yield AnswerRefusalEvent(run_id=run.id, code=code, message=message)
            return

        drafts = [self._citation_draft(run.id, item) for item in evidence]
        candidates = [
            self._candidate(item, citation.id)
            for item, citation in zip(evidence, drafts, strict=True)
        ]
        yield AnswerStatusEvent(run_id=run.id, status="generating")
        answer_parts: list[str] = []
        try:
            async for delta in self._generator.stream_answer(
                question=content,
                evidence=candidates,
            ):
                answer_parts.append(delta)
                yield AnswerDeltaEvent(run_id=run.id, delta=delta)
        except LlmProviderError as error:
            await self._fail(run, error.code, error.safe_message)
            yield AnswerErrorEvent(
                run_id=run.id, code=error.code, message=error.safe_message
            )
            return
        answer = "".join(answer_parts).strip()
        if not answer:
            code = "LLM_EMPTY_RESPONSE"
            message = "Language model returned an empty response"
            await self._fail(run, code, message)
            yield AnswerErrorEvent(run_id=run.id, code=code, message=message)
            return
        allowed_citations = {str(draft.id): draft for draft in drafts}
        cited_labels = _CITATION_LABEL.findall(answer)
        if not cited_labels or any(
            label not in allowed_citations for label in cited_labels
        ):
            code = "CITATION_VALIDATION_FAILED"
            message = "The generated answer did not contain a valid evidence citation."
            await self._repository.complete_refusal(run, code=code, message=message)
            await self._repository.commit()
            yield AnswerRefusalEvent(run_id=run.id, code=code, message=message)
            return
        cited_ids = set(cited_labels)
        cited_drafts = [draft for draft in drafts if str(draft.id) in cited_ids]
        await self._repository.complete_answer(
            run,
            answer=answer,
            citations=cited_drafts,
        )
        await self._repository.commit()
        yield AnswerFinalEvent(
            run_id=run.id,
            answer=answer,
            citations=[
                self._citation_response(knowledge_base_id, item)
                for item in cited_drafts
            ],
        )

    async def _fail(self, run: AnswerRun, code: str, message: str) -> None:
        await self._repository.fail(run, code=code, message=message)
        await self._repository.commit()

    @staticmethod
    def _candidate(evidence: RetrievedEvidence, citation_id: UUID) -> RetrievalCandidate:
        return RetrievalCandidate(
            chunk_id=str(evidence.chunk_id),
            content=evidence.text,
            score=evidence.score,
            citation_id=str(citation_id),
        )

    @staticmethod
    def _citation_draft(run_id: UUID, evidence: RetrievedEvidence) -> CitationDraft:
        return CitationDraft(
            id=uuid5(run_id, str(evidence.chunk_id)),
            chunk_id=evidence.chunk_id,
            document_id=evidence.document_id,
            document_version_id=evidence.document_version_id,
            document_name=evidence.document_name,
            page_number=evidence.page_number,
            excerpt=evidence.text,
        )

    @staticmethod
    def _citation_response(
        knowledge_base_id: UUID,
        citation: CitationDraft | Citation,
    ) -> CitationResponse:
        return CitationResponse(
            id=str(citation.id),
            document_id=str(citation.document_id),
            document_version_id=str(citation.document_version_id),
            document_name=citation.document_name,
            page_number=citation.page_number,
            excerpt=citation.excerpt,
            source_url=(
                f"/api/v1/knowledge-bases/{knowledge_base_id}/documents/"
                f"{citation.document_id}/versions/{citation.document_version_id}/source"
                f"#page={citation.page_number}"
            ),
        )

    def _history_item(self, row: AnswerHistoryRow) -> AnswerHistoryItem:
        run = row.run
        return AnswerHistoryItem(
            id=run.id,
            question_id=run.question_id,
            question_content=row.question.content,
            status=run.status,
            outcome=run.outcome,
            answer=run.answer_text,
            refusal_code=run.refusal_code,
            refusal_message=run.refusal_message,
            failure_code=run.failure_code,
            failure_message=run.failure_message,
            llm_provider=run.llm_provider,
            llm_model=run.llm_model,
            prompt_version=run.prompt_version,
            retrieval_version=run.retrieval_version,
            workflow_version=run.workflow_version,
            created_at=run.created_at,
            completed_at=run.completed_at,
            citations=[
                self._citation_response(run.knowledge_base_id, citation)
                for citation in row.citations
            ],
        )
