from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from sourcetrace.evaluation.models import (
    EvaluationCase,
    EvaluationDataset,
    EvaluationObservation,
    StrictModel,
)


class FixtureObservation(EvaluationObservation):
    case_id: str = Field(min_length=1)

    def as_observation(self) -> EvaluationObservation:
        return EvaluationObservation.model_validate(
            self.model_dump(exclude={"case_id"})
        )


class FixtureObservationSet(StrictModel):
    schema_version: Literal["1"]
    dataset_id: str
    dataset_version: str
    observations: list[FixtureObservation] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> "FixtureObservationSet":
        case_ids = [item.case_id for item in self.observations]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("fixture observation case IDs must be unique")
        return self


class FixtureEvaluationSubject:
    def __init__(
        self,
        dataset: EvaluationDataset,
        observations: FixtureObservationSet,
    ) -> None:
        if (
            observations.dataset_id != dataset.dataset_id
            or observations.dataset_version != dataset.dataset_version
        ):
            raise ValueError("fixture observations must match the dataset version")
        expected_ids = {case.id for case in dataset.cases}
        observed_ids = {item.case_id for item in observations.observations}
        if observed_ids != expected_ids:
            raise ValueError("fixture observations must cover every dataset case exactly once")
        self._observations = {
            item.case_id: item.as_observation() for item in observations.observations
        }

    async def evaluate(self, case: EvaluationCase) -> EvaluationObservation:
        return self._observations[case.id]


def load_fixture_observations(path: Path) -> FixtureObservationSet:
    return FixtureObservationSet.model_validate_json(path.read_text(encoding="utf-8"))
