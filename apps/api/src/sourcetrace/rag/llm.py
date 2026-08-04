import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from sourcetrace.rag.ports import EvidenceDecision, RetrievalCandidate


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
    evidence_text = "\n\n".join(
        f"[{item.citation_id}]\n{item.content}" for item in evidence
    )
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


def _question_rewrite_prompt(
    question: str,
    recent_questions: Sequence[str],
) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Rewrite the current user question into a standalone retrieval query. "
                "Use recent user questions only to resolve references. Do not answer the "
                "question, add facts, or use outside knowledge. Keep the current question's "
                "language. Return JSON with exactly one string field named retrieval_query."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                {
                    "recent_user_questions": list(recent_questions),
                    "current_question": question,
                },
                ensure_ascii=False,
            ),
        },
    ]


def _evidence_assessment_prompt(
    question: str,
    query: str,
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
                    "retrieval_query": query,
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


class OpenAICompatibleQuestionRewriter:
    def __init__(
        self,
        config: OpenAICompatibleConfig,
        *,
        client: httpx.AsyncClient,
    ) -> None:
        self.config = config
        self._client = client

    async def rewrite(
        self,
        *,
        question: str,
        recent_questions: Sequence[str],
    ) -> str:
        try:
            parsed = await _structured_completion(
                self.config,
                self._client,
                _question_rewrite_prompt(question, recent_questions),
            )
            if set(parsed) != {"retrieval_query"}:
                raise ValueError
            query = parsed["retrieval_query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError
            return query.strip()
        except (TypeError, ValueError) as error:
            raise LlmProviderError(
                "LLM_INVALID_RESPONSE",
                "Language model returned an invalid response",
            ) from error


async def _structured_completion(
    config: OpenAICompatibleConfig,
    client: httpx.AsyncClient,
    messages: list[dict[str, str]],
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
                if config.structured_output_mode == "json_object":
                    request["response_format"] = {"type": "json_object"}
                if config.structured_output_thinking != "default":
                    request["thinking"] = {"type": config.structured_output_thinking}
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {config.api_key}"},
                    json=request,
                    timeout=config.timeout_seconds,
                )
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
        return json.loads(content)
    except json.JSONDecodeError:
        return json.loads(_escape_invalid_json_string_backslashes(content))


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
        query: str,
        evidence: Sequence[RetrievalCandidate],
        supplemental_allowed: bool,
    ) -> EvidenceDecision:
        parsed = await _structured_completion(
            self.config,
            self._client,
            _evidence_assessment_prompt(
                question,
                query,
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
                    supplemental_query.strip()
                    if isinstance(supplemental_query, str)
                    else None
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
