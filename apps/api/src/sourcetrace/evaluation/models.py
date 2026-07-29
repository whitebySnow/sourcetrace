from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetReview(StrictModel):
    status: Literal["reviewed", "fixture"]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    @model_validator(mode="after")
    def require_review_metadata(self) -> "DatasetReview":
        if self.status == "reviewed" and (
            self.reviewed_by is None
            or not self.reviewed_by.strip()
            or self.reviewed_at is None
            or self.reviewed_at.utcoffset() is None
        ):
            raise ValueError("reviewed datasets require reviewer identity and timezone-aware time")
        return self


class EvidenceReference(StrictModel):
    document_version_id: UUID
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class ExpectedResult(StrictModel):
    outcome: Literal["answered", "refused"]
    reference_answer: str | None = None
    evidence: list[EvidenceReference]

    @model_validator(mode="after")
    def require_consistent_ground_truth(self) -> "ExpectedResult":
        if self.outcome == "answered" and (
            self.reference_answer is None
            or not self.reference_answer.strip()
            or not self.evidence
        ):
            raise ValueError("answered cases require a reference answer and evidence")
        if self.outcome == "refused" and self.reference_answer is not None:
            raise ValueError("refused cases cannot include a reference answer")
        return self


class EvaluationCase(StrictModel):
    id: str = Field(min_length=1)
    category: Literal["direct", "multi_chunk", "unanswerable", "confusing"]
    question: str = Field(min_length=1)
    expected: ExpectedResult


class EvaluationDataset(StrictModel):
    schema_version: Literal["1"]
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    knowledge_base_id: UUID
    review: DatasetReview
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> "EvaluationDataset":
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case IDs must be unique")
        return self


class ObservedEvidence(StrictModel):
    document_version_id: UUID
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class EvaluationObservation(StrictModel):
    outcome: Literal["answered", "refused", "error"]
    answer: str | None
    retrieved_evidence: tuple[ObservedEvidence, ...]
    citations: tuple[ObservedEvidence, ...]


class EvaluationRunMetadata(StrictModel):
    code_commit: str = Field(min_length=1)
    model_provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    workflow_version: str = Field(min_length=1)
    chunking_version: str = Field(min_length=1)
    embedding_version: str = Field(min_length=1)
    retrieval_version: str = Field(min_length=1)


type EvaluationStatus = Literal["passed", "failed", "pending_review", "not_applicable"]
type HumanJudgment = Literal["passed", "failed"]


class CaseEvaluationResult(StrictModel):
    case_id: str
    retrieval: EvaluationStatus
    citation: EvaluationStatus
    refusal: EvaluationStatus
    end_to_end: EvaluationStatus
    observation: EvaluationObservation


class EvaluationSummary(StrictModel):
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pending_review: int = Field(ge=0)
    not_applicable: int = Field(ge=0)


class EvaluationReport(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str
    dataset_version: str
    metadata: EvaluationRunMetadata
    cases: list[CaseEvaluationResult]
    retrieval_summary: EvaluationSummary
    citation_summary: EvaluationSummary
    refusal_summary: EvaluationSummary
    end_to_end_summary: EvaluationSummary
