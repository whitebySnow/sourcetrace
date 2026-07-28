import { describe, expect, it, vi } from "vitest";

import { useAnswers } from "../composables/useAnswers";

const { listAnswers, streamAnswer } = vi.hoisted(() => ({
  listAnswers: vi.fn(),
  streamAnswer: vi.fn(),
}));

vi.mock("../api/answers", () => ({
  listAnswers,
  streamAnswer,
}));

describe("useAnswers", () => {
  it("removes a recent duplicate once the persisted run is loaded", async () => {
    streamAnswer.mockImplementation(async (_kb, _conversation, content, onEvent) => {
      onEvent({
        version: "1",
        type: "final",
        run_id: content === "First question" ? "run-1" : "run-2",
        answer: content === "First question" ? "First answer" : "Second answer",
        citations: [],
      });
    });
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
    expect(answers.recentAnswers.value.map((item) => item.id)).toEqual(["run-1"]);

    await answers.load();

    expect(answers.recentAnswers.value).toEqual([]);
    expect(answers.answers.value.map((item) => item.id)).toEqual(["run-1"]);
  });
});
