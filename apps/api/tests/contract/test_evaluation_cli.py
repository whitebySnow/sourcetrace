import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest

from sourcetrace.evaluation.cli import main
from sourcetrace.evaluation.models import RerankerEvaluationReport


def test_fake_cli_writes_a_versioned_report_without_external_providers(tmp_path) -> None:
    knowledge_base_id = uuid4()
    document_version_id = uuid4()
    dataset_path = tmp_path / "dataset.json"
    observations_path = tmp_path / "observations.json"
    metadata_path = tmp_path / "metadata.json"
    judgments_path = tmp_path / "judgments.json"
    output_path = tmp_path / "reports" / "report.json"
    reviewed_output_path = tmp_path / "reports" / "reviewed-report.json"
    diagnostics_output_path = tmp_path / "reports" / "retrieval-diagnostics.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dataset_id": "cli-fixture",
                "dataset_version": "1.0.0",
                "knowledge_base_id": str(knowledge_base_id),
                "document_version_ids": [str(document_version_id)],
                "review": {"status": "fixture"},
                "cases": [
                    {
                        "id": "direct-001",
                        "category": "direct",
                        "question": "How are vectors stored?",
                        "expected": {
                            "outcome": "answered",
                            "reference_answer": "They are normalized.",
                            "evidence": [
                                {
                                    "document_version_id": str(document_version_id),
                                    "page_number": 4,
                                    "text": "Vectors are normalized.",
                                }
                            ],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    observations_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dataset_id": "cli-fixture",
                "dataset_version": "1.0.0",
                "observations": [
                    {
                        "case_id": "direct-001",
                        "outcome": "answered",
                        "answer": "They are normalized.",
                        "retrieved_evidence": [
                            {
                                "document_version_id": str(document_version_id),
                                "page_number": 4,
                                "text": "Vectors are normalized.",
                            }
                        ],
                        "citations": [
                            {
                                "document_version_id": str(document_version_id),
                                "page_number": 4,
                                "text": "Vectors are normalized.",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    metadata_path.write_text(
        json.dumps(
            {
                "code_commit": "test-commit",
                "model_provider": "fake",
                "model_name": "deterministic-fixture-v1",
                "workflow_version": "langgraph-bounded-v1",
                "parser_version": "fake-parser-v1",
                "tokenizer": "cl100k_base",
                "chunk_size": 500,
                "chunk_overlap": 80,
                "chunking_version": "token-window-v1",
                "embedding_provider": "fake",
                "embedding_model": "deterministic-fixture",
                "embedding_revision": "1",
                "embedding_dimension": 4,
                "embedding_version": "bge-m3-dense-v1",
                "retrieval_version": "pgvector-cosine-v1",
                "retrieval_top_k": 8,
                "retrieval_minimum_score": 0.5,
                "retrieval_minimum_evidence": 1,
                "generation_prompt_version": "grounded-answer-v1",
                "question_rewrite_prompt_version": "follow-up-query-v1",
                "evidence_assessment_prompt_version": "evidence-assessment-v1",
                "citation_repair_prompt_version": "citation-repair-v1",
            }
        ),
        encoding="utf-8",
    )
    exit_code = main(
        [
            "fake",
            "--dataset",
            str(dataset_path),
            "--observations",
            str(observations_path),
            "--metadata",
            str(metadata_path),
            "--output",
            str(output_path),
        ]
    )
    report_sha256 = hashlib.sha256(output_path.read_bytes()).hexdigest()
    judgments_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dataset_id": "cli-fixture",
                "dataset_version": "1.0.0",
                "report_sha256": report_sha256,
                "review": {
                    "status": "reviewed",
                    "reviewed_by": "project-owner",
                    "reviewed_at": "2026-07-29T03:00:00Z",
                },
                "judgments": [{"case_id": "direct-001", "status": "passed"}],
            }
        ),
        encoding="utf-8",
    )

    review_exit_code = main(
        [
            "review",
            "--report",
            str(output_path),
            "--judgments",
            str(judgments_path),
            "--output",
            str(reviewed_output_path),
        ]
    )
    diagnostics_exit_code = main(
        [
            "diagnose-retrieval",
            "--dataset",
            str(dataset_path),
            "--report",
            str(output_path),
            "--output",
            str(diagnostics_output_path),
        ]
    )

    report = json.loads(reviewed_output_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert review_exit_code == 0
    assert diagnostics_exit_code == 0
    assert report["dataset_id"] == "cli-fixture"
    assert report["metadata"]["code_commit"] == "test-commit"
    assert report["retrieval_summary"]["passed"] == 1
    assert report["end_to_end_summary"]["passed"] == 1
    assert report["judgment_review"]["reviewed_by"] == "project-owner"
    assert diagnostics["dataset_id"] == "cli-fixture"
    assert diagnostics["cases"] == []


def test_real_cli_requires_explicit_provider_confirmation() -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "real",
                "--dataset",
                "reviewed-dataset.json",
                "--code-commit",
                "abc123",
                "--output",
                "report.json",
            ]
        )

    assert error.value.code == 2


def test_rerank_cli_requires_explicit_local_model_confirmation() -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "rerank",
                "--dataset",
                "reviewed-dataset.json",
                "--report",
                "baseline-report.json",
                "--model",
                "local-reranker",
                "--model-revision",
                "revision",
                "--model-weight-sha256",
                "a" * 64,
                "--code-commit",
                "abc123",
                "--output",
                "reranker-report.json",
            ]
        )

    assert error.value.code == 2


def test_repository_reranker_report_schema_matches_model() -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads(
        (root / "evals/schema/reranker-report-v1.schema.json").read_text(encoding="utf-8")
    )

    assert schema == RerankerEvaluationReport.model_json_schema()


def test_repository_fixture_replays_all_four_categories(tmp_path) -> None:
    root = Path(__file__).resolve().parents[4]
    output_path = tmp_path / "fixture-report.json"

    exit_code = main(
        [
            "fake",
            "--dataset",
            str(root / "evals/fixtures/dataset-v1.json"),
            "--observations",
            str(root / "evals/fixtures/observations-v1.json"),
            "--metadata",
            str(root / "evals/fixtures/metadata-v1.json"),
            "--output",
            str(output_path),
        ]
    )

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert [case["case_id"] for case in report["cases"]] == [
        "direct-001",
        "multi-chunk-001",
        "unanswerable-001",
        "confusing-001",
    ]
    assert report["cases"][3]["citation"] == "failed"
