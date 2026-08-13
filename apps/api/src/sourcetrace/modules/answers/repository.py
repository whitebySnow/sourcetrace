from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.answers.models import AnswerRun, AnswerRunStatus, Citation
from sourcetrace.modules.answers.service import (
    ActiveAnswerRunExistsError,
    AnswerHistoryRow,
    CitationDraft,
)
from sourcetrace.modules.conversations.models import Question


class AnswerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

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
        provider_connect_timeout_seconds: float,
        provider_read_timeout_seconds: float,
        provider_request_timeout_seconds: float,
        provider_operation_deadline_seconds: float,
    ) -> AnswerRun:
        run = AnswerRun(
            question_id=question.id,
            conversation_id=question.conversation_id,
            knowledge_base_id=question.knowledge_base_id,
            status="pending",
            llm_provider=llm_provider,
            llm_model=llm_model,
            prompt_version=prompt_version,
            retrieval_version=retrieval_version,
            retrieval_query=question.content,
            query_rewrite_version=query_rewrite_version,
            evidence_assessment_prompt_version=evidence_assessment_prompt_version,
            citation_repair_prompt_version=citation_repair_prompt_version,
            workflow_version=workflow_version,
            provider_connect_timeout_seconds=provider_connect_timeout_seconds,
            provider_read_timeout_seconds=provider_read_timeout_seconds,
            provider_request_timeout_seconds=provider_request_timeout_seconds,
            provider_operation_deadline_seconds=provider_operation_deadline_seconds,
            workflow_trace={
                "retrieval_plan_version": None,
                "retrieval_queries": [],
                "retrieval_rounds": [],
                "assessments": [],
                "citation_validations": [],
                "supplemental_retrieval_attempts": 0,
                "citation_repair_attempts": 0,
            },
        )
        self._session.add(run)
        try:
            await self._session.flush()
        except IntegrityError as error:
            constraint_name = self._constraint_name(error)
            await self._session.rollback()
            if constraint_name == "uq_answer_runs_one_active_per_conversation":
                raise ActiveAnswerRunExistsError from error
            raise
        await self._session.refresh(run)
        return run

    @staticmethod
    def _constraint_name(error: BaseException) -> str | None:
        current: BaseException | None = error
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            constraint_name = getattr(current, "constraint_name", None)
            if isinstance(constraint_name, str):
                return constraint_name
            current = current.__cause__ or current.__context__
        return None

    async def mark_running(self, run_id: UUID) -> bool:
        updated_id = await self._session.scalar(
            update(AnswerRun)
            .where(AnswerRun.id == run_id, AnswerRun.status == "pending")
            .values(status="running")
            .returning(AnswerRun.id)
        )
        return updated_id is not None

    async def set_retrieval_query(self, run_id: UUID, query: str) -> bool:
        updated_id = await self._session.scalar(
            update(AnswerRun)
            .where(AnswerRun.id == run_id, AnswerRun.status == "running")
            .values(retrieval_query=query)
            .returning(AnswerRun.id)
        )
        return updated_id is not None

    async def set_workflow_trace(
        self,
        run_id: UUID,
        trace: dict[str, object],
    ) -> bool:
        updated_id = await self._session.scalar(
            update(AnswerRun)
            .where(AnswerRun.id == run_id, AnswerRun.status == "running")
            .values(workflow_trace=trace)
            .returning(AnswerRun.id)
        )
        return updated_id is not None

    async def get_status(self, run_id: UUID) -> AnswerRunStatus | None:
        return await self._session.scalar(select(AnswerRun.status).where(AnswerRun.id == run_id))

    async def request_cancel(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        run_id: UUID,
    ) -> AnswerRunStatus | None:
        run = await self._session.scalar(
            select(AnswerRun)
            .where(
                AnswerRun.id == run_id,
                AnswerRun.knowledge_base_id == knowledge_base_id,
                AnswerRun.conversation_id == conversation_id,
            )
            .with_for_update()
        )
        if run is None:
            return None
        if run.status in {"pending", "running"}:
            run.status = "cancel_requested"
            await self._session.flush()
        return run.status

    async def cancel(self, run_id: UUID) -> bool:
        updated_id = await self._session.scalar(
            update(AnswerRun)
            .where(
                AnswerRun.id == run_id,
                AnswerRun.status.in_(("pending", "running", "cancel_requested")),
            )
            .values(status="cancelled", completed_at=datetime.now(UTC))
            .returning(AnswerRun.id)
        )
        return updated_id is not None

    async def fail_interrupted_runs(self) -> int:
        result = await self._session.execute(
            update(AnswerRun)
            .where(AnswerRun.status.in_(("pending", "running", "cancel_requested")))
            .values(
                status="failed",
                failure_code="ANSWER_RUN_INTERRUPTED",
                failure_message="Answer run was interrupted before completion",
                completed_at=datetime.now(UTC),
            )
        )
        return result.rowcount  # type: ignore[attr-defined, no-any-return]

    async def complete_refusal(
        self,
        run: AnswerRun,
        *,
        code: str,
        message: str,
    ) -> bool:
        updated_id = await self._session.scalar(
            update(AnswerRun)
            .where(AnswerRun.id == run.id, AnswerRun.status == "running")
            .values(
                status="completed",
                outcome="refused",
                refusal_code=code,
                refusal_message=message,
                completed_at=datetime.now(UTC),
            )
            .returning(AnswerRun.id)
        )
        return updated_id is not None

    async def complete_answer(
        self,
        run: AnswerRun,
        *,
        answer: str,
        citations: list[CitationDraft],
    ) -> bool:
        updated_id = await self._session.scalar(
            update(AnswerRun)
            .where(AnswerRun.id == run.id, AnswerRun.status == "running")
            .values(
                status="completed",
                outcome="answered",
                answer_text=answer,
                completed_at=datetime.now(UTC),
            )
            .returning(AnswerRun.id)
        )
        if updated_id is None:
            return False
        self._session.add_all(
            [
                Citation(
                    id=item.id,
                    answer_run_id=run.id,
                    chunk_id=item.chunk_id,
                    document_id=item.document_id,
                    document_version_id=item.document_version_id,
                    document_name=item.document_name,
                    page_number=item.page_number,
                    excerpt=item.excerpt,
                )
                for item in citations
            ]
        )
        await self._session.flush()
        return True

    async def fail(self, run_id: UUID, *, code: str, message: str) -> bool:
        updated_id = await self._session.scalar(
            update(AnswerRun)
            .where(AnswerRun.id == run_id, AnswerRun.status == "running")
            .values(
                status="failed",
                failure_code=code,
                failure_message=message,
                completed_at=datetime.now(UTC),
            )
            .returning(AnswerRun.id)
        )
        return updated_id is not None

    async def list_page(
        self,
        knowledge_base_id: UUID,
        conversation_id: UUID,
        *,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[AnswerHistoryRow]:
        statement = (
            select(AnswerRun, Question)
            .join(Question, Question.id == AnswerRun.question_id)
            .where(
                AnswerRun.knowledge_base_id == knowledge_base_id,
                AnswerRun.conversation_id == conversation_id,
            )
        )
        if after is not None:
            created_at, run_id = after
            statement = statement.where(
                or_(
                    AnswerRun.created_at > created_at,
                    and_(AnswerRun.created_at == created_at, AnswerRun.id > run_id),
                )
            )
        rows = (
            await self._session.execute(
                statement.order_by(AnswerRun.created_at, AnswerRun.id).limit(limit)
            )
        ).all()
        run_ids = [row.AnswerRun.id for row in rows]
        citations_by_run: dict[UUID, list[Citation]] = {run_id: [] for run_id in run_ids}
        if run_ids:
            citations = await self._session.scalars(
                select(Citation)
                .where(Citation.answer_run_id.in_(run_ids))
                .order_by(Citation.answer_run_id, Citation.id)
            )
            for citation in citations:
                citations_by_run[citation.answer_run_id].append(citation)
        return [
            AnswerHistoryRow(
                run=row.AnswerRun,
                question=row.Question,
                citations=citations_by_run[row.AnswerRun.id],
            )
            for row in rows
        ]

    async def commit(self) -> None:
        await self._session.commit()

    async def rollback(self) -> None:
        await self._session.rollback()
