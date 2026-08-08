import httpx

from sourcetrace.core.config import Settings
from sourcetrace.db.session import session_factory
from sourcetrace.evaluation.harness import EvaluationHarness
from sourcetrace.evaluation.models import (
    EvaluationDataset,
    EvaluationReport,
    EvaluationRunMetadata,
)
from sourcetrace.evaluation.repository import CorpusProvenance, EvaluationCorpusRepository
from sourcetrace.evaluation.workflow_subject import WorkflowEvaluationSubject
from sourcetrace.modules.retrieval.repository import PgVectorRetrievalRepository
from sourcetrace.modules.retrieval.service import RetrievalService
from sourcetrace.rag.embeddings import BgeM3EmbeddingProvider, EmbeddingConfig
from sourcetrace.rag.llm import (
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleCitationRepairer,
    OpenAICompatibleConfig,
    OpenAICompatibleEvidenceAssessor,
    OpenAICompatibleQuestionPlanner,
)
from sourcetrace.rag.rerankers import BgeCrossEncoderReranker, RerankerConfig
from sourcetrace.rag.workflow import AnswerWorkflow


def _model_identity(model: str) -> str:
    parts = [part for part in model.replace("\\", "/").rstrip("/").split("/") if part]
    return "/".join(parts[-2:])


def _resolve_embedding_model(provenance: CorpusProvenance, settings: Settings) -> str:
    mismatches: list[str] = []
    if settings.embedding_provider != provenance.embedding_provider:
        mismatches.append("embedding provider")
    if _model_identity(settings.embedding_model) != _model_identity(provenance.embedding_model):
        mismatches.append("embedding model")
    if settings.embedding_model_revision != provenance.embedding_revision:
        mismatches.append("embedding revision")
    if settings.embedding_dimension != provenance.embedding_dimension:
        mismatches.append("embedding dimension")
    if settings.embedding_config_version != provenance.embedding_version:
        mismatches.append("embedding config version")
    if mismatches:
        details = ", ".join(mismatches)
        raise RuntimeError(
            f"runtime embedding configuration does not match corpus provenance: {details}"
        )
    return settings.embedding_model


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
        structured_output_mode=settings.llm_structured_output_mode,
        structured_output_thinking=settings.llm_structured_output_thinking,
    )


async def run_real_evaluation(
    dataset: EvaluationDataset,
    *,
    code_commit: str,
    settings: Settings,
) -> EvaluationReport:
    if dataset.review.status != "reviewed":
        raise ValueError("real evaluations require a human-reviewed dataset")
    async with session_factory() as session, httpx.AsyncClient() as client:
        provenance = await EvaluationCorpusRepository(session).get_provenance(
            dataset.knowledge_base_id,
            dataset.document_version_ids,
        )
        if provenance.embedding_provider != "sentence-transformers":
            raise RuntimeError(f"unsupported embedding provider: {provenance.embedding_provider}")
        embedding = BgeM3EmbeddingProvider(
            EmbeddingConfig(
                provider=provenance.embedding_provider,
                model=_resolve_embedding_model(provenance, settings),
                revision=provenance.embedding_revision,
                cache_dir=settings.embedding_cache_dir,
                endpoint=settings.embedding_hf_endpoint,
                device=settings.embedding_device,
                batch_size=settings.embedding_batch_size,
                dimension=provenance.embedding_dimension,
                version=provenance.embedding_version,
            )
        )
        planner = OpenAICompatibleQuestionPlanner(
            _llm_config(
                settings,
                prompt_version=settings.llm_retrieval_plan_prompt_version,
            ),
            client=client,
        )
        if settings.reranker_provider != "sentence-transformers":
            raise RuntimeError(f"unsupported reranker provider: {settings.reranker_provider}")
        reranker = BgeCrossEncoderReranker(
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
        retrieval = RetrievalService(
            repository=PgVectorRetrievalRepository(
                session,
                document_version_ids=dataset.document_version_ids,
                channel_rrf_rank_constant=settings.retrieval_rrf_rank_constant,
            ),
            embedding_provider=embedding,
            question_planner=planner,
            reranker=reranker,
            top_k=settings.retrieval_top_k,
            page_neighbor_count=settings.retrieval_page_neighbor_count,
            rrf_rank_constant=settings.retrieval_rrf_rank_constant,
        )
        subject = WorkflowEvaluationSubject(
            retrieval=retrieval,
            workflow_factory=lambda recording_retrieval, run_control: AnswerWorkflow(
                retrieval=recording_retrieval,
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
                run_control=run_control,
                minimum_score=settings.retrieval_minimum_score,
                minimum_evidence=settings.retrieval_minimum_evidence,
            ),
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
                parser_version=provenance.parser_version,
                tokenizer=provenance.tokenizer,
                chunk_size=provenance.chunk_size,
                chunk_overlap=provenance.chunk_overlap,
                chunking_version=provenance.chunking_version,
                embedding_provider=provenance.embedding_provider,
                embedding_model=provenance.embedding_model,
                embedding_revision=provenance.embedding_revision,
                embedding_dimension=provenance.embedding_dimension,
                embedding_version=provenance.embedding_version,
                retrieval_version=settings.retrieval_config_version,
                retrieval_top_k=settings.retrieval_top_k,
                retrieval_page_neighbor_count=settings.retrieval_page_neighbor_count,
                retrieval_rrf_rank_constant=settings.retrieval_rrf_rank_constant,
                retrieval_minimum_score=settings.retrieval_minimum_score,
                retrieval_minimum_evidence=settings.retrieval_minimum_evidence,
                generation_prompt_version=settings.llm_prompt_version,
                question_rewrite_prompt_version=(settings.llm_retrieval_plan_prompt_version),
                evidence_assessment_prompt_version=(
                    settings.llm_evidence_assessment_prompt_version
                ),
                citation_repair_prompt_version=(settings.llm_citation_repair_prompt_version),
            ),
        )
