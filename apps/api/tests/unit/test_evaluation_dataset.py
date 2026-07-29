import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sourcetrace.evaluation import EvaluationDataset, load_dataset


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
                "review": {"status": "fixture"},
                "cases": [case, case],
            }
        )
