from collections.abc import Sequence
from uuid import UUID

import pytest

from sourcetrace.modules.retrieval.hybrid import FusedChannelCandidate
from sourcetrace.modules.retrieval.service import (
    RetrievalPlan,
    RetrievalService,
    RetrievedEvidence,
)
from sourcetrace.rag.ports import RerankerIdentity, RetrievalPlanProposal
from tests.helpers import PreserveOrderReranker


class StaticPlanner:
    def __init__(self, *additional_queries: str) -> None:
        self.additional_queries = additional_queries
        self.calls: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = []

    async def plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
        document_titles: Sequence[str],
    ) -> RetrievalPlanProposal:
        self.calls.append((question, tuple(recent_questions), tuple(document_titles)))
        return RetrievalPlanProposal(additional_queries=self.additional_queries)


class RecordingEmbeddingProvider:
    def __init__(self, embeddings: Sequence[Sequence[float]]) -> None:
        self.embeddings = embeddings
        self.calls: list[tuple[str, ...]] = []

    async def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        self.calls.append(tuple(texts))
        return self.embeddings


class RecordingReranker:
    identity = RerankerIdentity(
        provider="test",
        model="recording-reranker",
        revision="v1",
        config_version="recording-reranker-v1",
    )

    def __init__(self, scores: Sequence[float]) -> None:
        self.scores = scores
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def score(
        self,
        *,
        question: str,
        passages: Sequence[str],
    ) -> Sequence[float]:
        self.calls.append((question, tuple(passages)))
        return self.scores


class QuerySpecificReranker:
    identity = RerankerIdentity(
        provider="test",
        model="query-specific-reranker",
        revision="v1",
        config_version="query-specific-reranker-v1",
    )

    def __init__(self, scores: dict[str, dict[str, float]]) -> None:
        self.scores = scores
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    async def score(
        self,
        *,
        question: str,
        passages: Sequence[str],
    ) -> Sequence[float]:
        self.calls.append((question, tuple(passages)))
        return tuple(self.scores[question][passage] for passage in passages)


class RankedListRepository:
    def __init__(
        self,
        ranked_lists: dict[tuple[float, ...], list[RetrievedEvidence]],
        *,
        document_titles: Sequence[str] = (),
    ) -> None:
        self.ranked_lists = ranked_lists
        self.document_titles = tuple(document_titles)
        self.title_calls: list[tuple[UUID, int]] = []
        self.search_calls: list[tuple[UUID, tuple[float, ...], int]] = []

    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        query: str,
        limit: int,
    ) -> list[FusedChannelCandidate[RetrievedEvidence]]:
        key = tuple(query_embedding)
        self.search_calls.append((knowledge_base_id, key, limit))
        return [
            FusedChannelCandidate(
                evidence=evidence,
                fused_score=1 / (60 + rank),
                channel_fused_rank=rank,
                dense_rank=rank,
                lexical_rank=None,
                dense_score=evidence.score,
                lexical_score=None,
            )
            for rank, evidence in enumerate(self.ranked_lists[key], start=1)
        ]

    async def list_searchable_document_titles(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        self.title_calls.append((knowledge_base_id, limit))
        return self.document_titles[:limit]

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]:
        return []


class HybridCandidateRepository:
    def __init__(
        self,
        candidates: Sequence[FusedChannelCandidate[RetrievedEvidence]],
    ) -> None:
        self.candidates = list(candidates)
        self.search_queries: list[str] = []

    async def search(
        self,
        knowledge_base_id: UUID,
        query_embedding: Sequence[float],
        *,
        query: str,
        limit: int,
    ) -> list[FusedChannelCandidate[RetrievedEvidence]]:
        self.search_queries.append(query)
        return self.candidates[:limit]

    async def list_searchable_document_titles(
        self,
        knowledge_base_id: UUID,
        *,
        limit: int,
    ) -> tuple[str, ...]:
        return ()

    async def expand_page_neighbors(
        self,
        knowledge_base_id: UUID,
        evidence: Sequence[RetrievedEvidence],
        *,
        neighbor_count: int,
    ) -> list[RetrievedEvidence]:
        return []


def _evidence(
    chunk_id: str,
    *,
    score: float,
    page_number: int,
    document_version_id: str = "10000000-0000-0000-0000-000000000001",
) -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=UUID(chunk_id),
        document_id=UUID("20000000-0000-0000-0000-000000000001"),
        document_version_id=UUID(document_version_id),
        document_name="paper.pdf",
        storage_key="knowledge/paper.pdf",
        page_number=page_number,
        text=f"Evidence {chunk_id}",
        score=score,
    )


async def test_plan_keeps_original_question_and_skips_normalized_duplicates() -> None:
    knowledge_base_id = UUID("30000000-0000-0000-0000-000000000001")
    planner = StaticPlanner(
        "  What does ReAct combine?  ",
        "ReAct reasoning and acting interaction",
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {},
            document_titles=("ReAct.pdf", "Self-RAG.pdf"),
        ),
        embedding_provider=RecordingEmbeddingProvider([]),
        question_planner=planner,
        reranker=PreserveOrderReranker(),
        top_k=8,
    )

    plan = await service.resolve_plan(
        knowledge_base_id=knowledge_base_id,
        question="What does ReAct combine?",
        recent_questions=("What is ReAct?",),
    )

    assert plan == RetrievalPlan(
        version="two-stage-evidence-slots-v6",
        queries=(
            "What does ReAct combine?",
            "ReAct reasoning and acting interaction",
        ),
    )
    assert planner.calls == [
        (
            "What does ReAct combine?",
            ("What is ReAct?",),
            ("ReAct.pdf", "Self-RAG.pdf"),
        ),
    ]


async def test_plan_keeps_at_most_two_unique_evidence_slot_queries() -> None:
    planner = StaticPlanner(
        "  Compare RAG, ReAct, and Self-RAG components  ",
        "ReAct task-specific environment actions",
        "Self-RAG three types of Critique tokens",
        "forbidden third slot query",
    )
    service = RetrievalService(
        repository=RankedListRepository({}),
        embedding_provider=RecordingEmbeddingProvider([]),
        question_planner=planner,
        reranker=PreserveOrderReranker(),
        top_k=8,
    )

    plan = await service.resolve_plan(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        question="Compare RAG, ReAct, and Self-RAG components",
        recent_questions=(),
    )

    assert plan == RetrievalPlan(
        version="two-stage-evidence-slots-v6",
        queries=(
            "Compare RAG, ReAct, and Self-RAG components",
            "ReAct task-specific environment actions",
            "Self-RAG three types of Critique tokens",
        ),
    )


async def test_search_preserves_hybrid_channel_diagnostics_through_reranking() -> None:
    evidence = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.42,
        page_number=11,
    )
    repository = HybridCandidateRepository(
        (
            FusedChannelCandidate(
                evidence=evidence,
                fused_score=1 / 73,
                channel_fused_rank=1,
                dense_rank=None,
                lexical_rank=13,
                dense_score=None,
                lexical_score=0.75,
            ),
        )
    )
    service = RetrievalService(
        repository=repository,
        embedding_provider=RecordingEmbeddingProvider(((1.0,),)),
        question_planner=StaticPlanner(),
        reranker=RecordingReranker((0.9,)),
        top_k=1,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("bounded lexical query",),
    )

    candidate = result.query_results[0].candidates[0]
    assert repository.search_queries == ["bounded lexical query"]
    assert candidate.rank == 1
    assert candidate.dense_rank is None
    assert candidate.lexical_rank == 13
    assert candidate.lexical_score == pytest.approx(0.75)
    assert candidate.channel_fused_score == pytest.approx(1 / 73)
    assert candidate.reranker_score == pytest.approx(0.9)
    assert result.primary_evidence == (evidence,)


async def test_multi_query_search_uses_rrf_before_page_diversity() -> None:
    knowledge_base_id = UUID("30000000-0000-0000-0000-000000000001")
    first = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.95,
        page_number=1,
    )
    repeated = _evidence(
        "00000000-0000-0000-0000-000000000002",
        score=0.80,
        page_number=1,
    )
    other_page = _evidence(
        "00000000-0000-0000-0000-000000000003",
        score=0.70,
        page_number=2,
    )
    repository = RankedListRepository(
        {
            (1.0, 0.0): [first, repeated],
            (0.0, 1.0): [repeated, other_page],
        }
    )
    embeddings = RecordingEmbeddingProvider(((1.0, 0.0), (0.0, 1.0)))
    service = RetrievalService(
        repository=repository,
        embedding_provider=embeddings,
        question_planner=StaticPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=2,
        rrf_rank_constant=60,
    )

    result = await service.search(
        knowledge_base_id=knowledge_base_id,
        queries=("original query", "expanded query"),
    )

    assert embeddings.calls == [("original query", "expanded query")]
    assert [call[1] for call in repository.search_calls] == [
        (1.0, 0.0),
        (0.0, 1.0),
    ]
    assert [item.chunk_id for item in result.primary_evidence] == [
        repeated.chunk_id,
        other_page.chunk_id,
    ]
    assert result.primary_evidence[0].score == 0.80
    assert result.query_results[0].candidates[1].rank == 2
    assert result.query_results[1].candidates[0].rank == 1
    fused = {item.evidence.chunk_id: item for item in result.fused_candidates}
    assert fused[repeated.chunk_id].fused_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[repeated.chunk_id].best_raw_score == 0.80
    assert fused[first.chunk_id].selected_as_primary is False


async def test_rrf_ties_use_best_raw_score_then_chunk_uuid() -> None:
    lower_score = _evidence(
        "00000000-0000-0000-0000-000000000003",
        score=0.80,
        page_number=1,
    )
    higher_uuid = _evidence(
        "00000000-0000-0000-0000-000000000002",
        score=0.90,
        page_number=2,
    )
    lower_uuid = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.90,
        page_number=3,
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {
                (1.0,): [lower_score],
                (2.0,): [higher_uuid],
                (3.0,): [lower_uuid],
            }
        ),
        embedding_provider=RecordingEmbeddingProvider(((1.0,), (2.0,), (3.0,))),
        question_planner=StaticPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=3,
        rrf_rank_constant=60,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("one", "two", "three"),
    )

    assert [item.chunk_id for item in result.primary_evidence] == [
        lower_uuid.chunk_id,
        higher_uuid.chunk_id,
        lower_score.chunk_id,
    ]


async def test_reranker_scores_fused_union_before_page_diversity() -> None:
    first = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.95,
        page_number=1,
    )
    same_page = _evidence(
        "00000000-0000-0000-0000-000000000002",
        score=0.90,
        page_number=1,
    )
    other_page = _evidence(
        "00000000-0000-0000-0000-000000000003",
        score=0.80,
        page_number=2,
    )
    reranker = RecordingReranker((0.1, 0.8, 0.9))
    service = RetrievalService(
        repository=RankedListRepository({(1.0,): [first, same_page, other_page]}),
        embedding_provider=RecordingEmbeddingProvider(((1.0,),)),
        question_planner=StaticPlanner(),
        reranker=reranker,
        top_k=2,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("original question",),
    )

    assert reranker.calls == [
        (
            "original question",
            (first.text, same_page.text, other_page.text),
        )
    ]
    first_query_candidates = result.query_results[0].candidates
    assert [item.reranker_score for item in first_query_candidates] == [0.1, 0.8, 0.9]
    assert [item.reranked_rank for item in first_query_candidates] == [3, 2, 1]
    assert [item.selected_for_query_coverage for item in first_query_candidates] == [
        False,
        True,
        True,
    ]
    assert [item.evidence.chunk_id for item in result.fused_candidates] == [
        other_page.chunk_id,
        same_page.chunk_id,
        first.chunk_id,
    ]
    assert [item.chunk_id for item in result.primary_evidence] == [
        other_page.chunk_id,
        same_page.chunk_id,
    ]
    assert [item.reranked_rank for item in result.fused_candidates] == [1, 2, 3]
    assert result.reranker_identity == reranker.identity


async def test_multi_query_reranking_preserves_evidence_for_each_query() -> None:
    first_target = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.95,
        page_number=1,
    )
    first_distractor = _evidence(
        "00000000-0000-0000-0000-000000000002",
        score=0.90,
        page_number=2,
    )
    second_target = _evidence(
        "00000000-0000-0000-0000-000000000003",
        score=0.85,
        page_number=3,
    )
    second_distractor = _evidence(
        "00000000-0000-0000-0000-000000000004",
        score=0.80,
        page_number=4,
    )
    reranker = QuerySpecificReranker(
        {
            "first aspect": {
                first_target.text: 0.9,
                first_distractor.text: 0.8,
                second_target.text: 0.01,
                second_distractor.text: 0.02,
            },
            "second aspect": {
                second_target.text: 0.9,
                second_distractor.text: 0.8,
            },
        }
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {
                (1.0,): [first_target, first_distractor],
                (2.0,): [second_target, second_distractor],
            }
        ),
        embedding_provider=RecordingEmbeddingProvider(((1.0,), (2.0,))),
        question_planner=StaticPlanner(),
        reranker=reranker,
        top_k=2,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("first aspect", "second aspect"),
    )

    assert reranker.calls == [
        ("first aspect", (first_target.text, first_distractor.text)),
        ("second aspect", (second_target.text, second_distractor.text)),
    ]
    assert [item.chunk_id for item in result.primary_evidence] == [
        first_target.chunk_id,
        second_target.chunk_id,
    ]


async def test_speculative_query_coverage_cannot_displace_original_evidence() -> None:
    original_first = _evidence(
        "00000000-0000-0000-0000-000000000001",
        score=0.95,
        page_number=1,
    )
    original_second = _evidence(
        "00000000-0000-0000-0000-000000000002",
        score=0.90,
        page_number=2,
    )
    expansion_one_first = _evidence(
        "00000000-0000-0000-0000-000000000003",
        score=0.85,
        page_number=3,
    )
    expansion_one_second = _evidence(
        "00000000-0000-0000-0000-000000000004",
        score=0.80,
        page_number=4,
    )
    expansion_two_first = _evidence(
        "00000000-0000-0000-0000-000000000005",
        score=0.75,
        page_number=5,
    )
    expansion_two_second = _evidence(
        "00000000-0000-0000-0000-000000000006",
        score=0.70,
        page_number=6,
    )
    reranker = QuerySpecificReranker(
        {
            "original": {
                original_first.text: 0.7,
                original_second.text: 0.6,
            },
            "speculative one": {
                expansion_one_first.text: 0.99,
                expansion_one_second.text: 0.98,
            },
            "speculative two": {
                expansion_two_first.text: 0.97,
                expansion_two_second.text: 0.96,
            },
        }
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {
                (1.0,): [original_first, original_second],
                (2.0,): [expansion_one_first, expansion_one_second],
                (3.0,): [expansion_two_first, expansion_two_second],
            }
        ),
        embedding_provider=RecordingEmbeddingProvider(((1.0,), (2.0,), (3.0,))),
        question_planner=StaticPlanner(),
        reranker=reranker,
        top_k=4,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("original", "speculative one", "speculative two"),
    )

    assert {item.chunk_id for item in result.primary_evidence} == {
        original_first.chunk_id,
        original_second.chunk_id,
        expansion_one_first.chunk_id,
        expansion_two_first.chunk_id,
    }


async def test_multi_slot_coverage_preserves_rank_two_evidence_within_top_eight() -> None:
    original = [
        _evidence(
            f"00000000-0000-0000-0000-00000000000{index}",
            score=0.90 - index / 100,
            page_number=index,
        )
        for index in range(1, 5)
    ]
    react_distractor = _evidence(
        "00000000-0000-0000-0000-000000000005",
        score=0.80,
        page_number=5,
    )
    react_target = _evidence(
        "00000000-0000-0000-0000-000000000006",
        score=0.79,
        page_number=6,
    )
    react_tail = _evidence(
        "00000000-0000-0000-0000-000000000007",
        score=0.78,
        page_number=7,
    )
    self_rag_first = _evidence(
        "00000000-0000-0000-0000-000000000008",
        score=0.77,
        page_number=8,
    )
    self_rag_second = _evidence(
        "00000000-0000-0000-0000-000000000009",
        score=0.76,
        page_number=9,
    )
    self_rag_tail = _evidence(
        "00000000-0000-0000-0000-000000000010",
        score=0.75,
        page_number=10,
    )
    reranker = QuerySpecificReranker(
        {
            "original": {item.text: 0.90 - index / 100 for index, item in enumerate(original)},
            "ReAct slot": {
                react_distractor.text: 0.99,
                react_target.text: 0.50,
                react_tail.text: 0.49,
            },
            "Self-RAG slot": {
                self_rag_first.text: 0.98,
                self_rag_second.text: 0.80,
                self_rag_tail.text: 0.70,
            },
        }
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {
                (1.0,): original,
                (2.0,): [react_distractor, react_target, react_tail],
                (3.0,): [self_rag_first, self_rag_second, self_rag_tail],
            }
        ),
        embedding_provider=RecordingEmbeddingProvider(((1.0,), (2.0,), (3.0,))),
        question_planner=StaticPlanner(),
        reranker=reranker,
        top_k=8,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("original", "ReAct slot", "Self-RAG slot"),
    )

    react_candidate = next(
        item
        for item in result.query_results[1].candidates
        if item.evidence.chunk_id == react_target.chunk_id
    )
    assert react_candidate.reranked_rank == 2
    assert react_candidate.selected_for_query_coverage is True
    assert react_target in result.primary_evidence
    assert sum(item.selected_for_query_coverage for item in result.query_results[0].candidates) == 4
    assert sum(item.selected_for_query_coverage for item in result.query_results[1].candidates) == 2
    assert sum(item.selected_for_query_coverage for item in result.query_results[2].candidates) == 2
    assert (
        next(
            item
            for item in result.query_results[2].candidates
            if item.evidence.chunk_id == self_rag_second.chunk_id
        ).selected_for_query_coverage
        is True
    )


async def test_low_top_k_balances_query_coverage_within_the_final_budget() -> None:
    original = [
        _evidence(
            f"00000000-0000-0000-0000-00000000001{index}",
            score=0.90 - index / 100,
            page_number=index,
        )
        for index in range(1, 5)
    ]
    first_slot = _evidence(
        "00000000-0000-0000-0000-000000000015",
        score=0.80,
        page_number=5,
    )
    second_slot = _evidence(
        "00000000-0000-0000-0000-000000000016",
        score=0.79,
        page_number=6,
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {
                (1.0,): original,
                (2.0,): [first_slot],
                (3.0,): [second_slot],
            }
        ),
        embedding_provider=RecordingEmbeddingProvider(((1.0,), (2.0,), (3.0,))),
        question_planner=StaticPlanner(),
        reranker=QuerySpecificReranker(
            {
                "original": {item.text: 0.60 for item in original},
                "first slot": {first_slot.text: 0.99},
                "second slot": {second_slot.text: 0.98},
            }
        ),
        top_k=4,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("original", "first slot", "second slot"),
    )

    assert all(item.selected_for_query_coverage for item in result.query_results[0].candidates[:2])
    assert not any(
        item.selected_for_query_coverage for item in result.query_results[0].candidates[2:]
    )
    assert result.query_results[1].candidates[0].selected_for_query_coverage
    assert result.query_results[2].candidates[0].selected_for_query_coverage
    assert (
        sum(
            item.selected_for_query_coverage
            for query_result in result.query_results
            for item in query_result.candidates
        )
        == 4
    )
    assert {item.chunk_id for item in result.primary_evidence} == {
        original[0].chunk_id,
        original[1].chunk_id,
        first_slot.chunk_id,
        second_slot.chunk_id,
    }


async def test_coverage_stops_in_query_order_when_top_k_is_exhausted() -> None:
    original = _evidence(
        "00000000-0000-0000-0000-000000000021",
        score=0.90,
        page_number=1,
    )
    first_slot = _evidence(
        "00000000-0000-0000-0000-000000000022",
        score=0.80,
        page_number=2,
    )
    second_slot = _evidence(
        "00000000-0000-0000-0000-000000000023",
        score=0.70,
        page_number=3,
    )
    service = RetrievalService(
        repository=RankedListRepository(
            {
                (1.0,): [original],
                (2.0,): [first_slot],
                (3.0,): [second_slot],
            }
        ),
        embedding_provider=RecordingEmbeddingProvider(((1.0,), (2.0,), (3.0,))),
        question_planner=StaticPlanner(),
        reranker=PreserveOrderReranker(),
        top_k=2,
    )

    result = await service.search(
        knowledge_base_id=UUID("30000000-0000-0000-0000-000000000001"),
        queries=("original", "first slot", "second slot"),
    )

    assert result.query_results[0].candidates[0].selected_for_query_coverage
    assert result.query_results[1].candidates[0].selected_for_query_coverage
    assert not result.query_results[2].candidates[0].selected_for_query_coverage
    assert {item.chunk_id for item in result.primary_evidence} == {
        original.chunk_id,
        first_slot.chunk_id,
    }
