import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from sourcetrace.evaluation.cli import main
from sourcetrace.evaluation.models import (
    CitationDiagnosticsReport,
    EvaluationFailureReport,
    EvidenceAssessmentDiagnosticsReport,
    HybridQueryPlanFixture,
    HybridRetrievalEvaluationReport,
    ObservedPlanningTrace,
    RerankerEvaluationReport,
    RetrievalStageDiagnosticsReport,
)
from sourcetrace.evaluation.real import RealEvaluationFailure


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
    citation_diagnostics_output_path = tmp_path / "reports" / "citation-diagnostics.json"
    assessment_diagnostics_output_path = tmp_path / "reports" / "assessment-diagnostics.json"
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
    citation_diagnostics_exit_code = main(
        [
            "diagnose-citations",
            "--dataset",
            str(dataset_path),
            "--report",
            str(output_path),
            "--output",
            str(citation_diagnostics_output_path),
        ]
    )
    assessment_diagnostics_exit_code = main(
        [
            "diagnose-assessments",
            "--dataset",
            str(dataset_path),
            "--report",
            str(output_path),
            "--output",
            str(assessment_diagnostics_output_path),
        ]
    )

    report = json.loads(reviewed_output_path.read_text(encoding="utf-8"))
    diagnostics = json.loads(diagnostics_output_path.read_text(encoding="utf-8"))
    citation_diagnostics = json.loads(citation_diagnostics_output_path.read_text(encoding="utf-8"))
    assessment_diagnostics = json.loads(
        assessment_diagnostics_output_path.read_text(encoding="utf-8")
    )
    assert exit_code == 0
    assert review_exit_code == 0
    assert diagnostics_exit_code == 0
    assert citation_diagnostics_exit_code == 0
    assert assessment_diagnostics_exit_code == 0
    assert report["dataset_id"] == "cli-fixture"
    assert report["metadata"]["code_commit"] == "test-commit"
    assert report["retrieval_summary"]["passed"] == 1
    assert report["end_to_end_summary"]["passed"] == 1
    assert report["judgment_review"]["reviewed_by"] == "project-owner"
    assert report["cases"][0]["observation"]["retrieved_evidence"][0]["chunk_id"] is None
    assert diagnostics["dataset_id"] == "cli-fixture"
    assert diagnostics["cases"] == []
    assert citation_diagnostics["report_sha256"] == report_sha256
    assert citation_diagnostics["summary"]["failed_answered_cases"] == 0
    assert citation_diagnostics["cases"] == []
    assert assessment_diagnostics["report_sha256"] == report_sha256
    assert assessment_diagnostics["summary"]["failed_answerable_refusals"] == 0
    assert assessment_diagnostics["cases"] == []


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


def test_real_cli_writes_an_unscored_failure_artifact(tmp_path, monkeypatch) -> None:
    knowledge_base_id = uuid4()
    document_version_id = uuid4()
    dataset_path = tmp_path / "dataset.json"
    output_path = tmp_path / "reports" / "report.json"
    dataset_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "dataset_id": "reviewed-fixture",
                "dataset_version": "1.0.0",
                "knowledge_base_id": str(knowledge_base_id),
                "document_version_ids": [str(document_version_id)],
                "review": {
                    "status": "reviewed",
                    "reviewed_by": "project-owner",
                    "reviewed_at": "2026-08-15T00:00:00Z",
                },
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

    async def fail_real_evaluation(*args, **kwargs):
        raise RealEvaluationFailure(
            EvaluationFailureReport(
                dataset_id="reviewed-fixture",
                dataset_version="1.0.0",
                knowledge_base_id=knowledge_base_id,
                document_version_ids=[document_version_id],
                metadata=None,
                failed_case_id="direct-001",
                phase="assessing",
                error_code="LLM_INVALID_RESPONSE",
                error_reason="provider_structured_invalid_json",
                planning=ObservedPlanningTrace(
                    initial_disposition="failed",
                    initial_correction_applied=True,
                    initial_slot_count=0,
                    selected_slots=(),
                ),
            )
        )

    monkeypatch.setattr(
        "sourcetrace.evaluation.real.run_real_evaluation",
        fail_real_evaluation,
    )

    exit_code = main(
        [
            "real",
            "--dataset",
            str(dataset_path),
            "--code-commit",
            "test-commit",
            "--output",
            str(output_path),
            "--confirm-real-provider",
        ]
    )

    failure_path = output_path.with_name("report-failure.json")
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert output_path.exists() is False
    assert failure["failed_case_id"] == "direct-001"
    assert failure["phase"] == "assessing"
    assert failure["error_code"] == "LLM_INVALID_RESPONSE"
    assert failure["error_reason"] == "provider_structured_invalid_json"
    assert failure["planning"] == {
        "initial_disposition": "failed",
        "initial_correction_applied": True,
        "initial_slot_count": 0,
        "selected_slots": [],
    }
    assert "cases" not in failure
    assert "question" not in failure
    assert "answer" not in failure
    with pytest.raises(ValidationError):
        main(
            [
                "review",
                "--report",
                str(failure_path),
                "--judgments",
                str(tmp_path / "judgments.json"),
                "--output",
                str(tmp_path / "reviewed.json"),
            ]
        )
    for mode in (
        "diagnose-retrieval",
        "diagnose-retrieval-stages",
        "diagnose-citations",
        "diagnose-assessments",
    ):
        arguments = [
            mode,
            "--dataset",
            str(dataset_path),
            "--report",
            str(failure_path),
            "--output",
            str(tmp_path / f"{mode}.json"),
        ]
        if mode == "diagnose-retrieval-stages":
            arguments.extend(["--stage-report", str(tmp_path / "stages.json")])
        with pytest.raises(ValidationError):
            main(arguments)


@pytest.mark.parametrize("existing_filename", ["report.json", "report-failure.json"])
def test_real_cli_refuses_to_overwrite_a_previous_evaluation_artifact(
    tmp_path,
    monkeypatch,
    existing_filename: str,
) -> None:
    output_path = tmp_path / "reports" / "report.json"
    output_path.parent.mkdir(parents=True)
    (output_path.parent / existing_filename).write_text("previous artifact", encoding="utf-8")
    invoked = False

    async def unexpected_real_evaluation(*args, **kwargs):
        nonlocal invoked
        invoked = True
        raise AssertionError("existing output must fail before a real provider invocation")

    monkeypatch.setattr(
        "sourcetrace.evaluation.real.run_real_evaluation",
        unexpected_real_evaluation,
    )

    with pytest.raises(FileExistsError, match="output or failure artifact already exists"):
        main(
            [
                "real",
                "--dataset",
                str(tmp_path / "unused-dataset.json"),
                "--code-commit",
                "test-commit",
                "--output",
                str(output_path),
                "--confirm-real-provider",
            ]
        )

    assert invoked is False


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


def test_hybrid_retrieval_cli_requires_explicit_local_model_confirmation() -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "hybrid-retrieval",
                "--dataset",
                "reviewed-dataset.json",
                "--query-plan",
                "query-plan.json",
                "--code-commit",
                "abc123",
                "--output",
                "hybrid-report.json",
            ]
        )

    assert error.value.code == 2


def test_repository_reranker_report_schema_matches_model() -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads(
        (root / "evals/schema/reranker-report-v1.schema.json").read_text(encoding="utf-8")
    )

    assert schema == RerankerEvaluationReport.model_json_schema()


def test_repository_citation_diagnostics_schema_matches_model() -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads(
        (root / "evals/schema/citation-diagnostics-v2.schema.json").read_text(encoding="utf-8")
    )

    assert schema == CitationDiagnosticsReport.model_json_schema()


def test_repository_evidence_assessment_diagnostics_schema_matches_model() -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads(
        (root / "evals/schema/evidence-assessment-diagnostics-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert schema == EvidenceAssessmentDiagnosticsReport.model_json_schema()


def test_repository_failure_report_schema_matches_model() -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads(
        (root / "evals/schema/failure-report-v1.schema.json").read_text(encoding="utf-8")
    )

    assert schema == EvaluationFailureReport.model_json_schema()


@pytest.mark.parametrize(
    ("filename", "model"),
    [
        ("hybrid-query-plan-v1.schema.json", HybridQueryPlanFixture),
        ("hybrid-retrieval-report-v2.schema.json", HybridRetrievalEvaluationReport),
    ],
)
def test_repository_hybrid_evaluation_schemas_match_models(
    filename: str,
    model: type[HybridQueryPlanFixture] | type[HybridRetrievalEvaluationReport],
) -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads((root / "evals/schema" / filename).read_text(encoding="utf-8"))

    assert schema == model.model_json_schema()


def test_repository_retrieval_stage_diagnostics_schema_matches_model() -> None:
    root = Path(__file__).resolve().parents[4]
    schema = json.loads(
        (root / "evals/schema/retrieval-stage-diagnostics-v1.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert schema == RetrievalStageDiagnosticsReport.model_json_schema()


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
