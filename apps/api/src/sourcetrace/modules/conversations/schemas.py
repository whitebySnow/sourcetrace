from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ConversationTitle = Annotated[str, StringConstraints(min_length=1, max_length=120)]
QuestionContent = Annotated[str, StringConstraints(min_length=1, max_length=4000)]


class ConversationCreate(BaseModel):
    title: ConversationTitle

    @field_validator("title", mode="before")
    @classmethod
    def strip_title(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]
    next_cursor: str | None = Field(default=None)


class QuestionCreate(BaseModel):
    content: QuestionContent

    @field_validator("content", mode="before")
    @classmethod
    def strip_content(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class QuestionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    conversation_id: UUID
    content: str
    created_at: datetime


class QuestionListResponse(BaseModel):
    items: list[QuestionResponse]
    next_cursor: str | None = Field(default=None)
