from collections.abc import Mapping
from uuid import uuid4

from sourcetrace.evaluation import (
    EvaluationDataset,
    EvaluationHarness,
    EvaluationObservation,
    EvaluationRunMetadata,
    ObservedEvidence,
)
from sourcetrace.evaluation.models import EvaluationCase, EvaluationJudgmentSet
from sourcetrace.evaluation.review import apply_judgments


class DeterministicSubject:
    def __init__(self, observations: Mapping[str, EvaluationObservation]) -> None:
        self._observations = observations

    async def evaluate(self, case: EvaluationCase) -> EvaluationObservation:
        return self._observations[case.id]


async def test_harness_reports_independent_results_with_replay_versions() -> None:
    knowledge_base_id = uuid4()
    document_version_id = uuid4()
    dataset = EvaluationDataset.model_validate(
        {
            "schema_version": "1",
            "dataset_id": "fixture-rag",
            "dataset_version": "1.0.0",
            "knowledge_base_id": knowledge_base_id,
            "document_version_ids": [document_version_id],
            "review": {"status": "fixture"},
            "cases": [
                {
                    "id": "direct-001",
                    "category": "direct",
                    "question": "How are vectors stored?",
                    "expected": {
                        "outcome": "answered",
                        "reference_answer": "They are normalized before storage.",
                        "evidence": [
                            {
                                "document_version_id": document_version_id,
                                "page_number": 4,
                                "text": "Vectors are normalized before storage.",
                            }
                        ],
                    },
                }
            ],
        }
    )
    evidence = ObservedEvidence(
        document_version_id=document_version_id,
        page_number=4,
        text="Vectors are normalized before storage.",
    )
    subject = DeterministicSubject(
        {
            "direct-001": EvaluationObservation(
                outcome="answered",
                answer="They are normalized before storage.",
                retrieved_evidence=(evidence,),
                citations=(evidence,),
            )
        }
    )
    metadata = EvaluationRunMetadata(
        code_commit="bf13fd430508dd1d47c112c024e1b0eef63d4e65",
        model_provider="fake",
        model_name="deterministic-fixture-v1",
        workflow_version="langgraph-bounded-v1",
        parser_version="fake-parser-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_version="token-window-v1",
        embedding_provider="fake",
        embedding_model="deterministic-fixture",
        embedding_revision="1",
        embedding_dimension=4,
        embedding_version="bge-m3-dense-v1",
        retrieval_version="pgvector-cosine-v1",
        retrieval_top_k=8,
        retrieval_minimum_score=0.5,
        retrieval_minimum_evidence=1,
        generation_prompt_version="grounded-answer-v1",
        question_rewrite_prompt_version="follow-up-query-v1",
        evidence_assessment_prompt_version="evidence-assessment-v1",
        citation_repair_prompt_version="citation-repair-v1",
    )

    report = await EvaluationHarness().run(dataset, subject, metadata=metadata)

    assert report.dataset_id == "fixture-rag"
    assert report.dataset_version == "1.0.0"
    assert report.knowledge_base_id == knowledge_base_id
    assert report.document_version_ids == [document_version_id]
    assert report.metadata == metadata
    assert report.cases[0].retrieval == "passed"
    assert report.cases[0].citation == "passed"
    assert report.cases[0].refusal == "not_applicable"
    assert report.cases[0].end_to_end == "pending_review"

    reviewed_report = apply_judgments(
        report,
        EvaluationJudgmentSet.model_validate(
            {
                "schema_version": "1",
                "dataset_id": "fixture-rag",
                "dataset_version": "1.0.0",
                "report_sha256": "a" * 64,
                "review": {
                    "status": "reviewed",
                    "reviewed_by": "project-owner",
                    "reviewed_at": "2026-07-29T03:00:00Z",
                },
                "judgments": [{"case_id": "direct-001", "status": "passed"}],
            }
        ),
        report_sha256="a" * 64,
    )

    assert reviewed_report.cases[0].end_to_end == "passed"
    assert reviewed_report.end_to_end_summary.passed == 1
    assert reviewed_report.end_to_end_summary.pending_review == 0
    assert reviewed_report.judgment_review is not None
    assert reviewed_report.judgment_review.reviewed_by == "project-owner"


async def test_refusal_results_are_reported_without_scoring_irrelevant_axes() -> None:
    document_version_id = uuid4()
    dataset = EvaluationDataset.model_validate(
        {
            "schema_version": "1",
            "dataset_id": "fixture-rag",
            "dataset_version": "1.0.0",
            "knowledge_base_id": uuid4(),
            "document_version_ids": [document_version_id],
            "review": {"status": "fixture"},
            "cases": [
                {
                    "id": "unanswerable-001",
                    "category": "unanswerable",
                    "question": "What is outside the source?",
                    "expected": {
                        "outcome": "refused",
                        "reference_answer": None,
                        "evidence": [],
                    },
                }
            ],
        }
    )
    subject = DeterministicSubject(
        {
            "unanswerable-001": EvaluationObservation(
                outcome="refused",
                answer=None,
                retrieved_evidence=(),
                citations=(),
            )
        }
    )
    metadata = EvaluationRunMetadata(
        code_commit="bf13fd430508dd1d47c112c024e1b0eef63d4e65",
        model_provider="fake",
        model_name="deterministic-fixture-v1",
        workflow_version="langgraph-bounded-v1",
        parser_version="fake-parser-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_version="token-window-v1",
        embedding_provider="fake",
        embedding_model="deterministic-fixture",
        embedding_revision="1",
        embedding_dimension=4,
        embedding_version="bge-m3-dense-v1",
        retrieval_version="pgvector-cosine-v1",
        retrieval_top_k=8,
        retrieval_minimum_score=0.5,
        retrieval_minimum_evidence=1,
        generation_prompt_version="grounded-answer-v1",
        question_rewrite_prompt_version="follow-up-query-v1",
        evidence_assessment_prompt_version="evidence-assessment-v1",
        citation_repair_prompt_version="citation-repair-v1",
    )

    report = await EvaluationHarness().run(dataset, subject, metadata=metadata)

    assert report.cases[0].retrieval == "not_applicable"
    assert report.cases[0].citation == "not_applicable"
    assert report.cases[0].refusal == "passed"
    assert report.cases[0].end_to_end == "passed"
    assert report.refusal_summary.passed == 1
    assert report.refusal_summary.not_applicable == 0
    assert report.retrieval_summary.not_applicable == 1
    assert report.citation_summary.not_applicable == 1


async def test_citation_fails_when_answer_cites_evidence_outside_ground_truth() -> None:
    expected_version_id = uuid4()
    unexpected_version_id = uuid4()
    dataset = EvaluationDataset.model_validate(
        {
            "schema_version": "1",
            "dataset_id": "fixture-rag",
            "dataset_version": "1.0.0",
            "knowledge_base_id": uuid4(),
            "document_version_ids": [expected_version_id, unexpected_version_id],
            "review": {"status": "fixture"},
            "cases": [
                {
                    "id": "confusing-001",
                    "category": "confusing",
                    "question": "Which source supports the answer?",
                    "expected": {
                        "outcome": "answered",
                        "reference_answer": "Only the expected source.",
                        "evidence": [
                            {
                                "document_version_id": expected_version_id,
                                "page_number": 2,
                                "text": "Expected evidence excerpt.",
                            }
                        ],
                    },
                }
            ],
        }
    )
    expected = ObservedEvidence(
        document_version_id=expected_version_id,
        page_number=2,
        text="A longer chunk containing the Expected evidence excerpt.",
    )
    unexpected = ObservedEvidence(
        document_version_id=unexpected_version_id,
        page_number=9,
        text="Distractor evidence.",
    )
    subject = DeterministicSubject(
        {
            "confusing-001": EvaluationObservation(
                outcome="answered",
                answer="Only the expected source.",
                retrieved_evidence=(expected, unexpected),
                citations=(expected, unexpected),
            )
        }
    )
    metadata = EvaluationRunMetadata(
        code_commit="test-commit",
        model_provider="fake",
        model_name="deterministic-fixture-v1",
        workflow_version="langgraph-bounded-v1",
        parser_version="fake-parser-v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_version="token-window-v1",
        embedding_provider="fake",
        embedding_model="deterministic-fixture",
        embedding_revision="1",
        embedding_dimension=4,
        embedding_version="bge-m3-dense-v1",
        retrieval_version="pgvector-cosine-v1",
        retrieval_top_k=8,
        retrieval_minimum_score=0.5,
        retrieval_minimum_evidence=1,
        generation_prompt_version="grounded-answer-v1",
        question_rewrite_prompt_version="follow-up-query-v1",
        evidence_assessment_prompt_version="evidence-assessment-v1",
        citation_repair_prompt_version="citation-repair-v1",
    )

    report = await EvaluationHarness().run(dataset, subject, metadata=metadata)

    assert report.cases[0].retrieval == "passed"
    assert report.cases[0].citation == "failed"
    assert report.cases[0].end_to_end == "failed"
