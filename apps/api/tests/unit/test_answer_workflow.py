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
from sourcetrace.rag.ports import (
    CitationValidationFeedback,
    ClaimSupportDecision,
    ClaimSupportValidationError,
    EvidenceDecision,
    GroundedClaim,
    RetrievalCandidate,
)
from sourcetrace.rag.workflow import AnswerWorkflow, WorkflowRequest, WorkflowTrace
from tests.helpers import PreserveOrderReranker


def _evidence(*, score: float = 0.9) -> RetrievedEvidence:
    return RetrievedEvidence(
        chunk_id=uuid4(),
        document_id=uuid4(),
        document_version_id=uuid4(),
        document_name="agent.pdf",
        storage_key="knowledge/agent.pdf",
        page_number=3,
        text="Agent workflows must remain bounded.",
        score=score,
    )


def _retrieval_result(
    queries: Sequence[str],
    evidence: Sequence[RetrievedEvidence],
) -> RetrievalResult:
    return RetrievalResult(
        evidence=tuple(evidence),
        primary_evidence=tuple(evidence),
        query_results=tuple(
            QueryRetrievalResult(
                query=query,
                candidates=tuple(
                    RankedRetrievalCandidate(rank=rank, evidence=item)
                    for rank, item in enumerate(evidence, start=1)
                ),
            )
            for query in queries
        ),
        fused_candidates=tuple(
            FusedRetrievalCandidate(
                evidence=item,
                fused_score=1 / (60 + rank),
                best_raw_score=item.score,
                reranker_score=float(len(evidence) - rank + 1),
                reranked_rank=rank,
                selected_as_primary=True,
            )
            for rank, item in enumerate(evidence, start=1)
        ),
        rrf_rank_constant=60,
        reranker_identity=PreserveOrderReranker.identity,
    )


class RecordingRetrieval:
    def __init__(self, evidence: Sequence[RetrievedEvidence]) -> None:
        self.evidence = list(evidence)
        self.queries: list[tuple[str, ...]] = []

    async def resolve_plan(
        self,
        *,
        knowledge_base_id: UUID,
        question: str,
        recent_questions: Sequence[str],
    ) -> RetrievalPlan:
        assert list(recent_questions) == []
        return RetrievalPlan("bounded-multi-query-v1", (question,))

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        queries: Sequence[str],
    ) -> RetrievalResult:
        executed = tuple(queries)
        self.queries.append(executed)
        return _retrieval_result(executed, self.evidence)


class SelectingAssessor:
    def __init__(self, selected_chunk_id: UUID) -> None:
        self.selected_chunk_id = selected_chunk_id

    async def assess(self, **kwargs: object) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=True,
            selected_chunk_ids=(str(self.selected_chunk_id),),
            supplemental_queries=(),
        )


class SelectingManyAssessor:
    def __init__(self, selected_chunk_ids: Sequence[UUID]) -> None:
        self.selected_chunk_ids = selected_chunk_ids

    async def assess(self, **kwargs: object) -> EvidenceDecision:
        return EvidenceDecision(
            sufficient=True,
            selected_chunk_ids=tuple(str(item) for item in self.selected_chunk_ids),
            supplemental_queries=(),
        )


class SequentialAssessor:
    def __init__(self, decisions: Sequence[EvidenceDecision]) -> None:
        self.decisions = list(decisions)
        self.supplemental_query_limits: list[int] = []

    async def assess(self, **kwargs: object) -> EvidenceDecision:
        self.supplemental_query_limits.append(int(kwargs["supplemental_query_limit"]))
        return self.decisions.pop(0)


class UnusedAssessor:
    async def assess(self, **kwargs: object) -> EvidenceDecision:
        raise AssertionError("evidence assessment must not run after cancellation")


class RecordingGenerator:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.evidence: Sequence[RetrievalCandidate] = ()

    async def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        self.evidence = evidence
        yield self.answer


class UnusedGenerator:
    def stream_answer(self, **kwargs: object) -> AsyncIterator[str]:
        raise AssertionError("generation must not run without sufficient evidence")


class UnusedCitationRepairer:
    async def repair(self, **kwargs: object) -> str:
        raise AssertionError("citation repair must not run for a valid answer")


class RecordingCitationRepairer:
    def __init__(self, repaired_answer: str) -> None:
        self.repaired_answer = repaired_answer
        self.answers: list[str] = []
        self.validation_feedbacks: list[CitationValidationFeedback] = []

    async def repair(self, **kwargs: object) -> str:
        self.answers.append(str(kwargs["answer"]))
        feedback = kwargs["validation_feedback"]
        assert isinstance(feedback, CitationValidationFeedback)
        self.validation_feedbacks.append(feedback)
        return self.repaired_answer


class RecordingClaimSupportVerifier:
    def __init__(self, decision: ClaimSupportDecision) -> None:
        self.decision = decision
        self.answers: list[str] = []

    async def verify(self, **kwargs: object) -> ClaimSupportDecision:
        self.answers.append(str(kwargs["answer"]))
        return self.decision


class CitationPreservingClaimSupportVerifier:
    async def verify(
        self,
        *,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
        **kwargs: object,
    ) -> ClaimSupportDecision:
        return ClaimSupportDecision(
            claims=(
                GroundedClaim(text=answer, citation_ids=(evidence[0].citation_id,)),
            )
        )


class RejectingClaimSupportVerifier:
    async def verify(self, **kwargs: object) -> ClaimSupportDecision:
        raise ClaimSupportValidationError


class ActiveRunControl:
    def __init__(self) -> None:
        self.retrieval_query: str | None = None
        self.traces: list[WorkflowTrace] = []

    async def record_retrieval_query(self, run_id: UUID, query: str) -> bool:
        self.retrieval_query = query
        return True

    async def record_workflow_trace(self, run_id: UUID, trace: WorkflowTrace) -> bool:
        self.traces.append(trace)
        return True

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return False


async def test_workflow_rewrites_claims_that_expand_beyond_the_evidence() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = str(uuid5(run_id, str(evidence.chunk_id)))
    expanded = (
        "RAG achieved state-of-the-art results on Natural Questions, TriviaQA, "
        f"WebQuestions, and CuratedTREC [{citation_id}]."
    )
    verifier = RecordingClaimSupportVerifier(
        ClaimSupportDecision(
            claims=(
                GroundedClaim(
                    text=(
                        "RAG achieved state-of-the-art results on Natural Questions, "
                        "WebQuestions, and CuratedTREC."
                    ),
                    citation_ids=(citation_id,),
                ),
                GroundedClaim(
                    text=(
                        "On TriviaQA, RAG strongly outperformed approaches using "
                        "specialized pre-training objectives."
                    ),
                    citation_ids=(citation_id,),
                ),
            )
        )
    )
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(expanded),
        claim_support_verifier=verifier,
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(
                run_id,
                uuid4(),
                "RAG reported leading results on which open-domain QA datasets?",
                (),
            )
        )
    ]

    assert verifier.answers == [expanded]
    assert events[-1].type == "answered"
    assert "state-of-the-art results on Natural Questions, TriviaQA" not in events[-1].answer
    assert "state-of-the-art results on Natural Questions, WebQuestions" in events[-1].answer
    assert "On TriviaQA, RAG strongly outperformed" in events[-1].answer


async def test_workflow_cites_each_sentence_in_a_supported_claim() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = str(uuid5(run_id, str(evidence.chunk_id)))
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(f"Initial supported sentence [{citation_id}]"),
        claim_support_verifier=RecordingClaimSupportVerifier(
            ClaimSupportDecision(
                claims=(
                    GroundedClaim(
                        text="First supported sentence. Second supported sentence.",
                        citation_ids=(citation_id,),
                    ),
                )
            )
        ),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(run_id, uuid4(), "Question", ())
        )
    ]

    assert events[-1].type == "answered"
    assert events[-1].answer == (
        f"First supported sentence. [{citation_id}]\n"
        f"Second supported sentence. [{citation_id}]"
    )


async def test_workflow_refuses_when_no_supported_claim_remains() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = uuid5(run_id, str(evidence.chunk_id))
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(f"Unsupported expansion [{citation_id}]"),
        claim_support_verifier=RecordingClaimSupportVerifier(
            ClaimSupportDecision(claims=())
        ),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(run_id, uuid4(), "Question", ())
        )
    ]

    assert events[-1].type == "refused"
    assert events[-1].code == "CLAIM_SUPPORT_VALIDATION_FAILED"


async def test_workflow_fails_closed_without_a_claim_support_verifier() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = uuid5(run_id, str(evidence.chunk_id))
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(f"Supported draft [{citation_id}]"),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(run_id, uuid4(), "Question", ())
        )
    ]

    assert events[-1].type == "refused"
    assert events[-1].code == "CLAIM_SUPPORT_VALIDATION_FAILED"


async def test_workflow_refuses_an_over_broad_claim_rejected_by_the_verifier() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = uuid5(run_id, str(evidence.chunk_id))
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(f"TriviaQA reached state-of-the-art [{citation_id}]"),
        claim_support_verifier=RejectingClaimSupportVerifier(),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(run_id, uuid4(), "Question", ())
        )
    ]

    assert events[-1].type == "refused"
    assert events[-1].code == "CLAIM_SUPPORT_VALIDATION_FAILED"


class CancellingRunControl(ActiveRunControl):
    def __init__(self, checks: Sequence[bool]) -> None:
        super().__init__()
        self.checks = list(checks)

    async def is_cancel_requested(self, run_id: UUID) -> bool:
        return self.checks.pop(0)


async def test_workflow_answers_from_only_the_assessor_selected_evidence() -> None:
    run_id = uuid4()
    knowledge_base_id = uuid4()
    rejected = _evidence(score=0.95)
    selected = _evidence(score=0.8)
    citation_id = uuid5(run_id, str(selected.chunk_id))
    retrieval = RecordingRetrieval([rejected, selected])
    generator = RecordingGenerator(f"Bounded agents are safer [{citation_id}]")
    control = ActiveRunControl()
    workflow = AnswerWorkflow(
        retrieval=retrieval,
        assessor=SelectingAssessor(selected.chunk_id),
        generator=generator,
        claim_support_verifier=CitationPreservingClaimSupportVerifier(),
        citation_repairer=UnusedCitationRepairer(),
        run_control=control,
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(
                run_id=run_id,
                knowledge_base_id=knowledge_base_id,
                question="Why must agents be bounded?",
                recent_questions=(),
            )
        )
    ]

    assert control.retrieval_query == "Why must agents be bounded?"
    assert retrieval.queries == [("Why must agents be bounded?",)]
    assert [candidate.chunk_id for candidate in generator.evidence] == [str(selected.chunk_id)]
    assert [event.type for event in events] == [
        "status",
        "status",
        "status",
        "status",
        "delta",
        "status",
        "status",
        "status",
        "answered",
    ]
    answered = events[-1]
    assert answered.type == "answered"
    assert answered.answer == f"Bounded agents are safer [{citation_id}]"
    assert [item.chunk_id for item in answered.evidence] == [selected.chunk_id]


async def test_workflow_performs_only_one_supplemental_retrieval() -> None:
    run_id = uuid4()
    initial = _evidence(score=0.8)
    supplemental = _evidence(score=0.85)
    citation_id = uuid5(run_id, str(supplemental.chunk_id))

    class SupplementalRetrieval(RecordingRetrieval):
        async def search(
            self,
            *,
            knowledge_base_id: UUID,
            queries: Sequence[str],
        ) -> RetrievalResult:
            executed = tuple(queries)
            self.queries.append(executed)
            evidence = [initial] if len(self.queries) == 1 else [supplemental]
            return _retrieval_result(executed, evidence)

    retrieval = SupplementalRetrieval([])
    assessor = SequentialAssessor(
        [
            EvidenceDecision(
                sufficient=False,
                selected_chunk_ids=(),
                supplemental_queries=("bounded agent maximum retrieval attempts",),
            ),
            EvidenceDecision(
                sufficient=True,
                selected_chunk_ids=(str(supplemental.chunk_id),),
                supplemental_queries=(),
            ),
        ]
    )
    generator = RecordingGenerator(f"Only one retry is allowed [{citation_id}]")
    control = ActiveRunControl()
    workflow = AnswerWorkflow(
        retrieval=retrieval,
        assessor=assessor,
        generator=generator,
        claim_support_verifier=CitationPreservingClaimSupportVerifier(),
        citation_repairer=UnusedCitationRepairer(),
        run_control=control,
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(
                run_id=run_id,
                knowledge_base_id=uuid4(),
                question="What is the retry limit?",
                recent_questions=(),
            )
        )
    ]

    assert retrieval.queries == [
        ("What is the retry limit?",),
        (
            "What is the retry limit?",
            "bounded agent maximum retrieval attempts",
        ),
    ]
    assert assessor.supplemental_query_limits == [2, 0]
    assert control.traces[-1].retrieval_queries == (
        "What is the retry limit?",
        "bounded agent maximum retrieval attempts",
    )
    assert [assessment.selected_chunk_ids for assessment in control.traces[-1].assessments] == [
        (),
        (str(supplemental.chunk_id),),
    ]
    assert control.traces[-1].supplemental_retrieval_attempts == 1
    assert control.traces[-1].citation_repair_attempts == 0
    assert [candidate.chunk_id for candidate in generator.evidence] == [str(supplemental.chunk_id)]
    assert events[-1].type == "answered"


async def test_workflow_repairs_invalid_citations_once_before_answering() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = uuid5(run_id, str(evidence.chunk_id))
    repairer = RecordingCitationRepairer(
        f"Bounded workflows prevent unbounded loops [{citation_id}]"
    )
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator("Bounded workflows prevent unbounded loops"),
        claim_support_verifier=CitationPreservingClaimSupportVerifier(),
        citation_repairer=repairer,
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(
                run_id=run_id,
                knowledge_base_id=uuid4(),
                question="Why are workflows bounded?",
                recent_questions=(),
            )
        )
    ]

    assert repairer.answers == ["Bounded workflows prevent unbounded loops"]
    assert [event.stage for event in events if event.type == "status"].count("repairing") == 1
    assert events[-1].type == "answered"
    assert events[-1].answer == (f"Bounded workflows prevent unbounded loops [{citation_id}]")


async def test_workflow_refuses_an_english_final_answer_for_a_chinese_question() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = str(uuid5(run_id, str(evidence.chunk_id)))
    english_answer = f"Agent workflows must remain bounded. [{citation_id}]"
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(english_answer),
        claim_support_verifier=RecordingClaimSupportVerifier(
            ClaimSupportDecision(
                claims=(
                    GroundedClaim(
                        text="Agent workflows must remain bounded.",
                        citation_ids=(citation_id,),
                    ),
                )
            )
        ),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(run_id, uuid4(), "为什么 Agent 工作流必须有界?", ())
        )
    ]

    assert events[-1].type == "refused"
    assert events[-1].code == "ANSWER_LANGUAGE_VALIDATION_FAILED"


async def test_workflow_keeps_chinese_after_citation_repair() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = str(uuid5(run_id, str(evidence.chunk_id)))
    repairer = RecordingCitationRepairer(
        f"Agent workflows must remain bounded. [{citation_id}]"
    )
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator("Agent 工作流必须保持有界。"),
        claim_support_verifier=CitationPreservingClaimSupportVerifier(),
        citation_repairer=repairer,
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(run_id, uuid4(), "为什么 Agent 工作流必须有界?", ())
        )
    ]

    assert repairer.answers == ["Agent 工作流必须保持有界。"]
    assert events[-1].type == "refused"
    assert events[-1].code == "ANSWER_LANGUAGE_VALIDATION_FAILED"


async def test_workflow_can_render_english_evidence_as_a_grounded_chinese_answer() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = str(uuid5(run_id, str(evidence.chunk_id)))
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(
            f"Agent workflows must remain bounded. [{citation_id}]"
        ),
        claim_support_verifier=RecordingClaimSupportVerifier(
            ClaimSupportDecision(
                claims=(
                    GroundedClaim(
                        text="Agent 工作流必须保持有界。",
                        citation_ids=(citation_id,),
                    ),
                )
            )
        ),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(run_id, uuid4(), "Agent 工作流必须满足什么约束?", ())
        )
    ]

    assert evidence.text == "Agent workflows must remain bounded."
    assert events[-1].type == "answered"
    assert events[-1].answer == f"Agent 工作流必须保持有界。 [{citation_id}]"


async def test_workflow_stops_when_cancellation_is_seen_between_nodes() -> None:
    evidence = _evidence()
    control = CancellingRunControl([False, False, True])
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=UnusedAssessor(),
        generator=RecordingGenerator("must not be generated"),
        citation_repairer=UnusedCitationRepairer(),
        run_control=control,
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(
                run_id=uuid4(),
                knowledge_base_id=uuid4(),
                question="Will this be cancelled?",
                recent_questions=(),
            )
        )
    ]

    assert [event.type for event in events] == ["status", "status", "cancelled"]


async def test_workflow_returns_only_evidence_actually_cited_by_the_answer() -> None:
    run_id = uuid4()
    cited = _evidence()
    selected_but_uncited = _evidence()
    citation_id = uuid5(run_id, str(cited.chunk_id))
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([cited, selected_but_uncited]),
        assessor=SelectingManyAssessor([cited.chunk_id, selected_but_uncited.chunk_id]),
        generator=RecordingGenerator(f"Supported statement [{citation_id}]"),
        claim_support_verifier=CitationPreservingClaimSupportVerifier(),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event
        async for event in workflow.run(
            WorkflowRequest(
                run_id=run_id,
                knowledge_base_id=uuid4(),
                question="What is supported?",
                recent_questions=(),
            )
        )
    ]

    assert events[-1].type == "answered"
    assert [item.chunk_id for item in events[-1].evidence] == [cited.chunk_id]


async def test_workflow_refuses_after_one_unsuccessful_supplemental_retrieval() -> None:
    initial = _evidence()
    supplemental = _evidence()

    class TwoResultRetrieval(RecordingRetrieval):
        async def search(
            self,
            *,
            knowledge_base_id: UUID,
            queries: Sequence[str],
        ) -> RetrievalResult:
            executed = tuple(queries)
            self.queries.append(executed)
            evidence = [initial] if len(self.queries) == 1 else [supplemental]
            return _retrieval_result(executed, evidence)

    retrieval = TwoResultRetrieval([])
    assessor = SequentialAssessor(
        [
            EvidenceDecision(False, (), ("one supplemental query",)),
            EvidenceDecision(False, (), ("a forbidden third query",)),
        ]
    )
    workflow = AnswerWorkflow(
        retrieval=retrieval,
        assessor=assessor,
        generator=UnusedGenerator(),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event async for event in workflow.run(WorkflowRequest(uuid4(), uuid4(), "Question", ()))
    ]

    assert retrieval.queries == [
        ("Question",),
        ("Question", "one supplemental query"),
    ]
    assert assessor.supplemental_query_limits == [2, 0]
    assert events[-1].type == "refused"
    assert events[-1].code == "INSUFFICIENT_EVIDENCE"


async def test_workflow_refuses_when_the_single_citation_repair_is_still_invalid() -> None:
    evidence = _evidence()
    repairer = RecordingCitationRepairer("Still has no citation")
    control = ActiveRunControl()
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator("Initial answer without citation"),
        claim_support_verifier=CitationPreservingClaimSupportVerifier(),
        citation_repairer=repairer,
        run_control=control,
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event async for event in workflow.run(WorkflowRequest(uuid4(), uuid4(), "Question", ()))
    ]

    assert repairer.answers == ["Initial answer without citation"]
    assert events[-1].type == "refused"
    assert events[-1].code == "CITATION_VALIDATION_FAILED"
    assert [item.issue for item in control.traces[-1].citation_validations] == [
        "uncited_claim",
        "uncited_claim",
    ]
    initial, repaired = control.traces[-1].citation_validations
    assert initial.attempt == "initial"
    assert repaired.attempt == "repair"
    assert initial.unit_count == repaired.unit_count == 1
    assert initial.citation_count == repaired.citation_count == 0
    assert initial.uncited_unit_indices == repaired.uncited_unit_indices == (0,)
    assert initial.unknown_label_unit_indices == repaired.unknown_label_unit_indices == ()
    feedback = repairer.validation_feedbacks[0]
    assert feedback.issue == "uncited_claim"
    assert feedback.unit_count == 1
    assert feedback.uncited_unit_indices == (0,)
    assert feedback.unknown_label_unit_indices == ()


async def test_workflow_rejects_an_assessment_that_selects_unknown_chunks() -> None:
    evidence = _evidence()
    assessor = SequentialAssessor(
        [
            EvidenceDecision(
                sufficient=True,
                selected_chunk_ids=(str(evidence.chunk_id), "unknown-chunk"),
                supplemental_queries=(),
            )
        ]
    )
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=assessor,
        generator=UnusedGenerator(),
        citation_repairer=UnusedCitationRepairer(),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event async for event in workflow.run(WorkflowRequest(uuid4(), uuid4(), "Question", ()))
    ]

    assert events[-1].type == "refused"
    assert events[-1].code == "INSUFFICIENT_EVIDENCE"


async def test_workflow_refuses_when_one_claim_has_no_citation() -> None:
    run_id = uuid4()
    evidence = _evidence()
    citation_id = uuid5(run_id, str(evidence.chunk_id))
    answer = f"This first claim has no source. This second claim is supported [{citation_id}]."
    workflow = AnswerWorkflow(
        retrieval=RecordingRetrieval([evidence]),
        assessor=SelectingAssessor(evidence.chunk_id),
        generator=RecordingGenerator(answer),
        citation_repairer=RecordingCitationRepairer(answer),
        run_control=ActiveRunControl(),
        minimum_score=0.5,
        minimum_evidence=1,
    )

    events = [
        event async for event in workflow.run(WorkflowRequest(run_id, uuid4(), "Question", ()))
    ]

    assert events[-1].type == "refused"
    assert events[-1].code == "CITATION_VALIDATION_FAILED"
