import { apiClient, toApiClientError } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type KnowledgeBase = components["schemas"]["KnowledgeBaseResponse"];
export type KnowledgeBasePage =
  components["schemas"]["KnowledgeBaseListResponse"];

export async function listKnowledgeBases(
  cursor?: string,
): Promise<KnowledgeBasePage> {
  const { data, error } = await apiClient.GET("/api/v1/knowledge-bases", {
    params: { query: { limit: 20, cursor } },
  });
  if (error) {
    throw toApiClientError(error);
  }
  return data;
}

export async function createKnowledgeBase(
  name: string,
): Promise<KnowledgeBase> {
  const { data, error } = await apiClient.POST("/api/v1/knowledge-bases", {
    body: { name },
  });
  if (error) {
    throw toApiClientError(error);
  }
  return data;
}

export async function getKnowledgeBase(id: string): Promise<KnowledgeBase> {
  const { data, error } = await apiClient.GET(
    "/api/v1/knowledge-bases/{knowledge_base_id}",
    {
      params: { path: { knowledge_base_id: id } },
    },
  );
  if (error) {
    throw toApiClientError(error);
  }
  return data;
}

export async function deleteKnowledgeBase(id: string): Promise<void> {
  const { error } = await apiClient.DELETE(
    "/api/v1/knowledge-bases/{knowledge_base_id}",
    {
      params: {
        path: { knowledge_base_id: id },
        query: { confirm: true },
      },
    },
  );
  if (error) {
    throw toApiClientError(error);
  }
}
