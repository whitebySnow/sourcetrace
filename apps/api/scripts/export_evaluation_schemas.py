import json
from pathlib import Path

from sourcetrace.evaluation.fixtures import FixtureObservationSet
from sourcetrace.evaluation.models import (
    CitationDiagnosticsReport,
    EvaluationDataset,
    EvaluationFailureReport,
    EvaluationJudgmentSet,
    EvaluationReport,
    HybridQueryPlanFixture,
    HybridRetrievalEvaluationReport,
    RerankerEvaluationReport,
    RetrievalDiagnosticsReport,
)


def main() -> None:
    output_dir = Path(__file__).resolve().parents[3] / "evals" / "schema"
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas = {
        "citation-diagnostics-v1.schema.json": CitationDiagnosticsReport.model_json_schema(),
        "dataset-v1.schema.json": EvaluationDataset.model_json_schema(),
        "failure-report-v1.schema.json": EvaluationFailureReport.model_json_schema(),
        "fixture-observations-v1.schema.json": FixtureObservationSet.model_json_schema(),
        "hybrid-query-plan-v1.schema.json": HybridQueryPlanFixture.model_json_schema(),
        "hybrid-retrieval-report-v1.schema.json": (
            HybridRetrievalEvaluationReport.model_json_schema()
        ),
        "judgments-v1.schema.json": EvaluationJudgmentSet.model_json_schema(),
        "report-v1.schema.json": EvaluationReport.model_json_schema(),
        "reranker-report-v1.schema.json": RerankerEvaluationReport.model_json_schema(),
        "retrieval-diagnostics-v1.schema.json": RetrievalDiagnosticsReport.model_json_schema(),
    }
    for filename, schema in schemas.items():
        (output_dir / filename).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
