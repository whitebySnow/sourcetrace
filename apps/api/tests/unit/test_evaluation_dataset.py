import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sourcetrace.evaluation import EvaluationDataset, EvaluationObservation, load_dataset

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_reviewed_agentic_rag_dataset_is_versioned_and_complete() -> None:
    dataset = load_dataset(
        REPOSITORY_ROOT / "evals" / "datasets" / "agentic-rag-foundations-v1.json"
    )

    assert dataset.dataset_id == "agentic-rag-foundations"
    assert dataset.dataset_version == "1.0.0"
    assert dataset.review.status == "reviewed"
    assert len(dataset.document_version_ids) == 3
    assert len(dataset.cases) == 30
    assert {case.category for case in dataset.cases} == {
        "direct",
        "multi_chunk",
        "unanswerable",
        "confusing",
    }
    refusal_cases = [case for case in dataset.cases if case.expected.outcome == "refused"]
    assert len(refusal_cases) == 3
    assert all(not case.expected.evidence for case in refusal_cases)


def test_reviewed_dataset_loads_with_versioned_ground_truth(tmp_path) -> None:
    knowledge_base_id = uuid4()
    document_version_id = uuid4()
    dataset_path = tmp_path / "reviewed-dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dataset_id": "sourcetrace-mvp",
                "dataset_version": "1.0.0",
                "knowledge_base_id": str(knowledge_base_id),
                "document_version_ids": [str(document_version_id)],
                "review": {
                    "status": "reviewed",
                    "reviewed_by": "project-owner",
                    "reviewed_at": "2026-07-29T02:00:00Z",
                },
                "cases": [
                    {
                        "id": "direct-001",
                        "category": "direct",
                        "question": "How are dense vectors stored?",
                        "expected": {
                            "outcome": "answered",
                            "reference_answer": "Dense vectors are normalized before storage.",
                            "evidence": [
                                {
                                    "document_version_id": str(document_version_id),
                                    "page_number": 4,
                                    "text": "Vectors are normalized before storage.",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dataset = load_dataset(dataset_path)

    assert dataset.dataset_id == "sourcetrace-mvp"
    assert dataset.dataset_version == "1.0.0"
    assert dataset.knowledge_base_id == knowledge_base_id
    assert dataset.document_version_ids == [document_version_id]
    assert dataset.review.reviewed_at == datetime(2026, 7, 29, 2, 0, tzinfo=UTC)
    assert dataset.cases[0].expected.evidence[0].document_version_id == document_version_id


def test_reviewed_dataset_requires_reviewer_identity_and_time(tmp_path) -> None:
    dataset_path = tmp_path / "unreviewed-dataset.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dataset_id": "sourcetrace-mvp",
                "dataset_version": "1.0.0",
                "knowledge_base_id": str(uuid4()),
                "document_version_ids": [str(uuid4())],
                "review": {"status": "reviewed"},
                "cases": [
                    {
                        "id": "unanswerable-001",
                        "category": "unanswerable",
                        "question": "What is not covered?",
                        "expected": {
                            "outcome": "refused",
                            "reference_answer": None,
                            "evidence": [],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_dataset(dataset_path)


@pytest.mark.parametrize(
    ("outcome", "reference_answer", "evidence"),
    [
        ("answered", None, []),
        (
            "refused",
            "This answer must not exist for a refusal case.",
            [],
        ),
    ],
)
def test_expected_outcome_requires_consistent_ground_truth(
    tmp_path,
    outcome: str,
    reference_answer: str | None,
    evidence: list[dict[str, object]],
) -> None:
    dataset_path = tmp_path / "inconsistent-ground-truth.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dataset_id": "sourcetrace-mvp",
                "dataset_version": "1.0.0",
                "knowledge_base_id": str(uuid4()),
                "document_version_ids": [str(uuid4())],
                "review": {"status": "fixture"},
                "cases": [
                    {
                        "id": "case-001",
                        "category": "direct",
                        "question": "What does the source say?",
                        "expected": {
                            "outcome": outcome,
                            "reference_answer": reference_answer,
                            "evidence": evidence,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_dataset(dataset_path)


def test_dataset_rejects_duplicate_case_ids() -> None:
    case = {
        "id": "duplicate-001",
        "category": "unanswerable",
        "question": "What is outside the source?",
        "expected": {
            "outcome": "refused",
            "reference_answer": None,
            "evidence": [],
        },
    }

    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(
            {
                "schema_version": "1",
                "dataset_id": "duplicate-fixture",
                "dataset_version": "1.0.0",
                "knowledge_base_id": uuid4(),
                "document_version_ids": [uuid4()],
                "review": {"status": "fixture"},
                "cases": [case, case],
            }
        )


def test_review_time_is_normalized_to_utc() -> None:
    dataset = EvaluationDataset.model_validate(
        {
            "schema_version": "1",
            "dataset_id": "review-time",
            "dataset_version": "1.0.0",
            "knowledge_base_id": uuid4(),
            "document_version_ids": [uuid4()],
            "review": {
                "status": "reviewed",
                "reviewed_by": "project-owner",
                "reviewed_at": "2026-07-29T10:00:00+08:00",
            },
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

    assert dataset.review.reviewed_at == datetime(2026, 7, 29, 2, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "review",
    [
        {
            "status": "fixture",
            "reviewed_at": "2026-07-29T03:00:00",
        },
        {
            "status": "fixture",
            "reviewed_by": "not-a-real-review",
            "reviewed_at": "2026-07-29T03:00:00Z",
        },
    ],
)
def test_fixture_rejects_review_metadata(review: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(
            {
                "schema_version": "1",
                "dataset_id": "fixture-review-boundary",
                "dataset_version": "1.0.0",
                "knowledge_base_id": uuid4(),
                "document_version_ids": [uuid4()],
                "review": review,
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


def test_dataset_rejects_evidence_outside_document_snapshot() -> None:
    with pytest.raises(ValidationError):
        EvaluationDataset.model_validate(
            {
                "schema_version": "1",
                "dataset_id": "invalid-snapshot",
                "dataset_version": "1.0.0",
                "knowledge_base_id": uuid4(),
                "document_version_ids": [uuid4()],
                "review": {"status": "fixture"},
                "cases": [
                    {
                        "id": "direct-001",
                        "category": "direct",
                        "question": "What does the source say?",
                        "expected": {
                            "outcome": "answered",
                            "reference_answer": "A fact.",
                            "evidence": [
                                {
                                    "document_version_id": uuid4(),
                                    "page_number": 1,
                                    "text": "A fact.",
                                }
                            ],
                        },
                    }
                ],
            }
        )


@pytest.mark.parametrize(
    "observation",
    [
        {
            "outcome": "answered",
            "answer": None,
            "retrieved_evidence": [],
            "citations": [],
        },
        {
            "outcome": "refused",
            "answer": "Contradictory answer",
            "retrieved_evidence": [],
            "citations": [],
        },
        {
            "outcome": "refused",
            "answer": None,
            "retrieved_evidence": [],
            "citations": [
                {
                    "document_version_id": uuid4(),
                    "page_number": 1,
                    "text": "Contradictory citation",
                }
            ],
        },
    ],
)
def test_observation_rejects_contradictory_outcomes(
    observation: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        EvaluationObservation.model_validate(observation)
