from collections.abc import AsyncIterator
from functools import lru_cache
from threading import Lock
from typing import Annotated

import httpx
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from sourcetrace.core.config import get_settings
from sourcetrace.db.session import get_session
from sourcetrace.modules.answers.repository import AnswerRepository
from sourcetrace.modules.answers.service import (
    AnswerExecutionMetadata,
    AnswerService,
    AnswerWorkflowRunControl,
)
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
    OpenAICompatibleCitationRepairer,
    OpenAICompatibleClaimSupportVerifier,
    OpenAICompatibleConfig,
    OpenAICompatibleEvidenceAssessor,
    OpenAICompatibleQuestionPlanner,
)
from sourcetrace.rag.ports import (
    AnswerGenerator,
    CitationRepairer,
    ClaimSupportVerifier,
    EmbeddingProvider,
    EvidenceAssessor,
    QuestionPlanner,
    Reranker,
)
from sourcetrace.rag.rerankers import BgeCrossEncoderReranker, RerankerConfig
from sourcetrace.rag.workflow import AnswerWorkflow


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
        answer_output_thinking=settings.llm_answer_output_thinking,
        structured_output_mode=settings.llm_structured_output_mode,
        structured_output_thinking=settings.llm_structured_output_thinking,
        structured_output_max_tokens=settings.llm_structured_output_max_tokens,
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


_reranker_dependency_lock = Lock()


def get_reranker() -> Reranker:
    with _reranker_dependency_lock:
        return _get_cached_reranker()


@lru_cache
def _get_cached_reranker() -> Reranker:
    settings = get_settings()
    if settings.reranker_provider != "sentence-transformers":
        raise RuntimeError(f"unsupported reranker provider: {settings.reranker_provider}")
    return BgeCrossEncoderReranker(
        RerankerConfig(
            provider=settings.reranker_provider,
            model=settings.reranker_model,
            revision=settings.reranker_model_revision,
            weight_sha256=settings.reranker_model_weight_sha256,
            cache_dir=settings.reranker_cache_dir,
            device=settings.reranker_device,
            batch_size=settings.reranker_batch_size,
            version=settings.reranker_config_version,
        )
    )


async def get_answer_generator() -> AsyncIterator[AnswerGenerator]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        yield OpenAICompatibleAnswerGenerator(
            _openai_compatible_config(prompt_version=settings.llm_prompt_version),
            client=client,
        )


async def get_question_planner() -> AsyncIterator[QuestionPlanner]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        yield OpenAICompatibleQuestionPlanner(
            _openai_compatible_config(prompt_version=settings.llm_retrieval_plan_prompt_version),
            client=client,
        )


async def get_evidence_assessor() -> AsyncIterator[EvidenceAssessor]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        yield OpenAICompatibleEvidenceAssessor(
            _openai_compatible_config(
                prompt_version=settings.llm_evidence_assessment_prompt_version
            ),
            client=client,
        )


async def get_citation_repairer() -> AsyncIterator[CitationRepairer]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        yield OpenAICompatibleCitationRepairer(
            _openai_compatible_config(prompt_version=settings.llm_citation_repair_prompt_version),
            client=client,
        )


async def get_claim_support_verifier() -> AsyncIterator[ClaimSupportVerifier]:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        yield OpenAICompatibleClaimSupportVerifier(
            _openai_compatible_config(prompt_version=settings.llm_prompt_version),
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
    reranker: Annotated[Reranker, Depends(get_reranker)],
    generator: Annotated[AnswerGenerator, Depends(get_answer_generator)],
    question_planner: Annotated[QuestionPlanner, Depends(get_question_planner)],
    evidence_assessor: Annotated[EvidenceAssessor, Depends(get_evidence_assessor)],
    citation_repairer: Annotated[CitationRepairer, Depends(get_citation_repairer)],
    claim_support_verifier: Annotated[
        ClaimSupportVerifier, Depends(get_claim_support_verifier)
    ],
) -> AnswerService:
    settings = get_settings()
    repository = AnswerRepository(session)
    retrieval = RetrievalService(
        repository=PgVectorRetrievalRepository(
            session,
            channel_rrf_rank_constant=settings.retrieval_rrf_rank_constant,
        ),
        embedding_provider=embedding_provider,
        question_planner=question_planner,
        reranker=reranker,
        top_k=settings.retrieval_top_k,
        page_neighbor_count=settings.retrieval_page_neighbor_count,
        rrf_rank_constant=settings.retrieval_rrf_rank_constant,
    )
    return AnswerService(
        repository=repository,
        conversations=conversations,
        workflow=AnswerWorkflow(
            retrieval=retrieval,
            assessor=evidence_assessor,
            generator=generator,
            claim_support_verifier=claim_support_verifier,
            citation_repairer=citation_repairer,
            run_control=AnswerWorkflowRunControl(repository),
            minimum_score=settings.retrieval_minimum_score,
            minimum_evidence=settings.retrieval_minimum_evidence,
        ),
        metadata=AnswerExecutionMetadata(
            llm_provider=settings.llm_provider,
            llm_model=settings.llm_model,
            prompt_version=settings.llm_prompt_version,
            retrieval_version=settings.retrieval_config_version,
            query_rewrite_version=settings.llm_retrieval_plan_prompt_version,
            evidence_assessment_prompt_version=(settings.llm_evidence_assessment_prompt_version),
            citation_repair_prompt_version=(settings.llm_citation_repair_prompt_version),
            workflow_version=settings.answer_workflow_version,
        ),
        context_question_limit=settings.answer_context_question_limit,
    )
