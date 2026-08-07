from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import UUID

from sourcetrace.rag.ports import EmbeddingProvider, QuestionPlanner

_PAGE_DIVERSITY_POOL_MULTIPLIER = 4
_MAX_CANDIDATE_POOL_SIZE = 100
_MAX_PRIMARY_CANDIDATES = 8
_MAX_ADDITIONAL_QUERIES = 2
_RETRIEVAL_PLAN_VERSION = "bounded-multi-query-v1"


@dataclass(frozen=True, slots=True)
class RetrievedEvidence:
    chunk_id: UUID
    document_id: UUID
    document_version_id: UUID
    document_name: str
    storage_key: str
    page_number: int
    text: str
    score: float
    page_chunk_index: int = 0


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    version: str
    queries: tuple[str, ...]

    def with_additional_query(self, query: str) -> RetrievalPlan | None:
        candidate = query.strip()
        normalized = _normalize_query(candidate)
        if (
            not normalized
            or normalized in {_normalize_query(item) for item in self.queries}
            or len(self.queries) >= _MAX_ADDITIONAL_QUERIES + 1
        ):
            return None
        return RetrievalPlan(
            version=self.version,
            queries=(*self.queries, candidate),
        )


@dataclass(frozen=True, slots=True)
class RankedRetrievalCandidate:
    rank: int
    evidence: RetrievedEvidence


@dataclass(frozen=True, slots=True)
class QueryRetrievalResult:
    query: str
    candidates: tuple[RankedRetrievalCandidate, ...]


@dataclass(frozen=True, slots=True)
class FusedRetrievalCandidate:
    evidence: RetrievedEvidence
    fused_score: float
    best_raw_score: float
    selected_as_primary: bool = False


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    evidence: tuple[RetrievedEvidence, ...]
    primary_evidence: tuple[RetrievedEvidence, ...]
    query_results: tuple[QueryRetrievalResult, ...]
    fused_candidates: tuple[FusedRetrievalCandidate, ...]
    rrf_rank_constant: int


class RetrievalRepositoryPort(Protocol):
    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        limit: int,
    ) -> list[RetrievedEvidence]: ...

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]: ...


class RetrievalService:
    def __init__(
        self,
        *,
        repository: RetrievalRepositoryPort,
        embedding_provider: EmbeddingProvider,
        question_planner: QuestionPlanner,
        top_k: int,
        page_neighbor_count: int = 0,
        rrf_rank_constant: int = 60,
    ) -> None:
        if top_k <= 0:
            raise ValueError("retrieval top_k must be positive")
        if top_k > _MAX_PRIMARY_CANDIDATES:
            raise ValueError("retrieval top_k must be at most 8")
        if page_neighbor_count < 0:
            raise ValueError("retrieval page neighbor count must not be negative")
        if rrf_rank_constant <= 0:
            raise ValueError("RRF rank constant must be positive")
        self._repository = repository
        self._embedding_provider = embedding_provider
        self._question_planner = question_planner
        self._top_k = top_k
        self._page_neighbor_count = page_neighbor_count
        self._rrf_rank_constant = rrf_rank_constant

    async def resolve_plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> RetrievalPlan:
        original_query = question.strip()
        if not original_query:
            raise ValueError("retrieval question must not be blank")
        proposal = await self._question_planner.plan(
            question=question,
            recent_questions=recent_questions,
        )
        queries = [original_query]
        normalized = {_normalize_query(original_query)}
        for additional_query in proposal.additional_queries:
            candidate = additional_query.strip()
            normalized_candidate = _normalize_query(candidate)
            if not normalized_candidate or normalized_candidate in normalized:
                continue
            queries.append(candidate)
            normalized.add(normalized_candidate)
            if len(queries) == _MAX_ADDITIONAL_QUERIES + 1:
                break
        return RetrievalPlan(version=_RETRIEVAL_PLAN_VERSION, queries=tuple(queries))

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[str],
    ) -> RetrievalResult:
        unique_queries = _unique_queries(queries)
        if not unique_queries:
            raise ValueError("at least one retrieval query is required")
        if len(unique_queries) > _MAX_ADDITIONAL_QUERIES + 1:
            raise ValueError("at most three unique retrieval queries are allowed")
        embeddings = await self._embedding_provider.embed(unique_queries)
        if len(embeddings) != len(unique_queries):
            raise ValueError("query embedding provider returned an invalid result")
        pool_limit = min(
            self._top_k * _PAGE_DIVERSITY_POOL_MULTIPLIER,
            _MAX_CANDIDATE_POOL_SIZE,
        )
        query_results: list[QueryRetrievalResult] = []
        for query, embedding in zip(unique_queries, embeddings, strict=True):
            candidates = await self._repository.search(
                knowledge_base_id,
                embedding,
                limit=pool_limit,
            )
            query_results.append(
                QueryRetrievalResult(
                    query=query,
                    candidates=tuple(
                        RankedRetrievalCandidate(rank=rank, evidence=evidence)
                        for rank, evidence in enumerate(candidates, start=1)
                    ),
                )
            )
        fused = _fuse_ranked_candidates(
            query_results,
            rank_constant=self._rrf_rank_constant,
        )
        primary = select_page_diverse(
            [item.evidence for item in fused],
            limit=self._top_k,
        )
        selected_ids = {item.chunk_id for item in primary}
        fused = [
            replace(item, selected_as_primary=item.evidence.chunk_id in selected_ids)
            for item in fused
        ]
        all_evidence = list(primary)
        if primary and self._page_neighbor_count > 0:
            neighbors = await self._repository.expand_page_neighbors(
                knowledge_base_id,
                primary,
                neighbor_count=self._page_neighbor_count,
            )
            known_ids = set(selected_ids)
            for neighbor in neighbors:
                if neighbor.chunk_id not in known_ids:
                    all_evidence.append(neighbor)
                    known_ids.add(neighbor.chunk_id)
        return RetrievalResult(
            evidence=tuple(all_evidence),
            primary_evidence=tuple(primary),
            query_results=tuple(query_results),
            fused_candidates=tuple(fused),
            rrf_rank_constant=self._rrf_rank_constant,
        )


def _normalize_query(query: str) -> str:
    return " ".join(query.split()).casefold()


def _unique_queries(queries: Sequence[str]) -> tuple[str, ...]:
    unique: list[str] = []
    normalized: set[str] = set()
    for query in queries:
        candidate = query.strip()
        key = _normalize_query(candidate)
        if not key or key in normalized:
            continue
        unique.append(candidate)
        normalized.add(key)
    return tuple(unique)


def _fuse_ranked_candidates(
    query_results: Sequence[QueryRetrievalResult],
    *,
    rank_constant: int,
) -> list[FusedRetrievalCandidate]:
    fused_scores: dict[UUID, float] = {}
    best_evidence: dict[UUID, RetrievedEvidence] = {}
    for result in query_results:
        seen_in_query: set[UUID] = set()
        for candidate in result.candidates:
            chunk_id = candidate.evidence.chunk_id
            if chunk_id in seen_in_query:
                continue
            seen_in_query.add(chunk_id)
            fused_scores[chunk_id] = fused_scores.get(chunk_id, 0.0) + (
                1.0 / (rank_constant + candidate.rank)
            )
            current = best_evidence.get(chunk_id)
            if current is None or candidate.evidence.score > current.score:
                best_evidence[chunk_id] = candidate.evidence
    fused = [
        FusedRetrievalCandidate(
            evidence=evidence,
            fused_score=fused_scores[chunk_id],
            best_raw_score=evidence.score,
        )
        for chunk_id, evidence in best_evidence.items()
    ]
    return sorted(
        fused,
        key=lambda item: (
            -item.fused_score,
            -item.best_raw_score,
            str(item.evidence.chunk_id),
        ),
    )


def select_page_diverse(
    evidence: Sequence[RetrievedEvidence],
    *,
    limit: int,
) -> list[RetrievedEvidence]:
    selected: list[RetrievedEvidence] = []
    deferred: list[RetrievedEvidence] = []
    selected_pages: set[tuple[UUID, int]] = set()

    for item in evidence:
        page = (item.document_version_id, item.page_number)
        if page in selected_pages:
            deferred.append(item)
            continue
        selected.append(item)
        selected_pages.add(page)
        if len(selected) == limit:
            return selected

    selected.extend(deferred[: limit - len(selected)])
    return selected
