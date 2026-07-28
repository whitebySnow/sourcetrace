from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.modules.answers.models import AnswerRun, Citation
from sourcetrace.modules.answers.service import AnswerHistoryRow, CitationDraft
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
        workflow_version: str,
    ) -> AnswerRun:
        run = AnswerRun(
            question_id=question.id,
            conversation_id=question.conversation_id,
            knowledge_base_id=question.knowledge_base_id,
            status="running",
            llm_provider=llm_provider,
            llm_model=llm_model,
            prompt_version=prompt_version,
            retrieval_version=retrieval_version,
            workflow_version=workflow_version,
        )
        self._session.add(run)
        await self._session.flush()
        await self._session.refresh(run)
        return run

    async def complete_refusal(
        self,
        run: AnswerRun,
        *,
        code: str,
        message: str,
    ) -> None:
        run.status = "completed"
        run.outcome = "refused"
        run.refusal_code = code
        run.refusal_message = message
        run.completed_at = datetime.now(UTC)
        await self._session.flush()

    async def complete_answer(
        self,
        run: AnswerRun,
        *,
        answer: str,
        citations: list[CitationDraft],
    ) -> None:
        run.status = "completed"
        run.outcome = "answered"
        run.answer_text = answer
        run.completed_at = datetime.now(UTC)
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

    async def fail(self, run: AnswerRun, *, code: str, message: str) -> None:
        run.status = "failed"
        run.failure_code = code
        run.failure_message = message
        run.completed_at = datetime.now(UTC)
        await self._session.flush()

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
