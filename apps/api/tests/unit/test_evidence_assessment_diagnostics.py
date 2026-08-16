from hashlib import sha256
from uuid import uuid4

from sourcetrace.evaluation.assessment_diagnostics import (
    build_evidence_assessment_diagnostics,
)
from sourcetrace.evaluation.models import EvaluationDataset, EvaluationReport


def test_diagnostics_classify_refusal_when_assessor_selects_no_evidence() -> None:
    document_version_id = uuid4()
    knowledge_base_id = uuid4()
    chunk_id = uuid4()
    dataset = EvaluationDataset.model_validate(
        {
            "schema_version": "1",
            "dataset_id": "assessment-diagnostic-fixture",
            "dataset_version": "1.0.0",
            "knowledge_base_id": str(knowledge_base_id),
            "document_version_ids": [str(document_version_id)],
            "review": {"status": "fixture"},
            "cases": [
                {
                    "id": "answerable-001",
                    "category": "direct",
                    "question": "Which component is used?",
                    "expected": {
                        "outcome": "answered",
                        "reference_answer": "The documented component is used.",
                        "evidence": [
                            {
                                "claim_id": "component-claim",
                                "document_version_id": str(document_version_id),
                                "page_number": 3,
                                "text": "The documented component is used.",
                            }
                        ],
                    },
                }
            ],
        }
    )
    report = EvaluationReport.model_validate(
        {
            "schema_version": "1",
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "knowledge_base_id": str(knowledge_base_id),
            "document_version_ids": [str(document_version_id)],
            "metadata": _metadata(),
            "cases": [
                {
                    "case_id": "answerable-001",
                    "retrieval": "passed",
                    "citation": "failed",
                    "refusal": "not_applicable",
                    "end_to_end": "failed",
                    "retrieval_evidence_matches": [
                        {
                            "claim_id": "component-claim",
                            "match_status": "canonical",
                            "matched_reference": {
                                "document_version_id": str(document_version_id),
                                "page_number": 3,
                                "text": "The documented component is used.",
                            },
                        }
                    ],
                    "observation": {
                        "outcome": "refused",
                        "answer": None,
                        "retrieved_evidence": [
                            {
                                "document_version_id": str(document_version_id),
                                "page_number": 3,
                                "text": "The documented component is used.",
                            }
                        ],
                        "citations": [],
                        "decision_trace": {
                            "retrievals": [
                                {
                                    "query": "Which component is used?",
                                    "candidates": [
                                        {
                                            "chunk_id": str(chunk_id),
                                            "document_version_id": str(document_version_id),
                                            "page_number": 3,
                                            "score": 0.9,
                                            "raw_rank": 1,
                                        }
                                    ],
                                }
                            ],
                            "retrieval_plan_version": "test-plan-v1",
                            "retrieval_rounds": [],
                            "assessments": [
                                {
                                    "sufficient": False,
                                    "selected_chunk_ids": [],
                                    "supplemental_queries": [],
                                }
                            ],
                            "citation_validations": [],
                            "supplemental_retrieval_attempts": 0,
                            "citation_repair_attempts": 0,
                        },
                    },
                }
            ],
            "retrieval_summary": _summary(passed=1),
            "citation_summary": _summary(failed=1),
            "refusal_summary": _summary(not_applicable=1),
            "end_to_end_summary": _summary(failed=1),
        }
    )

    diagnostics = build_evidence_assessment_diagnostics(
        dataset,
        report,
        report_sha256=sha256(b"source report").hexdigest(),
    )

    assert diagnostics.summary.failed_answerable_refusals == 1
    assert diagnostics.summary.no_evidence_selected == 1
    assert diagnostics.summary.expected_source_pages_not_selected == 0
    assert diagnostics.summary.expected_source_pages_selected_but_insufficient == 0
    case = diagnostics.cases[0]
    assert case.case_id == "answerable-001"
    assert case.primary_mechanism == "no_evidence_selected"
    assert case.claims[0].claim_id == "component-claim"
    assert case.claims[0].retrieval_match_status == "canonical"
    assert case.claims[0].selected_source_page is False
    assert case.assessment_rounds[0].selected_chunk_count == 0
    assert case.assessment_rounds[0].supplemental_query_count == 0
    serialized = diagnostics.model_dump_json()
    assert "Which component is used?" not in serialized
    assert "The documented component is used." not in serialized


def test_diagnostics_classify_refusal_when_expected_source_page_is_not_selected() -> None:
    knowledge_base_id = uuid4()
    first_document_id = uuid4()
    second_document_id = uuid4()
    selected_chunk_id = uuid4()
    omitted_chunk_id = uuid4()
    dataset = EvaluationDataset.model_validate(
        {
            "schema_version": "1",
            "dataset_id": "assessment-diagnostic-fixture",
            "dataset_version": "1.0.0",
            "knowledge_base_id": str(knowledge_base_id),
            "document_version_ids": [str(first_document_id), str(second_document_id)],
            "review": {"status": "fixture"},
            "cases": [
                {
                    "id": "multi-001",
                    "category": "multi_chunk",
                    "question": "Which sources own both components?",
                    "expected": {
                        "outcome": "answered",
                        "reference_answer": "Each source owns one component.",
                        "evidence": [
                            {
                                "claim_id": "first-component",
                                "document_version_id": str(first_document_id),
                                "page_number": 3,
                                "text": "The first source owns component A.",
                            },
                            {
                                "claim_id": "second-component",
                                "document_version_id": str(second_document_id),
                                "page_number": 1,
                                "text": "The second source owns component B.",
                            },
                        ],
                    },
                }
            ],
        }
    )
    report = EvaluationReport.model_validate(
        {
            "schema_version": "1",
            "dataset_id": dataset.dataset_id,
            "dataset_version": dataset.dataset_version,
            "knowledge_base_id": str(knowledge_base_id),
            "document_version_ids": [str(first_document_id), str(second_document_id)],
            "metadata": _metadata(),
            "cases": [
                {
                    "case_id": "multi-001",
                    "retrieval": "passed",
                    "citation": "failed",
                    "refusal": "not_applicable",
                    "end_to_end": "failed",
                    "observation": {
                        "outcome": "refused",
                        "answer": None,
                        "retrieved_evidence": [
                            {
                                "document_version_id": str(first_document_id),
                                "page_number": 3,
                                "text": "The first source owns component A.",
                            },
                            {
                                "document_version_id": str(second_document_id),
                                "page_number": 1,
                                "text": "The second source owns component B.",
                            },
                        ],
                        "citations": [],
                        "decision_trace": {
                            "retrievals": [
                                {
                                    "query": "Which sources own both components?",
                                    "candidates": [
                                        {
                                            "chunk_id": str(selected_chunk_id),
                                            "document_version_id": str(first_document_id),
                                            "page_number": 3,
                                            "score": 0.9,
                                            "raw_rank": 1,
                                        },
                                        {
                                            "chunk_id": str(omitted_chunk_id),
                                            "document_version_id": str(second_document_id),
                                            "page_number": 1,
                                            "score": 0.8,
                                            "raw_rank": 2,
                                        },
                                    ],
                                }
                            ],
                            "retrieval_plan_version": "test-plan-v1",
                            "retrieval_rounds": [],
                            "assessments": [
                                {
                                    "sufficient": False,
                                    "selected_chunk_ids": [str(selected_chunk_id)],
                                    "supplemental_queries": ["second component source"],
                                },
                                {
                                    "sufficient": False,
                                    "selected_chunk_ids": [str(selected_chunk_id)],
                                    "supplemental_queries": [],
                                },
                            ],
                            "citation_validations": [],
                            "supplemental_retrieval_attempts": 1,
                            "citation_repair_attempts": 0,
                        },
                    },
                }
            ],
            "retrieval_summary": _summary(passed=1),
            "citation_summary": _summary(failed=1),
            "refusal_summary": _summary(not_applicable=1),
            "end_to_end_summary": _summary(failed=1),
        }
    )

    diagnostics = build_evidence_assessment_diagnostics(
        dataset,
        report,
        report_sha256=sha256(b"source report").hexdigest(),
    )

    assert diagnostics.summary.failed_answerable_refusals == 1
    assert diagnostics.summary.no_evidence_selected == 0
    assert diagnostics.summary.expected_source_pages_not_selected == 1
    case = diagnostics.cases[0]
    assert case.primary_mechanism == "expected_source_pages_not_selected"
    assert [claim.selected_source_page for claim in case.claims] == [True, False]
    assert [round_.selected_chunk_count for round_ in case.assessment_rounds] == [1, 1]
    assert [round_.supplemental_query_count for round_ in case.assessment_rounds] == [1, 0]


def _metadata() -> dict[str, object]:
    return {
        "code_commit": "test-commit",
        "model_provider": "fake",
        "model_name": "deterministic-fixture-v1",
        "workflow_version": "test-workflow-v1",
        "parser_version": "test-parser-v1",
        "tokenizer": "cl100k_base",
        "chunk_size": 500,
        "chunk_overlap": 80,
        "chunking_version": "test-chunking-v1",
        "embedding_provider": "fake",
        "embedding_model": "deterministic-fixture",
        "embedding_revision": "1",
        "embedding_dimension": 4,
        "embedding_version": "test-embedding-v1",
        "retrieval_version": "test-retrieval-v1",
        "retrieval_top_k": 8,
        "retrieval_minimum_score": 0.5,
        "retrieval_minimum_evidence": 1,
        "generation_prompt_version": "test-generation-v1",
        "question_rewrite_prompt_version": "test-rewrite-v1",
        "evidence_assessment_prompt_version": "test-assessment-v1",
        "citation_repair_prompt_version": "test-repair-v1",
    }


def _summary(
    *,
    passed: int = 0,
    failed: int = 0,
    not_applicable: int = 0,
) -> dict[str, int]:
    return {
        "passed": passed,
        "failed": failed,
        "pending_review": 0,
        "not_applicable": not_applicable,
    }
