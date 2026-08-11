from collections.abc import AsyncIterator, Sequence
from uuid import UUID, uuid4, uuid5

from sourcetrace.modules.retrieval.service import (
    FusedRetrievalCandidate,
    QueryRetrievalResult,
    RankedRetrievalCandidate,
    RetrievalPlan,
    RetrievalResult,
    RetrievedEvidence,
)
from sourcetrace.rag.ports import EvidenceDecision, RetrievalCandidate
from sourcetrace.rag.workflow import AnswerWorkflow, WorkflowRequest, WorkflowTrace
from tests.helpers import PreserveOrderReranker


def _evidence() -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_name="paper.pdf",
        storage_key="knowledge/paper.pdf",
        page_number=2,
        text="Bounded retrieval keeps the run replayable.",
        score=0.9,
    )


def _result(queries: Sequence[str], evidence: RetrievedEvidence) -> RetrievalResult:
    query_results = tuple(
        QueryRetrievalResult(
            query=query,
            candidates=(
                RankedRetrievalCandidate(
                    rank=1,
                    evidence=evidence,
                    dense_rank=None,
                    lexical_rank=13,
                    dense_score=None,
                    lexical_score=0.75,
                    channel_fused_score=1 / 73,
                    reranker_score=1.0,
                    reranked_rank=1,
                    selected_for_query_coverage=True,
                ),
            ),
        )
        for query in queries
    )
    return RetrievalResult(
        evidence=(evidence,),
        primary_evidence=(evidence,),
        query_results=query_results,
        fused_candidates=(
            FusedRetrievalCandidate(
                evidence=evidence,
                fused_score=sum(1 / 61 for _query in queries),
                best_raw_score=evidence.score,
                reranker_score=1.0,
                reranked_rank=1,
                selected_as_primary=True,
            ),
        ),
        rrf_rank_constant=60,
        reranker_identity=PreserveOrderReranker.identity,
    )


class PlannedRetrieval:
    def __init__(self, plan: RetrievalPlan, evidence: RetrievedEvidence) -> None:
        self.plan = plan
        self.evidence = evidence
        self.searches: list[tuple[str, ...]] = []

    async def resolve_plan(self, **kwargs: object) -> RetrievalPlan:
        return self.plan

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[str],
    ) -> RetrievalResult:
        executed = tuple(queries)
        self.searches.append(executed)
        return _result(executed, self.evidence)


class SequentialAssessor:
    def __init__(self, *decisions: EvidenceDecision) -> None:
        self.decisions = list(decisions)
        self.supplemental_query_limits: list[int] = []
        self.queries: list[tuple[str, ...]] = []

    async def assess(self, **kwargs: object) -> EvidenceDecision:
        self.supplemental_query_limits.append(int(kwargs["supplemental_query_limit"]))
        self.queries.append(tuple(kwargs["queries"]))  # type: ignore[arg-type]
        return self.decisions.pop(0)


class UnusedGenerator:
    def stream_answer(self, **kwargs: object) -> AsyncIterator[str]:
        raise AssertionError("generation must not start")


class StaticGenerator:
    def __init__(self, answer: str) -> None:
        self.answer = answer

    async def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        yield self.answer


class UnusedRepairer:
    async def repair(self, **kwargs: object) -> str:
        raise AssertionError("repair must not start")


class ActiveControl:
    def __init__(self) -> None:
        self.recorded_query: str | None = None
        self.traces: list[WorkflowTrace] = []

    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool:
        self.recorded_query = query
        return True

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool:
        self.traces.append(trace)
        return True

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return False


async def test_initial_plan_uses_the_shared_extra_query_budget() -> None:
    evidence = _evidence()
    retrieval = PlannedRetrieval(
        RetrievalPlan(
            version="bounded-multi-query-v1",
            queries=("original", "expansion one", "expansion two"),
        ),
        evidence,
    )
    assessor = SequentialAssessor(EvidenceDecision(False, (), ()))
    control = ActiveControl()
    workflow = AnswerWorkflow(
        retrieval=retrieval,
        assessor=assessor,
        generator=UnusedGenerator(),
        citation_repairer=UnusedRepairer(),
        run_control=control,
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event async for event in workflow.run(WorkflowRequest(uuid4(), uuid4(), "original", ()))
    ]

    assert retrieval.searches == [("original", "expansion one", "expansion two")]
    assert assessor.supplemental_query_limits == [0]
    assert assessor.queries == [("original", "expansion one", "expansion two")]
    assert events[-1].type == "refused"
    assert control.traces[-1].retrieval_plan_version == "bounded-multi-query-v1"
    assert len(control.traces[-1].retrieval_rounds) == 1
    candidate_trace = control.traces[-1].to_payload()["retrieval_rounds"][0][
        "query_results"
    ][0]["candidates"][0]
    assert candidate_trace["dense_rank"] is None
    assert candidate_trace["lexical_rank"] == 13
    assert candidate_trace["lexical_score"] == 0.75
    assert candidate_trace["channel_fused_score"] == 1 / 73


async def test_duplicate_supplemental_query_is_not_executed() -> None:
    evidence = _evidence()
    retrieval = PlannedRetrieval(
        RetrievalPlan(
            version="bounded-multi-query-v1",
            queries=("original question", "standalone expansion"),
        ),
        evidence,
    )
    assessor = SequentialAssessor(EvidenceDecision(False, (), ("  ORIGINAL   QUESTION  ",)))
    workflow = AnswerWorkflow(
        retrieval=retrieval,
        assessor=assessor,
        generator=UnusedGenerator(),
        citation_repairer=UnusedRepairer(),
        run_control=ActiveControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(WorkflowRequest(uuid4(), uuid4(), "original question", ()))
    ]

    assert retrieval.searches == [("original question", "standalone expansion")]
    assert events[-1].type == "refused"


async def test_one_unique_supplemental_query_creates_one_more_fusion_round() -> None:
    run_id = uuid4()
    evidence = _evidence()
    retrieval = PlannedRetrieval(
        RetrievalPlan(
            version="bounded-multi-query-v1",
            queries=("original", "initial expansion"),
        ),
        evidence,
    )
    assessor = SequentialAssessor(
        EvidenceDecision(False, (), ("supplemental expansion",)),
        EvidenceDecision(True, (str(evidence.chunk_id),), ()),
    )
    citation_id = uuid5(run_id, str(evidence.chunk_id))
    control = ActiveControl()
    workflow = AnswerWorkflow(
        retrieval=retrieval,
        assessor=assessor,
        generator=StaticGenerator(f"Supported answer [{citation_id}]"),
        citation_repairer=UnusedRepairer(),
        run_control=control,
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event async for event in workflow.run(WorkflowRequest(run_id, uuid4(), "original", ()))
    ]

    assert retrieval.searches == [
        ("original", "initial expansion"),
        ("original", "initial expansion", "supplemental expansion"),
    ]
    assert assessor.supplemental_query_limits == [1, 0]
    assert events[-1].type == "answered"
    final_trace = control.traces[-1]
    assert final_trace.retrieval_queries == (
        "original",
        "initial expansion",
        "supplemental expansion",
    )
    assert len(final_trace.retrieval_rounds) == 2
    assert final_trace.supplemental_retrieval_attempts == 1


async def test_two_missing_evidence_slots_share_one_supplemental_round() -> None:
    run_id = uuid4()
    evidence = _evidence()
    retrieval = PlannedRetrieval(
        RetrievalPlan(
            version="bounded-multi-query-v1",
            queries=("original",),
        ),
        evidence,
    )
    assessor = SequentialAssessor(
        EvidenceDecision(
            sufficient=False,
            selected_chunk_ids=(),
            supplemental_queries=("ReAct environment actions", "Self-RAG critique tokens"),
        ),
        EvidenceDecision(
            sufficient=True,
            selected_chunk_ids=(str(evidence.chunk_id),),
            supplemental_queries=(),
        ),
    )
    citation_id = uuid5(run_id, str(evidence.chunk_id))
    workflow = AnswerWorkflow(
        retrieval=retrieval,
        assessor=assessor,
        generator=StaticGenerator(f"Supported answer [{citation_id}]"),
        citation_repairer=UnusedRepairer(),
        run_control=ActiveControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event async for event in workflow.run(WorkflowRequest(run_id, uuid4(), "original", ()))
    ]

    assert retrieval.searches == [
        ("original",),
        ("original", "ReAct environment actions", "Self-RAG critique tokens"),
    ]
    assert assessor.supplemental_query_limits == [2, 0]
    assert events[-1].type == "answered"
