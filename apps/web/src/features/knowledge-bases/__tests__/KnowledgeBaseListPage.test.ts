import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import KnowledgeBaseListPage from "../pages/KnowledgeBaseListPage.vue";

const { push, listKnowledgeBases, createKnowledgeBase, deleteKnowledgeBase } =
  vi.hoisted(() => ({
    push: vi.fn(),
    listKnowledgeBases: vi.fn(),
    createKnowledgeBase: vi.fn(),
    deleteKnowledgeBase: vi.fn(),
  }));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("../api/knowledgeBases", () => ({
  listKnowledgeBases,
  createKnowledgeBase,
  deleteKnowledgeBase,
}));

describe("KnowledgeBaseListPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listKnowledgeBases.mockResolvedValue({
      items: [
        {
          id: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
          name: "Agent 工程资料",
          created_at: "2026-07-25T08:00:00Z",
          updated_at: "2026-07-25T08:00:00Z",
        },
      ],
      next_cursor: null,
    });
  });

  it("loads knowledge bases and opens the selected item", async () => {
    const wrapper = mount(KnowledgeBaseListPage);
    await flushPromises();

    expect(wrapper.text()).toContain("Agent 工程资料");
    await wrapper.get('[aria-label="打开 Agent 工程资料"]').trigger("click");
    expect(push).toHaveBeenCalledWith({
      name: "knowledge-base-detail",
      params: { id: "4a43e866-5694-4d4c-955d-69d1a58a2a17" },
    });
  });

  it("creates a trimmed knowledge base and adds it to the list", async () => {
    createKnowledgeBase.mockResolvedValue({
      id: "5ab05aa0-1320-40d6-a2b3-750cfcc7708e",
      name: "RAG 论文",
      created_at: "2026-07-25T09:00:00Z",
      updated_at: "2026-07-25T09:00:00Z",
    });

    const wrapper = mount(KnowledgeBaseListPage);
    await flushPromises();
    await wrapper.get('[aria-label="新建知识库"]').trigger("click");
    await wrapper
      .get('input[name="knowledge-base-name"]')
      .setValue("  RAG 论文  ");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(createKnowledgeBase).toHaveBeenCalledWith("RAG 论文");
    expect(wrapper.text()).toContain("RAG 论文");
  });
});
