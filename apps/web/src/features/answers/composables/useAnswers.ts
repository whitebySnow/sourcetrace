import { computed, ref } from "vue";

import { apiErrorText } from "@/shared/api/errors";

import {
  cancelAnswer,
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
  const activeStatus = ref<
    "retrieving" | "generating" | "cancelling" | "completed" | "cancelled"
  >();
  const activeRunId = ref("");
  const recentAnswers = ref<RecentAnswer[]>([]);
  const cancelling = ref(false);
  let activeController: AbortController | undefined;
  let activeTask: Promise<boolean> | undefined;
  let cancellationTask: Promise<void> | undefined;

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
    } else if (event.type === "error") {
      activeAnswer.value = "";
      activeCitations.value = [];
      activeRefusal.value = "";
      activeFailure.value = event.message;
      activeStatus.value = "completed";
    } else {
      activeAnswer.value = "";
      activeCitations.value = [];
      activeRefusal.value = "";
      activeFailure.value = "";
      activeStatus.value = "cancelled";
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

  function reconcileActiveRun(runId: string): boolean {
    const persisted = answers.value.find((answer) => answer.id === runId);
    if (!persisted) return false;
    activeQuestion.value = persisted.question_content;
    activeRunId.value = persisted.id;
    activeAnswer.value = persisted.answer ?? "";
    activeCitations.value = persisted.citations;
    activeRefusal.value = persisted.refusal_message ?? "";
    activeFailure.value = persisted.failure_message ?? "";
    activeStatus.value =
      persisted.status === "cancelled" ? "cancelled" : "completed";
    return true;
  }

  async function executeAsk(content: string): Promise<boolean> {
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
    const controller = new AbortController();
    activeController = controller;
    try {
      await streamAnswer(
        knowledgeBaseId,
        conversationId,
        content,
        handleEvent,
        controller.signal,
      );
      return true;
    } catch (error) {
      if (
        error !== null &&
        typeof error === "object" &&
        "name" in error &&
        error.name === "AbortError"
      ) {
        return false;
      }
      activeAnswer.value = "";
      activeCitations.value = [];
      activeRefusal.value = "";
      activeFailure.value = apiErrorText(error, "无法生成回答。");
      activeStatus.value = "completed";
      return false;
    } finally {
      if (activeController === controller) activeController = undefined;
      if (!cancellationTask) submitting.value = false;
    }
  }

  async function ask(content: string): Promise<boolean> {
    if (activeTask) {
      const previousTask = activeTask;
      await cancel();
      await previousTask;
    }
    const task = executeAsk(content);
    activeTask = task;
    try {
      return await task;
    } finally {
      if (activeTask === task) activeTask = undefined;
    }
  }

  async function performCancellation(): Promise<void> {
    const runId = activeRunId.value;
    cancelling.value = true;
    activeController?.abort();
    activeAnswer.value = "";
    activeCitations.value = [];
    activeRefusal.value = "";
    activeFailure.value = "";
    activeStatus.value = "cancelling";
    if (!runId) {
      activeStatus.value = "cancelled";
      return;
    }
    try {
      const result = await cancelAnswer(knowledgeBaseId, conversationId, runId);
      if (result.status === "completed" || result.status === "failed") {
        await load();
        if (!reconcileActiveRun(runId)) {
          errorMessage.value = "回答已结束，但暂时无法加载最终状态。";
        }
      } else {
        activeStatus.value = "cancelled";
      }
    } catch (error) {
      errorMessage.value = apiErrorText(error, "无法确认回答已取消。");
      activeStatus.value = undefined;
      await load();
      reconcileActiveRun(runId);
    } finally {
      cancelling.value = false;
    }
  }

  function cancel(): Promise<void> {
    if (cancellationTask) return cancellationTask;
    if (!submitting.value) return Promise.resolve();
    const task = performCancellation();
    cancellationTask = task;
    return task.finally(() => {
      if (cancellationTask === task) cancellationTask = undefined;
      submitting.value = false;
    });
  }

  return {
    activeAnswer,
    activeCitations,
    activeFailure,
    activeQuestion,
    activeRefusal,
    activeStatus,
    activeRunId,
    answers,
    answersByQuestion,
    cancelling,
    errorMessage,
    loading,
    loadingMore,
    nextCursor,
    recentAnswers,
    submitting,
    ask,
    cancel,
    load,
    loadMore,
  };
}
