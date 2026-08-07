import asyncio
import base64
import binascii
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID, uuid5

from anyio import CancelScope

from sourcetrace.core.logging import get_logger
from sourcetrace.modules.answers.models import AnswerRun, AnswerRunStatus, Citation
from sourcetrace.modules.answers.schemas import (
    AnswerCancellationResponse,
    AnswerCancelledEvent,
    AnswerDeltaEvent,
    AnswerErrorEvent,
    AnswerEvent,
    AnswerFinalEvent,
    AnswerHistoryItem,
    AnswerRefusalEvent,
    AnswerStatusEvent,
    AnswerWorkflowTrace,
    CitationResponse,
)
from sourcetrace.modules.conversations.models import Question
from sourcetrace.modules.conversations.service import ConversationService
from sourcetrace.modules.retrieval.service import RetrievedEvidence
from sourcetrace.rag.embeddings import EmbeddingProviderError
from sourcetrace.rag.llm import LlmProviderError
from sourcetrace.rag.rerankers import RerankerProviderError
from sourcetrace.rag.workflow import (
    AnswerWorkflow,
    WorkflowAnswered,
    WorkflowCancelled,
    WorkflowDelta,
    WorkflowRefused,
    WorkflowRequest,
    WorkflowStatus,
    WorkflowTrace,
)

logger = get_logger(__name__)


class InvalidAnswerCursorError(ValueError):
    pass


class ActiveAnswerRunExistsError(RuntimeError):
    pass


class AnswerRunNotFoundError(LookupError):
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
    query_rewrite_version: str
    evidence_assessment_prompt_version: str
    citation_repair_prompt_version: str
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
        query_rewrite_version: str,
        evidence_assessment_prompt_version: str,
        citation_repair_prompt_version: str,
        workflow_version: str,
    ) -> AnswerRun: ...

    async def complete_refusal(self, run: AnswerRun, *, code: str, message: str) -> bool: ...

    async def complete_answer(
        self,
        run: AnswerRun,
        *,
        answer: str,
        citations: list[CitationDraft],
    ) -> bool: ...

    async def fail(self, run_id: UUID, *, code: str, message: str) -> bool: ...

    async def mark_running(self, run_id: UUID) -> bool: ...

    async def set_retrieval_query(self, run_id: UUID, query: str) -> bool: ...

    async def set_workflow_trace(
        self,
        run_id: UUID,
        trace: dict[str, object],
    ) -> bool: ...

    async def get_status(self, run_id: UUID) -> AnswerRunStatus | None: ...

    async def request_cancel(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> AnswerRunStatus | None: ...

    async def cancel(self, run_id: UUID) -> bool: ...

    async def rollback(self) -> None: ...

    async def list_page(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[AnswerHistoryRow]: ...

    async def commit(self) -> None: ...


class AnswerWorkflowRunControl:
    def __init__(self, repository: AnswerRepositoryPort) -> None:
        self._repository = repository

    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool:
        updated = await self._repository.set_retrieval_query(run_id, query)
        await self._repository.commit()
        return updated

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool:
        updated = await self._repository.set_workflow_trace(run_id, trace.to_payload())
        await self._repository.commit()
        return updated

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return await self._repository.get_status(run_id) in {
            "cancel_requested",
            "cancelled",
        }


class AnswerService:
    def __init__(
        self,
        *,
        repository: AnswerRepositoryPort,
        conversations: ConversationService,
        workflow: AnswerWorkflow,
        metadata: AnswerExecutionMetadata,
        context_question_limit: int,
    ) -> None:
        if context_question_limit <= 0:
            raise ValueError("context question limit must be positive")
        self._repository = repository
        self._conversations = conversations
        self._workflow = workflow
        self._metadata = metadata
        self._context_question_limit = context_question_limit

    async def start(
        self,
        *,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        content: str,
    ) -> AsyncIterator[AnswerEvent]:
        recent_questions = await self._conversations.recent_questions(
            knowledge_base_id,
            conversation_id,
            limit=self._context_question_limit,
        )
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
            query_rewrite_version=self._metadata.query_rewrite_version,
            evidence_assessment_prompt_version=(
                self._metadata.evidence_assessment_prompt_version
            ),
            citation_repair_prompt_version=self._metadata.citation_repair_prompt_version,
            workflow_version=self._metadata.workflow_version,
        )
        await self._repository.commit()
        return self._stream(
            run=run,
            knowledge_base_id=knowledge_base_id,
            content=content,
            recent_questions=[item.content for item in recent_questions],
        )

    async def request_cancel(
        self,
        *,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> AnswerCancellationResponse:
        await self._conversations.get(knowledge_base_id, conversation_id)
        run_status = await self._repository.request_cancel(
            knowledge_base_id,
            conversation_id,
            run_id,
        )
        if run_status is None:
            raise AnswerRunNotFoundError
        await self._repository.commit()
        return AnswerCancellationResponse(run_id=run_id, status=run_status)

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
            _encode_cursor(selected[-1].run.created_at, selected[-1].run.id) if has_more else None
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
        recent_questions: list[str],
    ) -> AsyncIterator[AnswerEvent]:
        run_id = run.id
        execution = self._execute_stream(
            run=run,
            knowledge_base_id=knowledge_base_id,
            content=content,
            recent_questions=recent_questions,
        )
        try:
            async for event in execution:
                yield event
        except (asyncio.CancelledError, GeneratorExit):
            with CancelScope(shield=True):
                await self._repository.rollback()
                await self._cancel(run_id)
            raise
        except Exception:
            logger.exception("answer_run_failed_unexpectedly", run_id=str(run_id))
            await self._repository.rollback()
            if await self._fail(
                run_id,
                "ANSWER_RUN_UNEXPECTED_ERROR",
                "Answer generation failed unexpectedly",
            ):
                yield AnswerErrorEvent(
                    run_id=run_id,
                    code="ANSWER_RUN_UNEXPECTED_ERROR",
                    message="Answer generation failed unexpectedly",
                )
            else:
                yield await self._cancel(run_id)
        finally:
            await self._close_stream(execution)

    async def _execute_stream(
        self,
        *,
        run: AnswerRun,
        knowledge_base_id: UUID,
        content: str,
        recent_questions: list[str],
    ) -> AsyncIterator[AnswerEvent]:
        if not await self._repository.mark_running(run.id):
            yield await self._cancel(run.id)
            return
        await self._repository.commit()
        emitted_retrieving = False
        emitted_generating = False
        execution = self._workflow.run(
            WorkflowRequest(
                run_id=run.id,
                knowledge_base_id=knowledge_base_id,
                question=content,
                recent_questions=recent_questions,
            )
        )
        try:
            async for event in execution:
                if isinstance(event, WorkflowStatus):
                    if event.stage in {"analyzing", "retrieving", "assessing"}:
                        if not emitted_retrieving:
                            emitted_retrieving = True
                            yield AnswerStatusEvent(run_id=run.id, status="retrieving")
                    elif not emitted_generating:
                        emitted_generating = True
                        yield AnswerStatusEvent(run_id=run.id, status="generating")
                elif isinstance(event, WorkflowDelta):
                    yield AnswerDeltaEvent(run_id=run.id, delta=event.delta)
                elif isinstance(event, WorkflowCancelled):
                    yield await self._cancel(run.id)
                    return
                elif isinstance(event, WorkflowRefused):
                    if not await self._repository.complete_refusal(
                        run,
                        code=event.code,
                        message=event.message,
                    ):
                        yield await self._cancel(run.id)
                        return
                    await self._repository.commit()
                    yield AnswerRefusalEvent(
                        run_id=run.id,
                        code=event.code,
                        message=event.message,
                    )
                    return
                elif isinstance(event, WorkflowAnswered):
                    drafts = [
                        self._citation_draft(run.id, item) for item in event.evidence
                    ]
                    if not await self._repository.complete_answer(
                        run,
                        answer=event.answer,
                        citations=drafts,
                    ):
                        yield await self._cancel(run.id)
                        return
                    await self._repository.commit()
                    yield AnswerFinalEvent(
                        run_id=run.id,
                        answer=event.answer,
                        citations=[
                            self._citation_response(knowledge_base_id, item)
                            for item in drafts
                        ],
                    )
                    return
            raise RuntimeError("answer workflow completed without a terminal event")
        except (EmbeddingProviderError, LlmProviderError, RerankerProviderError) as error:
            if not await self._fail(run.id, error.code, error.safe_message):
                yield await self._cancel(run.id)
                return
            yield AnswerErrorEvent(run_id=run.id, code=error.code, message=error.safe_message)
        finally:
            await self._close_stream(execution)

    async def _fail(self, run_id: UUID, code: str, message: str) -> bool:
        failed = await self._repository.fail(run_id, code=code, message=message)
        await self._repository.commit()
        return failed

    async def _cancel(self, run_id: UUID) -> AnswerCancelledEvent:
        await self._repository.cancel(run_id)
        await self._repository.commit()
        return AnswerCancelledEvent(run_id=run_id)

    @staticmethod
    async def _close_stream(stream: AsyncIterator[object]) -> None:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()

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
            retrieval_query=run.retrieval_query,
            query_rewrite_version=run.query_rewrite_version,
            evidence_assessment_prompt_version=(
                run.evidence_assessment_prompt_version
            ),
            citation_repair_prompt_version=run.citation_repair_prompt_version,
            workflow_version=run.workflow_version,
            workflow_trace=AnswerWorkflowTrace.model_validate(run.workflow_trace),
            created_at=run.created_at,
            completed_at=run.completed_at,
            citations=[
                self._citation_response(run.knowledge_base_id, citation)
                for citation in row.citations
            ],
        )
