<script setup lang="ts">
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CheckCircle2,
  FileText,
  LoaderCircle,
  Trash2,
  Upload,
} from "@lucide/vue";
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import {
  type DocumentVersion,
  listDocumentVersions,
  uploadDocument,
} from "@/features/documents/api/documents";
import { ApiClientError } from "@/shared/api/client";

import {
  deleteKnowledgeBase,
  getKnowledgeBase,
  type KnowledgeBase,
} from "../api/knowledgeBases";

const route = useRoute();
const router = useRouter();
const knowledgeBase = ref<KnowledgeBase>();
const loading = ref(true);
const deleting = ref(false);
const showDelete = ref(false);
const errorMessage = ref("");
const successMessage = ref("");
const documents = ref<DocumentVersion[]>([]);
const nextCursor = ref<string | null>();
const selectedFile = ref<File>();
const fileInput = ref<HTMLInputElement>();
const uploading = ref(false);
const loadingMore = ref(false);
const id = String(route.params.id);

function errorText(error: unknown, fallback: string) {
  if (error instanceof ApiClientError && error.requestId) {
    return `${error.message}（请求 ID：${error.requestId}）`;
  }
  return error instanceof Error ? error.message : fallback;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatFileSize(bytes: number) {
  if (bytes < 1024 * 1024) {
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    processing: "处理中",
    completed: "已完成",
    failed: "失败",
  };
  return labels[status] ?? status;
}

async function load() {
  try {
    knowledgeBase.value = await getKnowledgeBase(id);
    const page = await listDocumentVersions(id);
    documents.value = page.items;
    nextCursor.value = page.next_cursor;
  } catch (error) {
    errorMessage.value = errorText(error, "无法加载知识库。");
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
    const result = await uploadDocument(id, selectedFile.value);
    const item: DocumentVersion = {
      document_id: result.document_id,
      version_id: result.version_id,
      name: result.name,
      version_number: result.version_number,
      checksum_sha256: result.checksum_sha256,
      file_size_bytes: result.file_size_bytes,
      page_count: result.page_count,
      status: result.status,
      created_at: result.created_at,
    };
    const existingIndex = documents.value.findIndex(
      (document) => document.version_id === item.version_id,
    );
    if (existingIndex === -1) {
      documents.value.unshift(item);
    } else {
      documents.value[existingIndex] = item;
    }
    successMessage.value = result.deduplicated
      ? "相同内容已存在，已显示原版本。"
      : "文档已登记，等待处理。";
    selectedFile.value = undefined;
    if (fileInput.value) fileInput.value.value = "";
  } catch (error) {
    errorMessage.value = errorText(error, "无法上传文档。");
  } finally {
    uploading.value = false;
  }
}

async function loadMore() {
  if (!nextCursor.value) return;
  loadingMore.value = true;
  errorMessage.value = "";
  try {
    const page = await listDocumentVersions(id, nextCursor.value);
    documents.value.push(...page.items);
    nextCursor.value = page.next_cursor;
  } catch (error) {
    errorMessage.value = errorText(error, "无法加载更多文档。");
  } finally {
    loadingMore.value = false;
  }
}

async function confirmDelete() {
  deleting.value = true;
  errorMessage.value = "";
  try {
    await deleteKnowledgeBase(id);
    await router.push({ name: "knowledge-bases" });
  } catch (error) {
    errorMessage.value = errorText(error, "无法删除知识库。");
    showDelete.value = false;
  } finally {
    deleting.value = false;
  }
}

onMounted(load);
</script>

<template>
  <section class="detail-page" aria-labelledby="detail-title">
    <button
      class="back-button"
      type="button"
      @click="router.push({ name: 'knowledge-bases' })"
    >
      <ArrowLeft :size="17" aria-hidden="true" />
      <span>返回知识库</span>
    </button>

    <div v-if="loading" class="message" aria-live="polite">
      <LoaderCircle :size="18" class="spinning" aria-hidden="true" />
      <span>正在加载知识库...</span>
    </div>
    <div
      v-else-if="errorMessage && !knowledgeBase"
      class="message error-message"
      role="alert"
    >
      <AlertTriangle :size="18" aria-hidden="true" />
      <span>{{ errorMessage }}</span>
    </div>

    <template v-else-if="knowledgeBase">
      <header class="detail-heading">
        <span class="heading-icon"
          ><BookOpen :size="22" aria-hidden="true"
        /></span>
        <div>
          <p class="eyebrow">知识库</p>
          <h1 id="detail-title">{{ knowledgeBase.name }}</h1>
          <p>创建于 {{ formatDate(knowledgeBase.created_at) }}</p>
        </div>
        <button
          class="delete-button"
          type="button"
          aria-label="删除知识库"
          @click="showDelete = true"
        >
          <Trash2 :size="17" aria-hidden="true" />
          <span>删除</span>
        </button>
      </header>

      <div v-if="errorMessage" class="message error-message" role="alert">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ errorMessage }}</span>
      </div>
      <div v-if="successMessage" class="message success-message" role="status">
        <CheckCircle2 :size="18" aria-hidden="true" />
        <span>{{ successMessage }}</span>
      </div>

      <section class="documents" aria-labelledby="documents-title">
        <div class="section-heading">
          <div>
            <h2 id="documents-title">文档</h2>
            <p>该知识库中的证据来源。</p>
          </div>
          <div class="upload-controls">
            <label class="file-picker">
              <FileText :size="16" aria-hidden="true" />
              <span>{{ selectedFile?.name ?? "选择 PDF" }}</span>
              <input
                ref="fileInput"
                data-test="pdf-input"
                type="file"
                accept=".pdf,application/pdf"
                @change="selectFile"
              />
            </label>
            <button
              class="upload-button"
              data-test="upload-document"
              type="button"
              :disabled="!selectedFile || uploading"
              @click="submitUpload"
            >
              <LoaderCircle
                v-if="uploading"
                :size="16"
                class="spinning"
                aria-hidden="true"
              />
              <Upload v-else :size="16" aria-hidden="true" />
              <span>{{ uploading ? "上传中..." : "上传" }}</span>
            </button>
          </div>
        </div>
        <div v-if="documents.length === 0" class="empty-documents">
          <FileText :size="27" aria-hidden="true" />
          <strong>尚未添加文档</strong>
          <span>选择 PDF 添加证据来源。</span>
        </div>
        <div v-else class="document-list">
          <article
            v-for="document in documents"
            :key="document.version_id"
            class="document-row"
          >
            <span class="document-icon">
              <FileText :size="19" aria-hidden="true" />
            </span>
            <div class="document-main">
              <strong>{{ document.name }}</strong>
              <span>
                版本 {{ document.version_number }} ·
                {{ document.page_count }} 页 ·
                {{ formatFileSize(document.file_size_bytes) }}
              </span>
            </div>
            <span class="status-badge">{{ statusLabel(document.status) }}</span>
            <time :datetime="document.created_at">
              {{ formatDate(document.created_at) }}
            </time>
          </article>
          <button
            v-if="nextCursor"
            class="load-more-button"
            type="button"
            :disabled="loadingMore"
            @click="loadMore"
          >
            <LoaderCircle
              v-if="loadingMore"
              :size="16"
              class="spinning"
              aria-hidden="true"
            />
            <span>{{ loadingMore ? "加载中..." : "加载更多" }}</span>
          </button>
        </div>
      </section>
    </template>

    <div
      v-if="showDelete && knowledgeBase"
      class="dialog-backdrop"
      role="presentation"
      @click.self="showDelete = false"
    >
      <section
        class="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-title"
      >
        <h2 id="delete-title">永久删除 {{ knowledgeBase.name }}？</h2>
        <p>此操作无法撤销，后续文档与索引也将一并删除。</p>
        <div class="dialog-actions">
          <button
            class="secondary-button"
            type="button"
            @click="showDelete = false"
          >
            取消
          </button>
          <button
            class="danger-button"
            data-test="confirm-delete"
            type="button"
            :disabled="deleting"
            @click="confirmDelete"
          >
            {{ deleting ? "删除中..." : "永久删除" }}
          </button>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.detail-page {
  width: min(920px, calc(100% - 40px));
  margin: 0 auto;
  padding: 42px 0 72px;
}
button {
  letter-spacing: 0;
}
.back-button {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  margin-bottom: 28px;
  padding: 7px 0;
  color: #536159;
  background: transparent;
  border: 0;
  cursor: pointer;
}
.back-button:hover {
  color: #1f6f4a;
}
.detail-heading {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr) auto;
  align-items: center;
  gap: 16px;
  margin-bottom: 32px;
}
.heading-icon {
  display: grid;
  width: 48px;
  height: 48px;
  place-items: center;
  color: #1f6f4a;
  background: #e4f1e9;
  border-radius: 7px;
}
.eyebrow {
  margin: 0 0 5px;
  color: #1f6f4a;
  font-size: 12px;
  font-weight: 700;
}
h1,
h2,
p {
  margin-top: 0;
}
h1 {
  margin-bottom: 5px;
  overflow-wrap: anywhere;
  font-size: 28px;
  line-height: 1.25;
  letter-spacing: 0;
}
.detail-heading p:last-child,
.section-heading p,
.dialog p {
  margin-bottom: 0;
  color: #6b756e;
  line-height: 1.55;
}
.delete-button,
.upload-button,
.load-more-button,
.secondary-button,
.danger-button {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 14px;
  border-radius: 6px;
  font-weight: 700;
  cursor: pointer;
}
.delete-button {
  color: #943632;
  background: #fff;
  border: 1px solid #dfb9b6;
}
.delete-button:hover {
  background: #fff3f2;
}
.upload-button {
  color: #fff;
  background: #1f6f4a;
  border: 1px solid #1f6f4a;
}
.upload-button:hover:not(:disabled) {
  background: #195d3e;
}
.load-more-button {
  margin: 16px auto 0;
  color: #315d47;
  background: #fff;
  border: 1px solid #b9c9bf;
}
.secondary-button {
  color: #39443d;
  background: #fff;
  border: 1px solid #cfd8d1;
}
.danger-button {
  color: #fff;
  background: #a43a35;
  border: 1px solid #a43a35;
}
button:disabled {
  cursor: wait;
  opacity: 0.6;
}
.message {
  display: flex;
  min-height: 64px;
  align-items: center;
  gap: 10px;
  padding: 18px;
  color: #4c5750;
  background: #fff;
  border: 1px solid #dfe5e0;
  border-radius: 8px;
}
.error-message {
  margin-bottom: 18px;
  color: #8d2929;
  background: #fff7f6;
  border-color: #eac7c3;
}
.success-message {
  margin-bottom: 18px;
  color: #23623f;
  background: #f2faf5;
  border-color: #bddac8;
}
.documents {
  border-top: 1px solid #dfe5e0;
}
.section-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 24px 0 18px;
}
.section-heading h2 {
  margin-bottom: 5px;
  font-size: 19px;
  letter-spacing: 0;
}
.empty-documents {
  display: grid;
  min-height: 220px;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: #778179;
  background: #fff;
  border: 1px dashed #cbd5cd;
  border-radius: 8px;
  text-align: center;
}
.empty-documents strong {
  color: #414c45;
}
.empty-documents span {
  font-size: 14px;
}
.upload-controls {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
}
.file-picker {
  display: flex;
  width: min(260px, 36vw);
  min-height: 40px;
  align-items: center;
  gap: 8px;
  padding: 0 12px;
  color: #414c45;
  background: #fff;
  border: 1px solid #cbd5cd;
  border-radius: 6px;
  cursor: pointer;
}
.file-picker span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.file-picker input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0 0 0 0);
  white-space: nowrap;
  clip-path: inset(50%);
}
.file-picker:focus-within {
  outline: 2px solid #4a8b69;
  outline-offset: 2px;
}
.document-list {
  border-top: 1px solid #dfe5e0;
}
.document-row {
  display: grid;
  grid-template-columns: 38px minmax(0, 1fr) auto 150px;
  align-items: center;
  gap: 12px;
  min-height: 74px;
  padding: 12px 4px;
  border-bottom: 1px solid #e5eae6;
}
.document-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #416250;
  background: #edf3ef;
  border-radius: 6px;
}
.document-main {
  display: grid;
  min-width: 0;
  gap: 5px;
}
.document-main strong {
  overflow: hidden;
  color: #26332b;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.document-main span,
.document-row time {
  color: #6b756e;
  font-size: 13px;
}
.document-row time {
  text-align: right;
}
.status-badge {
  padding: 4px 8px;
  color: #7a5716;
  background: #fff7df;
  border: 1px solid #ead79d;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
}
.dialog-backdrop {
  position: fixed;
  z-index: 10;
  inset: 0;
  display: grid;
  place-items: center;
  padding: 20px;
  background: rgb(20 29 23 / 42%);
}
.dialog {
  width: min(440px, 100%);
  padding: 24px;
  background: #fff;
  border: 1px solid #d5ddd7;
  border-radius: 8px;
  box-shadow: 0 18px 50px rgb(20 35 26 / 20%);
}
.dialog h2 {
  margin-bottom: 10px;
  font-size: 19px;
  letter-spacing: 0;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 24px;
}
.spinning {
  animation: spin 0.9s linear infinite;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@media (max-width: 640px) {
  .detail-page {
    width: min(100% - 32px, 920px);
    padding: 32px 0 56px;
  }
  .detail-heading {
    grid-template-columns: 44px minmax(0, 1fr);
    align-items: start;
  }
  .delete-button {
    grid-column: 2;
    justify-self: start;
  }
  .heading-icon {
    width: 44px;
    height: 44px;
  }
  .section-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .upload-controls {
    width: 100%;
  }
  .file-picker {
    width: auto;
    min-width: 0;
    flex: 1;
  }
  .document-row {
    grid-template-columns: 38px minmax(0, 1fr) auto;
  }
  .document-row time {
    grid-column: 2 / -1;
    text-align: left;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
