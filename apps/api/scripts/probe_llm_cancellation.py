import asyncio
from time import perf_counter

import httpx

from sourcetrace.core.config import get_settings
from sourcetrace.rag.llm import (
    LlmProviderError,
    OpenAICompatibleAnswerGenerator,
    OpenAICompatibleConfig,
)
from sourcetrace.rag.ports import RetrievalCandidate


async def probe() -> int:
    settings = get_settings()
    if settings.llm_api_key is None:
        print("provider_cancel_probe_failed code=LLM_API_KEY_MISSING")
        return 1
    async with httpx.AsyncClient() as client:
        provider = OpenAICompatibleAnswerGenerator(
            OpenAICompatibleConfig(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key.get_secret_value(),
                model=settings.llm_model,
                connect_timeout_seconds=settings.llm_connect_timeout_seconds,
                read_timeout_seconds=settings.llm_read_timeout_seconds,
                request_timeout_seconds=settings.llm_request_timeout_seconds,
                operation_deadline_seconds=settings.llm_operation_deadline_seconds,
                prompt_version=settings.llm_prompt_version,
            ),
            client=client,
        )
        stream = provider.stream_answer(
            question="How are vectors stored?",
            evidence=[
                RetrievalCandidate(
                    chunk_id="provider-cancel-probe-chunk",
                    content="Vectors are normalized before storage.",
                    score=1.0,
                    citation_id="provider-cancel-probe-citation",
                )
            ],
        )
        started_at = perf_counter()
        try:
            await anext(stream)
        except (LlmProviderError, StopAsyncIteration) as error:
            code = error.code if isinstance(error, LlmProviderError) else "LLM_EMPTY_RESPONSE"
            print(f"provider_cancel_probe_failed code={code}")
            return 1
        first_delta_ms = round((perf_counter() - started_at) * 1000)
        close_started_at = perf_counter()
        await stream.aclose()
        close_ms = round((perf_counter() - close_started_at) * 1000)
    print(
        f"provider_cancel_probe_ok model={settings.llm_model} "
        f"first_delta_received=true first_delta_ms={first_delta_ms} close_ms={close_ms}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
