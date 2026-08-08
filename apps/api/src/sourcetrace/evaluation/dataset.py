from pathlib import Path

from sourcetrace.evaluation.models import (
    EvaluationDataset,
    EvaluationJudgmentSet,
    EvaluationReport,
    HybridQueryPlanFixture,
)


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))


def load_judgments(path: Path) -> EvaluationJudgmentSet:
    return EvaluationJudgmentSet.model_validate_json(path.read_text(encoding="utf-8"))


def load_report(path: Path) -> EvaluationReport:
    return EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_hybrid_query_plan(path: Path) -> HybridQueryPlanFixture:
    return HybridQueryPlanFixture.model_validate_json(path.read_text(encoding="utf-8"))
