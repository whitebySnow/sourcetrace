import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError } from "@/shared/api/client";

import KnowledgeBaseDetailPage from "../pages/KnowledgeBaseDetailPage.vue";

const {
  push,
  getKnowledgeBase,
  deleteKnowledgeBase,
  listDocumentVersions,
  uploadDocument,
  retryDocumentIngestion,
} = vi.hoisted(() => ({
  push: vi.fn(),
  getKnowledgeBase: vi.fn(),
  deleteKnowledgeBase: vi.fn(),
  listDocumentVersions: vi.fn(),
  uploadDocument: vi.fn(),
  retryDocumentIngestion: vi.fn(),
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
  retryDocumentIngestion,
}));

describe("KnowledgeBaseDetailPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    getKnowledgeBase.mockResolvedValue({
      id: "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      name: "Agent 工程资料",
      created_at: "2026-07-25T08:00:00Z",
      updated_at: "2026-07-25T08:00:00Z",
    });
    listDocumentVersions.mockResolvedValue({ items: [], next_cursor: null });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls active ingestion every two seconds and stops at a terminal state", async () => {
    vi.useFakeTimers();
    const pending = {
      document_id: "2d26f860-9374-4f86-b5c7-821355328cbc",
      version_id: "006871ac-1c11-4854-ae6e-084a67cac73a",
      name: "processing.pdf",
      version_number: 1,
      checksum_sha256: "c".repeat(64),
      file_size_bytes: 1024,
      page_count: 2,
      status: "processing",
      stage: "parsing",
      attempt_count: 1,
      retryable: false,
      failure_code: null,
      failure_message: null,
      created_at: "2026-07-27T08:00:00Z",
    };
    listDocumentVersions
      .mockResolvedValueOnce({ items: [pending], next_cursor: null })
      .mockResolvedValueOnce({
        items: [{ ...pending, status: "chunked", stage: "chunked" }],
        next_cursor: null,
      });
    mount(KnowledgeBaseDetailPage);
    await flushPromises();

    await vi.advanceTimersByTimeAsync(1_999);
    expect(listDocumentVersions).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    await flushPromises();
    expect(listDocumentVersions).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(4_000);
    expect(listDocumentVersions).toHaveBeenCalledTimes(2);
  });

  it("offers manual retry only for recoverable failures", async () => {
    const failed = {
      document_id: "2d26f860-9374-4f86-b5c7-821355328cbc",
      version_id: "006871ac-1c11-4854-ae6e-084a67cac73a",
      name: "failed.pdf",
      version_number: 1,
      checksum_sha256: "d".repeat(64),
      file_size_bytes: 1024,
      page_count: 2,
      status: "failed",
      stage: "failed",
      attempt_count: 3,
      retryable: true,
      failure_code: "STORAGE_UNAVAILABLE",
      failure_message: "Storage is temporarily unavailable",
      created_at: "2026-07-27T08:00:00Z",
    };
    listDocumentVersions.mockResolvedValue({
      items: [failed],
      next_cursor: null,
    });
    retryDocumentIngestion.mockResolvedValue({
      version_id: failed.version_id,
      status: "pending",
      stage: "queued",
      attempt_count: 0,
      retryable: false,
      failure_code: null,
    });
    const wrapper = mount(KnowledgeBaseDetailPage);
    await flushPromises();

    expect(wrapper.text()).toContain("Storage is temporarily unavailable");
    await wrapper.get('[data-test="retry-ingestion"]').trigger("click");
    await flushPromises();

    expect(retryDocumentIngestion).toHaveBeenCalledWith(
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      failed.version_id,
    );
    expect(wrapper.text()).toContain("待处理");
  });

  it("polls active documents loaded from later pages until they are terminal", async () => {
    vi.useFakeTimers();
    const base = {
      document_id: "2d26f860-9374-4f86-b5c7-821355328cbc",
      version_id: "006871ac-1c11-4854-ae6e-084a67cac73a",
      name: "older.pdf",
      version_number: 1,
      checksum_sha256: "e".repeat(64),
      file_size_bytes: 1024,
      page_count: 2,
      attempt_count: 1,
      retryable: false,
      failure_code: null,
      failure_message: null,
      created_at: "2026-07-27T08:00:00Z",
    };
    const recent = {
      ...base,
      version_id: "106871ac-1c11-4854-ae6e-084a67cac73a",
      name: "recent.pdf",
      status: "chunked",
      stage: "chunked",
    };
    const active = { ...base, status: "processing", stage: "parsing" };
    listDocumentVersions
      .mockResolvedValueOnce({ items: [recent], next_cursor: "cursor-2" })
      .mockResolvedValueOnce({ items: [active], next_cursor: null })
      .mockResolvedValueOnce({ items: [recent], next_cursor: "cursor-2" })
      .mockResolvedValueOnce({
        items: [{ ...active, status: "chunked", stage: "chunked" }],
        next_cursor: null,
      });
    const wrapper = mount(KnowledgeBaseDetailPage);
    await flushPromises();
    const loadMore = wrapper
      .findAll("button")
      .find((button) => button.text().includes("加载更多"));
    expect(loadMore).toBeDefined();
    await loadMore!.trigger("click");
    await flushPromises();

    await vi.advanceTimersByTimeAsync(2_000);
    await flushPromises();

    expect(listDocumentVersions).toHaveBeenNthCalledWith(
      4,
      "4a43e866-5694-4d4c-955d-69d1a58a2a17",
      "cursor-2",
    );
    await vi.advanceTimersByTimeAsync(4_000);
    expect(listDocumentVersions).toHaveBeenCalledTimes(4);
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
