import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ConversationPage from "../pages/ConversationPage.vue";

const { createQuestion, getConversation, listQuestions, push } = vi.hoisted(() => ({
  createQuestion: vi.fn(),
  getConversation: vi.fn(),
  listQuestions: vi.fn(),
  push: vi.fn(),
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
  createQuestion,
  getConversation,
  listQuestions,
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
  });

  it("shows durable question history and records a new question", async () => {
    createQuestion.mockResolvedValue({
      id: "397ac9a7-8c66-4703-9ef3-b5ae3b015b9c",
      conversation_id: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      content: "How is cosine distance used?",
      created_at: "2026-07-28T08:02:00Z",
    });
    const wrapper = mount(ConversationPage);
    await flushPromises();

    expect(wrapper.text()).toContain("Embedding discussion");
    expect(wrapper.text()).toContain("What is dense retrieval?");
    await wrapper
      .get('[data-test="question-content"]')
      .setValue("How is cosine distance used?");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(createQuestion).toHaveBeenCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      "How is cosine distance used?",
    );
    expect(wrapper.text()).toContain("How is cosine distance used?");
  });
});
