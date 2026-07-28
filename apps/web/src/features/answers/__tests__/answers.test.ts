import { afterEach, describe, expect, it, vi } from "vitest";

import { streamAnswer, type AnswerEvent } from "../api/answers";

describe("answer stream API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("parses SSE events split across network chunks", async () => {
    const encoder = new TextEncoder();
    const chunks = [
      'event: status\ndata: {"version":"1","type":"status","run_id":',
      '"397ac9a7-8c66-4703-9ef3-b5ae3b015b9c","status":"retrieving"}\n\n' +
        'event: delta\ndata: {"version":"1","type":"delta","run_id":',
      '"397ac9a7-8c66-4703-9ef3-b5ae3b015b9c","delta":"Evidence"}\n\n' +
        'event: final\ndata: {"version":"1","type":"final","run_id":' +
        '"397ac9a7-8c66-4703-9ef3-b5ae3b015b9c","answer":"Evidence",' +
        '"citations":[]}\n\n',
    ];
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
        controller.close();
      },
    });
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(body, {
        status: 200,
        headers: { "content-type": "text/event-stream" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const events: AnswerEvent[] = [];
    const controller = new AbortController();

    await streamAnswer(
      "kb-id",
      "conversation-id",
      "Question",
      (event) => {
        events.push(event);
      },
      controller.signal,
    );

    expect(fetchMock).toHaveBeenCalledWith(
      "http://localhost:8000/api/v1/knowledge-bases/kb-id/conversations/" +
        "conversation-id/answers",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ content: "Question" }),
        signal: controller.signal,
      }),
    );
    expect(events).toEqual([
      {
        version: "1",
        type: "status",
        run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
        status: "retrieving",
      },
      {
        version: "1",
        type: "delta",
        run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
        delta: "Evidence",
      },
      {
        version: "1",
        type: "final",
        run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
        answer: "Evidence",
        citations: [],
      },
    ]);
  });

  it("rejects a stream that ends without a terminal event", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'data: {"version":"1","type":"delta","run_id":' +
              '"397ac9a7-8c66-4703-9ef3-b5ae3b015b9c","delta":"Partial"}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(body, { status: 200 })),
    );

    await expect(
      streamAnswer("kb-id", "conversation-id", "Question", () => undefined),
    ).rejects.toMatchObject({ code: "INVALID_STREAM" });
  });

  it("accepts cancellation as a terminal stream event", async () => {
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          new TextEncoder().encode(
            'data: {"version":"1","type":"cancelled","run_id":' +
              '"397ac9a7-8c66-4703-9ef3-b5ae3b015b9c"}\n\n',
          ),
        );
        controller.close();
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response(body, { status: 200 })),
    );
    const events: AnswerEvent[] = [];

    await streamAnswer("kb-id", "conversation-id", "Question", (event) => {
      events.push(event);
    });

    expect(events).toEqual([
      {
        version: "1",
        type: "cancelled",
        run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
      },
    ]);
  });
});
