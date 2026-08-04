from hashlib import sha256
from uuid import uuid4

from sourcetrace.evaluation.models import (
    CaseEvaluationResult,
    DatasetReview,
    EvaluationCase,
    EvaluationDataset,
    EvaluationDecisionTrace,
    EvaluationObservation,
    EvaluationReport,
    EvaluationRunMetadata,
    EvaluationSummary,
    ObservedEvidence,
    ObservedRetrieval,
    ObservedRetrievalCandidate,
)
from sourcetrace.evaluation.retrieval_diagnostics import build_retrieval_diagnostics


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


def _summary() -> EvaluationSummary:
    return EvaluationSummary(passed=0, failed=1, pending_review=0, not_applicable=0)


def test_diagnostics_classify_same_page_without_expected_text_as_chunk_boundary() -> None:
    version_id = uuid4()
    case = EvaluationCase.model_validate(
        {
            "id": "boundary-001",
            "category": "direct",
            "question": "Where is the answer?",
            "expected": {
                "outcome": "answered",
                "reference_answer": "On page two.",
                "evidence": [
                    {
                        "document_version_id": str(version_id),
                        "page_number": 2,
                        "text": "Expected sentence.",
                    }
                ],
            },
        }
    )
    dataset = EvaluationDataset(
        schema_version="1",
        dataset_id="diagnostic-fixture",
        dataset_version="1.0.0",
        knowledge_base_id=uuid4(),
        document_version_ids=[version_id],
        review=DatasetReview(status="fixture"),
        cases=[case],
    )
    observation = EvaluationObservation(
        outcome="refused",
        answer=None,
        citations=(),
        retrieved_evidence=(
            ObservedEvidence(document_version_id=version_id, page_number=2, text="Adjacent chunk."),
        ),
        decision_trace=EvaluationDecisionTrace(
            retrievals=(
                ObservedRetrieval(
                    query=case.question,
                    candidates=(
                        ObservedRetrievalCandidate(
                            chunk_id=uuid4(),
                            document_version_id=version_id,
                            page_number=2,
                            score=0.8,
                        ),
                    ),
                ),
            ),
            assessments=(),
            citation_validations=(),
            supplemental_retrieval_attempts=0,
            citation_repair_attempts=0,
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
                retrieval="failed",
                citation="failed",
                refusal="not_applicable",
                end_to_end="failed",
                observation=observation,
            )
        ],
        retrieval_summary=_summary(),
        citation_summary=_summary(),
        refusal_summary=EvaluationSummary(passed=0, failed=0, pending_review=0, not_applicable=1),
        end_to_end_summary=_summary(),
    )

    diagnostics = build_retrieval_diagnostics(
        dataset, report, report_sha256=sha256(b"report").hexdigest()
    )

    assert diagnostics.cases[0].primary_mechanism == "chunk_boundary_mismatch"
    assert diagnostics.cases[0].expected_evidence[0].match_status == "same_page_different_chunk"
    assert "Adjacent chunk." not in diagnostics.model_dump_json()


def test_diagnostics_classify_missing_expected_page_as_embedding_weakness() -> None:
    version_id = uuid4()
    other_version_id = uuid4()
    case = EvaluationCase.model_validate(
        {
            "id": "dense-001",
            "category": "direct",
            "question": "Which source?",
            "expected": {
                "outcome": "answered",
                "reference_answer": "The expected source.",
                "evidence": [
                    {
                        "document_version_id": str(version_id),
                        "page_number": 4,
                        "text": "Expected sentence.",
                    }
                ],
            },
        }
    )
    dataset = EvaluationDataset(
        schema_version="1",
        dataset_id="diagnostic-fixture",
        dataset_version="1.0.0",
        knowledge_base_id=uuid4(),
        document_version_ids=[version_id, other_version_id],
        review=DatasetReview(status="fixture"),
        cases=[case],
    )
    observation = EvaluationObservation(
        outcome="refused",
        answer=None,
        citations=(),
        retrieved_evidence=(
            ObservedEvidence(
                document_version_id=other_version_id, page_number=1, text="Wrong page."
            ),
        ),
        decision_trace=EvaluationDecisionTrace(
            retrievals=(
                ObservedRetrieval(
                    query=case.question,
                    candidates=(
                        ObservedRetrievalCandidate(
                            chunk_id=uuid4(),
                            document_version_id=other_version_id,
                            page_number=1,
                            score=0.8,
                        ),
                    ),
                ),
            ),
            assessments=(),
            citation_validations=(),
            supplemental_retrieval_attempts=0,
            citation_repair_attempts=0,
        ),
    )
    report = EvaluationReport(
        dataset_id=dataset.dataset_id,
        dataset_version=dataset.dataset_version,
        knowledge_base_id=dataset.knowledge_base_id,
        document_version_ids=[version_id, other_version_id],
        metadata=_metadata(),
        cases=[
            CaseEvaluationResult(
                case_id=case.id,
                retrieval="failed",
                citation="failed",
                refusal="not_applicable",
                end_to_end="failed",
                observation=observation,
            )
        ],
        retrieval_summary=_summary(),
        citation_summary=_summary(),
        refusal_summary=EvaluationSummary(passed=0, failed=0, pending_review=0, not_applicable=1),
        end_to_end_summary=_summary(),
    )

    diagnostics = build_retrieval_diagnostics(
        dataset, report, report_sha256=sha256(b"report").hexdigest()
    )

    assert diagnostics.cases[0].primary_mechanism == "embedding_retrieval_weakness"
    assert diagnostics.cases[0].expected_evidence[0].match_status == "not_retrieved"
