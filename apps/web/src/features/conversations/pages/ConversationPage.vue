<script setup lang="ts">
import {
  AlertTriangle,
  ArrowLeft,
  LoaderCircle,
  MessageSquare,
  Plus,
} from "@lucide/vue";
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useConversationHistory } from "../composables/useConversationHistory";

const route = useRoute();
const router = useRouter();
const knowledgeBaseId = String(route.params.knowledgeBaseId);
const conversationId = String(route.params.conversationId);
const content = ref("");
const {
  conversation,
  errorMessage,
  loading,
  loadingMore,
  nextCursor,
  questions,
  submitting,
  addQuestion,
  load,
  loadMore,
} = useConversationHistory(knowledgeBaseId, conversationId);

async function submit() {
  const value = content.value.trim();
  if (!value || submitting.value) return;
  if (await addQuestion(value)) content.value = "";
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

onMounted(load);
</script>

<template>
  <section class="conversation-page" aria-labelledby="conversation-title">
    <button
      class="back-button"
      type="button"
      @click="
        router.push({
          name: 'knowledge-base-detail',
          params: { id: knowledgeBaseId },
        })
      "
    >
      <ArrowLeft :size="17" aria-hidden="true" />
      <span>返回知识库</span>
    </button>

    <div v-if="loading" class="message" aria-live="polite">
      <LoaderCircle :size="18" class="spinning" aria-hidden="true" />
      <span>正在加载会话...</span>
    </div>
    <div v-else-if="errorMessage && !conversation" class="message error-message" role="alert">
      <AlertTriangle :size="18" aria-hidden="true" />
      <span>{{ errorMessage }}</span>
    </div>

    <template v-else-if="conversation">
      <header class="conversation-heading">
        <span class="heading-icon">
          <MessageSquare :size="22" aria-hidden="true" />
        </span>
        <div>
          <p class="eyebrow">会话</p>
          <h1 id="conversation-title">{{ conversation.title }}</h1>
          <p>创建于 {{ formatDate(conversation.created_at) }}</p>
        </div>
      </header>

      <div v-if="errorMessage" class="message error-message" role="alert">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ errorMessage }}</span>
      </div>

      <section class="history" aria-labelledby="history-title">
        <h2 id="history-title">问题历史</h2>
        <div v-if="questions.length === 0" class="empty-history">
          <MessageSquare :size="27" aria-hidden="true" />
          <strong>尚无问题</strong>
        </div>
        <div v-else class="question-list">
          <article v-for="question in questions" :key="question.id" class="question-row">
            <p>{{ question.content }}</p>
            <time :datetime="question.created_at">{{ formatDate(question.created_at) }}</time>
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

      <form class="question-form" @submit.prevent="submit">
        <label for="question-content">记录问题</label>
        <textarea
          id="question-content"
          v-model="content"
          data-test="question-content"
          maxlength="4000"
          rows="3"
        />
        <button
          data-test="create-question"
          type="submit"
          :disabled="!content.trim() || submitting"
        >
          <LoaderCircle
            v-if="submitting"
            :size="16"
            class="spinning"
            aria-hidden="true"
          />
          <Plus v-else :size="16" aria-hidden="true" />
          <span>{{ submitting ? "保存中..." : "保存问题" }}</span>
        </button>
      </form>
    </template>
  </section>
</template>

<style scoped>
.conversation-page {
  width: min(920px, calc(100% - 40px));
  margin: 0 auto;
  padding: 42px 0 72px;
}
button,
textarea {
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
.conversation-heading {
  display: grid;
  grid-template-columns: 48px minmax(0, 1fr);
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
.conversation-heading p:last-child {
  margin-bottom: 0;
  color: #6b756e;
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
.history {
  padding-top: 24px;
  border-top: 1px solid #dfe5e0;
}
.history h2 {
  margin-bottom: 18px;
  font-size: 19px;
}
.empty-history {
  display: grid;
  min-height: 220px;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: #778179;
  border: 1px dashed #cbd5cd;
  border-radius: 8px;
}
.question-list {
  border-top: 1px solid #dfe5e0;
}
.question-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 18px;
  padding: 18px 4px;
  border-bottom: 1px solid #e5eae6;
}
.question-row p {
  margin-bottom: 0;
  overflow-wrap: anywhere;
  line-height: 1.6;
}
.question-row time {
  color: #6b756e;
  font-size: 13px;
  white-space: nowrap;
}
.load-more-button,
.question-form button {
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
.load-more-button {
  margin: 16px auto 0;
  color: #315d47;
  background: #fff;
  border: 1px solid #b9c9bf;
}
.question-form {
  display: grid;
  gap: 10px;
  margin-top: 32px;
  padding-top: 24px;
  border-top: 1px solid #dfe5e0;
}
.question-form label {
  font-weight: 700;
}
.question-form textarea {
  width: 100%;
  min-height: 92px;
  resize: vertical;
  padding: 12px;
  color: #202522;
  background: #fff;
  border: 1px solid #cbd5cd;
  border-radius: 6px;
  line-height: 1.5;
}
.question-form button {
  justify-self: end;
  color: #fff;
  background: #1f6f4a;
  border: 1px solid #1f6f4a;
}
button:disabled {
  cursor: wait;
  opacity: 0.6;
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
  .conversation-page {
    width: min(100% - 32px, 920px);
    padding: 32px 0 56px;
  }
  .question-row {
    grid-template-columns: 1fr;
    gap: 8px;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
