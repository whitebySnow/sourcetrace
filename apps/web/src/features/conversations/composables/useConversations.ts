import { ref } from "vue";

import { apiErrorText } from "@/shared/api/errors";

import {
  createConversation,
  listConversations,
  type Conversation,
} from "../api/conversations";

export function useConversations(knowledgeBaseId: string) {
  const conversations = ref<Conversation[]>([]);
  const nextCursor = ref<string>();
  const loading = ref(true);
  const loadingMore = ref(false);
  const creating = ref(false);
  const errorMessage = ref("");

  async function load() {
    try {
      const page = await listConversations(knowledgeBaseId);
      conversations.value = page.items;
      nextCursor.value = page.next_cursor ?? undefined;
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法加载会话。");
    } finally {
      loading.value = false;
    }
  }

  async function loadMore() {
    if (!nextCursor.value || loadingMore.value) return;
    loadingMore.value = true;
    try {
      const page = await listConversations(knowledgeBaseId, nextCursor.value);
      conversations.value.push(...page.items);
      nextCursor.value = page.next_cursor ?? undefined;
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法加载更多会话。");
    } finally {
      loadingMore.value = false;
    }
  }

  async function create(title: string): Promise<Conversation | undefined> {
    creating.value = true;
    errorMessage.value = "";
    try {
      return await createConversation(knowledgeBaseId, title);
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法创建会话。");
      return undefined;
    } finally {
      creating.value = false;
    }
  }

  return {
    conversations,
    creating,
    errorMessage,
    loading,
    loadingMore,
    nextCursor,
    create,
    load,
    loadMore,
  };
}
