from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from sourcetrace.api.dependencies import get_knowledge_base_service
from sourcetrace.core.errors import AppError, ErrorResponse
from sourcetrace.modules.knowledge_bases.schemas import (
    KnowledgeBaseCreate,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
)
from sourcetrace.modules.knowledge_bases.service import (
    InvalidKnowledgeBaseCursorError,
    KnowledgeBaseNameConflictError,
    KnowledgeBaseNotFoundError,
    KnowledgeBaseService,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


ServiceDependency = Annotated[KnowledgeBaseService, Depends(get_knowledge_base_service)]
PageLimit = Annotated[int, Query(ge=1, le=100)]


@router.post(
    "",
    response_model=KnowledgeBaseResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Validation Error",
        },
    },
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    service: ServiceDependency,
) -> KnowledgeBaseResponse:
    try:
        knowledge_base = await service.create(payload.name)
    except KnowledgeBaseNameConflictError as error:
        raise AppError(
            code="KNOWLEDGE_BASE_NAME_CONFLICT",
            message="A knowledge base with this name already exists",
            status_code=status.HTTP_409_CONFLICT,
        ) from error
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.get(
    "",
    response_model=KnowledgeBaseListResponse,
    responses={
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Validation Error",
        }
    },
)
async def list_knowledge_bases(
    service: ServiceDependency,
    limit: PageLimit = 20,
    cursor: str | None = None,
) -> KnowledgeBaseListResponse:
    try:
        page = await service.list(limit=limit, cursor=cursor)
    except InvalidKnowledgeBaseCursorError as error:
        raise AppError(
            code="INVALID_CURSOR",
            message="The pagination cursor is invalid",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        ) from error
    return KnowledgeBaseListResponse(
        items=[KnowledgeBaseResponse.model_validate(item) for item in page.items],
        next_cursor=page.next_cursor,
    )


@router.get(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Validation Error",
        },
    },
)
async def get_knowledge_base(
    knowledge_base_id: UUID,
    service: ServiceDependency,
) -> KnowledgeBaseResponse:
    try:
        knowledge_base = await service.get(knowledge_base_id)
    except KnowledgeBaseNotFoundError as error:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="Knowledge base not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    return KnowledgeBaseResponse.model_validate(knowledge_base)


@router.delete(
    "/{knowledge_base_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {
            "model": ErrorResponse,
            "description": "Validation Error",
        },
    },
)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    service: ServiceDependency,
    confirm: Annotated[bool, Query()],
) -> Response:
    if not confirm:
        raise AppError(
            code="DELETE_CONFIRMATION_REQUIRED",
            message="Permanent deletion must be explicitly confirmed",
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    try:
        await service.delete(knowledge_base_id)
    except KnowledgeBaseNotFoundError as error:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="Knowledge base not found",
            status_code=status.HTTP_404_NOT_FOUND,
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)
