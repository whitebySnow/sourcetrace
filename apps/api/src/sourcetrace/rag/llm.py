import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from sourcetrace.rag.ports import RetrievalCandidate


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
                "answer and do not create any other citation labels. Use the same language "
                "as the question. Do not use outside knowledge. If the evidence cannot "
                f"answer the question, say so.\n\n{evidence_text}"
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
        except httpx.TimeoutException as error:
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
        url = f"{self.config.base_url.rstrip('/')}/chat/completions"
        try:
            response = await self._client.post(
                url,
                headers={"Authorization": f"Bearer {self.config.api_key}"},
                json={
                    "model": self.config.model,
                    "messages": _question_rewrite_prompt(question, recent_questions),
                    "stream": False,
                },
                timeout=self.config.timeout_seconds,
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
            if not isinstance(content, str):
                raise ValueError
            parsed = json.loads(content)
            if not isinstance(parsed, dict) or set(parsed) != {"retrieval_query"}:
                raise ValueError
            query = parsed["retrieval_query"]
            if not isinstance(query, str) or not query.strip():
                raise ValueError
            return query.strip()
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            raise LlmProviderError(
                "LLM_INVALID_RESPONSE",
                "Language model returned an invalid response",
            ) from error
        except httpx.TimeoutException as error:
            raise LlmProviderError(
                "LLM_TIMEOUT",
                "Language model request timed out",
            ) from error
        except httpx.HTTPError as error:
            raise LlmProviderError(
                "LLM_PROVIDER_UNAVAILABLE",
                "Language model is temporarily unavailable",
            ) from error
