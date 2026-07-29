"""Exercise the complete SourceTrace MVP through its public HTTP interface."""

import argparse
import asyncio
import json
import time
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

TERMINAL_EVENT_TYPES = {"final", "refusal", "error", "cancelled"}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="verify-sourcetrace-mvp")
    parser.add_argument("--base-url", default="http://localhost:5173")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ingestion-timeout-seconds", type=int, default=600)
    return parser


def _verification_pdf() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_reference = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_reference})}
    )
    content = DecodedStreamObject()
    content.set_data(
        b"BT /F1 12 Tf 72 720 Td "
        b"(SourceTrace verification fact: The Atlas retention period is exactly 37 days.) "
        b"Tj ET"
    )
    page[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


async def _events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    event_name: str | None = None
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
        elif not line and data_lines:
            event = json.loads("\n".join(data_lines))
            if not isinstance(event, dict):
                raise RuntimeError("SSE data is not an object")
            if event_name is not None and event.get("type") != event_name:
                raise RuntimeError("SSE event name does not match its payload")
            yield event
            event_name = None
            data_lines = []
    if data_lines:
        event = json.loads("\n".join(data_lines))
        if not isinstance(event, dict):
            raise RuntimeError("SSE data is not an object")
        yield event


async def _stream_answer(
    client: httpx.AsyncClient,
    answer_url: str,
    content: str,
) -> tuple[list[str], dict[str, Any]]:
    event_types: list[str] = []
    terminal: dict[str, Any] | None = None
    async with client.stream("POST", answer_url, json={"content": content}) as response:
        response.raise_for_status()
        async for event in _events(response):
            event_type = str(event.get("type"))
            event_types.append(event_type)
            if event_type in TERMINAL_EVENT_TYPES:
                terminal = event
    if terminal is None:
        raise RuntimeError("answer stream ended without a terminal event")
    return event_types, terminal


async def _stream_and_cancel(
    client: httpx.AsyncClient,
    answer_url: str,
    content: str,
) -> tuple[list[str], dict[str, Any], str]:
    event_types: list[str] = []
    terminal: dict[str, Any] | None = None
    cancellation_status: str | None = None
    async with client.stream("POST", answer_url, json={"content": content}) as response:
        response.raise_for_status()
        async for event in _events(response):
            event_type = str(event.get("type"))
            event_types.append(event_type)
            if cancellation_status is None:
                run_id = event.get("run_id")
                if not isinstance(run_id, str):
                    raise RuntimeError("answer event is missing its run identifier")
                cancellation = await client.post(f"{answer_url}/{run_id}/cancel")
                cancellation.raise_for_status()
                cancellation_status = str(cancellation.json()["status"])
            if event_type in TERMINAL_EVENT_TYPES:
                terminal = event
    if terminal is None or cancellation_status is None:
        raise RuntimeError("cancelled stream did not produce the expected lifecycle")
    return event_types, terminal, cancellation_status


async def _wait_for_ingestion(
    client: httpx.AsyncClient,
    documents_url: str,
    version_id: str,
    timeout_seconds: int,
) -> list[str]:
    deadline = time.monotonic() + timeout_seconds
    observed: list[str] = []
    while time.monotonic() < deadline:
        response = await client.get(documents_url, params={"limit": 20})
        response.raise_for_status()
        version = next(
            item for item in response.json()["items"] if item["version_id"] == version_id
        )
        state = f"{version['status']}:{version['stage']}"
        if not observed or observed[-1] != state:
            observed.append(state)
        if version["status"] == "completed":
            return observed
        if version["status"] == "failed":
            raise RuntimeError(
                f"ingestion failed: {version['failure_code']} - {version['failure_message']}"
            )
        await asyncio.sleep(2)
    raise TimeoutError("document ingestion did not complete before the configured timeout")


async def _verify(args: argparse.Namespace) -> dict[str, Any]:
    base_url = str(args.base_url).rstrip("/")
    api_url = "/api/v1"
    knowledge_base_id: str | None = None
    cleanup_status: int | None = None
    result: dict[str, Any] = {
        "schema_version": "1",
        "verified_at": datetime.now(UTC).isoformat(),
        "base_url": base_url,
    }
    timeout = httpx.Timeout(300, connect=10)
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout) as client:
        try:
            response = await client.post(
                f"{api_url}/knowledge-bases",
                json={"name": f"MVP verification {uuid4().hex[:8]}"},
            )
            response.raise_for_status()
            knowledge_base_id = str(response.json()["id"])
            documents_url = f"{api_url}/knowledge-bases/{knowledge_base_id}/documents"

            response = await client.post(
                documents_url,
                files={"file": ("mvp-verification.pdf", _verification_pdf(), "application/pdf")},
            )
            response.raise_for_status()
            version_id = str(response.json()["version_id"])
            result["ingestion"] = {
                "upload_status": response.status_code,
                "states": await _wait_for_ingestion(
                    client,
                    documents_url,
                    version_id,
                    args.ingestion_timeout_seconds,
                ),
            }

            response = await client.post(
                f"{api_url}/knowledge-bases/{knowledge_base_id}/conversations",
                json={"title": "MVP verification"},
            )
            response.raise_for_status()
            conversation_id = str(response.json()["id"])
            conversation_url = (
                f"{api_url}/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}"
            )
            answer_url = f"{conversation_url}/answers"

            answer_events, answer = await _stream_answer(
                client,
                answer_url,
                "According to the uploaded document, how long is the Atlas retention period?",
            )
            if answer.get("type") != "final":
                raise RuntimeError(f"grounded question ended with {answer.get('type')}")
            citations = answer.get("citations")
            if not isinstance(citations, list) or len(citations) != 1:
                raise RuntimeError("grounded answer did not contain exactly one citation")
            citation = citations[0]
            if citation.get("document_version_id") != version_id:
                raise RuntimeError("citation does not target the uploaded document version")
            source = await client.get(str(citation["source_url"]))
            source.raise_for_status()
            result["answer"] = {
                "events": answer_events,
                "terminal": "final",
                "citation_count": len(citations),
                "citation_page": citation["page_number"],
                "source_status": source.status_code,
            }

            refusal_events, refusal = await _stream_answer(
                client,
                answer_url,
                "What launch date was approved for Project Orion?",
            )
            if refusal.get("type") != "refusal":
                raise RuntimeError(f"unsupported question ended with {refusal.get('type')}")
            result["refusal"] = {
                "events": refusal_events,
                "terminal": "refusal",
                "code": refusal.get("code"),
            }

            cancellation_events, cancellation, request_status = await _stream_and_cancel(
                client,
                answer_url,
                "Provide a detailed explanation of the Atlas retention policy.",
            )
            if cancellation.get("type") != "cancelled":
                raise RuntimeError(f"cancelled question ended with {cancellation.get('type')}")
            result["cancellation"] = {
                "events": cancellation_events,
                "request_status": request_status,
                "terminal": "cancelled",
            }

            answers = await client.get(answer_url, params={"limit": 20})
            answers.raise_for_status()
            questions = await client.get(f"{conversation_url}/questions", params={"limit": 20})
            questions.raise_for_status()
            answer_items = answers.json()["items"]
            result["history"] = {
                "answer_run_count": len(answer_items),
                "question_count": len(questions.json()["items"]),
                "statuses": sorted(item["status"] for item in answer_items),
                "outcomes": sorted(
                    item["outcome"] for item in answer_items if item["outcome"] is not None
                ),
            }
            if result["history"]["statuses"] != ["cancelled", "completed", "completed"]:
                raise RuntimeError("answer history does not contain the expected terminal states")
            if result["history"]["outcomes"] != ["answered", "refused"]:
                raise RuntimeError("answer history does not contain answer and refusal outcomes")
            if result["history"]["question_count"] != 3:
                raise RuntimeError("question history does not contain all verification questions")
        finally:
            if knowledge_base_id is not None:
                cleanup = await client.delete(
                    f"{api_url}/knowledge-bases/{knowledge_base_id}",
                    params={"confirm": "true"},
                )
                cleanup_status = cleanup.status_code
    result["cleanup_status"] = cleanup_status
    if cleanup_status != 204:
        raise RuntimeError("verification knowledge base cleanup failed")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = asyncio.run(_verify(args))
    serialized = json.dumps(result, indent=2, ensure_ascii=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
