import { describe, expect, it, vi } from "vitest";

import { useAnswers } from "../composables/useAnswers";

const { cancelAnswer, listAnswers, streamAnswer } = vi.hoisted(() => ({
  cancelAnswer: vi.fn(),
  listAnswers: vi.fn(),
  streamAnswer: vi.fn(),
}));

vi.mock("../api/answers", () => ({
  cancelAnswer,
  listAnswers,
  streamAnswer,
}));

describe("useAnswers", () => {
  it("removes a recent duplicate once the persisted run is loaded", async () => {
    streamAnswer.mockImplementation(
      async (_kb, _conversation, content, onEvent) => {
        onEvent({
          version: "1",
          type: "final",
          run_id: content === "First question" ? "run-1" : "run-2",
          answer:
            content === "First question" ? "First answer" : "Second answer",
          citations: [],
        });
      },
    );
    listAnswers.mockResolvedValue({
      items: [
        {
          id: "run-1",
          question_id: "question-1",
          question_content: "First question",
          status: "completed",
          outcome: "answered",
          answer: "First answer",
          refusal_code: null,
          refusal_message: null,
          failure_code: null,
          failure_message: null,
          llm_provider: "openai-compatible",
          llm_model: "gpt-5.6-luna",
          prompt_version: "grounded-answer-v1",
          retrieval_version: "pgvector-cosine-v1",
          workflow_version: "linear-grounded-v1",
          created_at: "2026-07-28T08:01:01Z",
          completed_at: "2026-07-28T08:01:02Z",
          citations: [],
        },
      ],
      next_cursor: null,
    });
    const answers = useAnswers("kb-id", "conversation-id");

    await answers.ask("First question");
    await answers.ask("Second question");
    expect(answers.recentAnswers.value.map((item) => item.id)).toEqual([
      "run-1",
    ]);

    await answers.load();

    expect(answers.recentAnswers.value).toEqual([]);
    expect(answers.answers.value.map((item) => item.id)).toEqual(["run-1"]);
  });

  it("aborts the stream, requests cancellation, and discards partial text", async () => {
    cancelAnswer.mockResolvedValue({ run_id: "run-1", status: "cancelled" });
    streamAnswer.mockImplementation(
      async (_kb, _conversation, _content, onEvent, signal: AbortSignal) => {
        onEvent({
          version: "1",
          type: "status",
          run_id: "run-1",
          status: "generating",
        });
        onEvent({
          version: "1",
          type: "delta",
          run_id: "run-1",
          delta: "Discard this partial answer",
        });
        await new Promise<void>((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        });
      },
    );
    const answers = useAnswers("kb-id", "conversation-id");

    const request = answers.ask("Question to cancel");
    await vi.waitFor(() => expect(answers.activeRunId.value).toBe("run-1"));
    await answers.cancel();
    await request;

    expect(cancelAnswer).toHaveBeenCalledWith(
      "kb-id",
      "conversation-id",
      "run-1",
    );
    expect(answers.activeAnswer.value).toBe("");
    expect(answers.activeCitations.value).toEqual([]);
    expect(answers.activeFailure.value).toBe("");
    expect(answers.activeStatus.value).toBe("cancelled");
    expect(answers.submitting.value).toBe(false);
  });

  it("keeps submission locked and reconciles a completion that wins cancellation", async () => {
    let resolveCancellation:
      ((value: { run_id: string; status: string }) => void) | undefined;
    cancelAnswer.mockReturnValue(
      new Promise((resolve) => {
        resolveCancellation = resolve;
      }),
    );
    listAnswers.mockResolvedValue({
      items: [
        {
          id: "run-1",
          question_id: "question-1",
          question_content: "Question completed during cancellation",
          status: "completed",
          outcome: "answered",
          answer: "Persisted final answer",
          refusal_code: null,
          refusal_message: null,
          failure_code: null,
          failure_message: null,
          llm_provider: "openai-compatible",
          llm_model: "gpt-5.6-luna",
          prompt_version: "grounded-answer-v1",
          retrieval_version: "pgvector-cosine-v1",
          workflow_version: "linear-grounded-v1",
          created_at: "2026-07-28T08:01:01Z",
          completed_at: "2026-07-28T08:01:02Z",
          citations: [],
        },
      ],
      next_cursor: null,
    });
    streamAnswer.mockImplementation(
      async (_kb, _conversation, _content, onEvent, signal: AbortSignal) => {
        onEvent({
          version: "1",
          type: "status",
          run_id: "run-1",
          status: "generating",
        });
        await new Promise<void>((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        });
      },
    );
    const answers = useAnswers("kb-id", "conversation-id");

    const request = answers.ask("Question completed during cancellation");
    await vi.waitFor(() => expect(answers.activeRunId.value).toBe("run-1"));
    const cancellation = answers.cancel();
    await vi.waitFor(() => expect(cancelAnswer).toHaveBeenCalled());

    expect(answers.submitting.value).toBe(true);
    resolveCancellation?.({ run_id: "run-1", status: "completed" });
    await cancellation;
    await request;

    expect(listAnswers).toHaveBeenCalledWith("kb-id", "conversation-id");
    expect(answers.activeStatus.value).toBe("completed");
    expect(answers.activeAnswer.value).toBe("Persisted final answer");
    expect(answers.submitting.value).toBe(false);
  });
});
