import { apiClient, toApiClientError } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type Conversation = components["schemas"]["ConversationResponse"];
export type ConversationPage = components["schemas"]["ConversationListResponse"];
export type Question = components["schemas"]["QuestionResponse"];
export type QuestionPage = components["schemas"]["QuestionListResponse"];

export async function listConversations(
  knowledgeBaseId: string,
  cursor?: string,
): Promise<ConversationPage> {
  const { data, error } = await apiClient.GET(
    "/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
    {
      params: {
        path: { knowledge_base_id: knowledgeBaseId },
        query: { limit: 20, cursor },
      },
    },
  );
  if (error) throw toApiClientError(error);
  return data;
}

export async function createConversation(
  knowledgeBaseId: string,
  title: string,
): Promise<Conversation> {
  const { data, error } = await apiClient.POST(
    "/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
    {
      params: { path: { knowledge_base_id: knowledgeBaseId } },
      body: { title },
    },
  );
  if (error) throw toApiClientError(error);
  return data;
}

export async function getConversation(
  knowledgeBaseId: string,
  conversationId: string,
): Promise<Conversation> {
  const { data, error } = await apiClient.GET(
    "/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}",
    {
      params: {
        path: {
          knowledge_base_id: knowledgeBaseId,
          conversation_id: conversationId,
        },
      },
    },
  );
  if (error) throw toApiClientError(error);
  return data;
}

export async function listQuestions(
  knowledgeBaseId: string,
  conversationId: string,
  cursor?: string,
): Promise<QuestionPage> {
  const { data, error } = await apiClient.GET(
    "/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/questions",
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

export async function createQuestion(
  knowledgeBaseId: string,
  conversationId: string,
  content: string,
): Promise<Question> {
  const { data, error } = await apiClient.POST(
    "/api/v1/knowledge-bases/{knowledge_base_id}/conversations/{conversation_id}/questions",
    {
      params: {
        path: {
          knowledge_base_id: knowledgeBaseId,
          conversation_id: conversationId,
        },
      },
      body: { content },
    },
  );
  if (error) throw toApiClientError(error);
  return data;
}
