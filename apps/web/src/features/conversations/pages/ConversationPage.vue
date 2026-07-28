<script setup lang="ts">
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  CircleStop,
  ExternalLink,
  LoaderCircle,
  MessageSquare,
  Send,
  ShieldAlert,
} from "@lucide/vue";
import { onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { useAnswers } from "@/features/answers/composables/useAnswers";
import { apiUrl } from "@/shared/api/client";

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
  load,
  loadMore,
} = useConversationHistory(knowledgeBaseId, conversationId);
const {
  activeAnswer,
  activeCitations,
  activeFailure,
  activeQuestion,
  activeRefusal,
  activeStatus,
  activeRunId,
  answersByQuestion,
  cancelling,
  errorMessage: answerErrorMessage,
  loading: answersLoading,
  loadingMore: answersLoadingMore,
  nextCursor: answerNextCursor,
  recentAnswers,
  submitting,
  ask,
  cancel,
  load: loadAnswers,
  loadMore: loadMoreAnswers,
} = useAnswers(knowledgeBaseId, conversationId);

async function loadMoreHistory() {
  await Promise.all([loadMore(), loadMoreAnswers()]);
}

async function submit() {
  const value = content.value.trim();
  if (!value || submitting.value) return;
  if (await ask(value)) content.value = "";
}

async function cancelActive() {
  await cancel();
}

function citationHref(path: string) {
  return apiUrl(path);
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

onMounted(() => {
  void Promise.all([load(), loadAnswers()]);
});
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
    <div
      v-else-if="errorMessage && !conversation"
      class="message error-message"
      role="alert"
    >
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
      <div v-if="answerErrorMessage" class="message error-message" role="alert">
        <AlertTriangle :size="18" aria-hidden="true" />
        <span>{{ answerErrorMessage }}</span>
      </div>

      <section class="history" aria-labelledby="history-title">
        <h2 id="history-title">问答历史</h2>
        <div v-if="answersLoading" class="message" aria-live="polite">
          <LoaderCircle :size="18" class="spinning" aria-hidden="true" />
          <span>正在加载回答...</span>
        </div>
        <div v-else-if="questions.length === 0" class="empty-history">
          <MessageSquare :size="27" aria-hidden="true" />
          <strong>尚无问题</strong>
        </div>
        <div v-else class="question-list">
          <article
            v-for="question in questions"
            :key="question.id"
            class="question-row"
          >
            <div class="question-heading">
              <p>{{ question.content }}</p>
              <time :datetime="question.created_at">{{
                formatDate(question.created_at)
              }}</time>
            </div>
            <div v-if="answersByQuestion.get(question.id)" class="answer-block">
              <template
                v-if="
                  answersByQuestion.get(question.id)?.status === 'completed' &&
                  answersByQuestion.get(question.id)?.outcome === 'answered'
                "
              >
                <p class="answer-text">
                  {{ answersByQuestion.get(question.id)?.answer }}
                </p>
                <div
                  v-if="answersByQuestion.get(question.id)?.citations.length"
                  class="citation-list"
                >
                  <a
                    v-for="citation in answersByQuestion.get(question.id)
                      ?.citations"
                    :key="citation.id"
                    class="citation"
                    :href="citationHref(citation.source_url)"
                    target="_blank"
                    rel="noreferrer"
                  >
                    <span class="citation-title">
                      <BookOpen :size="15" aria-hidden="true" />
                      {{ citation.document_name }} · 第
                      {{ citation.page_number }} 页
                      <ExternalLink :size="13" aria-hidden="true" />
                    </span>
                    <span>{{ citation.excerpt }}</span>
                  </a>
                </div>
              </template>
              <p
                v-else-if="
                  answersByQuestion.get(question.id)?.status === 'failed'
                "
                class="failure-text"
              >
                <AlertTriangle :size="17" aria-hidden="true" />
                {{
                  answersByQuestion.get(question.id)?.failure_message ??
                  "回答生成失败。"
                }}
              </p>
              <p
                v-else-if="
                  answersByQuestion.get(question.id)?.status === 'cancelled'
                "
                class="cancellation-text"
              >
                <CircleStop :size="17" aria-hidden="true" />
                回答已取消
              </p>
              <p
                v-else-if="
                  answersByQuestion.get(question.id)?.outcome === 'refused'
                "
                class="refusal-text"
              >
                <ShieldAlert :size="17" aria-hidden="true" />
                {{ answersByQuestion.get(question.id)?.refusal_message }}
              </p>
              <p v-else class="stream-status">
                <LoaderCircle :size="15" class="spinning" aria-hidden="true" />
                回答仍在处理中
              </p>
            </div>
          </article>
          <button
            v-if="nextCursor || answerNextCursor"
            class="load-more-button"
            type="button"
            :disabled="loadingMore || answersLoadingMore"
            @click="loadMoreHistory"
          >
            <LoaderCircle
              v-if="loadingMore || answersLoadingMore"
              :size="16"
              class="spinning"
              aria-hidden="true"
            />
            <span>{{
              loadingMore || answersLoadingMore ? "加载中..." : "加载更多"
            }}</span>
          </button>
        </div>
      </section>

      <section
        v-for="answer in recentAnswers"
        :key="answer.id"
        class="active-answer"
      >
        <div class="question-heading">
          <p>{{ answer.question }}</p>
        </div>
        <p v-if="answer.answer" class="answer-text">{{ answer.answer }}</p>
        <p v-if="answer.refusal" class="refusal-text">
          <ShieldAlert :size="17" aria-hidden="true" />
          {{ answer.refusal }}
        </p>
        <p v-if="answer.failure" class="failure-text">
          <AlertTriangle :size="17" aria-hidden="true" />
          {{ answer.failure }}
        </p>
        <div v-if="answer.citations.length" class="citation-list">
          <a
            v-for="citation in answer.citations"
            :key="citation.id"
            class="citation"
            :href="citationHref(citation.source_url)"
            target="_blank"
            rel="noreferrer"
          >
            <span class="citation-title">
              <BookOpen :size="15" aria-hidden="true" />
              {{ citation.document_name }} · 第 {{ citation.page_number }} 页
              <ExternalLink :size="13" aria-hidden="true" />
            </span>
            <span>{{ citation.excerpt }}</span>
          </a>
        </div>
      </section>

      <section v-if="activeQuestion" class="active-answer" aria-live="polite">
        <div class="question-heading">
          <p>{{ activeQuestion }}</p>
          <span
            v-if="
              activeStatus === 'retrieving' ||
              activeStatus === 'generating' ||
              activeStatus === 'cancelling'
            "
            class="stream-status"
          >
            <LoaderCircle :size="15" class="spinning" aria-hidden="true" />
            {{
              activeStatus === "retrieving"
                ? "检索证据"
                : activeStatus === "cancelling"
                  ? "正在取消"
                  : "生成回答"
            }}
          </span>
        </div>
        <p v-if="activeAnswer" class="answer-text">{{ activeAnswer }}</p>
        <p v-if="activeRefusal" class="refusal-text">
          <ShieldAlert :size="17" aria-hidden="true" />
          {{ activeRefusal }}
        </p>
        <p v-if="activeFailure" class="failure-text">
          <AlertTriangle :size="17" aria-hidden="true" />
          {{ activeFailure }}
        </p>
        <p v-if="activeStatus === 'cancelled'" class="cancellation-text">
          <CircleStop :size="17" aria-hidden="true" />
          回答已取消
        </p>
        <div v-if="activeCitations.length" class="citation-list">
          <a
            v-for="citation in activeCitations"
            :key="citation.id"
            class="citation"
            :href="citationHref(citation.source_url)"
            target="_blank"
            rel="noreferrer"
          >
            <span class="citation-title">
              <BookOpen :size="15" aria-hidden="true" />
              {{ citation.document_name }} · 第 {{ citation.page_number }} 页
              <ExternalLink :size="13" aria-hidden="true" />
            </span>
            <span>{{ citation.excerpt }}</span>
          </a>
        </div>
      </section>

      <form class="question-form" @submit.prevent="submit">
        <label for="question-content">向知识库提问</label>
        <textarea
          id="question-content"
          v-model="content"
          data-test="question-content"
          maxlength="4000"
          rows="3"
        />
        <button
          v-if="submitting"
          data-test="cancel-answer"
          class="cancel-button"
          type="button"
          :disabled="!activeRunId || cancelling"
          @click="cancelActive"
        >
          <CircleStop :size="16" aria-hidden="true" />
          <span>{{
            cancelling
              ? "正在取消..."
              : activeRunId
                ? "取消回答"
                : "正在启动..."
          }}</span>
        </button>
        <button
          v-else
          data-test="create-question"
          type="submit"
          :disabled="!content.trim()"
        >
          <Send :size="16" aria-hidden="true" />
          <span>发送问题</span>
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
  padding: 18px 4px;
  border-bottom: 1px solid #e5eae6;
}
.question-heading {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: start;
  gap: 18px;
}
.question-heading p {
  margin-bottom: 0;
  overflow-wrap: anywhere;
  font-weight: 700;
  line-height: 1.6;
}
.question-heading time,
.stream-status {
  color: #6b756e;
  font-size: 13px;
  white-space: nowrap;
}
.answer-block {
  margin-top: 14px;
  padding-left: 16px;
  border-left: 2px solid #b8cfc1;
}
.answer-text {
  margin-bottom: 0;
  white-space: pre-wrap;
  line-height: 1.65;
}
.refusal-text,
.failure-text,
.cancellation-text,
.stream-status {
  display: flex;
  align-items: center;
  gap: 7px;
}
.refusal-text {
  margin-bottom: 0;
  color: #7c3b23;
}
.failure-text {
  margin-bottom: 0;
  color: #8d2929;
}
.cancellation-text {
  margin-bottom: 0;
  color: #59635d;
}
.citation-list {
  display: grid;
  gap: 8px;
  margin-top: 14px;
}
.citation {
  display: grid;
  gap: 6px;
  padding: 11px 12px;
  overflow-wrap: anywhere;
  color: #35433b;
  text-decoration: none;
  background: #f7faf8;
  border: 1px solid #d8e2dc;
  border-radius: 6px;
  font-size: 13px;
  line-height: 1.45;
}
.citation:hover {
  border-color: #8fac9a;
}
.citation-title {
  display: flex;
  align-items: center;
  gap: 5px;
  color: #1f6f4a;
  font-weight: 700;
}
.active-answer {
  margin-top: 24px;
  padding: 18px;
  background: #fff;
  border: 1px solid #cfdad3;
  border-radius: 8px;
}
.active-answer .answer-text,
.active-answer .refusal-text,
.active-answer .failure-text {
  margin-top: 14px;
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
.question-form .cancel-button {
  color: #7f2f2f;
  background: #fff;
  border-color: #cfaeae;
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
    padding: 16px 0;
  }
  .question-heading {
    grid-template-columns: 1fr;
    gap: 7px;
  }
  .question-heading time,
  .stream-status {
    white-space: normal;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
