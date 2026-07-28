import { onBeforeUnmount, ref } from "vue";

import { apiErrorText } from "@/shared/api/errors";

import {
  type DocumentVersion,
  listDocumentVersions,
  retryDocumentIngestion,
  uploadDocument,
} from "../api/documents";

export function useDocuments(knowledgeBaseId: string) {
  const documents = ref<DocumentVersion[]>([]);
  const nextCursor = ref<string | null>();
  const selectedFile = ref<File>();
  const fileInput = ref<HTMLInputElement>();
  const loading = ref(true);
  const uploading = ref(false);
  const loadingMore = ref(false);
  const retryingVersionIds = ref<Set<string>>(new Set());
  const errorMessage = ref("");
  const successMessage = ref("");
  let pollTimer: ReturnType<typeof setTimeout> | undefined;
  let unmounted = false;

  function hasActiveIngestion() {
    return documents.value.some((document) =>
      ["pending", "processing", "chunked"].includes(document.status),
    );
  }

  function schedulePoll() {
    if (pollTimer) clearTimeout(pollTimer);
    pollTimer = undefined;
    if (unmounted || !hasActiveIngestion()) return;
    pollTimer = setTimeout(() => void pollDocuments(), 2_000);
  }

  function mergeUpdates(items: DocumentVersion[]) {
    const updates = new Map(items.map((item) => [item.version_id, item]));
    const currentIds = new Set(documents.value.map((item) => item.version_id));
    documents.value = [
      ...items.filter((item) => !currentIds.has(item.version_id)),
      ...documents.value.map((item) => updates.get(item.version_id) ?? item),
    ];
  }

  async function pollDocuments() {
    try {
      let page = await listDocumentVersions(knowledgeBaseId);
      const updates = [...page.items];
      while (page.next_cursor && updates.length < documents.value.length) {
        page = await listDocumentVersions(knowledgeBaseId, page.next_cursor);
        updates.push(...page.items);
      }
      mergeUpdates(updates);
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法刷新文档处理状态。");
    } finally {
      schedulePoll();
    }
  }

  async function load() {
    try {
      const page = await listDocumentVersions(knowledgeBaseId);
      documents.value = page.items;
      nextCursor.value = page.next_cursor;
      schedulePoll();
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法加载文档。");
    } finally {
      loading.value = false;
    }
  }

  function selectFile(event: Event) {
    const input = event.target as HTMLInputElement;
    selectedFile.value = input.files?.[0];
    successMessage.value = "";
  }

  async function submitUpload() {
    if (!selectedFile.value) return;
    uploading.value = true;
    errorMessage.value = "";
    successMessage.value = "";
    try {
      const result = await uploadDocument(knowledgeBaseId, selectedFile.value);
      mergeUpdates([result]);
      successMessage.value = result.deduplicated
        ? "相同内容已存在，已显示原版本。"
        : "文档已登记，等待处理。";
      selectedFile.value = undefined;
      if (fileInput.value) fileInput.value.value = "";
      schedulePoll();
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法上传文档。");
    } finally {
      uploading.value = false;
    }
  }

  async function retryIngestion(document: DocumentVersion) {
    retryingVersionIds.value = new Set(retryingVersionIds.value).add(
      document.version_id,
    );
    errorMessage.value = "";
    try {
      const result = await retryDocumentIngestion(
        knowledgeBaseId,
        document.version_id,
      );
      documents.value = documents.value.map((item) =>
        item.version_id === document.version_id
          ? { ...item, ...result, failure_message: null }
          : item,
      );
      schedulePoll();
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法重试文档处理。");
    } finally {
      const next = new Set(retryingVersionIds.value);
      next.delete(document.version_id);
      retryingVersionIds.value = next;
    }
  }

  async function loadMore() {
    if (!nextCursor.value) return;
    loadingMore.value = true;
    errorMessage.value = "";
    try {
      const page = await listDocumentVersions(
        knowledgeBaseId,
        nextCursor.value,
      );
      documents.value.push(...page.items);
      nextCursor.value = page.next_cursor;
      schedulePoll();
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法加载更多文档。");
    } finally {
      loadingMore.value = false;
    }
  }

  onBeforeUnmount(() => {
    unmounted = true;
    if (pollTimer) clearTimeout(pollTimer);
  });

  return {
    documents,
    errorMessage,
    fileInput,
    loading,
    loadingMore,
    nextCursor,
    retryingVersionIds,
    selectedFile,
    successMessage,
    uploading,
    load,
    loadMore,
    retryIngestion,
    selectFile,
    submitUpload,
  };
}
