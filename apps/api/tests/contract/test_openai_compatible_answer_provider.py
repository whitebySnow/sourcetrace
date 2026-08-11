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
    OpenAICompatibleQuestionPlanner,
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


class BrokenResponseStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        raise httpx.RemoteProtocolError("upstream closed before first delta")
        yield b""

    async def aclose(self) -> None:
        return None


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
            await _collect(provider.stream_answer(question="Question", evidence=_evidence()))

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
            await _collect(provider.stream_answer(question="Question", evidence=_evidence()))

    assert error.value.code == "LLM_INVALID_RESPONSE"


async def test_provider_reconnects_once_before_the_first_stream_delta() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=BrokenResponseStream(),
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=(b'data: {"choices":[{"delta":{"content":"Recovered"}}]}\n\ndata: [DONE]\n\n'),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleAnswerGenerator(_config(), client=client)
        deltas = await _collect(provider.stream_answer(question="Question", evidence=_evidence()))

    assert deltas == ["Recovered"]
    assert attempts == 2


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
            await _collect(provider.stream_answer(question="Question", evidence=_evidence()))

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
                    queries=("Question",),
                    evidence=_evidence(),
                    supplemental_query_limit=1,
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


async def test_question_planner_uses_only_recent_user_questions() -> None:
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
                                    "evidence_groups": [
                                        {
                                            "query": "Why are BGE-M3 dense vectors normalized?",
                                            "document_title": "BGE-M3.pdf",
                                        },
                                    ]
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Why is it normalized?",
            recent_questions=[
                "What is BGE-M3 dense retrieval?",
                "How are its vectors stored?",
            ],
            document_titles=["BGE-M3.pdf", "Vector Storage Notes.pdf"],
        )

    assert proposal.additional_queries == ("Why are BGE-M3 dense vectors normalized?",)
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["stream"] is False
    assert payload["temperature"] == 0
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["system", "user"]
    system_prompt = messages[0]["content"]
    assert "zero to three evidence groups" in system_prompt
    assert "whole-run budget" in system_prompt
    assert "not broad bags of keywords" in system_prompt
    assert "one named evidence slot" in system_prompt
    assert "distinctive mechanism, trigger, or data object" in system_prompt
    assert "merely restate the comparison dimension" in system_prompt
    assert "comparison, attribution, or multi-part question" in system_prompt
    assert "original question as the baseline query" in system_prompt
    assert "different supplied document titles" in system_prompt
    assert "same supplied title as one evidence group" in system_prompt
    assert "assigns the first group to the original question" in system_prompt
    assert "selects the later evidence groups tied to supplied titles" in system_prompt
    assert "Never create a paper or framework that is absent from the supplied titles" in (
        system_prompt
    )
    assert "A1 and A2 belong to Method A" in system_prompt
    assert "Do not emit separate groups for A1 and A2" in system_prompt
    assert "one query for B1 in Method B, and one for C1" in system_prompt
    assert "absolute claim or negation" in system_prompt
    assert "who supports what" in system_prompt
    assert "English technical terms" in system_prompt
    assert "prefer concise English queries" in system_prompt
    assert "only as search hypotheses" in system_prompt
    assert "Simple fact questions map to []" in system_prompt
    assert "outputs not fully supported by sources" in system_prompt
    serialized = json.dumps(messages)
    assert "What is BGE-M3 dense retrieval?" in serialized
    assert "How are its vectors stored?" in serialized
    assert "BGE-M3.pdf" in serialized
    assert "Vector Storage Notes.pdf" in serialized
    assert "UNSUPPORTED PRIOR ANSWER" not in serialized


async def test_question_planner_rejects_an_invalid_response_shape() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await planner.plan(
                question="Why is it normalized?",
                recent_questions=["How are BGE-M3 vectors stored?"],
            )

    assert error.value.code == "LLM_INVALID_RESPONSE"


async def test_question_planner_accepts_two_evidence_slot_queries() -> None:
    refinements_started: set[str] = set()
    all_refinements_started = asyncio.Event()
    refinement_payloads: dict[str, dict[str, object]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload["messages"]
        user_input = json.loads(messages[-1]["content"])
        proposed = user_input.get("proposed_evidence_group")
        if proposed is not None:
            title = proposed["document_title"]
            refinement_payloads[title] = payload
            refinements_started.add(title)
            if len(refinements_started) == 2:
                all_refinements_started.set()
            await asyncio.wait_for(all_refinements_started.wait(), timeout=1)
            refined_queries = {
                "ReAct.pdf": "ReAct task-specific actions interact with environment",
                "Self-RAG.pdf": "Self-RAG Critique tokens support self-reflection",
            }
            content = {
                "evidence_group": {
                    "query": refined_queries[title],
                    "document_title": title,
                }
            }
        else:
            content = {
                "evidence_groups": [
                    {
                        "query": "ReAct task-specific environment actions",
                        "document_title": "ReAct.pdf",
                    },
                    {
                        "query": "Self-RAG three types of Critique tokens",
                        "document_title": "Self-RAG.pdf",
                    },
                ]
            }
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(content)},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question=(
                "DPR, BART, environment actions, and critique tokens belong to which "
                "paper components?"
            ),
            recent_questions=[],
            document_titles=["RAG.pdf", "ReAct.pdf", "Self-RAG.pdf"],
        )

    assert proposal.additional_queries == (
        "ReAct task-specific actions interact with environment",
        "Self-RAG Critique tokens support self-reflection",
    )
    assert set(refinement_payloads) == {"ReAct.pdf", "Self-RAG.pdf"}
    react_payload = json.dumps(refinement_payloads["ReAct.pdf"])
    self_rag_payload = json.dumps(refinement_payloads["Self-RAG.pdf"])
    assert "Self-RAG three types of Critique tokens" not in react_payload
    assert "ReAct task-specific environment actions" not in self_rag_payload
    assert "retrieved chunks" in react_payload
    assert "expected answers" in react_payload
    assert "must differ from the proposed query" in react_payload
    assert "do not satisfy refinement" in react_payload


async def test_question_planner_discards_refinement_that_changes_document() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload["messages"]
        user_input = json.loads(messages[-1]["content"])
        proposed = user_input.get("proposed_evidence_group")
        content = (
            {
                "evidence_groups": [
                    {"query": "ReAct actions", "document_title": "ReAct.pdf"},
                    {
                        "query": "Self-RAG critique tokens",
                        "document_title": "Self-RAG.pdf",
                    },
                ]
            }
            if proposed is None
            else {
                "evidence_group": {
                    "query": f"refined {proposed['query']}",
                    "document_title": "Self-RAG.pdf",
                }
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(content)},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Compare ReAct and Self-RAG components",
            recent_questions=[],
            document_titles=["ReAct.pdf", "Self-RAG.pdf"],
        )

    assert proposal.additional_queries == ("refined Self-RAG critique tokens",)


async def test_question_planner_discards_unchanged_refinement() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload["messages"]
        user_input = json.loads(messages[-1]["content"])
        proposed = user_input.get("proposed_evidence_group")
        content = (
            {
                "evidence_groups": [
                    {"query": "ReAct actions", "document_title": "ReAct.pdf"},
                    {
                        "query": "Self-RAG critique tokens",
                        "document_title": "Self-RAG.pdf",
                    },
                ]
            }
            if proposed is None
            else {"evidence_group": proposed}
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(content)},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Compare ReAct and Self-RAG components",
            recent_questions=[],
            document_titles=["ReAct.pdf", "Self-RAG.pdf"],
        )

    assert proposal.additional_queries == ()


async def test_question_planner_rejects_more_than_three_evidence_groups() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "evidence_groups": [
                                        {
                                            "query": "first expansion",
                                            "document_title": "First.pdf",
                                        },
                                        {
                                            "query": "second expansion",
                                            "document_title": "Second.pdf",
                                        },
                                        {
                                            "query": "third expansion",
                                            "document_title": "Third.pdf",
                                        },
                                        {
                                            "query": "forbidden fourth expansion",
                                            "document_title": "Fourth.pdf",
                                        },
                                    ]
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        with pytest.raises(LlmProviderError) as error:
            await planner.plan(
                question="Question",
                recent_questions=[],
                document_titles=[
                    "First.pdf",
                    "Second.pdf",
                    "Third.pdf",
                    "Fourth.pdf",
                ],
            )

    assert error.value.code == "LLM_INVALID_RESPONSE"
    assert attempts == 2


async def test_question_planner_corrects_duplicate_document_groups() -> None:
    initial_attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal initial_attempts
        payload = json.loads(request.content)
        messages = payload["messages"]
        user_message = next(message for message in reversed(messages) if message["role"] == "user")
        user_input = json.loads(user_message["content"])
        proposed = user_input.get("proposed_evidence_group")
        if proposed is not None:
            content = {
                "evidence_group": {
                    "query": f"refined {proposed['query']}",
                    "document_title": proposed["document_title"],
                }
            }
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "message": {"content": json.dumps(content)},
                            "finish_reason": "stop",
                        }
                    ]
                },
            )
        initial_attempts += 1
        queries = (
            [
                {"query": "DPR component", "document_title": "RAG.pdf"},
                {"query": "BART component", "document_title": "RAG.pdf"},
            ]
            if initial_attempts == 1
            else [
                {
                    "query": "ReAct task-specific environment actions",
                    "document_title": "ReAct.pdf",
                },
                {
                    "query": "Self-RAG three types of Critique tokens",
                    "document_title": "Self-RAG.pdf",
                },
            ]
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"evidence_groups": queries})},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Which components belong to RAG, ReAct, and Self-RAG?",
            recent_questions=[],
            document_titles=["RAG.pdf", "ReAct.pdf", "Self-RAG.pdf"],
        )

    assert initial_attempts == 2
    assert proposal.additional_queries == (
        "refined ReAct task-specific environment actions",
        "refined Self-RAG three types of Critique tokens",
    )


async def test_question_planner_discards_persistently_duplicate_document_groups() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "evidence_groups": [
                                        {
                                            "query": "DPR component",
                                            "document_title": "RAG.pdf",
                                        },
                                        {
                                            "query": "BART component",
                                            "document_title": "RAG.pdf",
                                        },
                                    ]
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Which components belong to the available papers?",
            recent_questions=[],
            document_titles=["RAG.pdf", "ReAct.pdf", "Self-RAG.pdf"],
        )

    assert attempts == 2
    assert proposal.additional_queries == ()


async def test_question_planner_uses_original_for_first_of_three_evidence_groups() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        messages = payload["messages"]
        user_input = json.loads(messages[-1]["content"])
        proposed = user_input.get("proposed_evidence_group")
        content = (
            {
                "evidence_group": {
                    "query": f"refined {proposed['query']}",
                    "document_title": proposed["document_title"],
                }
            }
            if proposed is not None
            else {
                "evidence_groups": [
                    {
                        "query": "RAG DPR retriever and BART generator",
                        "document_title": "RAG.pdf",
                    },
                    {
                        "query": "ReAct task-specific environment actions",
                        "document_title": "ReAct.pdf",
                    },
                    {
                        "query": "Self-RAG three types of Critique tokens",
                        "document_title": "Self-RAG.pdf",
                    },
                ]
            }
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(content)},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Which components belong to RAG, ReAct, and Self-RAG?",
            recent_questions=[],
            document_titles=["RAG.pdf", "ReAct.pdf", "Self-RAG.pdf"],
        )

    assert proposal.additional_queries == (
        "refined ReAct task-specific environment actions",
        "refined Self-RAG three types of Critique tokens",
    )


async def test_question_planner_corrects_one_invalid_response() -> None:
    attempts = 0
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        payloads.append(json.loads(request.content))
        queries = (
            [
                {"query": "first", "document_title": "First.pdf"},
                {"query": "second", "document_title": "Second.pdf"},
                {"query": "third", "document_title": "Third.pdf"},
                {"query": "forbidden fourth", "document_title": "Fourth.pdf"},
            ]
            if attempts == 1
            else [{"query": "grouped first", "document_title": "First.pdf"}]
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps({"evidence_groups": queries})},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Question",
            recent_questions=[],
            document_titles=["First.pdf", "Second.pdf", "Third.pdf", "Fourth.pdf"],
        )

    assert attempts == 2
    assert proposal.additional_queries == ("grouped first",)
    retry_messages = payloads[1]["messages"]
    assert isinstance(retry_messages, list)
    assert "previous response violated" in retry_messages[-1]["content"]


async def test_question_planner_retries_one_transient_disconnect() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.RemoteProtocolError("server disconnected before sending a response")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "evidence_groups": [
                                        {
                                            "query": "standalone expansion",
                                            "document_title": "Paper.pdf",
                                        }
                                    ]
                                }
                            )
                        },
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        planner = OpenAICompatibleQuestionPlanner(_config(), client=client)

        proposal = await planner.plan(
            question="Question",
            recent_questions=[],
            document_titles=["Paper.pdf"],
        )

    assert attempts == 2
    assert proposal.additional_queries == ("standalone expansion",)


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
                                    "supplemental_queries": [
                                        "BGE-M3 normalization indexing",
                                    ],
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
            queries=("How are vectors indexed?", "vector indexing"),
            evidence=_evidence(),
            supplemental_query_limit=2,
        )

    assert decision.sufficient is False
    assert decision.selected_chunk_ids == ()
    assert decision.supplemental_queries == ("BGE-M3 normalization indexing",)
    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["stream"] is False
    serialized = json.dumps(payload["messages"])
    assert "chunk-1" in serialized
    assert "BGE-M3 dense vectors" in serialized
    assert "supplemental_query_limit" in serialized
    assert "retrieval_queries" in serialized


async def test_evidence_assessor_retries_one_incorrect_top_level_field_set() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert isinstance(payload, dict)
        payloads.append(payload)
        content: dict[str, object] = {
            "sufficient": True,
            "selected_chunk_ids": ["chunk-1"],
            "supplemental_queries": [],
        }
        if len(payloads) == 1:
            content["explanation"] = "The evidence supports the answer."
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": json.dumps(content)},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assessor = OpenAICompatibleEvidenceAssessor(_config(), client=client)

        decision = await assessor.assess(
            question="How are vectors indexed?",
            queries=("How are vectors indexed?", "vector indexing"),
            evidence=_evidence(),
            supplemental_query_limit=1,
        )

    assert decision.sufficient is True
    assert decision.selected_chunk_ids == ("chunk-1",)
    assert len(payloads) == 2
    retry_messages = payloads[1]["messages"]
    assert isinstance(retry_messages, list)
    retry_instruction = retry_messages[-1]
    assert isinstance(retry_instruction, dict)
    assert "exactly these three top-level fields" in retry_instruction["content"]
    assert "Return no other fields" in retry_instruction["content"]


async def test_evidence_assessor_retries_an_empty_json_mode_response_once() -> None:
    payloads: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert isinstance(payload, dict)
        payloads.append(payload)
        content = (
            ""
            if len(payloads) == 1
            else json.dumps(
                {
                    "sufficient": True,
                    "selected_chunk_ids": ["chunk-1"],
                    "supplemental_queries": [],
                }
            )
        )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}]},
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
            queries=("How are vectors indexed?", "vector indexing"),
            evidence=_evidence(),
            supplemental_query_limit=1,
        )

    assert decision.sufficient is True
    assert decision.selected_chunk_ids == ("chunk-1",)
    assert len(payloads) == 2
    assert all(payload["response_format"] == {"type": "json_object"} for payload in payloads)
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
                            "content": json.dumps({"answer": "Vectors are normalized [citation-1]"})
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


async def test_citation_repairer_accepts_json_followed_by_explanatory_text() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"answer":"Vectors are normalized [citation-1]"}'
                                "\n\nThe citation has been repaired."
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


async def test_citation_repairer_recovers_literal_backslashes_in_json_strings() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": ('{"answer":"The objective is \\(x + y\\) [citation-1]"}')
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
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
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
                queries=("Question", "query"),
                evidence=_evidence(),
                supplemental_query_limit=1,
            )

    assert error.value.code == "LLM_INVALID_RESPONSE"
    assert attempts == 2


async def test_evidence_assessor_rejects_queries_over_remaining_capacity() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
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
                                    "supplemental_queries": ["first", "second"],
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

        with pytest.raises(LlmProviderError) as error:
            await assessor.assess(
                question="Question",
                queries=("Question", "initial expansion"),
                evidence=_evidence(),
                supplemental_query_limit=1,
            )

    assert error.value.code == "LLM_INVALID_RESPONSE"
    assert attempts == 1


@pytest.mark.parametrize(
    "supplemental_queries",
    [
        ["duplicate query", "duplicate query"],
        ["valid query", "   "],
    ],
)
async def test_evidence_assessor_rejects_duplicate_or_blank_queries(
    supplemental_queries: list[str],
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
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
                                    "supplemental_queries": supplemental_queries,
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

        with pytest.raises(LlmProviderError) as error:
            await assessor.assess(
                question="Question",
                queries=("Question",),
                evidence=_evidence(),
                supplemental_query_limit=2,
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
