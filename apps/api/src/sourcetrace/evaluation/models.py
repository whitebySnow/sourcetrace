from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DatasetReview(StrictModel):
    status: Literal["reviewed", "fixture"]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    @field_validator("reviewed_at")
    @classmethod
    def normalize_review_time(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return value
        if value.utcoffset() is None:
            raise ValueError("review times must be timezone-aware UTC")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def require_review_metadata(self) -> "DatasetReview":
        if self.status == "reviewed" and (
            self.reviewed_by is None
            or not self.reviewed_by.strip()
            or self.reviewed_at is None
            or self.reviewed_at.utcoffset() is None
        ):
            raise ValueError("reviewed datasets require reviewer identity and timezone-aware time")
        if self.status == "fixture" and (
            self.reviewed_by is not None or self.reviewed_at is not None
        ):
            raise ValueError("evaluation fixtures cannot include review metadata")
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
            self.reference_answer is None or not self.reference_answer.strip() or not self.evidence
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
    document_version_ids: list[UUID] = Field(min_length=1)
    review: DatasetReview
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def require_unique_case_ids(self) -> "EvaluationDataset":
        case_ids = [case.id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("evaluation case IDs must be unique")
        if len(self.document_version_ids) != len(set(self.document_version_ids)):
            raise ValueError("document version IDs in the evaluation snapshot must be unique")
        snapshot = set(self.document_version_ids)
        evidence_versions = {
            reference.document_version_id
            for case in self.cases
            for reference in case.expected.evidence
        }
        if not evidence_versions <= snapshot:
            raise ValueError("evaluation evidence must belong to the document snapshot")
        return self


class ObservedEvidence(StrictModel):
    document_version_id: UUID
    page_number: int = Field(ge=1)
    text: str = Field(min_length=1)


class ObservedRetrievalCandidate(StrictModel):
    chunk_id: UUID
    document_version_id: UUID
    page_number: int = Field(ge=1)
    score: float = Field(ge=-1, le=1)


class ObservedRetrieval(StrictModel):
    query: str = Field(min_length=1)
    candidates: tuple[ObservedRetrievalCandidate, ...]


class ObservedEvidenceAssessment(StrictModel):
    sufficient: bool
    selected_chunk_ids: tuple[str, ...]
    supplemental_query: str | None


class EvaluationDecisionTrace(StrictModel):
    retrievals: tuple[ObservedRetrieval, ...]
    assessments: tuple[ObservedEvidenceAssessment, ...]
    supplemental_retrieval_attempts: int = Field(ge=0, le=1)
    citation_repair_attempts: int = Field(ge=0, le=1)


class EvaluationObservation(StrictModel):
    outcome: Literal["answered", "refused", "error"]
    answer: str | None
    retrieved_evidence: tuple[ObservedEvidence, ...]
    citations: tuple[ObservedEvidence, ...]
    decision_trace: EvaluationDecisionTrace | None = None

    @model_validator(mode="after")
    def require_consistent_outcome(self) -> "EvaluationObservation":
        if self.outcome == "answered" and (self.answer is None or not self.answer.strip()):
            raise ValueError("answered observations require an answer")
        if self.outcome != "answered" and (self.answer is not None or self.citations):
            raise ValueError("refused and error observations cannot include answers or citations")
        return self


class EvaluationRunMetadata(StrictModel):
    code_commit: str = Field(min_length=1)
    model_provider: str = Field(min_length=1)
    model_name: str = Field(min_length=1)
    workflow_version: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    tokenizer: str = Field(min_length=1)
    chunk_size: int = Field(gt=0)
    chunk_overlap: int = Field(ge=0)
    chunking_version: str = Field(min_length=1)
    embedding_provider: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    embedding_dimension: int = Field(gt=0)
    embedding_version: str = Field(min_length=1)
    retrieval_version: str = Field(min_length=1)
    retrieval_top_k: int = Field(gt=0)
    retrieval_minimum_score: float = Field(ge=-1, le=1)
    retrieval_minimum_evidence: int = Field(gt=0)
    generation_prompt_version: str = Field(min_length=1)
    question_rewrite_prompt_version: str = Field(min_length=1)
    evidence_assessment_prompt_version: str = Field(min_length=1)
    citation_repair_prompt_version: str = Field(min_length=1)


type EvaluationStatus = Literal["passed", "failed", "pending_review", "not_applicable"]
type HumanJudgment = Literal["passed", "failed"]


class CaseJudgment(StrictModel):
    case_id: str = Field(min_length=1)
    status: HumanJudgment


class EvaluationJudgmentSet(StrictModel):
    schema_version: Literal["1"]
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    review: DatasetReview
    judgments: list[CaseJudgment] = Field(min_length=1)

    @model_validator(mode="after")
    def require_reviewed_unique_judgments(self) -> "EvaluationJudgmentSet":
        if self.review.status != "reviewed":
            raise ValueError("end-to-end judgments require human review metadata")
        case_ids = [judgment.case_id for judgment in self.judgments]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("end-to-end judgment case IDs must be unique")
        return self

    def as_mapping(self) -> dict[str, HumanJudgment]:
        return {judgment.case_id: judgment.status for judgment in self.judgments}


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
    knowledge_base_id: UUID
    document_version_ids: list[UUID]
    metadata: EvaluationRunMetadata
    judgment_review: DatasetReview | None = None
    cases: list[CaseEvaluationResult]
    retrieval_summary: EvaluationSummary
    citation_summary: EvaluationSummary
    refusal_summary: EvaluationSummary
    end_to_end_summary: EvaluationSummary
