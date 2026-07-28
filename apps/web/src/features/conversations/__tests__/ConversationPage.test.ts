import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ConversationPage from "../pages/ConversationPage.vue";

const {
  cancelAnswer,
  getConversation,
  listAnswers,
  listQuestions,
  push,
  streamAnswer,
} = vi.hoisted(() => ({
  cancelAnswer: vi.fn(),
  getConversation: vi.fn(),
  listAnswers: vi.fn(),
  listQuestions: vi.fn(),
  push: vi.fn(),
  streamAnswer: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({
    params: {
      knowledgeBaseId: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      conversationId: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
    },
  }),
  useRouter: () => ({ push }),
}));

vi.mock("../api/conversations", () => ({
  getConversation,
  listQuestions,
}));

vi.mock("@/features/answers/api/answers", () => ({
  cancelAnswer,
  listAnswers,
  streamAnswer,
}));

describe("ConversationPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getConversation.mockResolvedValue({
      id: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      knowledge_base_id: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      title: "Embedding discussion",
      created_at: "2026-07-28T08:00:00Z",
      updated_at: "2026-07-28T08:00:00Z",
    });
    listQuestions.mockResolvedValue({
      items: [
        {
          id: "352ec085-e8f4-4b9c-b488-a971b8460ab3",
          conversation_id: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
          content: "What is dense retrieval?",
          created_at: "2026-07-28T08:01:00Z",
        },
      ],
      next_cursor: null,
    });
    listAnswers.mockResolvedValue({
      items: [
        {
          id: "0c2852c2-d2a7-429a-91fe-644e6bcdbf3a",
          question_id: "352ec085-e8f4-4b9c-b488-a971b8460ab3",
          question_content: "What is dense retrieval?",
          status: "completed",
          outcome: "answered",
          answer: "Dense retrieval compares vector similarity.",
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
    cancelAnswer.mockResolvedValue({ run_id: "run-1", status: "cancelled" });
  });

  it("renders persisted answers and streams a cited answer", async () => {
    streamAnswer.mockImplementation(
      async (_kb, _conversation, _content, onEvent) => {
        onEvent({
          version: "1",
          type: "status",
          run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
          status: "retrieving",
        });
        onEvent({
          version: "1",
          type: "delta",
          run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
          delta: "Cosine distance ",
        });
        onEvent({
          version: "1",
          type: "delta",
          run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
          delta: "ranks normalized vectors.",
        });
        onEvent({
          version: "1",
          type: "final",
          run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
          answer: "Cosine distance ranks normalized vectors.",
          citations: [
            {
              id: "4cdca710-0c4e-5aaa-9060-fbe1195924d0",
              document_id: "502fd665-d160-4087-a38a-82072331933e",
              document_version_id: "257284be-79e8-4e61-82d7-88b086d18001",
              document_name: "vectors.pdf",
              page_number: 4,
              excerpt: "Vectors are normalized before cosine search.",
              source_url: "/api/v1/source.pdf#page=4",
            },
          ],
        });
      },
    );
    const wrapper = mount(ConversationPage);
    await flushPromises();

    expect(wrapper.text()).toContain("Embedding discussion");
    expect(wrapper.text()).toContain("What is dense retrieval?");
    expect(wrapper.text()).toContain(
      "Dense retrieval compares vector similarity.",
    );
    await wrapper
      .get('[data-test="question-content"]')
      .setValue("How is cosine distance used?");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(streamAnswer).toHaveBeenCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      "How is cosine distance used?",
      expect.any(Function),
      expect.any(AbortSignal),
    );
    expect(wrapper.text()).toContain(
      "Cosine distance ranks normalized vectors.",
    );
    expect(wrapper.text()).toContain("vectors.pdf");
    expect(wrapper.text()).toContain("第 4 页");
  });

  it("renders an explicit refusal without a fabricated citation", async () => {
    listQuestions.mockResolvedValue({ items: [], next_cursor: null });
    listAnswers.mockResolvedValue({ items: [], next_cursor: null });
    streamAnswer.mockImplementation(
      async (_kb, _conversation, _content, onEvent) => {
        onEvent({
          version: "1",
          type: "delta",
          run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
          delta: "Unvalidated draft must disappear.",
        });
        onEvent({
          version: "1",
          type: "refusal",
          run_id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
          code: "INSUFFICIENT_EVIDENCE",
          message:
            "The knowledge base does not contain enough evidence to answer.",
        });
      },
    );
    const wrapper = mount(ConversationPage);
    await flushPromises();

    await wrapper
      .get('[data-test="question-content"]')
      .setValue("Unknown topic");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain(
      "The knowledge base does not contain enough evidence to answer.",
    );
    expect(wrapper.text()).not.toContain("Unvalidated draft must disappear.");
    expect(wrapper.findAll(".citation")).toHaveLength(0);
  });

  it("keeps a completed answer visible when another question is asked", async () => {
    listQuestions.mockResolvedValue({ items: [], next_cursor: null });
    listAnswers.mockResolvedValue({ items: [], next_cursor: null });
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
    const wrapper = mount(ConversationPage);
    await flushPromises();

    await wrapper
      .get('[data-test="question-content"]')
      .setValue("First question");
    await wrapper.get("form").trigger("submit");
    await flushPromises();
    await wrapper
      .get('[data-test="question-content"]')
      .setValue("Second question");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(wrapper.text()).toContain("First answer");
    expect(wrapper.text()).toContain("Second answer");
  });

  it("loads answer history alongside the next page of questions", async () => {
    listQuestions
      .mockResolvedValueOnce({
        items: [
          {
            id: "question-page-1",
            conversation_id: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
            content: "Question on page one",
            created_at: "2026-07-28T08:01:00Z",
          },
        ],
        next_cursor: "question-cursor",
      })
      .mockResolvedValueOnce({
        items: [
          {
            id: "question-page-2",
            conversation_id: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
            content: "Question on page two",
            created_at: "2026-07-28T08:02:00Z",
          },
        ],
        next_cursor: null,
      });
    listAnswers
      .mockResolvedValueOnce({ items: [], next_cursor: "answer-cursor" })
      .mockResolvedValueOnce({
        items: [
          {
            id: "answer-page-2",
            question_id: "question-page-2",
            question_content: "Question on page two",
            status: "completed",
            outcome: "answered",
            answer: "Answer on page two",
            refusal_code: null,
            refusal_message: null,
            failure_code: null,
            failure_message: null,
            llm_provider: "openai-compatible",
            llm_model: "gpt-5.6-luna",
            prompt_version: "grounded-answer-v1",
            retrieval_version: "pgvector-cosine-v1",
            workflow_version: "linear-grounded-v1",
            created_at: "2026-07-28T08:02:01Z",
            completed_at: "2026-07-28T08:02:02Z",
            citations: [],
          },
        ],
        next_cursor: null,
      });
    const wrapper = mount(ConversationPage);
    await flushPromises();

    await wrapper.get(".load-more-button").trigger("click");
    await flushPromises();

    expect(listQuestions).toHaveBeenLastCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      "question-cursor",
    );
    expect(listAnswers).toHaveBeenLastCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      "answer-cursor",
    );
    expect(wrapper.text()).toContain("Answer on page two");
  });

  it("renders a persisted provider failure as a failure, not a refusal", async () => {
    listAnswers.mockResolvedValue({
      items: [
        {
          id: "failed-run",
          question_id: "352ec085-e8f4-4b9c-b488-a971b8460ab3",
          question_content: "What is dense retrieval?",
          status: "failed",
          outcome: null,
          answer: null,
          refusal_code: null,
          refusal_message: null,
          failure_code: "LLM_TIMEOUT",
          failure_message: "Language model request timed out",
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
    const wrapper = mount(ConversationPage);
    await flushPromises();

    expect(wrapper.text()).toContain("Language model request timed out");
    expect(wrapper.findAll(".failure-text")).toHaveLength(1);
    expect(wrapper.findAll(".refusal-text")).toHaveLength(0);
  });

  it("cancels an active answer and removes its partial text", async () => {
    listQuestions.mockResolvedValue({ items: [], next_cursor: null });
    listAnswers.mockResolvedValue({ items: [], next_cursor: null });
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
          delta: "Partial answer to remove",
        });
        await new Promise<void>((_resolve, reject) => {
          signal.addEventListener("abort", () => {
            reject(new DOMException("Aborted", "AbortError"));
          });
        });
      },
    );
    const wrapper = mount(ConversationPage);
    await flushPromises();

    await wrapper.get('[data-test="question-content"]').setValue("Cancel me");
    await wrapper.get("form").trigger("submit");
    await vi.waitFor(() => {
      expect(wrapper.get('[data-test="cancel-answer"]').text()).toContain(
        "取消回答",
      );
    });
    expect(wrapper.text()).toContain("Partial answer to remove");

    await wrapper.get('[data-test="cancel-answer"]').trigger("click");
    await flushPromises();

    expect(cancelAnswer).toHaveBeenCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      "run-1",
    );
    expect(wrapper.text()).toContain("回答已取消");
    expect(wrapper.text()).not.toContain("Partial answer to remove");
  });

  it("renders a persisted cancelled run as cancelled", async () => {
    listAnswers.mockResolvedValue({
      items: [
        {
          id: "cancelled-run",
          question_id: "352ec085-e8f4-4b9c-b488-a971b8460ab3",
          question_content: "What is dense retrieval?",
          status: "cancelled",
          outcome: null,
          answer: null,
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
    const wrapper = mount(ConversationPage);
    await flushPromises();

    expect(wrapper.text()).toContain("回答已取消");
    expect(wrapper.text()).not.toContain("回答仍在处理中");
  });
});
