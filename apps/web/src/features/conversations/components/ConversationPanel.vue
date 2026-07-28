<script setup lang="ts">
import {
  AlertTriangle,
  ChevronRight,
  LoaderCircle,
  MessageSquare,
  Plus,
} from "@lucide/vue";
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { useConversations } from "../composables/useConversations";

const props = defineProps<{ knowledgeBaseId: string }>();
const router = useRouter();
const title = ref("");
const {
  conversations,
  creating,
  errorMessage,
  loading,
  loadingMore,
  nextCursor,
  create,
  load,
  loadMore,
} = useConversations(props.knowledgeBaseId);

function openConversation(conversationId: string) {
  return router.push({
    name: "conversation",
    params: {
      knowledgeBaseId: props.knowledgeBaseId,
      conversationId,
    },
  });
}

async function submit() {
  const value = title.value.trim();
  if (!value || creating.value) return;
  const conversation = await create(value);
  if (conversation) await openConversation(conversation.id);
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

onMounted(load);
</script>

<template>
  <section class="conversations" aria-labelledby="conversations-title">
    <div class="section-heading">
      <div>
        <h2 id="conversations-title">会话</h2>
        <p>{{ conversations.length }} 个已保存会话</p>
      </div>
      <form class="create-form" @submit.prevent="submit">
        <input
          v-model="title"
          data-test="conversation-title"
          maxlength="120"
          aria-label="会话标题"
          placeholder="会话标题"
        />
        <button
          data-test="create-conversation"
          type="submit"
          :disabled="!title.trim() || creating"
        >
          <LoaderCircle
            v-if="creating"
            :size="16"
            class="spinning"
            aria-hidden="true"
          />
          <Plus v-else :size="16" aria-hidden="true" />
          <span>新建</span>
        </button>
      </form>
    </div>

    <div v-if="errorMessage" class="message error-message" role="alert">
      <AlertTriangle :size="18" aria-hidden="true" />
      <span>{{ errorMessage }}</span>
    </div>
    <div v-if="loading" class="empty-state">
      <LoaderCircle :size="22" class="spinning" aria-hidden="true" />
    </div>
    <div v-else-if="conversations.length === 0" class="empty-state">
      <MessageSquare :size="27" aria-hidden="true" />
      <strong>尚无会话</strong>
    </div>
    <div v-else class="conversation-list">
      <button
        v-for="conversation in conversations"
        :key="conversation.id"
        class="conversation-row"
        data-test="open-conversation"
        type="button"
        @click="openConversation(conversation.id)"
      >
        <span class="conversation-icon">
          <MessageSquare :size="18" aria-hidden="true" />
        </span>
        <span class="conversation-main">
          <strong>{{ conversation.title }}</strong>
          <time :datetime="conversation.updated_at">
            {{ formatDate(conversation.updated_at) }}
          </time>
        </span>
        <ChevronRight :size="18" aria-hidden="true" />
      </button>
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
button,
input {
  letter-spacing: 0;
}
.conversations {
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
.create-form {
  display: flex;
  min-width: 0;
  gap: 8px;
}
.create-form input {
  width: min(260px, 34vw);
  min-height: 40px;
  padding: 0 12px;
  color: #202522;
  background: #fff;
  border: 1px solid #cbd5cd;
  border-radius: 6px;
}
.create-form button,
.load-more-button {
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
.create-form button {
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
button:disabled {
  cursor: wait;
  opacity: 0.6;
}
.message {
  display: flex;
  min-height: 58px;
  align-items: center;
  gap: 10px;
  padding: 16px;
  border: 1px solid #dfe5e0;
  border-radius: 8px;
}
.error-message {
  margin-bottom: 16px;
  color: #8d2929;
  background: #fff7f6;
  border-color: #eac7c3;
}
.empty-state {
  display: grid;
  min-height: 150px;
  place-items: center;
  align-content: center;
  gap: 8px;
  color: #778179;
  border: 1px dashed #cbd5cd;
  border-radius: 8px;
}
.conversation-list {
  border-top: 1px solid #dfe5e0;
}
.conversation-row {
  display: grid;
  width: 100%;
  grid-template-columns: 36px minmax(0, 1fr) 20px;
  align-items: center;
  gap: 12px;
  min-height: 68px;
  padding: 10px 4px;
  color: #202522;
  background: transparent;
  border: 0;
  border-bottom: 1px solid #e5eae6;
  cursor: pointer;
  text-align: left;
}
.conversation-row:hover {
  background: #f0f5f1;
}
.conversation-icon {
  display: grid;
  width: 34px;
  height: 34px;
  place-items: center;
  color: #416250;
  background: #edf3ef;
  border-radius: 6px;
}
.conversation-main {
  display: grid;
  min-width: 0;
  gap: 5px;
}
.conversation-main strong {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.conversation-main time {
  color: #6b756e;
  font-size: 13px;
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
  .create-form input {
    width: auto;
    min-width: 0;
    flex: 1;
  }
}
@media (prefers-reduced-motion: reduce) {
  .spinning {
    animation: none;
  }
}
</style>
