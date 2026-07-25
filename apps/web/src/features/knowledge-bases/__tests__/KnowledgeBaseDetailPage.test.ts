import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import KnowledgeBaseDetailPage from "../pages/KnowledgeBaseDetailPage.vue";

const { push, getKnowledgeBase, deleteKnowledgeBase } = vi.hoisted(() => ({
  push: vi.fn(),
  getKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "4a43e866-5694-4d4c-955d-69d1a58a2a17" } }),
  useRouter: () => ({ push }),
}));

vi.mock("../api/knowledgeBases", () => ({
  getKnowledgeBase,
  deleteKnowledgeBase,
}));

describe("KnowledgeBaseDetailPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getKnowledgeBase.mockResolvedValue({
      id: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      name: "Agent 工程资料",
      created_at: "2026-07-25T08:00:00Z",
      updated_at: "2026-07-25T08:00:00Z",
    });
  });

  it("requires confirmation before permanently deleting", async () => {
    deleteKnowledgeBase.mockResolvedValue(undefined);
    const wrapper = mount(KnowledgeBaseDetailPage);
    await flushPromises();

    await wrapper.get('[aria-label="删除知识库"]').trigger("click");
    expect(deleteKnowledgeBase).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("永久删除 Agent 工程资料？");

    await wrapper.get('[data-test="confirm-delete"]').trigger("click");
    await flushPromises();

    expect(deleteKnowledgeBase).toHaveBeenCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
    );
    expect(push).toHaveBeenCalledWith({ name: "knowledge-bases" });
  });
});
