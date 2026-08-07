from collections.abc import Sequence
from uuid import UUID

import pytest

from sourcetrace.evaluation.reranking import (
    RerankableCandidate,
    rerank_fixed_candidates,
)
from sourcetrace.modules.retrieval.service import RetrievedEvidence


class RecordingReranker:
    def __init__(self, scores: Sequence[float]) -> None:
        self._scores = scores
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def score(self, question: str, passages: Sequence[str]) -> Sequence[float]:
        self.calls.append((question, tuple(passages)))
        return self._scores


def _candidate(
    chunk_id: str,
    *,
    page_number: int,
    text: str,
    fused_score: float,
    best_raw_score: float,
    baseline_rank: int,
) -> RerankableCandidate:
    return RerankableCandidate(
        evidence=RetrievedEvidence(
            chunk_id=UUID(chunk_id),
            document_id=UUID("20000000-0000-0000-0000-000000000001"),
            document_version_id=UUID("30000000-0000-0000-0000-000000000001"),
            document_name="paper.pdf",
            storage_key="knowledge/paper.pdf",
            page_number=page_number,
            text=text,
            score=best_raw_score,
        ),
        fused_score=fused_score,
        best_raw_score=best_raw_score,
        baseline_rank=baseline_rank,
        baseline_selected=baseline_rank <= 2,
    )


def test_reranker_scores_fixed_candidates_before_page_diversity() -> None:
    first_page = _candidate(
        "00000000-0000-0000-0000-000000000001",
        page_number=1,
        text="weak passage",
        fused_score=0.9,
        best_raw_score=0.9,
        baseline_rank=1,
    )
    same_page = _candidate(
        "00000000-0000-0000-0000-000000000002",
        page_number=1,
        text="strong passage",
        fused_score=0.8,
        best_raw_score=0.8,
        baseline_rank=2,
    )
    other_page = _candidate(
        "00000000-0000-0000-0000-000000000003",
        page_number=2,
        text="supporting passage",
        fused_score=0.7,
        best_raw_score=0.7,
        baseline_rank=3,
    )
    reranker = RecordingReranker((0.1, 0.9, 0.8))

    result = rerank_fixed_candidates(
        "Which passages answer the question?",
        (first_page, same_page, other_page),
        reranker=reranker,
        limit=2,
    )

    assert reranker.calls == [
        (
            "Which passages answer the question?",
            ("weak passage", "strong passage", "supporting passage"),
        )
    ]
    assert [item.chunk_id for item in result.primary_evidence] == [
        same_page.evidence.chunk_id,
        other_page.evidence.chunk_id,
    ]
    assert [item.reranked_rank for item in result.candidates] == [1, 2, 3]
    assert [item.selected_as_primary for item in result.candidates] == [True, True, False]


def test_reranker_ties_use_fusion_raw_score_then_chunk_uuid() -> None:
    lower_fusion = _candidate(
        "00000000-0000-0000-0000-000000000003",
        page_number=3,
        text="lower fusion",
        fused_score=0.7,
        best_raw_score=0.9,
        baseline_rank=3,
    )
    higher_uuid = _candidate(
        "00000000-0000-0000-0000-000000000002",
        page_number=2,
        text="higher uuid",
        fused_score=0.8,
        best_raw_score=0.9,
        baseline_rank=2,
    )
    lower_uuid = _candidate(
        "00000000-0000-0000-0000-000000000001",
        page_number=1,
        text="lower uuid",
        fused_score=0.8,
        best_raw_score=0.9,
        baseline_rank=1,
    )

    result = rerank_fixed_candidates(
        "question",
        (lower_fusion, higher_uuid, lower_uuid),
        reranker=RecordingReranker((0.5, 0.5, 0.5)),
        limit=3,
    )

    assert [item.evidence.chunk_id for item in result.candidates] == [
        lower_uuid.evidence.chunk_id,
        higher_uuid.evidence.chunk_id,
        lower_fusion.evidence.chunk_id,
    ]


@pytest.mark.parametrize("scores", [(0.1,), (0.1, float("nan"))])
def test_reranker_rejects_invalid_scores(scores: Sequence[float]) -> None:
    candidates = (
        _candidate(
            "00000000-0000-0000-0000-000000000001",
            page_number=1,
            text="one",
            fused_score=0.9,
            best_raw_score=0.9,
            baseline_rank=1,
        ),
        _candidate(
            "00000000-0000-0000-0000-000000000002",
            page_number=2,
            text="two",
            fused_score=0.8,
            best_raw_score=0.8,
            baseline_rank=2,
        ),
    )

    with pytest.raises(ValueError, match="reranker scores"):
        rerank_fixed_candidates(
            "question",
            candidates,
            reranker=RecordingReranker(scores),
            limit=2,
        )
