from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse

from sourcetrace.api.dependencies import get_answer_service
from sourcetrace.core.errors import AppError, ErrorResponse
from sourcetrace.modules.answers.schemas import (
    AnswerCancellationResponse,
    AnswerEvent,
    AnswerHistoryResponse,
    AnswerRequest,
)
from sourcetrace.modules.answers.service import (
    ActiveAnswerRunExistsError,
    AnswerRunNotFoundError,
    AnswerService,
    InvalidAnswerCursorError,
)
from sourcetrace.modules.conversations.service import ConversationNotFoundError

router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
    tags=["answers"],
)

ServiceDependency = Annotated[AnswerService, Depends(get_answer_service)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


async def _encode_events(events: AsyncIterator[AnswerEvent]) -> AsyncIterator[str]:
    try:
        async for event in events:
            yield f"event: {event.type}\ndata: {event.model_dump_json()}\n\n"
    finally:
        close = getattr(events, "aclose", None)
        if close is not None:
            await close()


@router.post(
    "",
    response_model=AnswerEvent,
    response_class=StreamingResponse,
    responses={
        status.HTTP_200_OK: {
            "description": "Versioned answer event stream",
            "content": {
                "text/event-stream": {
                    "schema": {"$ref": "#/components/schemas/AnswerEvent"}
                }
            },
        },
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unprocessable Content",
        },
    },
)
async def stream_answer(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    payload: AnswerRequest,
    service: ServiceDependency,
) -> StreamingResponse:
    try:
        events = await service.start(
            knowledge_base_id=knowledge_base_id,
            conversation_id=conversation_id,
            content=payload.content,
        )
    except ConversationNotFoundError as error:
        raise AppError(
            code="CONVERSATION_NOT_FOUND",
            message="Conversation not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    except ActiveAnswerRunExistsError as error:
        raise AppError(
            code="ANSWER_RUN_ALREADY_ACTIVE",
            message="This conversation already has an active answer run",
            status_code=status.HTTP_409_CONFLICT,
        ) from error
    return StreamingResponse(
        _encode_events(events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/{run_id}/cancel",
    response_model=AnswerCancellationResponse,
    responses={status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def cancel_answer(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    service: ServiceDependency,
) -> AnswerCancellationResponse:
    try:
        return await service.request_cancel(
            knowledge_base_id=knowledge_base_id,
            conversation_id=conversation_id,
            run_id=run_id,
        )
    except (ConversationNotFoundError, AnswerRunNotFoundError) as error:
        raise AppError(
            code="ANSWER_RUN_NOT_FOUND",
            message="Answer run not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error


@router.get(
    "",
    response_model=AnswerHistoryResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unprocessable Content",
        },
    },
)
async def list_answers(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    service: ServiceDependency,
    limit: PageLimit = 20,
    cursor: str | None = None,
) -> AnswerHistoryResponse:
    try:
        page = await service.list_history(
            knowledge_base_id=knowledge_base_id,
            conversation_id=conversation_id,
            limit=limit,
            cursor=cursor,
        )
    except ConversationNotFoundError as error:
        raise AppError(
            code="CONVERSATION_NOT_FOUND",
            message="Conversation not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    except InvalidAnswerCursorError as error:
        raise AppError(
            code="INVALID_CURSOR",
            message="The pagination cursor is invalid",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    return AnswerHistoryResponse(items=page.items, next_cursor=page.next_cursor)
