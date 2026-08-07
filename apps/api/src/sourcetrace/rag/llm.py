import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from sourcetrace.rag.ports import (
    EvidenceDecision,
    RetrievalCandidate,
    RetrievalPlanProposal,
)


class LlmProviderError(Exception):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float
    prompt_version: str
    structured_output_mode: Literal["text", "json_object"] = "text"
    structured_output_thinking: Literal["default", "enabled", "disabled"] = "default"

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("LLM base URL must use HTTP or HTTPS")
        if not self.api_key:
            raise ValueError("LLM API key is required")
        if not self.model:
            raise ValueError("LLM model is required")
        if self.timeout_seconds <= 0:
            raise ValueError("LLM timeout must be positive")


def _grounded_prompt(
    question: str,
    evidence: Sequence[RetrievalCandidate],
) -> list[dict[str, str]]:
    evidence_text = "\n\n".join(f"[{item.citation_id}]\n{item.content}" for item in evidence)
    return [
        {
            "role": "system",
            "content": (
                "Answer only from the evidence below. Cite the evidence labels in the "
                "answer and do not create any other citation labels. Put an allowed label in "
                "or immediately after every sentence or list item that makes a factual claim. "
                "Every citation must use ASCII square brackets in exactly this form: "
                "[citation_id]. Replace citation_id with a supplied label copied verbatim. "
                "Do not use bare IDs, full-width brackets, footnotes, or a sources section. "
                "Use the same language as the question. Do not use outside knowledge. If the "
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
                "named additional_queries. Follow every rule: "
                "1. Return zero or one additional standalone query. One is a hard limit for "
                "initial planning because a later evidence assessment owns the remaining query "
                "budget. "
                "2. Write concise source-like propositions, not broad bags of keywords. Preserve "
                "logical polarity, subject, object, and who supports what. "
                "3. Return an additional query only for an absolute claim or negation that needs "
                "a counterstatement to search for an omitted limitation or failure mode. For "
                "simple facts, comparisons, attribution, and multi-part questions, return an "
                "empty array and let the original question establish the retrieval baseline. "
                "4. The counterstatement must preserve the failure mode and use qualifiers such "
                "as 'can still' or 'not fully'. "
                "5. Preserve named entities and English technical terms. When an otherwise "
                "non-English question names English methods, prefer concise English queries "
                "using likely source-paper terminology. You may use model knowledge for "
                "well-known method aliases or framework associations only as search hypotheses. "
                "Use the supplied searchable document titles to constrain framework and paper "
                "associations. Titles are retrieval hints, not answer evidence. "
                "Do not invent bibliographic titles or assign concepts to unrelated frameworks. "
                "Pattern example for comparison: 'How do Method A and Method B schedule "
                "retrieval?' maps to []. Pattern example for claim checking: "
                "'Method X "
                "completely solved outputs lacking source support' maps to ['Method X can still "
                "produce outputs not fully supported by sources']; preserve what is unsupported "
                "and "
                "what provides support. "
                "Use recent questions only to resolve references. Do not answer or add conclusions."
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


def _evidence_assessment_prompt(
    question: str,
    queries: Sequence[str],
    evidence: Sequence[RetrievalCandidate],
    *,
    supplemental_allowed: bool,
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Judge whether the candidate evidence is sufficient to answer the question. "
                "Use only the candidates, select only candidate chunk IDs, and do not answer "
                "the question or use outside knowledge. If evidence is insufficient and a "
                "supplemental retrieval is allowed, provide one standalone supplemental query. "
                "Return JSON with exactly: sufficient (boolean), selected_chunk_ids (array of "
                "strings), and supplemental_query (string or null)."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "retrieval_queries": list(queries),
                    "supplemental_allowed": supplemental_allowed,
                    "candidates": [
                        {
                            "chunk_id": item.chunk_id,
                            "content": item.content,
                            "score": item.score,
                        }
                        for item in evidence
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
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Repair the draft so every factual claim is supported by the supplied evidence "
                "and cites only its allowed citation labels. Do not add claims or use outside "
                "knowledge. Every factual sentence or list item must cite an allowed label with "
                "ASCII square brackets in exactly this form: [citation_id]. Replace citation_id "
                "with a supplied label copied verbatim; do not use bare IDs, full-width brackets, "
                "footnotes, or a sources section. Keep the question's language. Return JSON with "
                "exactly one string field named answer."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "question": question,
                    "draft_answer": answer,
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
            async with asyncio.timeout(self.config.timeout_seconds):
                emitted_content = False
                for attempt in range(2):
                    try:
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
                            },
                            timeout=self.config.timeout_seconds,
                        ) as response:
                            response.raise_for_status()
                            async for line in response.aiter_lines():
                                if not line.startswith("data:"):
                                    continue
                                data = line.removeprefix("data:").strip()
                                if data == "[DONE]":
                                    return
                                try:
                                    payload = json.loads(data)
                                except (json.JSONDecodeError, TypeError) as error:
                                    raise LlmProviderError(
                                        "LLM_INVALID_RESPONSE",
                                        "Language model returned an invalid response",
                                    ) from error
                                content = _delta_content(payload)
                                if content is not None:
                                    emitted_content = True
                                    yield content
                                finish_reason = _finish_reason(payload)
                                if finish_reason is not None:
                                    if finish_reason == "stop":
                                        return
                                    raise LlmProviderError(
                                        "LLM_INCOMPLETE_RESPONSE",
                                        "Language model did not complete the response",
                                    )
                            raise LlmProviderError(
                                "LLM_INVALID_RESPONSE",
                                "Language model returned an incomplete response",
                            )
                    except httpx.RemoteProtocolError:
                        if emitted_content or attempt == 1:
                            raise
                        continue
        except LlmProviderError:
            raise
        except (httpx.TimeoutException, TimeoutError) as error:
            raise LlmProviderError(
                "LLM_TIMEOUT",
                "Language model request timed out",
            ) from error
        except httpx.HTTPError as error:
            raise LlmProviderError(
                "LLM_PROVIDER_UNAVAILABLE",
                "Language model is temporarily unavailable",
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
            for attempt in range(2):
                parsed = await _structured_completion(
                    self.config,
                    self._client,
                    messages,
                    temperature=0,
                )
                additional_queries = parsed.get("additional_queries")
                if (
                    set(parsed) == {"additional_queries"}
                    and isinstance(additional_queries, list)
                    and len(additional_queries) <= 1
                    and all(
                        isinstance(query, str) and query.strip()
                        for query in additional_queries
                    )
                ):
                    return RetrievalPlanProposal(
                        additional_queries=tuple(
                            query.strip() for query in additional_queries
                        )
                    )
                if attempt == 0:
                    messages = [
                        *messages,
                        {
                            "role": "system",
                            "content": (
                                "The previous response violated the JSON contract. Retry once with "
                                "exactly one additional_queries array containing at most one "
                                "non-empty counterstatement query, or an empty array. Return no "
                                "other fields."
                            ),
                        },
                    ]
            raise ValueError
        except (TypeError, ValueError) as error:
            raise LlmProviderError(
                "LLM_INVALID_RESPONSE",
                "Language model returned an invalid response",
            ) from error


async def _structured_completion(
    config: OpenAICompatibleConfig,
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
) -> dict[str, Any]:
    url = f"{config.base_url.rstrip('/')}/chat/completions"
    try:
        async with asyncio.timeout(config.timeout_seconds):
            for attempt in range(2):
                request: dict[str, Any] = {
                    "model": config.model,
                    "messages": messages,
                    "stream": False,
                }
                if temperature is not None:
                    request["temperature"] = temperature
                if config.structured_output_mode == "json_object":
                    request["response_format"] = {"type": "json_object"}
                if config.structured_output_thinking != "default":
                    request["thinking"] = {"type": config.structured_output_thinking}
                try:
                    response = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {config.api_key}"},
                        json=request,
                        timeout=config.timeout_seconds,
                    )
                except (httpx.NetworkError, httpx.ProtocolError):
                    if attempt == 1:
                        raise
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError
                choices = payload.get("choices")
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError
                choice = choices[0]
                if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
                    raise ValueError
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
        ) from error
    except (httpx.TimeoutException, TimeoutError) as error:
        raise LlmProviderError(
            "LLM_TIMEOUT",
            "Language model request timed out",
        ) from error
    except httpx.HTTPError as error:
        raise LlmProviderError(
            "LLM_PROVIDER_UNAVAILABLE",
            "Language model is temporarily unavailable",
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
        supplemental_allowed: bool,
    ) -> EvidenceDecision:
        parsed = await _structured_completion(
            self.config,
            self._client,
            _evidence_assessment_prompt(
                question,
                queries,
                evidence,
                supplemental_allowed=supplemental_allowed,
            ),
        )
        try:
            if set(parsed) != {
                "sufficient",
                "selected_chunk_ids",
                "supplemental_query",
            }:
                raise ValueError
            sufficient = parsed["sufficient"]
            selected = parsed["selected_chunk_ids"]
            supplemental_query = parsed["supplemental_query"]
            if not isinstance(sufficient, bool):
                raise ValueError
            if not isinstance(selected, list) or any(
                not isinstance(item, str) or not item for item in selected
            ):
                raise ValueError
            if len(set(selected)) != len(selected):
                raise ValueError
            if supplemental_query is not None and (
                not isinstance(supplemental_query, str) or not supplemental_query.strip()
            ):
                raise ValueError
            if not supplemental_allowed and supplemental_query is not None:
                raise ValueError
            return EvidenceDecision(
                sufficient=sufficient,
                selected_chunk_ids=tuple(selected),
                supplemental_query=(
                    supplemental_query.strip() if isinstance(supplemental_query, str) else None
                ),
            )
        except (TypeError, ValueError) as error:
            raise LlmProviderError(
                "LLM_INVALID_RESPONSE",
                "Language model returned an invalid response",
            ) from error


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
    ) -> str:
        parsed = await _structured_completion(
            self.config,
            self._client,
            _citation_repair_prompt(question, answer, evidence),
        )
        try:
            if set(parsed) != {"answer"}:
                raise ValueError
            repaired = parsed["answer"]
            if not isinstance(repaired, str) or not repaired.strip():
                raise ValueError
            return repaired.strip()
        except (TypeError, ValueError) as error:
            raise LlmProviderError(
                "LLM_INVALID_RESPONSE",
                "Language model returned an invalid response",
            ) from error
