<script setup lang="ts">
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  LoaderCircle,
  Plus,
  Trash2,
  X,
} from "@lucide/vue";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { apiErrorText } from "@/shared/api/errors";

import {
  createKnowledgeBase,
  deleteKnowledgeBase,
  listKnowledgeBases,
  type KnowledgeBase,
} from "../api/knowledgeBases";

const router = useRouter();
const items = ref<KnowledgeBase[]>([]);
const nextCursor = ref<string | null>();
const loading = ref(true);
const loadingMore = ref(false);
const saving = ref(false);
const deleting = ref(false);
const errorMessage = ref("");
const showCreate = ref(false);
const name = ref("");
const pendingDelete = ref<KnowledgeBase>();

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

async function loadInitial() {
  loading.value = true;
  errorMessage.value = "";
  try {
    const page = await listKnowledgeBases();
    items.value = page.items;
    nextCursor.value = page.next_cursor;
  } catch (error) {
    errorMessage.value = apiErrorText(error, "无法加载知识库。");
  } finally {
    loading.value = false;
  }
}

async function loadMore() {
  if (!nextCursor.value) return;
  loadingMore.value = true;
  try {
    const page = await listKnowledgeBases(nextCursor.value);
    items.value.push(...page.items);
    nextCursor.value = page.next_cursor;
  } catch (error) {
    errorMessage.value = apiErrorText(error, "无法加载更多知识库。");
  } finally {
    loadingMore.value = false;
  }
}

function openCreate() {
  name.value = "";
  errorMessage.value = "";
  showCreate.value = true;
}

async function submitCreate() {
  const trimmedName = name.value.trim();
  if (!trimmedName) return;
  saving.value = true;
  errorMessage.value = "";
  try {
    const created = await createKnowledgeBase(trimmedName);
    items.value.unshift(created);
    showCreate.value = false;
  } catch (error) {
    errorMessage.value = apiErrorText(error, "无法创建知识库。");
  } finally {
    saving.value = false;
  }
}

async function confirmDelete() {
  if (!pendingDelete.value) return;
  deleting.value = true;
  errorMessage.value = "";
  try {
    await deleteKnowledgeBase(pendingDelete.value.id);
    items.value = items.value.filter(
      (item) => item.id !== pendingDelete.value?.id,
    );
    pendingDelete.value = undefined;
  } catch (error) {
    errorMessage.value = apiErrorText(error, "无法删除知识库。");
  } finally {
    deleting.value = false;
  }
}

function openKnowledgeBase(item: KnowledgeBase) {
  void router.push({ name: "knowledge-base-detail", params: { id: item.id } });
}

onMounted(loadInitial);
</script>

<template>
  <section class="knowledge-page" aria-labelledby="page-title">
    <div class="page-heading">
      <div>
        <p class="eyebrow">资料空间</p>
        <h1 id="page-title">知识库</h1>
        <p class="description">管理用于检索与证据引用的文档集合。</p>
      </div>
      <button
        class="primary-button"
        type="button"
        aria-label="新建知识库"
        @click="openCreate"
      >
        <Plus :size="18" aria-hidden="true" />
        <span>新建知识库</span>
      </button>
    </div>

    <div v-if="errorMessage" class="message error-message" role="alert">
      <AlertTriangle :size="18" aria-hidden="true" />
      <span>{{ errorMessage }}</span>
    </div>

    <div v-if="loading" class="message" aria-live="polite">
      <LoaderCircle :size="18" class="spinning" aria-hidden="true" />
      <span>正在加载知识库...</span>
    </div>

    <div v-else-if="items.length === 0" class="empty-state">
      <BookOpen :size="28" aria-hidden="true" />
      <h2>还没有知识库</h2>
      <p>创建一个知识库后即可添加和检索资料。</p>
    </div>

    <div v-else class="knowledge-list" role="list">
      <article
        v-for="item in items"
        :key="item.id"
        class="knowledge-row"
        role="listitem"
      >
        <button
          class="row-main"
          type="button"
          :aria-label="`打开 ${item.name}`"
          @click="openKnowledgeBase(item)"
        >
          <span class="item-icon"
            ><BookOpen :size="19" aria-hidden="true"
          /></span>
          <span class="item-copy">
            <strong>{{ item.name }}</strong>
            <small>创建于 {{ formatDate(item.created_at) }}</small>
          </span>
          <ArrowRight :size="18" aria-hidden="true" />
        </button>
        <button
          class="icon-button danger"
          type="button"
          :aria-label="`删除 ${item.name}`"
          :title="`删除 ${item.name}`"
          @click="pendingDelete = item"
        >
          <Trash2 :size="17" aria-hidden="true" />
        </button>
      </article>
    </div>

    <button
      v-if="nextCursor"
      class="secondary-button load-more"
      type="button"
      :disabled="loadingMore"
      @click="loadMore"
    >
      <LoaderCircle
        v-if="loadingMore"
        :size="17"
        class="spinning"
        aria-hidden="true"
      />
      <span>{{ loadingMore ? "正在加载" : "加载更多" }}</span>
    </button>

    <div
      v-if="showCreate"
      class="dialog-backdrop"
      role="presentation"
      @click.self="showCreate = false"
    >
      <section
        class="dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-title"
      >
        <div class="dialog-heading">
          <h2 id="create-title">新建知识库</h2>
          <button
            class="icon-button"
            type="button"
            title="关闭"
            aria-label="关闭"
            @click="showCreate = false"
          >
            <X :size="18" aria-hidden="true" />
          </button>
        </div>
        <form @submit.prevent="submitCreate">
          <label for="knowledge-base-name">名称</label>
          <input
            id="knowledge-base-name"
            v-model="name"
            name="knowledge-base-name"
            maxlength="120"
            autocomplete="off"
            autofocus
            placeholder="例如：Agent 工程资料"
          />
          <div class="dialog-actions">
            <button
              class="secondary-button"
              type="button"
              @click="showCreate = false"
            >
              取消
            </button>
            <button
              class="primary-button"
              type="submit"
              :disabled="saving || !name.trim()"
            >
              {{ saving ? "创建中..." : "创建" }}
            </button>
          </div>
        </form>
      </section>
    </div>

    <div
      v-if="pendingDelete"
      class="dialog-backdrop"
      role="presentation"
      @click.self="pendingDelete = undefined"
    >
      <section
        class="dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-title"
      >
        <h2 id="delete-title">永久删除 {{ pendingDelete.name }}？</h2>
        <p class="dialog-description">
          此操作无法撤销，后续文档与索引也将一并删除。
        </p>
        <div class="dialog-actions">
          <button
            class="secondary-button"
            type="button"
            @click="pendingDelete = undefined"
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
.knowledge-page {
  width: min(920px, calc(100% - 40px));
  margin: 0 auto;
  padding: 56px 0 72px;
}
.page-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 28px;
}
.eyebrow {
  margin: 0 0 8px;
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
  margin-bottom: 8px;
  font-size: 30px;
  line-height: 1.2;
  letter-spacing: 0;
}
.description,
.dialog-description {
  margin-bottom: 0;
  color: #68716b;
  line-height: 1.6;
}
button {
  letter-spacing: 0;
}
.primary-button,
.secondary-button,
.danger-button {
  display: inline-flex;
  min-height: 40px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 15px;
  border-radius: 6px;
  font-weight: 700;
  cursor: pointer;
}
.primary-button {
  color: #fff;
  background: #1f6f4a;
  border: 1px solid #1f6f4a;
}
.primary-button:hover:not(:disabled) {
  background: #185b3c;
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
.message,
.empty-state {
  border: 1px solid #dfe5e0;
  border-radius: 8px;
  background: #fff;
}
.message {
  display: flex;
  min-height: 64px;
  align-items: center;
  gap: 10px;
  padding: 18px;
  color: #4c5750;
}
.error-message {
  margin-bottom: 16px;
  color: #8d2929;
  background: #fff7f6;
  border-color: #eac7c3;
}
.empty-state {
  padding: 56px 24px;
  color: #536159;
  text-align: center;
}
.empty-state h2 {
  margin: 14px 0 6px;
  color: #253029;
  font-size: 18px;
}
.empty-state p {
  margin-bottom: 0;
}
.knowledge-list {
  overflow: hidden;
  border: 1px solid #dfe5e0;
  border-radius: 8px;
  background: #fff;
}
.knowledge-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 52px;
  align-items: stretch;
  border-bottom: 1px solid #e7ebe8;
}
.knowledge-row:last-child {
  border-bottom: 0;
}
.row-main {
  display: grid;
  min-width: 0;
  grid-template-columns: 40px minmax(0, 1fr) 20px;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
  color: #26312a;
  text-align: left;
  background: transparent;
  border: 0;
  cursor: pointer;
}
.row-main:hover {
  background: #f7faf8;
}
.item-icon {
  display: grid;
  width: 40px;
  height: 40px;
  place-items: center;
  color: #1f6f4a;
  background: #e8f3ec;
  border-radius: 6px;
}
.item-copy {
  display: grid;
  min-width: 0;
  gap: 5px;
}
.item-copy strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-copy small {
  color: #758078;
}
.icon-button {
  display: grid;
  width: 38px;
  height: 38px;
  align-self: center;
  place-items: center;
  color: #526057;
  background: transparent;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}
.icon-button:hover {
  background: #edf2ee;
}
.icon-button.danger {
  color: #9a3733;
}
.icon-button.danger:hover {
  background: #fff0ef;
}
.load-more {
  margin-top: 18px;
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
.dialog-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;
}
.dialog h2 {
  margin-bottom: 10px;
  font-size: 19px;
  letter-spacing: 0;
}
.dialog-heading h2 {
  margin-bottom: 0;
}
label {
  display: block;
  margin-bottom: 7px;
  color: #39443d;
  font-size: 14px;
  font-weight: 700;
}
input {
  width: 100%;
  height: 42px;
  padding: 0 12px;
  color: #202522;
  border: 1px solid #bcc8bf;
  border-radius: 6px;
  outline: none;
}
input:focus {
  border-color: #1f6f4a;
  box-shadow: 0 0 0 3px #d9ecdf;
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
  .knowledge-page {
    width: min(100% - 32px, 920px);
    padding: 40px 0 56px;
  }
  .page-heading {
    align-items: stretch;
    flex-direction: column;
  }
  .primary-button {
    align-self: flex-start;
  }
  .row-main {
    grid-template-columns: 36px minmax(0, 1fr);
    padding: 15px 14px;
  }
  .row-main > svg {
    display: none;
  }
  .item-icon {
    width: 36px;
    height: 36px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
