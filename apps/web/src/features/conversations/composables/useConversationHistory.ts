import { ref } from "vue";

import { apiErrorText } from "@/shared/api/errors";

import {
  createQuestion,
  getConversation,
  listQuestions,
  type Conversation,
  type Question,
} from "../api/conversations";

export function useConversationHistory(
  knowledgeBaseId: string,
  conversationId: string,
) {
  const conversation = ref<Conversation>();
  const questions = ref<Question[]>([]);
  const nextCursor = ref<string>();
  const loading = ref(true);
  const loadingMore = ref(false);
  const submitting = ref(false);
  const errorMessage = ref("");

  async function load() {
    try {
      const [conversationResult, questionPage] = await Promise.all([
        getConversation(knowledgeBaseId, conversationId),
        listQuestions(knowledgeBaseId, conversationId),
      ]);
      conversation.value = conversationResult;
      questions.value = questionPage.items;
      nextCursor.value = questionPage.next_cursor ?? undefined;
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
      const page = await listQuestions(
        knowledgeBaseId,
        conversationId,
        nextCursor.value,
      );
      questions.value.push(...page.items);
      nextCursor.value = page.next_cursor ?? undefined;
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法加载更多问题。");
    } finally {
      loadingMore.value = false;
    }
  }

  async function addQuestion(content: string): Promise<boolean> {
    submitting.value = true;
    errorMessage.value = "";
    try {
      const question = await createQuestion(
        knowledgeBaseId,
        conversationId,
        content,
      );
      questions.value.push(question);
      return true;
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法记录问题。");
      return false;
    } finally {
      submitting.value = false;
    }
  }

  return {
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
  };
}
