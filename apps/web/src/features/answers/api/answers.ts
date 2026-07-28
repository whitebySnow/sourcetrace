import {
  ApiClientError,
  apiClient,
  streamRequest,
  toApiClientError,
} from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type AnswerEvent = components["schemas"]["AnswerEvent"];
export type AnswerHistory = components["schemas"]["AnswerHistoryItem"];
export type AnswerHistoryPage = components["schemas"]["AnswerHistoryResponse"];
export type Citation = components["schemas"]["CitationResponse"];

export async function listAnswers(
  knowledgeBaseId: string,
  conversationId: string,
  cursor?: string,
): Promise<AnswerHistoryPage> {
  const { data, error } = await apiClient.GET(
    "/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/answers",
    {
      params: {
        path: {
          knowledge_base_id: knowledgeBaseId,
          conversation_id: conversationId,
        },
        query: { limit: 20, cursor },
      },
    },
  );
  if (error) throw toApiClientError(error);
  return data;
}

function isAnswerEvent(value: unknown): value is AnswerEvent {
  if (!value || typeof value !== "object") return false;
  const event = value as { version?: unknown; type?: unknown };
  return (
    event.version === "1" &&
    ["status", "delta", "final", "refusal", "error"].includes(
      String(event.type),
    )
  );
}

function parseBlock(block: string): AnswerEvent | undefined {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n");
  if (!data) return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    throw new ApiClientError("INVALID_STREAM", "回答流包含无效数据。");
  }
  if (!isAnswerEvent(parsed)) {
    throw new ApiClientError("INVALID_STREAM", "回答流事件格式无效。");
  }
  return parsed;
}

export async function streamAnswer(
  knowledgeBaseId: string,
  conversationId: string,
  content: string,
  onEvent: (event: AnswerEvent) => void,
): Promise<void> {
  const response = await streamRequest(
    `/api/v1/knowledge-bases/${encodeURIComponent(knowledgeBaseId)}/conversations/` +
      `${encodeURIComponent(conversationId)}/answers`,
    { content },
  );
  if (!response.body) {
    throw new ApiClientError("INVALID_STREAM", "浏览器未提供回答流。");
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminalSeen = false;
  const deliver = (block: string) => {
    const event = parseBlock(block);
    if (!event) return;
    if (terminalSeen) {
      throw new ApiClientError("INVALID_STREAM", "回答流在终态后仍包含事件。");
    }
    onEvent(event);
    terminalSeen = ["final", "refusal", "error"].includes(event.type);
  };
  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value, { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";
    for (const block of blocks) deliver(block);
    if (done) break;
  }
  if (buffer.trim()) deliver(buffer);
  if (!terminalSeen) {
    throw new ApiClientError("INVALID_STREAM", "回答流未包含终态事件。");
  }
}
