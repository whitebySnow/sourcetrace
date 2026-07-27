import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError } from "@/shared/api/client";

import KnowledgeBaseDetailPage from "../pages/KnowledgeBaseDetailPage.vue";

const {
  push,
  getKnowledgeBase,
  deleteKnowledgeBase,
  listDocumentVersions,
  uploadDocument,
} = vi.hoisted(() => ({
  push: vi.fn(),
  getKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  listDocumentVersions: vi.fn(),
  uploadDocument: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => ({ params: { id: "4a43e866-5694-4d4c-955d-69d1a58a2a17" } }),
  useRouter: () => ({ push }),
}));

vi.mock("../api/knowledgeBases", () => ({
  getKnowledgeBase,
  deleteKnowledgeBase,
}));

vi.mock("@/features/documents/api/documents", () => ({
  listDocumentVersions,
  uploadDocument,
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
    listDocumentVersions.mockResolvedValue({ items: [], next_cursor: null });
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

  it("uploads a selected PDF and displays its pending version", async () => {
    uploadDocument.mockResolvedValue({
      document_id: "2d26f860-9374-4f86-b5c7-821355328cbc",
      version_id: "006871ac-1c11-4854-ae6e-084a67cac73a",
      name: "paper.pdf",
      version_number: 1,
      checksum_sha256: "a".repeat(64),
      file_size_bytes: 1024,
      page_count: 4,
      status: "pending",
      created_at: "2026-07-27T08:00:00Z",
      deduplicated: false,
      request_id: "request-123",
    });
    const wrapper = mount(KnowledgeBaseDetailPage);
    await flushPromises();
    const file = new File(["%PDF-1.7"], "paper.pdf", {
      type: "application/pdf",
    });
    const input = wrapper.get('[data-test="pdf-input"]');
    Object.defineProperty(input.element, "files", { value: [file] });

    await input.trigger("change");
    await wrapper.get('[data-test="upload-document"]').trigger("click");
    await flushPromises();

    expect(uploadDocument).toHaveBeenCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      file,
    );
    expect(wrapper.text()).toContain("paper.pdf");
    expect(wrapper.text()).toContain("待处理");
    expect(wrapper.text()).toContain("4 页");
  });

  it("displays pending versions loaded from the API", async () => {
    listDocumentVersions.mockResolvedValue({
      items: [
        {
          document_id: "2d26f860-9374-4f86-b5c7-821355328cbc",
          version_id: "006871ac-1c11-4854-ae6e-084a67cac73a",
          name: "existing.pdf",
          version_number: 2,
          checksum_sha256: "b".repeat(64),
          file_size_bytes: 2_097_152,
          page_count: 12,
          status: "pending",
          created_at: "2026-07-27T08:00:00Z",
        },
      ],
      next_cursor: null,
    });

    const wrapper = mount(KnowledgeBaseDetailPage);
    await flushPromises();

    expect(wrapper.text()).toContain("existing.pdf");
    expect(wrapper.text()).toContain("版本 2");
    expect(wrapper.text()).toContain("12 页");
    expect(wrapper.text()).toContain("2.0 MB");
    expect(wrapper.text()).toContain("待处理");
  });

  it("displays the ingestion status returned by the API", async () => {
    listDocumentVersions.mockResolvedValue({
      items: [
        {
          document_id: "2d26f860-9374-4f86-b5c7-821355328cbc",
          version_id: "006871ac-1c11-4854-ae6e-084a67cac73a",
          name: "processing.pdf",
          version_number: 1,
          checksum_sha256: "c".repeat(64),
          file_size_bytes: 1024,
          page_count: 2,
          status: "processing",
          created_at: "2026-07-27T08:00:00Z",
        },
      ],
      next_cursor: null,
    });

    const wrapper = mount(KnowledgeBaseDetailPage);
    await flushPromises();

    expect(wrapper.text()).toContain("处理中");
    expect(wrapper.text()).not.toContain("待处理");
  });

  it("shows the safe API error and request ID when upload fails", async () => {
    uploadDocument.mockRejectedValue(
      new ApiClientError(
        "PDF_ENCRYPTED",
        "Encrypted PDFs are not supported",
        "request-456",
      ),
    );
    const wrapper = mount(KnowledgeBaseDetailPage);
    await flushPromises();
    const file = new File(["%PDF-1.7"], "secret.pdf", {
      type: "application/pdf",
    });
    const input = wrapper.get('[data-test="pdf-input"]');
    Object.defineProperty(input.element, "files", { value: [file] });

    await input.trigger("change");
    await wrapper.get('[data-test="upload-document"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("Encrypted PDFs are not supported");
    expect(wrapper.text()).toContain("request-456");
    expect(wrapper.text()).not.toContain("%PDF-1.7");
  });
});
