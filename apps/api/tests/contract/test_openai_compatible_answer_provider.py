import asyncio
import json
from collections.abc import AsyncIterator
from typing import Literal

import httpx
import pytest

from sourcetrace.rag.llm import (
    LlmProviderError,
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleCitationRepairer,
    OpenAICompatibleConfig,
    OpenAICompatibleEvidenceAssessor,
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


class KeepAliveResponseStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        while True:
            yield b": keep-alive\n\n"
            await asyncio.sleep(0)

    async def aclose(self) -> None:
        self.closed = True


def _evidence() -> list[RetrievalCandidate]:
    return [
        RetrievalCandidate(
            chunk_id="chunk-1",
            content="BGE-M3 dense vectors are normalized before indexing.",
            score=0.91,
            citation_id="citation-1",
        )
    ]


def _config(
    *,
    structured_output_mode: Literal["text", "json_object"] = "text",
    structured_output_thinking: Literal["default", "enabled", "disabled"] = "default",
) -> OpenAICompatibleConfig:
    return OpenAICompatibleConfig(
        base_url="https://gateway.example/v1",
        api_key="test-secret",
        model="gpt-5.6-luna",
        timeout_seconds=30,
        prompt_version="grounded-answer-v1",
        structured_output_mode=structured_output_mode,
        structured_output_thinking=structured_output_thinking,
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
    assert "ASCII square brackets" in payload["messages"][0]["content"]
    assert "[citation_id]" in payload["messages"][0]["content"]


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


async def test_provider_times_out_a_keep_alive_only_stream() -> None:
    upstream = KeepAliveResponseStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=upstream,
        )

    config = OpenAICompatibleConfig(
        base_url="https://gateway.example/v1",
        api_key="test-secret",
        model="gpt-5.6-luna",
        timeout_seconds=0.01,
        prompt_version="grounded-answer-v1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleAnswerGenerator(config, client=client)

        with pytest.raises(LlmProviderError) as error:
            await asyncio.wait_for(
                _collect(provider.stream_answer(question="Question", evidence=_evidence())),
                timeout=0.5,
            )

    assert error.value.code == "LLM_TIMEOUT"
    assert upstream.closed is True


async def test_evidence_assessor_times_out_a_keep_alive_only_response() -> None:
    upstream = KeepAliveResponseStream()

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=upstream)

    config = OpenAICompatibleConfig(
        base_url="https://gateway.example/v1",
        api_key="test-secret",
        model="gpt-5.6-luna",
        timeout_seconds=0.01,
        prompt_version="evidence-assessment-v1",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assessor = OpenAICompatibleEvidenceAssessor(config, client=client)

        with pytest.raises(LlmProviderError) as error:
            await asyncio.wait_for(
                assessor.assess(
                    question="Question",
                    query="Question",
                    evidence=_evidence(),
                    supplemental_allowed=True,
                ),
                timeout=0.5,
            )

    assert error.value.code == "LLM_TIMEOUT"
    assert upstream.closed is True


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


async def test_evidence_assessor_returns_a_structured_bounded_decision() -> None:
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
                                    "sufficient": False,
                                    "selected_chunk_ids": [],
                                    "supplemental_query": "BGE-M3 normalization indexing",
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assessor = OpenAICompatibleEvidenceAssessor(_config(), client=client)

        decision = await assessor.assess(
            question="How are vectors indexed?",
            query="vector indexing",
            evidence=_evidence(),
            supplemental_allowed=True,
        )

    assert decision.sufficient is False
    assert decision.selected_chunk_ids == ()
    assert decision.supplemental_query == "BGE-M3 normalization indexing"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["stream"] is False
    serialized = json.dumps(payload["messages"])
    assert "chunk-1" in serialized
    assert "BGE-M3 dense vectors" in serialized
    assert "supplemental_allowed" in serialized


async def test_evidence_assessor_retries_an_empty_json_mode_response_once() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert isinstance(payload, dict)
        payloads.append(payload)
        content = "" if len(payloads) == 1 else json.dumps(
            {
                "sufficient": True,
                "selected_chunk_ids": ["chunk-1"],
                "supplemental_query": None,
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": content}, "finish_reason": "stop"}
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assessor = OpenAICompatibleEvidenceAssessor(
            _config(
                structured_output_mode="json_object",
                structured_output_thinking="disabled",
            ),
            client=client,
        )

        decision = await assessor.assess(
            question="How are vectors indexed?",
            query="vector indexing",
            evidence=_evidence(),
            supplemental_allowed=True,
        )

    assert decision.sufficient is True
    assert decision.selected_chunk_ids == ("chunk-1",)
    assert len(payloads) == 2
    assert all(
        payload["response_format"] == {"type": "json_object"}
        for payload in payloads
    )
    assert all(payload["thinking"] == {"type": "disabled"} for payload in payloads)


async def test_citation_repairer_returns_only_the_repaired_answer() -> None:
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
                                {"answer": "Vectors are normalized [citation-1]"}
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repairer = OpenAICompatibleCitationRepairer(_config(), client=client)

        answer = await repairer.repair(
            question="How are vectors stored?",
            answer="Vectors are normalized.",
            evidence=_evidence(),
        )

    assert answer == "Vectors are normalized [citation-1]"
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["stream"] is False
    serialized = json.dumps(payload["messages"])
    assert "Vectors are normalized." in serialized
    assert "citation-1" in serialized
    assert "ASCII square brackets" in payload["messages"][0]["content"]
    assert "[citation_id]" in payload["messages"][0]["content"]


async def test_citation_repairer_recovers_literal_backslashes_in_json_strings() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"The objective is \\(x + y\\) '
                                '[citation-1]"}'
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repairer = OpenAICompatibleCitationRepairer(_config(), client=client)

        answer = await repairer.repair(
            question="What is the objective?",
            answer="The objective is x + y.",
            evidence=_evidence(),
        )

    assert answer == r"The objective is \(x + y\) [citation-1]"


async def test_evidence_assessor_rejects_unstructured_output() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"sufficient": True})},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assessor = OpenAICompatibleEvidenceAssessor(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await assessor.assess(
                question="Question",
                query="query",
                evidence=_evidence(),
                supplemental_allowed=True,
            )

    assert error.value.code == "LLM_INVALID_RESPONSE"


async def test_citation_repairer_maps_timeout_without_leaking_details() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("private repair timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        repairer = OpenAICompatibleCitationRepairer(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await repairer.repair(
                question="Question",
                answer="Draft",
                evidence=_evidence(),
            )

    assert error.value.code == "LLM_TIMEOUT"
    assert "private" not in str(error.value)
