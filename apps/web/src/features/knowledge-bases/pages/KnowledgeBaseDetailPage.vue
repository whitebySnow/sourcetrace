<script setup lang="ts">
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  LoaderCircle,
  Trash2,
} from "@lucide/vue";
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import DocumentPanel from "@/features/documents/components/DocumentPanel.vue";
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

async function load() {
  try {
    knowledgeBase.value = await getKnowledgeBase(id);
  } catch (error) {
    errorMessage.value = errorText(error, "无法加载知识库。");
  } finally {
    loading.value = false;
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
      <DocumentPanel :knowledge-base-id="id" />
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
.dialog p {
  margin-bottom: 0;
  color: #6b756e;
  line-height: 1.55;
}
.delete-button,
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
}
@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
