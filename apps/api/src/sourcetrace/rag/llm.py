import asyncio
import json
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from sourcetrace.rag.answer_text import split_answer_units
from sourcetrace.rag.language import (
    answer_language_instruction,
    answer_matches_question_language,
)
from sourcetrace.rag.ports import (
    CitationValidationFeedback,
    ClaimSupportDecision,
    ClaimSupportValidationError,
    EvidenceDecision,
    GroundedClaim,
    RetrievalCandidate,
    RetrievalPlanProposal,
)

_UUID_CITATION_LABEL = re.compile(
    r"\[[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\]"
)
_TRANSIENT_HTTP_STATUS_CODES = frozenset({429, 500, 503})
_PROVIDER_RETRY_DELAY_SECONDS = 0.1


class LlmProviderError(Exception):
    def __init__(self, code: str, safe_message: str, *, reason: str | None = None) -> None:
        diagnostic_message = f"{safe_message} [{reason}]" if reason is not None else safe_message
        super().__init__(diagnostic_message)
        self.code = code
        self.safe_message = safe_message
        self.reason = reason


class _CitationRepairValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    connect_timeout_seconds: float
    read_timeout_seconds: float
    request_timeout_seconds: float
    operation_deadline_seconds: float
    prompt_version: str
    answer_output_thinking: Literal["default", "enabled", "disabled"] = "disabled"
    structured_output_mode: Literal["text", "json_object"] = "json_object"
    structured_output_thinking: Literal["default", "enabled", "disabled"] = "disabled"
    structured_output_max_tokens: int = 2048

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("LLM base URL must use HTTP or HTTPS")
        if not self.api_key:
            raise ValueError("LLM API key is required")
        if not self.model:
            raise ValueError("LLM model is required")
        timeout_values = (
            self.connect_timeout_seconds,
            self.read_timeout_seconds,
            self.request_timeout_seconds,
            self.operation_deadline_seconds,
        )
        if any(value <= 0 for value in timeout_values):
            raise ValueError("LLM timeouts must be positive")
        if self.connect_timeout_seconds > self.request_timeout_seconds:
            raise ValueError("LLM connect timeout cannot exceed the request timeout")
        if self.read_timeout_seconds > self.request_timeout_seconds:
            raise ValueError("LLM read timeout cannot exceed the request timeout")
        minimum_deadline = self.request_timeout_seconds * 2 + _PROVIDER_RETRY_DELAY_SECONDS
        if self.operation_deadline_seconds < minimum_deadline:
            raise ValueError("LLM operation deadline must allow two requests and retry delay")
        if self.structured_output_max_tokens <= 0:
            raise ValueError("LLM structured output max tokens must be positive")


@dataclass(frozen=True, slots=True)
class _PlannedEvidenceGroup:
    query: str
    document_title: str


def _http_timeout(config: OpenAICompatibleConfig) -> httpx.Timeout:
    return httpx.Timeout(
        timeout=config.request_timeout_seconds,
        connect=config.connect_timeout_seconds,
        read=config.read_timeout_seconds,
        write=config.request_timeout_seconds,
        pool=config.connect_timeout_seconds,
    )


def _grounded_prompt(
    question: str,
    evidence: Sequence[RetrievalCandidate],
) -> list[dict[str, str]]:
    evidence_text = "\n\n".join(f"[{item.citation_id}]\n{item.content}" for item in evidence)
    language_instruction = answer_language_instruction(question)
    return [
        {
            "role": "system",
            "content": (
                "Answer only from the evidence below. Cite the evidence labels in the "
                "answer and do not create any other citation labels. Put an allowed label in "
                "or immediately after every sentence or list item that makes a factual claim. "
                "Every citation must use ASCII square brackets in exactly this form: "
                "[citation_id]. Replace citation_id with a supplied label copied verbatim. "
                "Use only plain paragraphs or bullet list items. Do not add standalone headings, "
                "tables, prefaces, conclusions, or a sources section. Put each citation in the "
                "same sentence or list item as its claim. Do not use bare IDs, full-width "
                "brackets, or footnotes. "
                f"{language_instruction} Do not use outside knowledge. If the "
                f"evidence cannot answer the question, say so.\n\n{evidence_text}"
            ),
        },
        {"role": "user", "content": question},
    ]


def _retrieval_plan_prompt(
    question: str,
    recent_questions: Sequence[str],
    document_titles: Sequence[str],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Plan bounded dense retrieval for the current user question. The application "
                "always executes the original question. Return JSON with exactly one array field "
                "named evidence_groups. Return at most three groups in the order their methods "
                "or entities first appear in the question. Each array item must be an object with "
                "exactly two "
                "string fields: query and document_title. Copy document_title exactly from "
                "searchable_document_titles; each title may appear at most once. Follow every "
                "rule: "
                "1. Return zero to three evidence groups, not a final query budget. Treat the "
                "original question as the baseline query. If three groups are returned, the "
                "application assigns the first group to the original question and executes only "
                "the second and third group queries, preserving the two-query whole-run budget. "
                "Do not fill unused groups. "
                "2. Write concise source-like propositions, not broad bags of keywords. Each "
                "group query must target one named evidence slot while preserving logical "
                "polarity, subject, object, and who supports what. Include a distinctive "
                "mechanism, trigger, or data object for the slot when the question names or "
                "strongly implies one. Do not merely restate the comparison dimension, such as "
                "saying only that one method retrieves before or during generation. "
                "3. For an explicit comparison, attribution, or multi-part question involving "
                "multiple named methods, entities, terms, or components, return one ordered object "
                "for each distinct evidence group, up to three. Prioritize groups associated with "
                "different supplied document titles. Treat multiple terms associated with the same "
                "supplied title as one evidence group. When three evidence groups are named, "
                "include all three in first-appearance order; the application, not you, assigns "
                "the first group to the original baseline and selects the later evidence groups "
                "tied to supplied titles. Terms that the question explicitly assigns to the same "
                "method or component share one slot. "
                "4. For an absolute claim or negation, return at most one counterstatement that "
                "searches for an omitted limitation or failure mode and preserves qualifiers such "
                "as 'can still' or 'not fully'. Simple fact questions map to []. If stable named "
                "slots cannot be identified, return []. "
                "5. Preserve named entities and English technical terms. When an otherwise "
                "non-English question names English methods, prefer concise English queries "
                "using likely source-paper terminology. You may use model knowledge for "
                "well-known method aliases or framework associations only as search hypotheses. "
                "Use the supplied searchable document titles to constrain framework and paper "
                "associations. Titles are retrieval hints, not answer evidence. "
                "Never create a paper or framework that is absent from the supplied titles. Do not "
                "assign concepts to unrelated frameworks; when an association is uncertain, use "
                "only names present in the question and supplied titles. "
                "Pattern example for comparison: 'How do Method A and Method B schedule "
                "retrieval?' maps to two ordered evidence_groups objects, one source-like query "
                "for Method A and one for Method B, each with its exact supplied title. "
                "Budget example: if A1 and A2 belong to Method A and the question also asks "
                "about B1 in Method B and C1 in Method C, return three ordered objects: one query "
                "grouping A1 and A2 under Method A, one query for B1 in Method B, and one for C1 "
                "in Method C. Do not emit separate groups for A1 and A2. The application removes "
                "the first query to preserve its budget. "
                "Pattern example for claim checking: 'Method X "
                "completely solved outputs lacking source support' maps to one evidence_groups "
                "object whose query says 'Method X can still produce outputs not fully supported "
                "by sources' and whose document_title exactly matches a supplied title; preserve "
                "what is unsupported and what provides support. "
                "Use recent questions only to resolve references. Do not answer, add conclusions, "
                "or include facts that are not needed to retrieve one slot. "
                'EXAMPLE JSON OUTPUT: {"evidence_groups": [{"query": "Method A '
                'distinctive mechanism", "document_title": "Method A.pdf"}]}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "recent_user_questions": list(recent_questions),
                    "searchable_document_titles": list(document_titles),
                    "current_question": question,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _retrieval_slot_refinement_prompt(
    question: str,
    recent_questions: Sequence[str],
    document_titles: Sequence[str],
    proposed_group: _PlannedEvidenceGroup,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Refine exactly one proposed retrieval evidence group. Return JSON with exactly "
                "one object field named evidence_group. That object must contain exactly two "
                "non-empty string fields: query and document_title. Copy document_title from the "
                "proposal without changing it. Replace a broad paraphrase with a concise, "
                "source-like search proposition that preserves the current question's entity, "
                "logical polarity, comparison dimension, and English technical terms while "
                "adding the distinctive mechanism, trigger, component, control signal, or data "
                "object likely used by the named method. The refined query must differ from the "
                "proposed query. Generic phrases such as adaptive retrieval, retrieval trigger, "
                "retrieves before generation, and retrieves during generation do not satisfy "
                "refinement. Name a concrete model input or output, token type, search operation, "
                "retrieved object, or architecture component instead. Silently consider candidate "
                "source terminology and return the most specific defensible search hypothesis. "
                "The refined text is only a search "
                "hypothesis. Do not answer the question, state a conclusion, add or merge slots, "
                "or invent a different paper association. Use recent questions only to resolve "
                "references. Searchable titles constrain associations but are not evidence. This "
                "request contains no retrieved chunks, expected answers, evaluation evidence, or "
                "labels, and you must not infer that they were supplied. "
                'EXAMPLE JSON OUTPUT: {"evidence_group": {"query": "Method A '
                'distinctive mechanism", "document_title": "Method A.pdf"}}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "recent_user_questions": list(recent_questions),
                    "searchable_document_titles": list(document_titles),
                    "current_question": question,
                    "proposed_evidence_group": {
                        "query": proposed_group.query,
                        "document_title": proposed_group.document_title,
                    },
                },
                ensure_ascii=False,
            ),
        },
    ]


def _evidence_assessment_prompt(
    question: str,
    queries: Sequence[str],
    evidence: Sequence[RetrievalCandidate],
    *,
    previously_selected_chunk_ids: Sequence[str],
    supplemental_query_limit: int,
) -> list[dict[str, str]]:
    previously_selected = set(previously_selected_chunk_ids)
    return [
        {
            "role": "system",
            "content": (
                "Judge whether the candidate evidence is sufficient to answer the question. "
                "Use only the candidates, select only candidate chunk IDs, and do not answer "
                "the question or use outside knowledge. If evidence is insufficient and "
                "previously_selected_chunk_ids is non-empty, those candidates were already "
                "identified as useful evidence in the earlier bounded assessment. Keep them "
                "selected while deciding whether new candidates fill the remaining gaps. "
                "Use document_title and page_number only as source identity and location "
                "metadata for the candidate content. matched_retrieval_queries only explain "
                "why a candidate was retrieved: use them to group candidates by the requested "
                "evidence component, but never treat query text as evidence. Before declaring "
                "insufficiency, complete a candidate-by-candidate closure check in "
                "candidate_index order. For every named method, term, source, or component in "
                "the question, independently inspect every candidate's content and source "
                "identity. Select every candidate that directly supports at least one requested "
                "component, even if other components remain missing. Do not return an empty "
                "selection when a candidate directly supports a named component, and do not let "
                "evidence for one named source substitute for another named source. For a "
                "correctness check, negated claim, or absolute claim, candidate "
                "content that explicitly gives a counterexample or limitation is sufficient to "
                "refute that claim; do not require evidence for both sides. For multi-component "
                "questions, sufficient may be true only when every requested component is "
                "covered by selected candidate content and source identity. "
                "supplemental retrieval capacity remains, provide at most that many standalone "
                "supplemental queries, ordered by importance. Each query must target one missing "
                "evidence component and must not repeat an executed query. Each supplemental "
                "query must contain only one missing evidence component. When multiple components "
                "are missing and capacity permits, return separate queries, one per component. "
                "Do not mix in a different term already supported by the selected candidates. "
                "Supplemental queries "
                "are search hypotheses, not conclusions: do not guess a paper, method, framework, "
                "component owner, or relationship that is not explicitly stated in the question "
                "or supported by a candidate. When the question asks which source owns a missing "
                "term and the candidates do not establish that association, use wording from the "
                "question without adding an owner. "
                "Return JSON with exactly: sufficient (boolean), selected_chunk_ids (array of "
                "strings), and supplemental_queries (array of strings). "
                'EXAMPLE JSON OUTPUT: {"sufficient": false, "selected_chunk_ids": [], '
                '"supplemental_queries": ["missing evidence component"]}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "retrieval_queries": list(queries),
                    "previously_selected_chunk_ids": list(previously_selected_chunk_ids),
                    "supplemental_query_limit": supplemental_query_limit,
                    "candidates": [
                        {
                            "candidate_index": index,
                            "chunk_id": item.chunk_id,
                            "document_title": item.document_title,
                            "page_number": item.page_number,
                            "matched_retrieval_queries": list(item.matched_queries),
                            "previously_selected": item.chunk_id in previously_selected,
                            "content": item.content,
                            "score": item.score,
                        }
                        for index, item in enumerate(evidence, start=1)
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _citation_repair_prompt(
    question: str,
    answer: str,
    evidence: Sequence[RetrievalCandidate],
    validation_feedback: CitationValidationFeedback,
) -> list[dict[str, str]]:
    language_instruction = answer_language_instruction(question)
    return [
        {
            "role": "system",
            "content": (
                "Repair the draft so every factual claim is supported by the supplied evidence "
                "and cites only its allowed citation labels. Do not add claims or use outside "
                "knowledge. Return JSON with exactly one array field named claims. Each claim "
                "must contain exactly two fields: text and citation_ids. text must contain only "
                "the claim text without citations; citation_ids must contain one or more supplied "
                "citation IDs copied verbatim. Use only evidence-supported claims, do not add "
                "outside knowledge, headings, tables, prefaces, conclusions, or a sources section. "
                "The application will deterministically place each claim's citations after every "
                "sentence or list item in text. The validation feedback contains zero-based "
                "indexes of draft units that failed; rewrite the entire draft. "
                f"{language_instruction} "
                'EXAMPLE JSON OUTPUT: {"claims": [{"text": "Evidence-supported claim", '
                '"citation_ids": ["allowed-citation-id"]}]}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "draft_answer": answer,
                    "validation_feedback": {
                        "issue": validation_feedback.issue,
                        "unit_count": validation_feedback.unit_count,
                        "citation_count": validation_feedback.citation_count,
                        "uncited_unit_indices": list(validation_feedback.uncited_unit_indices),
                        "unknown_label_unit_indices": list(
                            validation_feedback.unknown_label_unit_indices
                        ),
                    },
                    "evidence": [
                        {
                            "citation_id": item.citation_id,
                            "content": item.content,
                        }
                        for item in evidence
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _claim_support_prompt(
    question: str,
    answer: str,
    evidence: Sequence[RetrievalCandidate],
) -> list[dict[str, str]]:
    language_instruction = answer_language_instruction(question)
    return [
        {
            "role": "system",
            "content": (
                "Check each factual claim in the draft against only the supplied evidence. "
                "Preserve distinctions and qualifiers from the evidence; never strengthen "
                "outperforming a named baseline into state-of-the-art, best, all, always, or "
                "similar stronger wording. Split a mixed claim when the evidence supports its "
                "parts with different qualifiers. Rewrite unsupported or over-broad claims into "
                "the narrowest evidence-supported wording, or omit them. Return JSON with exactly "
                "one array field named claims. Each claim must contain exactly text and "
                "citation_ids. "
                "Every claim must be non-empty and cite one or more supplied IDs copied verbatim. "
                f"{language_instruction} Do not add outside knowledge. "
                'EXAMPLE JSON OUTPUT: {"claims":[{"text":"Supported claim",'
                '"citation_ids":["citation-id"]}]}'
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "draft_answer": answer,
                    "evidence": [
                        {"citation_id": item.citation_id, "content": item.content}
                        for item in evidence
                    ],
                },
                ensure_ascii=False,
            ),
        },
    ]


def _delta_content(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None
    delta = choice.get("delta")
    if not isinstance(delta, dict):
        return None
    content = delta.get("content")
    return content if isinstance(content, str) and content else None


def _finish_reason(payload: Any) -> object | None:
    if not isinstance(payload, dict):
        return None
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return None
    choice = choices[0]
    if not isinstance(choice, dict):
        return None
    reason: object | None = choice.get("finish_reason")
    return reason


def _finish_reason_error(finish_reason: object) -> LlmProviderError:
    if finish_reason == "insufficient_system_resource":
        return LlmProviderError(
            "LLM_PROVIDER_UNAVAILABLE",
            "Language model is temporarily unavailable",
            reason="provider_finish_insufficient_system_resource",
        )
    if finish_reason == "length":
        return LlmProviderError(
            "LLM_INCOMPLETE_RESPONSE",
            "Language model did not complete the response",
            reason="provider_finish_length",
        )
    if finish_reason == "content_filter":
        return LlmProviderError(
            "LLM_CONTENT_FILTERED",
            "Language model response was blocked",
            reason="provider_finish_content_filter",
        )
    if finish_reason == "tool_calls":
        return LlmProviderError(
            "LLM_INVALID_RESPONSE",
            "Language model returned an invalid response",
            reason="provider_finish_tool_calls",
        )
    return LlmProviderError(
        "LLM_INVALID_RESPONSE",
        "Language model returned an invalid response",
        reason="provider_finish_unknown",
    )


def _http_status_error(status_code: int) -> LlmProviderError:
    if status_code == 400:
        return LlmProviderError(
            "LLM_INVALID_REQUEST",
            "Language model request was invalid",
            reason="provider_http_invalid_format",
        )
    if status_code == 401:
        return LlmProviderError(
            "LLM_AUTHENTICATION_FAILED",
            "Language model authentication failed",
            reason="provider_http_authentication_failed",
        )
    if status_code == 402:
        return LlmProviderError(
            "LLM_INSUFFICIENT_BALANCE",
            "Language model account has insufficient balance",
            reason="provider_http_insufficient_balance",
        )
    if status_code == 422:
        return LlmProviderError(
            "LLM_INVALID_REQUEST",
            "Language model request was invalid",
            reason="provider_http_invalid_parameters",
        )
    if status_code == 429:
        return LlmProviderError(
            "LLM_PROVIDER_UNAVAILABLE",
            "Language model is temporarily unavailable",
            reason="provider_http_rate_limited",
        )
    if status_code == 500:
        return LlmProviderError(
            "LLM_PROVIDER_UNAVAILABLE",
            "Language model is temporarily unavailable",
            reason="provider_http_server_error",
        )
    if status_code == 503:
        return LlmProviderError(
            "LLM_PROVIDER_UNAVAILABLE",
            "Language model is temporarily unavailable",
            reason="provider_http_overloaded",
        )
    return LlmProviderError(
        "LLM_PROVIDER_UNAVAILABLE",
        "Language model is temporarily unavailable",
        reason="provider_http_unavailable",
    )


class OpenAICompatibleAnswerGenerator:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client

    async def stream_answer(
        self,
        *,
        question: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> AsyncIterator[str]:
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        try:
            async with asyncio.timeout(self.config.operation_deadline_seconds):
                emitted_content = False
                for attempt in range(2):
                    try:
                        async with asyncio.timeout(self.config.request_timeout_seconds):
                            async with self._client.stream(
                                "POST",
                                url,
                                headers={
                                    "Authorization": f"Bearer {self.config.api_key}",
                                    "Accept": "text/event-stream",
                                },
                                json={
                                    "model": self.config.model,
                                    "messages": _grounded_prompt(question, evidence),
                                    "stream": True,
                                    **(
                                        {"thinking": {"type": self.config.answer_output_thinking}}
                                        if self.config.answer_output_thinking != "default"
                                        else {}
                                    ),
                                },
                                timeout=_http_timeout(self.config),
                            ) as response:
                                if response.status_code in _TRANSIENT_HTTP_STATUS_CODES:
                                    if attempt == 0:
                                        await asyncio.sleep(_PROVIDER_RETRY_DELAY_SECONDS)
                                        continue
                                    raise _http_status_error(response.status_code)
                                if not response.is_success:
                                    raise _http_status_error(response.status_code)
                                completed = False
                                retry_after_resource_shortage = False
                                async for line in response.aiter_lines():
                                    if not line.startswith("data:"):
                                        continue
                                    data = line.removeprefix("data:").strip()
                                    if data == "[DONE]":
                                        if completed:
                                            return
                                        raise LlmProviderError(
                                            "LLM_INVALID_RESPONSE",
                                            "Language model returned an incomplete response",
                                            reason="provider_stream_missing_stop",
                                        )
                                    try:
                                        payload = json.loads(data)
                                    except (json.JSONDecodeError, TypeError) as error:
                                        raise LlmProviderError(
                                            "LLM_INVALID_RESPONSE",
                                            "Language model returned an invalid response",
                                        ) from error
                                    content = _delta_content(payload)
                                    if completed and content is not None:
                                        raise LlmProviderError(
                                            "LLM_INVALID_RESPONSE",
                                            "Language model returned an invalid response",
                                            reason="provider_stream_content_after_stop",
                                        )
                                    if content is not None:
                                        emitted_content = True
                                        yield content
                                    finish_reason = _finish_reason(payload)
                                    if finish_reason is not None:
                                        if finish_reason == "stop":
                                            completed = True
                                            continue
                                        if finish_reason == "insufficient_system_resource":
                                            if not emitted_content and attempt == 0:
                                                retry_after_resource_shortage = True
                                                break
                                        raise _finish_reason_error(finish_reason)
                                if retry_after_resource_shortage:
                                    continue
                                if completed:
                                    return
                                raise LlmProviderError(
                                    "LLM_INVALID_RESPONSE",
                                    "Language model returned an incomplete response",
                                    reason="provider_stream_missing_stop",
                                )
                    except (httpx.TimeoutException, TimeoutError):
                        if emitted_content or attempt == 1:
                            raise
                        await asyncio.sleep(_PROVIDER_RETRY_DELAY_SECONDS)
                        continue
                    except (httpx.NetworkError, httpx.ProtocolError):
                        if emitted_content or attempt == 1:
                            raise
                        await asyncio.sleep(_PROVIDER_RETRY_DELAY_SECONDS)
                        continue
        except LlmProviderError:
            raise
        except (httpx.TimeoutException, TimeoutError) as error:
            raise LlmProviderError(
                "LLM_TIMEOUT",
                "Language model request timed out",
                reason="provider_request_timeout",
            ) from error
        except httpx.HTTPStatusError as error:
            raise _http_status_error(error.response.status_code) from error
        except httpx.HTTPError as error:
            raise LlmProviderError(
                "LLM_PROVIDER_UNAVAILABLE",
                "Language model is temporarily unavailable",
                reason="provider_network_error",
            ) from error


class OpenAICompatibleQuestionPlanner:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client

    async def plan(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
        document_titles: Sequence[str] = (),
    ) -> RetrievalPlanProposal:
        try:
            messages = _retrieval_plan_prompt(
                question,
                recent_questions,
                document_titles,
            )
            semantic_violation_count = 0
            for attempt in range(2):
                parsed = await _structured_completion(
                    self.config,
                    self._client,
                    messages,
                    temperature=0,
                )
                evidence_groups = parsed.get("evidence_groups")
                if set(parsed) == {"evidence_groups"} and _has_planned_query_shape(evidence_groups):
                    validated = _validate_planned_evidence_groups(
                        evidence_groups,
                        document_titles=document_titles,
                    )
                    if validated is not None:
                        selected_groups = validated[-2:] if len(validated) == 3 else validated
                        if len(selected_groups) < 2:
                            return RetrievalPlanProposal(
                                additional_queries=tuple(group.query for group in selected_groups),
                            )
                        refined_groups = await asyncio.gather(
                            *(
                                self._refine_group(
                                    question=question,
                                    recent_questions=recent_questions,
                                    document_titles=document_titles,
                                    proposed_group=group,
                                )
                                for group in selected_groups
                            )
                        )
                        return RetrievalPlanProposal(
                            additional_queries=tuple(
                                group.query for group in refined_groups if group is not None
                            ),
                        )
                    semantic_violation_count += 1
                if attempt == 0:
                    messages = [
                        *messages,
                        {
                            "role": "system",
                            "content": (
                                "The previous response violated the JSON contract. Retry once with "
                                "exactly one evidence_groups array containing at most three "
                                "objects. Each object must contain exactly one non-empty query and "
                                "one document_title copied exactly from the supplied searchable "
                                "titles. Order groups by first appearance in the question and use "
                                "each title at most once, or return an empty array. Return no "
                                "other fields."
                            ),
                        },
                    ]
            if semantic_violation_count == 2:
                return RetrievalPlanProposal(additional_queries=())
            raise ValueError
        except (TypeError, ValueError) as error:
            raise LlmProviderError(
                "LLM_INVALID_RESPONSE",
                "Language model returned an invalid response",
            ) from error

    async def _refine_group(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
        document_titles: Sequence[str],
        proposed_group: _PlannedEvidenceGroup,
    ) -> _PlannedEvidenceGroup | None:
        try:
            parsed = await _structured_completion(
                self.config,
                self._client,
                _retrieval_slot_refinement_prompt(
                    question,
                    recent_questions,
                    document_titles,
                    proposed_group,
                ),
                temperature=0,
            )
        except LlmProviderError:
            return None
        if set(parsed) != {"evidence_group"}:
            return None
        return _validate_refined_evidence_group(
            parsed["evidence_group"],
            proposed_group=proposed_group,
        )


def _validate_planned_evidence_groups(
    value: object,
    *,
    document_titles: Sequence[str],
) -> tuple[_PlannedEvidenceGroup, ...] | None:
    if not isinstance(value, list) or len(value) > 3:
        return None
    allowed_titles = set(document_titles)
    seen_titles: set[str] = set()
    groups: list[_PlannedEvidenceGroup] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"query", "document_title"}:
            return None
        query = item["query"]
        document_title = item["document_title"]
        if (
            not isinstance(query, str)
            or not query.strip()
            or not isinstance(document_title, str)
            or document_title not in allowed_titles
            or document_title in seen_titles
        ):
            return None
        groups.append(
            _PlannedEvidenceGroup(
                query=query.strip(),
                document_title=document_title,
            )
        )
        seen_titles.add(document_title)
    return tuple(groups)


def _validate_refined_evidence_group(
    value: object,
    *,
    proposed_group: _PlannedEvidenceGroup,
) -> _PlannedEvidenceGroup | None:
    if not isinstance(value, dict) or set(value) != {"query", "document_title"}:
        return None
    query = value["query"]
    document_title = value["document_title"]
    if (
        not isinstance(query, str)
        or not query.strip()
        or document_title != proposed_group.document_title
        or query.strip().casefold() == proposed_group.query.casefold()
    ):
        return None
    return _PlannedEvidenceGroup(
        query=query.strip(),
        document_title=proposed_group.document_title,
    )


def _has_planned_query_shape(value: object) -> bool:
    if not isinstance(value, list) or len(value) > 3:
        return False
    return all(
        isinstance(item, dict)
        and set(item) == {"query", "document_title"}
        and isinstance(item["query"], str)
        and bool(item["query"].strip())
        and isinstance(item["document_title"], str)
        and bool(item["document_title"].strip())
        for item in value
    )


async def _structured_completion(
    config: OpenAICompatibleConfig,
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
) -> dict[str, Any]:
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    try:
        async with asyncio.timeout(config.operation_deadline_seconds):
            for attempt in range(2):
                request: dict[str, Any] = {
                    "model": config.model,
                    "messages": messages,
                    "stream": False,
                    "max_tokens": config.structured_output_max_tokens,
                }
                if temperature is not None:
                    request["temperature"] = temperature
                if config.structured_output_mode == "json_object":
                    request["response_format"] = {"type": "json_object"}
                if config.structured_output_thinking != "default":
                    request["thinking"] = {"type": config.structured_output_thinking}
                try:
                    async with asyncio.timeout(config.request_timeout_seconds):
                        response = await client.post(
                            url,
                            headers={"Authorization": f"Bearer {config.api_key}"},
                            json=request,
                            timeout=_http_timeout(config),
                        )
                except (httpx.TimeoutException, TimeoutError):
                    if attempt == 1:
                        raise
                    await asyncio.sleep(_PROVIDER_RETRY_DELAY_SECONDS)
                    continue
                except (httpx.NetworkError, httpx.ProtocolError):
                    if attempt == 1:
                        raise
                    await asyncio.sleep(_PROVIDER_RETRY_DELAY_SECONDS)
                    continue
                if response.status_code in _TRANSIENT_HTTP_STATUS_CODES:
                    if attempt == 0:
                        await asyncio.sleep(_PROVIDER_RETRY_DELAY_SECONDS)
                        continue
                    raise _http_status_error(response.status_code)
                if not response.is_success:
                    raise _http_status_error(response.status_code)
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError
                choices = payload.get("choices")
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError
                choice = choices[0]
                if not isinstance(choice, dict):
                    raise ValueError
                finish_reason = choice.get("finish_reason")
                if finish_reason == "insufficient_system_resource":
                    if attempt == 0:
                        continue
                    raise _finish_reason_error(finish_reason)
                if finish_reason != "stop":
                    raise _finish_reason_error(finish_reason)
                message = choice.get("message")
                if not isinstance(message, dict):
                    raise ValueError
                content = message.get("content")
                if isinstance(content, str) and content.strip():
                    parsed = _load_structured_json(content)
                    if not isinstance(parsed, dict):
                        raise ValueError
                    return parsed
                if attempt == 1:
                    raise ValueError
            raise ValueError
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise LlmProviderError(
            "LLM_INVALID_RESPONSE",
            "Language model returned an invalid response",
            reason="provider_structured_invalid_response",
        ) from error
    except (httpx.TimeoutException, TimeoutError) as error:
        raise LlmProviderError(
            "LLM_TIMEOUT",
            "Language model request timed out",
            reason="provider_request_timeout",
        ) from error
    except httpx.HTTPStatusError as error:
        raise _http_status_error(error.response.status_code) from error
    except httpx.HTTPError as error:
        raise LlmProviderError(
            "LLM_PROVIDER_UNAVAILABLE",
            "Language model is temporarily unavailable",
            reason="provider_network_error",
        ) from error


def _load_structured_json(content: str) -> Any:
    try:
        return _decode_first_json_value(content)
    except json.JSONDecodeError:
        return _decode_first_json_value(_escape_invalid_json_string_backslashes(content))


def _decode_first_json_value(content: str) -> Any:
    value, _ = json.JSONDecoder().raw_decode(content.lstrip())
    return value


def _escape_invalid_json_string_backslashes(content: str) -> str:
    valid_simple_escapes = {'"', "\\", "/", "b", "f", "n", "r", "t"}
    result: list[str] = []
    in_string = False
    index = 0

    while index < len(content):
        character = content[index]
        if character == '"':
            in_string = not in_string
            result.append(character)
            index += 1
            continue
        if not in_string or character != "\\":
            result.append(character)
            index += 1
            continue

        next_index = index + 1
        if next_index < len(content) and content[next_index] in valid_simple_escapes:
            result.extend((character, content[next_index]))
            index += 2
            continue
        if (
            next_index < len(content)
            and content[next_index] == "u"
            and index + 6 <= len(content)
            and all(
                hex_character in "0123456789abcdefABCDEF"
                for hex_character in content[index + 2 : index + 6]
            )
        ):
            result.extend(content[index : index + 6])
            index += 6
            continue

        result.append("\\\\")
        index += 1

    return "".join(result)


class OpenAICompatibleEvidenceAssessor:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client

    async def assess(
        self,
        *,
        question: str,
        queries: Sequence[str],
        evidence: Sequence[RetrievalCandidate],
        previously_selected_chunk_ids: Sequence[str] = (),
        supplemental_query_limit: int,
    ) -> EvidenceDecision:
        expected_fields = {
            "sufficient",
            "selected_chunk_ids",
            "supplemental_queries",
        }
        messages = _evidence_assessment_prompt(
            question,
            queries,
            evidence,
            previously_selected_chunk_ids=previously_selected_chunk_ids,
            supplemental_query_limit=supplemental_query_limit,
        )
        contract_retry_used = False
        parsed = await _structured_completion(
            self.config,
            self._client,
            messages,
        )
        if set(parsed) != expected_fields:
            contract_retry_used = True
            parsed = await _structured_completion(
                self.config,
                self._client,
                [
                    *messages,
                    {
                        "role": "system",
                        "content": (
                            "The previous response violated the JSON contract. Retry once with "
                            "exactly these three top-level fields: sufficient, "
                            "selected_chunk_ids, and supplemental_queries. Return no other "
                            "fields. Preserve the required value types and configured limits."
                        ),
                    },
                ],
            )
        executed_queries = {_normalize_retrieval_query(item) for item in queries}
        while True:
            try:
                if set(parsed) != expected_fields:
                    raise ValueError
                sufficient = parsed["sufficient"]
                selected = parsed["selected_chunk_ids"]
                supplemental_queries = parsed["supplemental_queries"]
                if not isinstance(sufficient, bool):
                    raise ValueError
                if not isinstance(selected, list) or any(
                    not isinstance(item, str) or not item for item in selected
                ):
                    raise ValueError
                normalized_selected = tuple(dict.fromkeys(selected))
                if not isinstance(supplemental_queries, list) or any(
                    not isinstance(item, str) or not item.strip() for item in supplemental_queries
                ):
                    raise ValueError
                normalized_supplemental = tuple(item.strip() for item in supplemental_queries)
                normalized_query_keys = tuple(
                    _normalize_retrieval_query(item) for item in normalized_supplemental
                )
                if len(set(normalized_query_keys)) != len(normalized_query_keys):
                    raise ValueError
                if not 0 <= supplemental_query_limit <= 2:
                    raise ValueError
                if len(normalized_supplemental) > supplemental_query_limit:
                    if contract_retry_used:
                        raise ValueError
                    contract_retry_used = True
                    parsed = await _structured_completion(
                        self.config,
                        self._client,
                        [
                            *messages,
                            {
                                "role": "system",
                                "content": (
                                    "The previous response exceeded the remaining supplemental "
                                    f"query capacity ({supplemental_query_limit}). Retry once "
                                    "with no more than that many unique, non-empty queries. "
                                    "Return exactly the required three fields."
                                ),
                            },
                        ],
                    )
                    continue
                if any(item in executed_queries for item in normalized_query_keys):
                    if contract_retry_used:
                        raise ValueError
                    contract_retry_used = True
                    parsed = await _structured_completion(
                        self.config,
                        self._client,
                        [
                            *messages,
                            {
                                "role": "system",
                                "content": (
                                    "The previous response repeated a retrieval query that has "
                                    "already been executed. Retry once with only new standalone "
                                    "queries that target missing evidence components. Preserve the "
                                    "remaining supplemental query capacity and return exactly the "
                                    "required three fields."
                                ),
                            },
                        ],
                    )
                    continue
                return EvidenceDecision(
                    sufficient=sufficient,
                    selected_chunk_ids=normalized_selected,
                    supplemental_queries=normalized_supplemental,
                )
            except (TypeError, ValueError) as error:
                raise LlmProviderError(
                    "LLM_INVALID_RESPONSE",
                    "Language model returned an invalid response",
                    reason="evidence_assessment_contract_violation",
                ) from error


def _normalize_retrieval_query(query: str) -> str:
    return " ".join(query.split()).casefold()


class OpenAICompatibleCitationRepairer:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client

    async def repair(
        self,
        *,
        question: str,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
        validation_feedback: CitationValidationFeedback,
    ) -> str:
        messages = _citation_repair_prompt(question, answer, evidence, validation_feedback)
        parsed = await _structured_completion(
            self.config,
            self._client,
            messages,
        )
        empty_citations_retry_used = False
        while True:
            try:
                return self._render_claims(parsed, evidence, question)
            except _CitationRepairValidationError as error:
                if (
                    error.reason == "citation_repair_empty_citations"
                    and not empty_citations_retry_used
                ):
                    empty_citations_retry_used = True
                    parsed = await _structured_completion(
                        self.config,
                        self._client,
                        [
                            *messages,
                            {
                                "role": "system",
                                "content": (
                                    "The previous response included a claim without citation_ids. "
                                    "Retry once. Every claim must include one or more supplied "
                                    "citation IDs copied verbatim. Return exactly the claims field "
                                    "and its required claim fields."
                                ),
                            },
                        ],
                    )
                    continue
                raise LlmProviderError(
                    "LLM_INVALID_RESPONSE",
                    "Language model returned an invalid response",
                    reason=error.reason,
                ) from error

    @staticmethod
    def _render_claims(
        parsed: dict[str, Any],
        evidence: Sequence[RetrievalCandidate],
        question: str,
    ) -> str:
        try:
            if set(parsed) != {"claims"}:
                raise _CitationRepairValidationError("citation_repair_invalid_fields")
            claims = parsed["claims"]
            if not isinstance(claims, list) or not claims:
                raise _CitationRepairValidationError("citation_repair_invalid_claims")
            allowed = {item.citation_id for item in evidence}
            rendered: list[str] = []
            for claim in claims:
                if not isinstance(claim, dict) or set(claim) != {"text", "citation_ids"}:
                    raise _CitationRepairValidationError("citation_repair_invalid_claim_fields")
                text = claim["text"]
                citation_ids = claim["citation_ids"]
                if not isinstance(text, str) or not text.strip():
                    raise _CitationRepairValidationError("citation_repair_empty_text")
                if not isinstance(citation_ids, list) or any(
                    not isinstance(item, str) for item in citation_ids
                ):
                    raise _CitationRepairValidationError("citation_repair_invalid_citations")
                normalized_ids = tuple(dict.fromkeys(citation_ids))
                if not normalized_ids:
                    raise _CitationRepairValidationError("citation_repair_empty_citations")
                if any(item not in allowed for item in normalized_ids):
                    raise _CitationRepairValidationError("citation_repair_unknown_citation")
                inline_ids = tuple(label[1:-1] for label in _UUID_CITATION_LABEL.findall(text))
                if any(item not in allowed for item in inline_ids):
                    raise _CitationRepairValidationError("citation_repair_unknown_inline_citation")
                normalized_text = _UUID_CITATION_LABEL.sub("", text).strip()
                if not answer_matches_question_language(
                    question=question,
                    answer=normalized_text,
                ):
                    raise _CitationRepairValidationError("citation_repair_language_mismatch")
                units = split_answer_units(normalized_text)
                if not units:
                    raise _CitationRepairValidationError("citation_repair_empty_text")
                labels = " ".join(f"[{item}]" for item in normalized_ids)
                rendered.extend(f"{unit} {labels}" for unit in units)
            return "\n".join(rendered)
        except (KeyError, TypeError) as error:
            raise _CitationRepairValidationError("citation_repair_invalid_claims") from error


class OpenAICompatibleClaimSupportVerifier:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client

    async def verify(
        self,
        *,
        question: str,
        answer: str,
        evidence: Sequence[RetrievalCandidate],
    ) -> ClaimSupportDecision:
        parsed = await _structured_completion(
            self.config,
            self._client,
            _claim_support_prompt(question, answer, evidence),
        )
        try:
            if set(parsed) != {"claims"}:
                raise ValueError
            claims = parsed["claims"]
            allowed = {item.citation_id for item in evidence}
            if not isinstance(claims, list) or not claims:
                raise ValueError
            result: list[GroundedClaim] = []
            for claim in claims:
                if not isinstance(claim, dict) or set(claim) != {"text", "citation_ids"}:
                    raise ValueError
                text = claim["text"]
                citation_ids = claim["citation_ids"]
                if not isinstance(text, str) or not text.strip():
                    raise ValueError
                if not isinstance(citation_ids, list) or not citation_ids:
                    raise ValueError
                normalized_ids = tuple(dict.fromkeys(citation_ids))
                if any(not isinstance(item, str) or item not in allowed for item in normalized_ids):
                    raise ValueError
                result.append(GroundedClaim(text=text.strip(), citation_ids=normalized_ids))
            return ClaimSupportDecision(claims=tuple(result))
        except (KeyError, TypeError, ValueError) as error:
            raise ClaimSupportValidationError from error
