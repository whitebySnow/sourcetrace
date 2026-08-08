from __future__ import annotations

from collections.abc import Sequence
from typing import Literal
from uuid import UUID

from sourcetrace.core.config import Settings
from sourcetrace.db.session import session_factory
from sourcetrace.evaluation.harness import EvaluationHarness
from sourcetrace.evaluation.hybrid_retrieval import resolve_query_plans
from sourcetrace.evaluation.models import (
    EvaluationDataset,
    HybridCandidateTrace,
    HybridQueryPlanFixture,
    HybridQueryTrace,
    HybridRetrievalCaseResult,
    HybridRetrievalEvaluationReport,
    HybridRetrievalRunMetadata,
    HybridRetrievalSummary,
    ObservedEvidence,
)
from sourcetrace.evaluation.real import _resolve_embedding_model
from sourcetrace.evaluation.repository import EvaluationCorpusRepository
from sourcetrace.modules.retrieval.hybrid import (
    FusedChannelCandidate,
    RankedChannelCandidate,
    build_lexical_search_query,
    fuse_ranked_channels,
)
from sourcetrace.modules.retrieval.repository import PgVectorRetrievalRepository
from sourcetrace.modules.retrieval.service import (
    RetrievalResult,
    RetrievalService,
    RetrievedEvidence,
)
from sourcetrace.rag.embeddings import BgeM3EmbeddingProvider, EmbeddingConfig
from sourcetrace.rag.ports import RetrievalPlanProposal
from sourcetrace.rag.rerankers import BgeCrossEncoderReranker, RerankerConfig

_PAGE_DIVERSITY_POOL_MULTIPLIER = 4
_MAX_CANDIDATE_POOL_SIZE = 100
_LEXICAL_PHRASE_WEIGHT = 2.0
_LEXICAL_VERSION: Literal["postgres-english-or-phrase-v1"] = (
    "postgres-english-or-phrase-v1"
)


class _MarkerEmbeddingProvider:
    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return tuple((float(index),) for index, _text in enumerate(texts))


class _UnusedPlanner:
    async def plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
        document_titles: Sequence[str] = (),
    ) -> RetrievalPlanProposal:
        raise AssertionError("query planning must not run during hybrid evaluation")


class _PrecomputedRepository:
    def __init__(
        self,
        candidates: Sequence[Sequence[FusedChannelCandidate[RetrievedEvidence]]],
        *,
        delegate: PgVectorRetrievalRepository,
    ) -> None:
        self._candidates = tuple(tuple(items) for items in candidates)
        self._delegate = delegate

    async def list_searchable_document_titles(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        return ()

    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        query: str,
        limit: int,
    ) -> list[FusedChannelCandidate[RetrievedEvidence]]:
        marker = int(query_embedding[0])
        return list(self._candidates[marker][:limit])

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]:
        return await self._delegate.expand_page_neighbors(
            knowledge_base_id,
            evidence,
            neighbor_count=neighbor_count,
        )


async def run_real_hybrid_retrieval_evaluation(
    dataset: EvaluationDataset,
    query_plan: HybridQueryPlanFixture,
    *,
    dataset_sha256: str,
    query_plan_sha256: str,
    code_commit: str,
    settings: Settings,
) -> HybridRetrievalEvaluationReport:
    queries_by_case = resolve_query_plans(
        {case.id: case.question for case in dataset.cases},
        query_plan,
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
    )
    pool_limit = min(
        settings.retrieval_top_k * _PAGE_DIVERSITY_POOL_MULTIPLIER,
        _MAX_CANDIDATE_POOL_SIZE,
    )
    results: list[HybridRetrievalCaseResult] = []
    async with session_factory() as session:
        corpus = EvaluationCorpusRepository(session)
        provenance = await corpus.get_provenance(
            dataset.knowledge_base_id,
            dataset.document_version_ids,
        )
        dense_repository = PgVectorRetrievalRepository(
            session,
            document_version_ids=dataset.document_version_ids,
            channel_rrf_rank_constant=settings.retrieval_rrf_rank_constant,
            lexical_phrase_weight=_LEXICAL_PHRASE_WEIGHT,
        )
        embedding_provider = BgeM3EmbeddingProvider(
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
        for case in dataset.cases:
            queries = queries_by_case[case.id]
            embeddings = await embedding_provider.embed(queries)
            if len(embeddings) != len(queries):
                raise ValueError("query embedding provider returned an invalid result")
            dense_lists: list[tuple[FusedChannelCandidate[RetrievedEvidence], ...]] = []
            hybrid_lists: list[tuple[FusedChannelCandidate[RetrievedEvidence], ...]] = []
            channel_fused_by_query: list[
                tuple[FusedChannelCandidate[RetrievedEvidence], ...]
            ] = []
            lexical_enabled: list[bool] = []
            for query, embedding in zip(queries, embeddings, strict=True):
                dense = tuple(
                    await dense_repository.search_dense(
                        dataset.knowledge_base_id,
                        embedding,
                        limit=pool_limit,
                    )
                )
                dense_channel = tuple(
                    RankedChannelCandidate(
                        channel="dense",
                        rank=rank,
                        channel_score=item.score,
                        evidence=item,
                    )
                    for rank, item in enumerate(dense, start=1)
                )
                dense_lists.append(
                    fuse_ranked_channels(
                        dense_channel,
                        rank_constant=settings.retrieval_rrf_rank_constant,
                        limit=pool_limit,
                    )
                )
                lexical_query = build_lexical_search_query(query)
                channel_fused = tuple(
                    await dense_repository.search(
                        dataset.knowledge_base_id,
                        embedding,
                        query=query,
                        limit=pool_limit,
                    )
                )
                hybrid_lists.append(channel_fused)
                channel_fused_by_query.append(channel_fused)
                lexical_enabled.append(lexical_query is not None)

            baseline = await _run_precomputed_retrieval(
                dataset.knowledge_base_id,
                queries,
                dense_lists,
                dense_repository=dense_repository,
                reranker=reranker,
                settings=settings,
            )
            hybrid = await _run_precomputed_retrieval(
                dataset.knowledge_base_id,
                queries,
                hybrid_lists,
                dense_repository=dense_repository,
                reranker=reranker,
                settings=settings,
            )
            baseline_observed = _eligible_observed(baseline, settings.retrieval_minimum_score)
            hybrid_observed = _eligible_observed(hybrid, settings.retrieval_minimum_score)
            baseline_status = EvaluationHarness.retrieval_status(
                case.expected.evidence,
                baseline_observed,
            )
            hybrid_status = EvaluationHarness.retrieval_status(
                case.expected.evidence,
                hybrid_observed,
            )
            eligible_primary_ids = {
                item.chunk_id
                for item in hybrid.primary_evidence
                if item.score >= settings.retrieval_minimum_score
            }
            eligible_expanded_ids = tuple(
                item.chunk_id
                for item in hybrid.evidence
                if item.score >= settings.retrieval_minimum_score
            )
            query_traces: list[HybridQueryTrace] = []
            for query_index, query_result in enumerate(hybrid.query_results):
                channel_by_id = {
                    item.evidence.chunk_id: (rank, item)
                    for rank, item in enumerate(
                        channel_fused_by_query[query_index],
                        start=1,
                    )
                }
                candidate_traces: list[HybridCandidateTrace] = []
                for candidate in query_result.candidates:
                    channel_rank, channel = channel_by_id[candidate.evidence.chunk_id]
                    if candidate.reranker_score is None or candidate.reranked_rank is None:
                        raise ValueError("hybrid candidate was not reranked")
                    candidate_traces.append(
                        HybridCandidateTrace(
                            chunk_id=candidate.evidence.chunk_id,
                            document_version_id=candidate.evidence.document_version_id,
                            page_number=candidate.evidence.page_number,
                            dense_rank=channel.dense_rank,
                            lexical_rank=channel.lexical_rank,
                            channel_fused_rank=channel_rank,
                            cosine_score=candidate.evidence.score,
                            lexical_score=channel.lexical_score,
                            channel_fused_score=channel.fused_score,
                            reranker_score=candidate.reranker_score,
                            reranked_rank=candidate.reranked_rank,
                            selected_for_query_coverage=(
                                candidate.selected_for_query_coverage
                            ),
                            selected_as_primary=(
                                candidate.evidence.chunk_id in eligible_primary_ids
                            ),
                        )
                    )
                query_traces.append(
                    HybridQueryTrace(
                        query=query_result.query,
                        lexical_enabled=lexical_enabled[query_index],
                        candidates=tuple(candidate_traces),
                    )
                )
            results.append(
                HybridRetrievalCaseResult(
                    case_id=case.id,
                    queries=queries,
                    baseline_retrieval=baseline_status,
                    hybrid_retrieval=hybrid_status,
                    query_traces=tuple(query_traces),
                    selected_primary_chunk_ids=tuple(
                        item.chunk_id
                        for item in hybrid.primary_evidence
                        if item.chunk_id in eligible_primary_ids
                    ),
                    expanded_evidence_chunk_ids=eligible_expanded_ids,
                )
            )

    improvements = tuple(
        item.case_id
        for item in results
        if item.baseline_retrieval == "failed" and item.hybrid_retrieval == "passed"
    )
    regressions = tuple(
        item.case_id
        for item in results
        if item.baseline_retrieval == "passed" and item.hybrid_retrieval == "failed"
    )
    return HybridRetrievalEvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=dataset.document_version_ids,
        metadata=HybridRetrievalRunMetadata(
            code_commit=code_commit,
            dataset_sha256=dataset_sha256.lower(),
            query_plan_sha256=query_plan_sha256.lower(),
            retrieval_version=settings.retrieval_config_version,
            planner_version=query_plan.planner_version,
            parser_version=provenance.parser_version,
            chunking_version=provenance.chunking_version,
            embedding_provider=provenance.embedding_provider,
            embedding_model=provenance.embedding_model,
            embedding_revision=provenance.embedding_revision,
            embedding_version=provenance.embedding_version,
            embedding_device=settings.embedding_device,
            reranker_provider=settings.reranker_provider,
            reranker_model=settings.reranker_model,
            reranker_revision=settings.reranker_model_revision,
            reranker_weight_sha256=settings.reranker_model_weight_sha256.lower(),
            reranker_version=settings.reranker_config_version,
            reranker_device=settings.reranker_device,
            lexical_version=_LEXICAL_VERSION,
            phrase_weight=_LEXICAL_PHRASE_WEIGHT,
            channel_rrf_rank_constant=settings.retrieval_rrf_rank_constant,
            channel_candidate_limit=pool_limit,
            retrieval_top_k=settings.retrieval_top_k,
            retrieval_minimum_score=settings.retrieval_minimum_score,
            retrieval_page_neighbor_count=settings.retrieval_page_neighbor_count,
        ),
        cases=tuple(results),
        summary=HybridRetrievalSummary(
            baseline_passed=sum(item.baseline_retrieval == "passed" for item in results),
            hybrid_passed=sum(item.hybrid_retrieval == "passed" for item in results),
            not_applicable=sum(
                item.hybrid_retrieval == "not_applicable" for item in results
            ),
            improvements=improvements,
            regressions=regressions,
        ),
    )


async def _run_precomputed_retrieval(
    knowledge_base_id: UUID,
    queries: Sequence[str],
    candidates: Sequence[Sequence[FusedChannelCandidate[RetrievedEvidence]]],
    *,
    dense_repository: PgVectorRetrievalRepository,
    reranker: BgeCrossEncoderReranker,
    settings: Settings,
) -> RetrievalResult:
    service = RetrievalService(
        repository=_PrecomputedRepository(candidates, delegate=dense_repository),
        embedding_provider=_MarkerEmbeddingProvider(),
        question_planner=_UnusedPlanner(),
        reranker=reranker,
        top_k=settings.retrieval_top_k,
        page_neighbor_count=settings.retrieval_page_neighbor_count,
        rrf_rank_constant=settings.retrieval_rrf_rank_constant,
    )
    return await service.search(
        knowledge_base_id=knowledge_base_id,
        queries=queries,
    )


def _eligible_observed(
    result: RetrievalResult,
    minimum_score: float,
) -> tuple[ObservedEvidence, ...]:
    return tuple(
        ObservedEvidence(
            document_version_id=item.document_version_id,
            page_number=item.page_number,
            text=item.text,
        )
        for item in result.evidence
        if item.score >= minimum_score
    )
