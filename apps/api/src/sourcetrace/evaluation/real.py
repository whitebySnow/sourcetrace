from uuid import UUID

import httpx

from sourcetrace.core.config import Settings
from sourcetrace.db.session import session_factory
from sourcetrace.evaluation.harness import EvaluationHarness
from sourcetrace.evaluation.models import (
    EvaluationDataset,
    EvaluationReport,
    EvaluationRunMetadata,
)
from sourcetrace.evaluation.workflow_subject import (
    RecordingWorkflowRetrieval,
    WorkflowEvaluationSubject,
)
from sourcetrace.modules.retrieval.repository import PgVectorRetrievalRepository
from sourcetrace.modules.retrieval.service import RetrievalService
from sourcetrace.rag.embeddings import BgeM3EmbeddingProvider, EmbeddingConfig
from sourcetrace.rag.llm import (
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleCitationRepairer,
    OpenAICompatibleConfig,
    OpenAICompatibleEvidenceAssessor,
    OpenAICompatibleQuestionRewriter,
)
from sourcetrace.rag.workflow import AnswerWorkflow, WorkflowTrace


class EvaluationRunControl:
    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool:
        return True

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool:
        return True

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return False


def _llm_config(settings: Settings, *, prompt_version: str) -> OpenAICompatibleConfig:
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


async def run_real_evaluation(
    dataset: EvaluationDataset,
    *,
    code_commit: str,
    settings: Settings,
) -> EvaluationReport:
    if dataset.review.status != "reviewed":
        raise ValueError("real evaluations require a human-reviewed dataset")
    if settings.embedding_provider != "sentence-transformers":
        raise RuntimeError(f"unsupported embedding provider: {settings.embedding_provider}")

    embedding = BgeM3EmbeddingProvider(
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
    async with session_factory() as session, httpx.AsyncClient() as client:
        rewriter = OpenAICompatibleQuestionRewriter(
            _llm_config(
                settings,
                prompt_version=settings.llm_question_rewrite_prompt_version,
            ),
            client=client,
        )
        retrieval = RecordingWorkflowRetrieval(
            RetrievalService(
                repository=PgVectorRetrievalRepository(session),
                embedding_provider=embedding,
                question_rewriter=rewriter,
                top_k=settings.retrieval_top_k,
            )
        )
        workflow = AnswerWorkflow(
            retrieval=retrieval,
            assessor=OpenAICompatibleEvidenceAssessor(
                _llm_config(
                    settings,
                    prompt_version=settings.llm_evidence_assessment_prompt_version,
                ),
                client=client,
            ),
            generator=OpenAICompatibleAnswerGenerator(
                _llm_config(settings, prompt_version=settings.llm_prompt_version),
                client=client,
            ),
            citation_repairer=OpenAICompatibleCitationRepairer(
                _llm_config(
                    settings,
                    prompt_version=settings.llm_citation_repair_prompt_version,
                ),
                client=client,
            ),
            run_control=EvaluationRunControl(),
            minimum_score=settings.retrieval_minimum_score,
            minimum_evidence=settings.retrieval_minimum_evidence,
        )
        subject = WorkflowEvaluationSubject(
            workflow=workflow,
            retrieval=retrieval,
            knowledge_base_id=dataset.knowledge_base_id,
        )
        return await EvaluationHarness().run(
            dataset,
            subject,
            metadata=EvaluationRunMetadata(
                code_commit=code_commit,
                model_provider=settings.llm_provider,
                model_name=settings.llm_model,
                workflow_version=settings.answer_workflow_version,
                chunking_version=settings.ingestion_chunking_config_version,
                embedding_version=settings.embedding_config_version,
                retrieval_version=settings.retrieval_config_version,
            ),
        )
