import asyncio

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
        print("provider_probe_failed code=LLM_API_KEY_MISSING")
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
        delta_count = 0
        character_count = 0
        try:
            async for delta in provider.stream_answer(
                question="How are vectors stored?",
                evidence=[
                    RetrievalCandidate(
                        chunk_id="provider-probe-chunk",
                        content="Vectors are normalized before storage.",
                        score=1.0,
                        citation_id="provider-probe-citation",
                    )
                ],
            ):
                delta_count += 1
                character_count += len(delta)
        except LlmProviderError as error:
            print(
                f"provider_probe_failed code={error.code} "
                f"message={error.safe_message}"
            )
            return 1
    if delta_count == 0 or character_count == 0:
        print("provider_probe_failed code=LLM_EMPTY_RESPONSE")
        return 1
    print(
        f"provider_probe_ok model={settings.llm_model} "
        f"deltas={delta_count} characters={character_count}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(probe()))
