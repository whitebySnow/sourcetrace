from pathlib import Path

from sourcetrace.evaluation.models import EvaluationDataset


def load_dataset(path: Path) -> EvaluationDataset:
    return EvaluationDataset.model_validate_json(path.read_text(encoding="utf-8"))
