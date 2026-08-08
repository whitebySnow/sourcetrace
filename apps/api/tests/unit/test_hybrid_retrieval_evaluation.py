from uuid import UUID

from sourcetrace.evaluation.hybrid_retrieval import resolve_query_plans
from sourcetrace.evaluation.models import HybridQueryPlanCase, HybridQueryPlanFixture
from sourcetrace.modules.retrieval.hybrid import (
    RankedChannelCandidate,
    build_lexical_search_query,
    fuse_ranked_channels,
)
from sourcetrace.modules.retrieval.service import RetrievedEvidence


def _evidence(chunk_id: str, *, cosine: float) -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=UUID(chunk_id),
        document_id=UUID("10000000-0000-0000-0000-000000000001"),
        document_version_id=UUID("20000000-0000-0000-0000-000000000001"),
        document_name="paper.pdf",
        storage_key="knowledge/paper.pdf",
        page_number=1,
        text=f"Evidence {chunk_id}",
        score=cosine,
    )


def test_channel_fusion_rewards_cross_channel_candidates_without_expanding_budget() -> None:
    shared = _evidence(
        "00000000-0000-0000-0000-000000000001",
        cosine=0.70,
    )
    dense_only = _evidence(
        "00000000-0000-0000-0000-000000000002",
        cosine=0.90,
    )
    lexical_only = _evidence(
        "00000000-0000-0000-0000-000000000003",
        cosine=0.60,
    )

    fused = fuse_ranked_channels(
        (
            RankedChannelCandidate(
                channel="dense",
                rank=1,
                channel_score=0.90,
                evidence=dense_only,
            ),
            RankedChannelCandidate(
                channel="dense",
                rank=2,
                channel_score=0.70,
                evidence=shared,
            ),
            RankedChannelCandidate(
                channel="lexical",
                rank=1,
                channel_score=0.80,
                evidence=shared,
            ),
            RankedChannelCandidate(
                channel="lexical",
                rank=2,
                channel_score=0.75,
                evidence=lexical_only,
            ),
        ),
        rank_constant=60,
        limit=2,
    )

    assert [item.evidence.chunk_id for item in fused] == [
        shared.chunk_id,
        dense_only.chunk_id,
    ]
    assert fused[0].dense_rank == 2
    assert fused[0].lexical_rank == 1
    assert fused[0].fused_score == (1 / 62) + (1 / 61)
    assert len(fused) == 2


def test_channel_fusion_counts_a_chunk_once_per_channel() -> None:
    evidence = _evidence(
        "00000000-0000-0000-0000-000000000001",
        cosine=0.70,
    )

    fused = fuse_ranked_channels(
        (
            RankedChannelCandidate(
                channel="lexical",
                rank=2,
                channel_score=0.80,
                evidence=evidence,
            ),
            RankedChannelCandidate(
                channel="lexical",
                rank=5,
                channel_score=0.60,
                evidence=evidence,
            ),
        ),
        rank_constant=60,
        limit=8,
    )

    assert fused[0].lexical_rank == 2
    assert fused[0].lexical_score == 0.80
    assert fused[0].fused_score == 1 / 62


def test_lexical_query_uses_safe_terms_and_query_derived_phrase_windows() -> None:
    query = build_lexical_search_query(
        "Self-RAG can still produce outputs not fully supported by cited sources"
    )

    assert query is not None
    assert query.disjunction == (
        "Self-RAG OR can OR still OR produce OR outputs OR not OR fully OR supported "
        "OR by OR cited OR sources"
    )
    assert '"fully supported"' in query.phrase_disjunction
    assert '"not fully supported by"' in query.phrase_disjunction
    assert "citations" not in query.disjunction


def test_lexical_query_skips_queries_with_too_few_latin_terms() -> None:
    assert build_lexical_search_query("ReAct 与 Self-RAG 有什么区别?") is None


def test_query_plan_fixture_adds_only_versioned_case_overrides() -> None:
    fixture = HybridQueryPlanFixture(
        dataset_id="dataset",
        dataset_version="1.0.0",
        planner_version="bounded-counterexample-v3",
        cases=(
            HybridQueryPlanCase(
                case_id="ARF-023",
                additional_queries=("counterexample query",),
            ),
        ),
    )

    plans = resolve_query_plans(
        {
            "ARF-001": "original one",
            "ARF-023": "original twenty-three",
        },
        fixture,
        dataset_id="dataset",
        dataset_version="1.0.0",
    )

    assert plans == {
        "ARF-001": ("original one",),
        "ARF-023": ("original twenty-three", "counterexample query"),
    }
