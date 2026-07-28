import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest

from sourcetrace.rag.llm import (
    LlmProviderError,
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleConfig,
    OpenAICompatibleQuestionRewriter,
)
from sourcetrace.rag.ports import RetrievalCandidate


class RecordingResponseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b'data: {"choices":[{"delta":{"content":"Partial"}}]}\n\n'
        await self.release.wait()
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        self.closed = True
        self.release.set()


def _evidence() -> list[RetrievalCandidate]:
    return [
        RetrievalCandidate(
            chunk_id="chunk-1",
            content="BGE-M3 dense vectors are normalized before indexing.",
            score=0.91,
            citation_id="citation-1",
        )
    ]


def _config() -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(
        base_url="https://gateway.example/v1",
        api_key="test-secret",
        model="gpt-5.6-luna",
        timeout_seconds=30,
        prompt_version="grounded-answer-v1",
    )


async def _collect(stream: AsyncIterator[str]) -> list[str]:
    return [item async for item in stream]


async def test_provider_streams_openai_chat_deltas_with_configured_model() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["authorization"]
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"Vectors "}}]}\n\n'
                b'data: {"choices":[{"delta":{"content":"are normalized."}}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleAnswerGenerator(_config(), client=client)

        deltas = await _collect(
            provider.stream_answer(
                question="How are vectors stored?",
                evidence=_evidence(),
            )
        )

    assert deltas == ["Vectors ", "are normalized."]
    assert captured["url"] == "https://gateway.example/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-secret"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["stream"] is True
    assert "citation-1" in payload["messages"][0]["content"]
    assert "BGE-M3 dense vectors" in payload["messages"][0]["content"]


@pytest.mark.parametrize(
    ("upstream", "expected_code"),
    [("timeout", "LLM_TIMEOUT"), ("server_error", "LLM_PROVIDER_UNAVAILABLE")],
)
async def test_provider_maps_upstream_failures_without_leaking_details(
    upstream: str,
    expected_code: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if upstream == "timeout":
            raise httpx.ReadTimeout("private gateway timeout detail", request=request)
        return httpx.Response(500, text="private upstream response")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleAnswerGenerator(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await _collect(
                provider.stream_answer(question="Question", evidence=_evidence())
            )

    assert error.value.code == expected_code
    assert "private" not in str(error.value)


async def test_provider_rejects_a_stream_that_ends_without_completion() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"choices":[{"delta":{"content":"Partial"}}]}\n\n',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleAnswerGenerator(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await _collect(
                provider.stream_answer(question="Question", evidence=_evidence())
            )

    assert error.value.code == "LLM_INVALID_RESPONSE"


async def test_provider_rejects_a_length_truncated_completion() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(
                b'data: {"choices":[{"delta":{"content":"Partial"},'
                b'"finish_reason":"length"}]}\n\n'
                b"data: [DONE]\n\n"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleAnswerGenerator(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await _collect(
                provider.stream_answer(question="Question", evidence=_evidence())
            )

    assert error.value.code == "LLM_INCOMPLETE_RESPONSE"


async def test_consumer_cancellation_closes_the_upstream_response_stream() -> None:
    upstream = RecordingResponseStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=upstream,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleAnswerGenerator(_config(), client=client)
        response_stream = provider.stream_answer(
            question="Question",
            evidence=_evidence(),
        )

        assert await anext(response_stream) == "Partial"
        await response_stream.aclose()

    assert upstream.closed is True


async def test_question_rewriter_uses_only_recent_user_questions() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "retrieval_query": (
                                        "Why are BGE-M3 dense vectors normalized?"
                                    )
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rewriter = OpenAICompatibleQuestionRewriter(_config(), client=client)

        rewritten = await rewriter.rewrite(
            question="Why is it normalized?",
            recent_questions=[
                "What is BGE-M3 dense retrieval?",
                "How are its vectors stored?",
            ],
        )

    assert rewritten == "Why are BGE-M3 dense vectors normalized?"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["stream"] is False
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["system", "user"]
    serialized = json.dumps(messages)
    assert "What is BGE-M3 dense retrieval?" in serialized
    assert "How are its vectors stored?" in serialized
    assert "UNSUPPORTED PRIOR ANSWER" not in serialized


async def test_question_rewriter_rejects_an_invalid_response_shape() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rewriter = OpenAICompatibleQuestionRewriter(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await rewriter.rewrite(
                question="Why is it normalized?",
                recent_questions=["How are BGE-M3 vectors stored?"],
            )

    assert error.value.code == "LLM_INVALID_RESPONSE"
