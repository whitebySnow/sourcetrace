import json
from pathlib import Path

from sourcetrace.evaluation.fixtures import FixtureObservationSet
from sourcetrace.evaluation.models import (
    EvaluationDataset,
    EvaluationJudgmentSet,
    EvaluationReport,
)


def main() -> None:
    output_dir = Path(__file__).resolve().parents[3] / "evals" / "schema"
    output_dir.mkdir(parents=True, exist_ok=True)
    schemas = {
        "dataset-v1.schema.json": EvaluationDataset.model_json_schema(),
        "fixture-observations-v1.schema.json": FixtureObservationSet.model_json_schema(),
        "judgments-v1.schema.json": EvaluationJudgmentSet.model_json_schema(),
        "report-v1.schema.json": EvaluationReport.model_json_schema(),
    }
    for filename, schema in schemas.items():
        (output_dir / filename).write_text(
            json.dumps(schema, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
