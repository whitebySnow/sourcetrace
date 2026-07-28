from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from sourcetrace.api.dependencies import get_conversation_service
from sourcetrace.core.errors import AppError, ErrorResponse
from sourcetrace.modules.conversations.schemas import (
    ConversationCreate,
    ConversationListResponse,
    ConversationResponse,
    QuestionCreate,
    QuestionListResponse,
    QuestionResponse,
)
from sourcetrace.modules.conversations.service import (
    ConversationNotFoundError,
    ConversationService,
    InvalidConversationCursorError,
)
from sourcetrace.modules.knowledge_bases.service import KnowledgeBaseNotFoundError

router = APIRouter(
    prefix="/knowledge-bases/{knowledge_base_id}/conversations",
    tags=["conversations"],
)

ServiceDependency = Annotated[ConversationService, Depends(get_conversation_service)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unprocessable Content",
        },
    },
)
async def create_conversation(
    knowledge_base_id: UUID,
    payload: ConversationCreate,
    service: ServiceDependency,
) -> ConversationResponse:
    try:
        conversation = await service.create(knowledge_base_id, payload.title)
    except KnowledgeBaseNotFoundError as error:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="Knowledge base not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    return ConversationResponse.model_validate(conversation)


@router.get(
    "",
    response_model=ConversationListResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unprocessable Content",
        },
    },
)
async def list_conversations(
    knowledge_base_id: UUID,
    service: ServiceDependency,
    limit: PageLimit = 20,
    cursor: str | None = None,
) -> ConversationListResponse:
    try:
        page = await service.list(
            knowledge_base_id,
            limit=limit,
            cursor=cursor,
        )
    except KnowledgeBaseNotFoundError as error:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="Knowledge base not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    except InvalidConversationCursorError as error:
        raise AppError(
            code="INVALID_CURSOR",
            message="The pagination cursor is invalid",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    return ConversationListResponse(
        items=[ConversationResponse.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{conversation_id}",
    response_model=ConversationResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unprocessable Content",
        },
    },
)
async def get_conversation(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    service: ServiceDependency,
) -> ConversationResponse:
    try:
        conversation = await service.get(knowledge_base_id, conversation_id)
    except ConversationNotFoundError as error:
        raise AppError(
            code="CONVERSATION_NOT_FOUND",
            message="Conversation not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    return ConversationResponse.model_validate(conversation)


@router.post(
    "/{conversation_id}/questions",
    response_model=QuestionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unprocessable Content",
        },
    },
)
async def create_question(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    payload: QuestionCreate,
    service: ServiceDependency,
) -> QuestionResponse:
    try:
        question = await service.create_question(
            knowledge_base_id,
            conversation_id,
            payload.content,
        )
    except ConversationNotFoundError as error:
        raise AppError(
            code="CONVERSATION_NOT_FOUND",
            message="Conversation not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    return QuestionResponse.model_validate(question)


@router.get(
    "/{conversation_id}/questions",
    response_model=QuestionListResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Unprocessable Content",
        },
    },
)
async def list_questions(
    knowledge_base_id: UUID,
    conversation_id: UUID,
    service: ServiceDependency,
    limit: PageLimit = 20,
    cursor: str | None = None,
) -> QuestionListResponse:
    try:
        page = await service.list_questions(
            knowledge_base_id,
            conversation_id,
            limit=limit,
            cursor=cursor,
        )
    except ConversationNotFoundError as error:
        raise AppError(
            code="CONVERSATION_NOT_FOUND",
            message="Conversation not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    except InvalidConversationCursorError as error:
        raise AppError(
            code="INVALID_CURSOR",
            message="The pagination cursor is invalid",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    return QuestionListResponse(
        items=[QuestionResponse.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
    )
