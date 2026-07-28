import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ConversationPanel from "../components/ConversationPanel.vue";

const { push, createConversation, listConversations } = vi.hoisted(() => ({
  push: vi.fn(),
  createConversation: vi.fn(),
  listConversations: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("../api/conversations", () => ({
  createConversation,
  listConversations,
}));

describe("ConversationPanel", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    listConversations.mockResolvedValue({
      items: [
        {
          id: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
          knowledge_base_id: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
          title: "Existing discussion",
          created_at: "2026-07-28T08:00:00Z",
          updated_at: "2026-07-28T08:00:00Z",
        },
      ],
      next_cursor: null,
    });
  });

  it("lists and opens an existing conversation", async () => {
    const wrapper = mount(ConversationPanel, {
      props: { knowledgeBaseId: "4a43e866-5694-4d4c-955d-69d1a58a2a17" },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("Existing discussion");
    await wrapper.get('[data-test="open-conversation"]').trigger("click");

    expect(push).toHaveBeenCalledWith({
      name: "conversation",
      params: {
        knowledgeBaseId: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
        conversationId: "1869ba43-f7a8-4618-9aa7-89694a0efc92",
      },
    });
  });

  it("creates a conversation and opens it", async () => {
    createConversation.mockResolvedValue({
      id: "58f47086-bc42-459c-ae56-ab625f95a5e7",
      knowledge_base_id: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      title: "Vector search",
      created_at: "2026-07-28T08:05:00Z",
      updated_at: "2026-07-28T08:05:00Z",
    });
    const wrapper = mount(ConversationPanel, {
      props: { knowledgeBaseId: "4a43e866-5694-4d4c-955d-69d1a58a2a17" },
    });
    await flushPromises();

    await wrapper.get('[data-test="conversation-title"]').setValue("Vector search");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(createConversation).toHaveBeenCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      "Vector search",
    );
    expect(push).toHaveBeenCalledWith({
      name: "conversation",
      params: {
        knowledgeBaseId: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
        conversationId: "58f47086-bc42-459c-ae56-ab625f95a5e7",
      },
    });
  });
});
