from collections.abc import Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import Protocol

from sourcetrace.modules.retrieval.service import RetrievedEvidence, select_page_diverse


class Reranker(Protocol):
    def score(self, question: str, passages: Sequence[str]) -> Sequence[float]: ...


@dataclass(frozen=True, slots=True)
class RerankableCandidate:
    evidence: RetrievedEvidence
    fused_score: float
    best_raw_score: float
    baseline_rank: int
    baseline_selected: bool


@dataclass(frozen=True, slots=True)
class RerankedCandidate:
    evidence: RetrievedEvidence
    fused_score: float
    best_raw_score: float
    baseline_rank: int
    baseline_selected: bool
    reranker_score: float
    reranked_rank: int
    selected_as_primary: bool = False


@dataclass(frozen=True, slots=True)
class RerankingResult:
    candidates: tuple[RerankedCandidate, ...]
    primary_evidence: tuple[RetrievedEvidence, ...]


def rerank_fixed_candidates(
    question: str,
    candidates: Sequence[RerankableCandidate],
    *,
    reranker: Reranker,
    limit: int,
) -> RerankingResult:
    if not question.strip():
        raise ValueError("reranker question must not be blank")
    if limit <= 0:
        raise ValueError("reranker candidate limit must be positive")
    if not candidates:
        return RerankingResult(candidates=(), primary_evidence=())
    scores = tuple(
        float(item)
        for item in reranker.score(
            question,
            tuple(candidate.evidence.text for candidate in candidates),
        )
    )
    if len(scores) != len(candidates) or not all(isfinite(score) for score in scores):
        raise ValueError("reranker scores must be finite and match the candidate count")

    ranked = sorted(
        zip(candidates, scores, strict=True),
        key=lambda item: (
            -item[1],
            -item[0].fused_score,
            -item[0].best_raw_score,
            str(item[0].evidence.chunk_id),
        ),
    )
    reranked = tuple(
        RerankedCandidate(
            evidence=candidate.evidence,
            fused_score=candidate.fused_score,
            best_raw_score=candidate.best_raw_score,
            baseline_rank=candidate.baseline_rank,
            baseline_selected=candidate.baseline_selected,
            reranker_score=score,
            reranked_rank=rank,
        )
        for rank, (candidate, score) in enumerate(ranked, start=1)
    )
    primary = tuple(
        select_page_diverse(
            [candidate.evidence for candidate in reranked],
            limit=limit,
        )
    )
    selected_ids = {item.chunk_id for item in primary}
    return RerankingResult(
        candidates=tuple(
            replace(
                candidate,
                selected_as_primary=candidate.evidence.chunk_id in selected_ids,
            )
            for candidate in reranked
        ),
        primary_evidence=primary,
    )
