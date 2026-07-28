import { computed, ref } from "vue";

import { apiErrorText } from "@/shared/api/errors";

import {
  listAnswers,
  streamAnswer,
  type AnswerEvent,
  type AnswerHistory,
  type Citation,
} from "../api/answers";

export interface RecentAnswer {
  id: string;
  question: string;
  answer: string;
  citations: Citation[];
  refusal: string;
  failure: string;
}

export function useAnswers(knowledgeBaseId: string, conversationId: string) {
  const answers = ref<AnswerHistory[]>([]);
  const nextCursor = ref<string>();
  const loading = ref(true);
  const loadingMore = ref(false);
  const submitting = ref(false);
  const errorMessage = ref("");
  const activeQuestion = ref("");
  const activeAnswer = ref("");
  const activeCitations = ref<Citation[]>([]);
  const activeRefusal = ref("");
  const activeFailure = ref("");
  const activeStatus = ref<"retrieving" | "generating" | "completed">();
  const activeRunId = ref("");
  const recentAnswers = ref<RecentAnswer[]>([]);

  const answersByQuestion = computed(
    () => new Map(answers.value.map((answer) => [answer.question_id, answer])),
  );

  function removePersistedRecentAnswers() {
    const persistedIds = new Set(answers.value.map((answer) => answer.id));
    recentAnswers.value = recentAnswers.value.filter(
      (answer) => !persistedIds.has(answer.id),
    );
  }

  async function load() {
    try {
      const page = await listAnswers(knowledgeBaseId, conversationId);
      answers.value = page.items;
      removePersistedRecentAnswers();
      nextCursor.value = page.next_cursor ?? undefined;
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法加载回答历史。");
    } finally {
      loading.value = false;
    }
  }

  async function loadMore() {
    if (!nextCursor.value || loadingMore.value) return;
    loadingMore.value = true;
    try {
      const page = await listAnswers(
        knowledgeBaseId,
        conversationId,
        nextCursor.value,
      );
      answers.value.push(...page.items);
      removePersistedRecentAnswers();
      nextCursor.value = page.next_cursor ?? undefined;
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法加载更多回答。");
    } finally {
      loadingMore.value = false;
    }
  }

  function handleEvent(event: AnswerEvent) {
    activeRunId.value = event.run_id;
    if (event.type === "status") {
      activeStatus.value = event.status;
    } else if (event.type === "delta") {
      activeAnswer.value += event.delta;
    } else if (event.type === "final") {
      activeAnswer.value = event.answer;
      activeCitations.value = event.citations;
      activeStatus.value = "completed";
    } else if (event.type === "refusal") {
      activeAnswer.value = "";
      activeCitations.value = [];
      activeRefusal.value = event.message;
      activeStatus.value = "completed";
    } else {
      activeAnswer.value = "";
      activeCitations.value = [];
      activeRefusal.value = "";
      activeFailure.value = event.message;
      activeStatus.value = "completed";
    }
  }

  function archiveActiveAnswer() {
    if (!activeQuestion.value || activeStatus.value !== "completed") return;
    recentAnswers.value.push({
      id: activeRunId.value || `recent-${recentAnswers.value.length}`,
      question: activeQuestion.value,
      answer: activeAnswer.value,
      citations: activeCitations.value,
      refusal: activeRefusal.value,
      failure: activeFailure.value,
    });
  }

  async function ask(content: string): Promise<boolean> {
    archiveActiveAnswer();
    submitting.value = true;
    errorMessage.value = "";
    activeQuestion.value = content;
    activeAnswer.value = "";
    activeCitations.value = [];
    activeRefusal.value = "";
    activeFailure.value = "";
    activeRunId.value = "";
    activeStatus.value = "retrieving";
    try {
      await streamAnswer(
        knowledgeBaseId,
        conversationId,
        content,
        handleEvent,
      );
      return true;
    } catch (error) {
      activeAnswer.value = "";
      activeCitations.value = [];
      activeRefusal.value = "";
      activeFailure.value = apiErrorText(error, "无法生成回答。");
      activeStatus.value = "completed";
      return false;
    } finally {
      submitting.value = false;
    }
  }

  return {
    activeAnswer,
    activeCitations,
    activeFailure,
    activeQuestion,
    activeRefusal,
    activeStatus,
    answers,
    answersByQuestion,
    errorMessage,
    loading,
    loadingMore,
    nextCursor,
    recentAnswers,
    submitting,
    ask,
    load,
    loadMore,
  };
}
