from hashlib import sha256
from uuid import uuid4

from sourcetrace.evaluation.citation_diagnostics import build_citation_diagnostics
from sourcetrace.evaluation.models import (
    CaseEvaluationResult,
    CitationDiagnosticsReport,
    DatasetReview,
    EvaluationCase,
    EvaluationDataset,
    EvaluationObservation,
    EvaluationReport,
    EvaluationRunMetadata,
    EvaluationSummary,
    ObservedEvidence,
)


def _metadata() -> EvaluationRunMetadata:
    return EvaluationRunMetadata(
        code_commit="test-commit",
        model_provider="fake",
        model_name="fake",
        workflow_version="v1",
        parser_version="v1",
        tokenizer="cl100k_base",
        chunk_size=500,
        chunk_overlap=80,
        chunking_version="v1",
        embedding_provider="fake",
        embedding_model="fake",
        embedding_revision="1",
        embedding_dimension=4,
        embedding_version="v1",
        retrieval_version="v1",
        retrieval_top_k=8,
        retrieval_minimum_score=0.5,
        retrieval_minimum_evidence=1,
        generation_prompt_version="v1",
        question_rewrite_prompt_version="v1",
        evidence_assessment_prompt_version="v1",
        citation_repair_prompt_version="v1",
    )


def _summary(*, passed: int = 0, failed: int = 0) -> EvaluationSummary:
    return EvaluationSummary(
        passed=passed,
        failed=failed,
        pending_review=0,
        not_applicable=0,
    )


def test_diagnostics_classify_partial_claim_coverage_without_copying_text() -> None:
    version_id = uuid4()
    case = EvaluationCase.model_validate(
        {
            "id": "citation-001",
            "category": "multi_chunk",
            "question": "Sensitive question must not be copied.",
            "expected": {
                "outcome": "answered",
                "reference_answer": "Sensitive reference answer.",
                "evidence": [
                    {
                        "claim_id": "first-claim",
                        "document_version_id": str(version_id),
                        "page_number": 2,
                        "text": "First private evidence sentence.",
                    },
                    {
                        "claim_id": "second-claim",
                        "document_version_id": str(version_id),
                        "page_number": 4,
                        "text": "Second private evidence sentence.",
                    },
                ],
            },
        }
    )
    dataset = EvaluationDataset(
        schema_version="1",
        dataset_id="citation-diagnostic-fixture",
        dataset_version="1.0.0",
        knowledge_base_id=uuid4(),
        document_version_ids=[version_id],
        review=DatasetReview(status="fixture"),
        cases=[case],
    )
    observation = EvaluationObservation(
        outcome="answered",
        answer="Sensitive generated answer.",
        retrieved_evidence=(
            ObservedEvidence(
                document_version_id=version_id,
                page_number=2,
                text="First private evidence sentence.",
            ),
            ObservedEvidence(
                document_version_id=version_id,
                page_number=4,
                text="Second private evidence sentence.",
            ),
        ),
        citations=(
            ObservedEvidence(
                document_version_id=version_id,
                page_number=2,
                text="First private evidence sentence.",
            ),
            ObservedEvidence(
                document_version_id=version_id,
                page_number=6,
                text="Different cited passage.",
            ),
        ),
    )
    report = EvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=[version_id],
        metadata=_metadata(),
        cases=[
            CaseEvaluationResult(
                case_id=case.id,
                retrieval="passed",
                citation="failed",
                refusal="not_applicable",
                end_to_end="failed",
                observation=observation,
            )
        ],
        retrieval_summary=_summary(passed=1),
        citation_summary=_summary(failed=1),
        refusal_summary=EvaluationSummary(
            passed=0,
            failed=0,
            pending_review=0,
            not_applicable=1,
        ),
        end_to_end_summary=_summary(failed=1),
    )

    diagnostics = build_citation_diagnostics(
        dataset,
        report,
        report_sha256=sha256(b"report").hexdigest(),
    )

    assert diagnostics.cases[0].primary_mechanism == "partial_claim_coverage"
    assert diagnostics.source_metadata.code_commit == "test-commit"
    assert diagnostics.summary.failed_answered_cases == 1
    assert diagnostics.summary.partial_claim_coverage == 1
    assert diagnostics.summary.expected_evidence_not_retrieved == 0
    assert [item.retrieval_status for item in diagnostics.cases[0].claims] == [
        "canonical",
        "canonical",
    ]
    assert [item.citation_status for item in diagnostics.cases[0].claims] == [
        "canonical",
        "not_observed",
    ]
    assert diagnostics.cases[0].cited_passages[1].page_number == 6
    serialized = diagnostics.model_dump_json()
    for private_text in (
        case.question,
        case.expected.reference_answer,
        observation.answer,
        "First private evidence sentence.",
        "Second private evidence sentence.",
        "Different cited passage.",
    ):
        assert private_text not in serialized


def test_diagnostics_classify_retrieved_evidence_that_was_not_cited() -> None:
    diagnostics = _build_single_claim_diagnostics(
        retrieved_page=2,
        retrieved_text="Expected evidence.",
        cited_page=3,
        cited_text="Different evidence.",
    )

    assert diagnostics.cases[0].primary_mechanism == "retrieved_but_not_cited"
    assert diagnostics.cases[0].claims[0].retrieval_status == "canonical"
    assert diagnostics.cases[0].claims[0].citation_status == "not_observed"


def test_diagnostics_exclude_citation_failures_when_retrieval_failed() -> None:
    diagnostics = _build_single_claim_diagnostics(
        retrieved_page=3,
        retrieved_text="Different evidence.",
        cited_page=3,
        cited_text="Different evidence.",
    )

    assert diagnostics.cases == ()
    assert diagnostics.summary.failed_answered_cases == 0
    assert diagnostics.summary.expected_evidence_not_retrieved == 0


def test_diagnostics_classify_same_page_different_chunk() -> None:
    diagnostics = _build_single_claim_diagnostics(
        retrieved_page=2,
        retrieved_text="Adjacent chunk.",
        cited_page=2,
        cited_text="Adjacent chunk.",
    )

    assert diagnostics.cases[0].primary_mechanism == "same_page_different_chunk"
    assert diagnostics.cases[0].claims[0].retrieval_status == (
        "same_page_different_chunk"
    )
    assert diagnostics.cases[0].claims[0].citation_status == (
        "same_page_different_chunk"
    )


def test_diagnostics_classify_same_page_retrieval_when_citation_uses_other_page() -> None:
    diagnostics = _build_single_claim_diagnostics(
        retrieved_page=2,
        retrieved_text="Adjacent chunk.",
        cited_page=3,
        cited_text="Different evidence.",
    )

    assert diagnostics.cases[0].primary_mechanism == "same_page_different_chunk"
    assert diagnostics.cases[0].claims[0].retrieval_status == (
        "same_page_different_chunk"
    )
    assert diagnostics.cases[0].claims[0].citation_status == "not_observed"


def _build_single_claim_diagnostics(
    *,
    retrieved_page: int,
    retrieved_text: str,
    cited_page: int,
    cited_text: str,
) -> CitationDiagnosticsReport:
    version_id = uuid4()
    case = EvaluationCase.model_validate(
        {
            "id": "citation-001",
            "category": "direct",
            "question": "Question",
            "expected": {
                "outcome": "answered",
                "reference_answer": "Answer",
                "evidence": [
                    {
                        "claim_id": "expected-claim",
                        "document_version_id": str(version_id),
                        "page_number": 2,
                        "text": "Expected evidence.",
                    }
                ],
            },
        }
    )
    dataset = EvaluationDataset(
        schema_version="1",
        dataset_id="citation-diagnostic-fixture",
        dataset_version="1.0.0",
        knowledge_base_id=uuid4(),
        document_version_ids=[version_id],
        review=DatasetReview(status="fixture"),
        cases=[case],
    )
    observation = EvaluationObservation(
        outcome="answered",
        answer="Answer",
        retrieved_evidence=(
            ObservedEvidence(
                document_version_id=version_id,
                page_number=retrieved_page,
                text=retrieved_text,
            ),
        ),
        citations=(
            ObservedEvidence(
                document_version_id=version_id,
                page_number=cited_page,
                text=cited_text,
            ),
        ),
    )
    report = EvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=[version_id],
        metadata=_metadata(),
        cases=[
            CaseEvaluationResult(
                case_id=case.id,
                retrieval="failed" if retrieved_page != 2 else "passed",
                citation="failed",
                refusal="not_applicable",
                end_to_end="failed",
                observation=observation,
            )
        ],
        retrieval_summary=_summary(
            passed=1 if retrieved_page == 2 else 0,
            failed=1 if retrieved_page != 2 else 0,
        ),
        citation_summary=_summary(failed=1),
        refusal_summary=EvaluationSummary(
            passed=0,
            failed=0,
            pending_review=0,
            not_applicable=1,
        ),
        end_to_end_summary=_summary(failed=1),
    )
    return build_citation_diagnostics(
        dataset,
        report,
        report_sha256=sha256(b"report").hexdigest(),
    )
