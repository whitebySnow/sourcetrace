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
    raw_rank: int | None = Field(default=None, gt=0)


class ObservedRetrieval(StrictModel):
    query: str = Field(min_length=1)
    candidates: tuple[ObservedRetrievalCandidate, ...]


class ObservedEvidenceAssessment(StrictModel):
    sufficient: bool
    selected_chunk_ids: tuple[str, ...]
    supplemental_query: str | None


class ObservedCitationValidation(StrictModel):
    valid: bool
    issue: Literal["empty_answer", "uncited_claim", "unknown_label", "valid"]


class ObservedQueryCandidateTrace(StrictModel):
    chunk_id: UUID
    raw_rank: int = Field(gt=0)
    raw_cosine_score: float = Field(ge=-1, le=1)
    reranker_score: float | None = None
    reranked_rank: int | None = Field(default=None, gt=0)
    selected_for_query_coverage: bool = False


class ObservedQueryRetrievalTrace(StrictModel):
    query: str = Field(min_length=1)
    candidates: tuple[ObservedQueryCandidateTrace, ...]


class ObservedFusedCandidateTrace(StrictModel):
    chunk_id: UUID
    fused_score: float = Field(gt=0)
    best_raw_cosine_score: float = Field(ge=-1, le=1)
    reranker_score: float | None = None
    reranked_rank: int | None = Field(default=None, gt=0)
    selected_as_primary: bool


class ObservedRerankerTrace(StrictModel):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    revision: str = Field(min_length=1)
    config_version: str = Field(min_length=1)


class ObservedRetrievalRoundTrace(StrictModel):
    round_number: int = Field(gt=0, le=2)
    queries: tuple[str, ...] = Field(min_length=1, max_length=3)
    query_results: tuple[ObservedQueryRetrievalTrace, ...]
    fused_candidates: tuple[ObservedFusedCandidateTrace, ...]
    final_evidence_chunk_ids: tuple[UUID, ...]
    rrf_rank_constant: int = Field(gt=0)
    reranker: ObservedRerankerTrace | None = None


class EvaluationDecisionTrace(StrictModel):
    retrievals: tuple[ObservedRetrieval, ...]
    retrieval_plan_version: str | None = None
    retrieval_rounds: tuple[ObservedRetrievalRoundTrace, ...] = ()
    assessments: tuple[ObservedEvidenceAssessment, ...]
    citation_validations: tuple[ObservedCitationValidation, ...]
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
    retrieval_page_neighbor_count: int = Field(default=0, ge=0)
    retrieval_rrf_rank_constant: int | None = Field(default=None, gt=0)
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


type RetrievalFailureMechanism = Literal[
    "query_rewrite_drift",
    "chunk_boundary_mismatch",
    "embedding_retrieval_weakness",
    "score_filtering",
]
type ExpectedEvidenceMatchStatus = Literal[
    "matched",
    "same_page_different_chunk",
    "not_retrieved",
]


class ExpectedEvidenceDiagnostic(StrictModel):
    document_version_id: UUID
    page_number: int = Field(ge=1)
    match_status: ExpectedEvidenceMatchStatus


class RetrievalCaseDiagnostic(StrictModel):
    case_id: str = Field(min_length=1)
    primary_mechanism: RetrievalFailureMechanism
    retrievals: tuple[ObservedRetrieval, ...]
    expected_evidence: tuple[ExpectedEvidenceDiagnostic, ...]


class RetrievalDiagnosticsReport(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    cases: tuple[RetrievalCaseDiagnostic, ...]


class HybridQueryPlanCase(StrictModel):
    case_id: str = Field(min_length=1)
    additional_queries: tuple[str, ...] = Field(max_length=2)


class HybridQueryPlanFixture(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    planner_version: str = Field(min_length=1)
    cases: tuple[HybridQueryPlanCase, ...]


class HybridRetrievalRunMetadata(StrictModel):
    code_commit: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    query_plan_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retrieval_version: str = Field(min_length=1)
    planner_version: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    chunking_version: str = Field(min_length=1)
    embedding_provider: str = Field(min_length=1)
    embedding_model: str = Field(min_length=1)
    embedding_revision: str = Field(min_length=1)
    embedding_version: str = Field(min_length=1)
    embedding_device: str = Field(min_length=1)
    reranker_provider: str = Field(min_length=1)
    reranker_model: str = Field(min_length=1)
    reranker_revision: str = Field(min_length=1)
    reranker_weight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reranker_version: str = Field(min_length=1)
    reranker_device: str = Field(min_length=1)
    lexical_version: Literal["postgres-english-or-phrase-v1"] = (
        "postgres-english-or-phrase-v1"
    )
    text_search_configuration: Literal["english"] = "english"
    phrase_weight: float = Field(ge=0)
    channel_rrf_rank_constant: int = Field(gt=0)
    channel_candidate_limit: int = Field(gt=0)
    retrieval_top_k: int = Field(gt=0, le=8)
    retrieval_minimum_score: float = Field(ge=-1, le=1)
    retrieval_page_neighbor_count: int = Field(ge=0)


class HybridCandidateTrace(StrictModel):
    chunk_id: UUID
    document_version_id: UUID
    page_number: int = Field(gt=0)
    dense_rank: int | None = Field(default=None, gt=0)
    lexical_rank: int | None = Field(default=None, gt=0)
    channel_fused_rank: int = Field(gt=0)
    cosine_score: float = Field(ge=-1, le=1)
    lexical_score: float | None = Field(default=None, ge=0)
    channel_fused_score: float = Field(gt=0)
    reranker_score: float
    reranked_rank: int = Field(gt=0)
    selected_for_query_coverage: bool
    selected_as_primary: bool


class HybridQueryTrace(StrictModel):
    query: str = Field(min_length=1)
    lexical_enabled: bool
    candidates: tuple[HybridCandidateTrace, ...]


class HybridRetrievalCaseResult(StrictModel):
    case_id: str = Field(min_length=1)
    queries: tuple[str, ...]
    baseline_retrieval: EvaluationStatus
    hybrid_retrieval: EvaluationStatus
    query_traces: tuple[HybridQueryTrace, ...]
    selected_primary_chunk_ids: tuple[UUID, ...]
    expanded_evidence_chunk_ids: tuple[UUID, ...]


class HybridRetrievalSummary(StrictModel):
    baseline_passed: int = Field(ge=0)
    hybrid_passed: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    improvements: tuple[str, ...]
    regressions: tuple[str, ...]


class HybridRetrievalEvaluationReport(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    knowledge_base_id: UUID
    document_version_ids: list[UUID]
    metadata: HybridRetrievalRunMetadata
    cases: tuple[HybridRetrievalCaseResult, ...]
    summary: HybridRetrievalSummary


class RerankerRunMetadata(StrictModel):
    code_commit: str = Field(min_length=1)
    source_report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    model_name: str = Field(min_length=1)
    model_revision: str = Field(min_length=1)
    model_weight_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    device: str = Field(min_length=1)
    device_name: str = Field(min_length=1)
    torch_version: str = Field(min_length=1)
    batch_size: int = Field(gt=0)
    candidate_pool_version: Literal["final-fused-candidates-v1"] = "final-fused-candidates-v1"
    selection_version: Literal["cross-encoder-page-diverse-v1"] = "cross-encoder-page-diverse-v1"
    model_load_ms: float = Field(ge=0)
    total_rerank_ms: float = Field(ge=0)
    peak_vram_mib: float | None = Field(default=None, ge=0)


class RerankedCandidateTrace(StrictModel):
    chunk_id: UUID
    document_version_id: UUID
    page_number: int = Field(ge=1)
    baseline_rank: int = Field(gt=0)
    reranked_rank: int = Field(gt=0)
    baseline_selected: bool
    reranked_selected: bool
    fused_score: float = Field(gt=0)
    best_raw_cosine_score: float = Field(ge=-1, le=1)
    reranker_score: float


class RerankerCaseResult(StrictModel):
    case_id: str = Field(min_length=1)
    baseline_retrieval: EvaluationStatus
    reranked_retrieval: EvaluationStatus
    rerank_ms: float = Field(ge=0)
    candidates: tuple[RerankedCandidateTrace, ...]
    selected_primary_chunk_ids: tuple[UUID, ...]
    expanded_evidence_chunk_ids: tuple[UUID, ...]


class RerankerEvaluationSummary(StrictModel):
    baseline_passed: int = Field(ge=0)
    reranked_passed: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    improvements: tuple[str, ...]
    regressions: tuple[str, ...]


class RerankerEvaluationReport(StrictModel):
    schema_version: Literal["1"] = "1"
    dataset_id: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    knowledge_base_id: UUID
    document_version_ids: list[UUID]
    metadata: RerankerRunMetadata
    cases: tuple[RerankerCaseResult, ...]
    summary: RerankerEvaluationSummary
