from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.core.config import get_settings
from sourcetrace.db.session import get_session
from sourcetrace.modules.answers.repository import AnswerRepository
from sourcetrace.modules.answers.service import AnswerExecutionMetadata, AnswerService
from sourcetrace.modules.conversations.repository import ConversationRepository
from sourcetrace.modules.conversations.service import ConversationService
from sourcetrace.modules.documents.repository import DocumentRepository
from sourcetrace.modules.documents.service import DocumentSourceService
from sourcetrace.modules.documents.storage import LocalDocumentStorage
from sourcetrace.modules.knowledge_bases.repository import KnowledgeBaseRepository
from sourcetrace.modules.knowledge_bases.service import KnowledgeBaseService
from sourcetrace.modules.retrieval.repository import PgVectorRetrievalRepository
from sourcetrace.modules.retrieval.service import RetrievalService
from sourcetrace.rag.embeddings import BgeM3EmbeddingProvider, EmbeddingConfig
from sourcetrace.rag.llm import (
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleConfig,
    OpenAICompatibleQuestionRewriter,
)
from sourcetrace.rag.ports import AnswerGenerator, EmbeddingProvider, QuestionRewriter


def _openai_compatible_config(*, prompt_version: str) -> OpenAICompatibleConfig:
    settings = get_settings()
    if settings.llm_provider != "openai-compatible":
        raise RuntimeError(f"unsupported LLM provider: {settings.llm_provider}")
    if settings.llm_api_key is None:
        raise RuntimeError("LLM API key is not configured")
    return OpenAICompatibleConfig(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        prompt_version=prompt_version,
    )


def get_knowledge_base_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeBaseService:
    return KnowledgeBaseService(
        KnowledgeBaseRepository(session),
        resource_cleaner=LocalDocumentStorage(get_settings().upload_dir),
    )


def get_conversation_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    knowledge_bases: Annotated[
        KnowledgeBaseService,
        Depends(get_knowledge_base_service),
    ],
) -> ConversationService:
    return ConversationService(
        ConversationRepository(session),
        knowledge_bases,
    )


def get_document_source_storage() -> LocalDocumentStorage:
    return LocalDocumentStorage(get_settings().upload_dir)


def get_document_source_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    storage: Annotated[
        LocalDocumentStorage,
        Depends(get_document_source_storage),
    ],
) -> DocumentSourceService:
    return DocumentSourceService(DocumentRepository(session), storage)


def get_query_embedding_provider() -> EmbeddingProvider:
    settings = get_settings()
    if settings.embedding_provider != "sentence-transformers":
        raise RuntimeError(f"unsupported embedding provider: {settings.embedding_provider}")
    return BgeM3EmbeddingProvider(
        EmbeddingConfig(
            provider=settings.embedding_provider,
            model=settings.embedding_model,
            revision=settings.embedding_model_revision,
            cache_dir=settings.embedding_cache_dir,
            endpoint=settings.embedding_hf_endpoint,
            device=settings.embedding_device,
            batch_size=settings.embedding_batch_size,
            dimension=settings.embedding_dimension,
            version=settings.embedding_config_version,
        )
    )


async def get_answer_generator() -> AsyncIterator[AnswerGenerator]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        yield OpenAICompatibleAnswerGenerator(
            _openai_compatible_config(prompt_version=settings.llm_prompt_version),
            client=client,
        )


async def get_question_rewriter() -> AsyncIterator[QuestionRewriter]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        yield OpenAICompatibleQuestionRewriter(
            _openai_compatible_config(
                prompt_version=settings.llm_question_rewrite_prompt_version
            ),
            client=client,
        )


def get_answer_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    conversations: Annotated[
        ConversationService,
        Depends(get_conversation_service),
    ],
    embedding_provider: Annotated[
        EmbeddingProvider,
        Depends(get_query_embedding_provider),
    ],
    generator: Annotated[AnswerGenerator, Depends(get_answer_generator)],
    question_rewriter: Annotated[QuestionRewriter, Depends(get_question_rewriter)],
) -> AnswerService:
    settings = get_settings()
    return AnswerService(
        repository=AnswerRepository(session),
        conversations=conversations,
        retrieval=RetrievalService(
            repository=PgVectorRetrievalRepository(session),
            embedding_provider=embedding_provider,
            question_rewriter=question_rewriter,
            top_k=settings.retrieval_top_k,
        ),
        generator=generator,
        metadata=AnswerExecutionMetadata(
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            prompt_version=settings.llm_prompt_version,
            retrieval_version=settings.retrieval_config_version,
            query_rewrite_version=settings.llm_question_rewrite_prompt_version,
            workflow_version=settings.answer_workflow_version,
        ),
        minimum_score=settings.retrieval_minimum_score,
        minimum_evidence=settings.retrieval_minimum_evidence,
        context_question_limit=settings.answer_context_question_limit,
    )
