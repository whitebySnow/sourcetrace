from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AnswerRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def normalize_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("question content must not be blank")
        return normalized


class AnswerStatusEvent(BaseModel):
    version: Literal["1"] = "1"
    type: Literal["status"] = "status"
    run_id: UUID
    status: Literal["retrieving", "generating"]


class AnswerDeltaEvent(BaseModel):
    version: Literal["1"] = "1"
    type: Literal["delta"] = "delta"
    run_id: UUID
    delta: str


class CitationResponse(BaseModel):
    id: str
    document_id: str
    document_version_id: str
    document_name: str
    page_number: int
    excerpt: str
    source_url: str


class AnswerFinalEvent(BaseModel):
    version: Literal["1"] = "1"
    type: Literal["final"] = "final"
    run_id: UUID
    answer: str
    citations: list[CitationResponse]


class AnswerRefusalEvent(BaseModel):
    version: Literal["1"] = "1"
    type: Literal["refusal"] = "refusal"
    run_id: UUID
    code: str
    message: str


class AnswerErrorEvent(BaseModel):
    version: Literal["1"] = "1"
    type: Literal["error"] = "error"
    run_id: UUID
    code: str
    message: str


class AnswerCancelledEvent(BaseModel):
    version: Literal["1"] = "1"
    type: Literal["cancelled"] = "cancelled"
    run_id: UUID


type AnswerEvent = Annotated[
    AnswerStatusEvent
    | AnswerDeltaEvent
    | AnswerFinalEvent
    | AnswerRefusalEvent
    | AnswerErrorEvent
    | AnswerCancelledEvent,
    Field(discriminator="type"),
]


class AnswerCancellationResponse(BaseModel):
    run_id: UUID
    status: Literal[
        "pending",
        "running",
        "cancel_requested",
        "cancelled",
        "completed",
        "failed",
    ]


class WorkflowEvidenceAssessmentTrace(BaseModel):
    sufficient: bool
    selected_chunk_ids: list[str]
    supplemental_queries: list[str] = Field(max_length=2)


class WorkflowRetrievalCandidateTrace(BaseModel):
    chunk_id: str
    raw_rank: int
    raw_cosine_score: float
    dense_rank: int | None = None
    lexical_rank: int | None = None
    dense_score: float | None = None
    lexical_score: float | None = None
    channel_fused_rank: int | None = None
    channel_fused_score: float | None = None
    reranker_score: float | None = None
    reranked_rank: int | None = None
    selected_for_query_coverage: bool = False


class WorkflowQueryRetrievalTrace(BaseModel):
    query: str
    candidates: list[WorkflowRetrievalCandidateTrace]


class WorkflowFusedCandidateTrace(BaseModel):
    chunk_id: str
    fused_score: float
    best_raw_cosine_score: float
    reranker_score: float | None = None
    reranked_rank: int | None = None
    selected_as_primary: bool


class WorkflowRerankerTrace(BaseModel):
    provider: str
    model: str
    revision: str
    config_version: str


class WorkflowRetrievalRoundTrace(BaseModel):
    round_number: int
    queries: list[str]
    query_results: list[WorkflowQueryRetrievalTrace]
    fused_candidates: list[WorkflowFusedCandidateTrace]
    final_evidence_chunk_ids: list[str]
    rrf_rank_constant: int
    reranker: WorkflowRerankerTrace | None = None


class WorkflowCitationValidationTrace(BaseModel):
    attempt: Literal["initial", "repair"] | None = None
    valid: bool
    issue: Literal["empty_answer", "uncited_claim", "unknown_label", "valid"]
    unit_count: int = Field(default=0, ge=0)
    citation_count: int = Field(default=0, ge=0)
    uncited_unit_indices: list[int] = Field(default_factory=list)
    unknown_label_unit_indices: list[int] = Field(default_factory=list)


class WorkflowPlanningSlotTrace(BaseModel):
    title_anchor: str = Field(min_length=3, max_length=255)
    refinement_disposition: Literal[
        "not_required",
        "accepted",
        "provider_error",
        "invalid_shape",
        "document_changed",
        "anchor_changed",
        "anchor_invalid",
        "unchanged_query",
        "title_attribution",
    ]


class WorkflowPlanningTrace(BaseModel):
    initial_disposition: Literal["empty", "accepted", "rejected"]
    initial_correction_applied: bool
    initial_slot_count: int = Field(ge=0, le=3)
    selected_slots: list[WorkflowPlanningSlotTrace] = Field(max_length=2)


class AnswerWorkflowTrace(BaseModel):
    retrieval_plan_version: str | None = None
    planning: WorkflowPlanningTrace | None = None
    retrieval_queries: list[str] = Field(default_factory=list)
    retrieval_rounds: list[WorkflowRetrievalRoundTrace] = Field(default_factory=list)
    assessments: list[WorkflowEvidenceAssessmentTrace] = Field(default_factory=list)
    citation_validations: list[WorkflowCitationValidationTrace] = Field(default_factory=list)
    supplemental_retrieval_attempts: int = 0
    citation_repair_attempts: int = 0


class AnswerHistoryItem(BaseModel):
    id: UUID
    question_id: UUID
    question_content: str
    status: Literal[
        "pending",
        "running",
        "cancel_requested",
        "cancelled",
        "completed",
        "failed",
    ]
    outcome: Literal["answered", "refused"] | None
    answer: str | None
    refusal_code: str | None
    refusal_message: str | None
    failure_code: str | None
    failure_message: str | None
    llm_provider: str
    llm_model: str
    prompt_version: str
    retrieval_version: str
    retrieval_query: str
    query_rewrite_version: str
    evidence_assessment_prompt_version: str
    citation_repair_prompt_version: str
    workflow_version: str
    provider_connect_timeout_seconds: float | None
    provider_read_timeout_seconds: float | None
    provider_request_timeout_seconds: float | None
    provider_operation_deadline_seconds: float | None
    workflow_trace: AnswerWorkflowTrace
    created_at: datetime
    completed_at: datetime | None
    citations: list[CitationResponse]


class AnswerHistoryResponse(BaseModel):
    items: list[AnswerHistoryItem]
    next_cursor: str | None
