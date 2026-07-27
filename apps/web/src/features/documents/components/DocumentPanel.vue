<script setup lang="ts">
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  LoaderCircle,
  RefreshCw,
  Upload,
} from "@lucide/vue";
import { onMounted } from "vue";

import { useDocuments } from "../composables/useDocuments";

const props = defineProps<{ knowledgeBaseId: string }>();
const {
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
} = useDocuments(props.knowledgeBaseId);

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
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    pending: "待处理",
    processing: "处理中",
    chunked: "已切分",
    completed: "已完成",
    failed: "失败",
  };
  return labels[status] ?? status;
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    queued: "等待任务",
    parsing: "解析 PDF",
    chunking: "切分文本",
    chunked: "等待向量化",
    completed: "处理完成",
    failed: "处理失败",
  };
  return labels[stage] ?? stage;
}

function setFileInput(element: unknown) {
  fileInput.value = element instanceof HTMLInputElement ? element : undefined;
}

onMounted(load);
</script>

<template>
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
            :ref="setFileInput"
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

    <div v-if="loading" class="empty-documents">
      <LoaderCircle :size="22" class="spinning" aria-hidden="true" />
    </div>
    <div v-else-if="documents.length === 0" class="empty-documents">
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
        <span class="document-icon"
          ><FileText :size="19" aria-hidden="true"
        /></span>
        <div class="document-main">
          <strong>{{ document.name }}</strong>
          <span>
            版本 {{ document.version_number }} · {{ document.page_count }} 页 ·
            {{ formatFileSize(document.file_size_bytes) }}
          </span>
          <span
            >{{ stageLabel(document.stage) }} · 尝试
            {{ document.attempt_count }}/3</span
          >
          <span v-if="document.failure_message" class="failure-message">
            {{ document.failure_message }}
          </span>
        </div>
        <div class="document-status">
          <span class="status-badge">{{ statusLabel(document.status) }}</span>
          <button
            v-if="document.status === 'failed' && document.retryable"
            class="retry-button"
            data-test="retry-ingestion"
            type="button"
            :disabled="retryingVersionIds.has(document.version_id)"
            @click="retryIngestion(document)"
          >
            <RefreshCw :size="14" aria-hidden="true" />
            <span>重试</span>
          </button>
        </div>
        <time :datetime="document.created_at">{{
          formatDate(document.created_at)
        }}</time>
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

<style scoped>
button {
  letter-spacing: 0;
}
.message {
  display: flex;
  min-height: 64px;
  align-items: center;
  gap: 10px;
  padding: 18px;
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
  margin: 0 0 5px;
  font-size: 19px;
}
.section-heading p {
  margin: 0;
  color: #6b756e;
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
  clip-path: inset(50%);
}
.upload-button,
.load-more-button,
.retry-button {
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
.upload-button {
  color: #fff;
  background: #1f6f4a;
  border: 1px solid #1f6f4a;
}
.load-more-button {
  margin: 16px auto 0;
  color: #315d47;
  background: #fff;
  border: 1px solid #b9c9bf;
}
.retry-button {
  min-height: 30px;
  padding: 0 9px;
  color: #315d47;
  background: #fff;
  border: 1px solid #b9c9bf;
}
button:disabled {
  cursor: wait;
  opacity: 0.6;
}
.empty-documents {
  display: grid;
  min-height: 220px;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: #778179;
  border: 1px dashed #cbd5cd;
  border-radius: 8px;
  text-align: center;
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
  text-overflow: ellipsis;
  white-space: nowrap;
}
.document-main span,
.document-row time {
  color: #6b756e;
  font-size: 13px;
}
.document-main .failure-message {
  color: #8d2929;
  white-space: normal;
}
.document-status {
  display: grid;
  justify-items: center;
  gap: 6px;
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
.document-row time {
  text-align: right;
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
