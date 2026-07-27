import { apiClient, toApiClientError } from "@/shared/api/client";
import type { components } from "@/shared/api/schema";

export type DocumentVersion = components["schemas"]["DocumentVersionItem"];
export type DocumentUpload = components["schemas"]["DocumentUploadResponse"];
export type DocumentVersionPage =
  components["schemas"]["DocumentVersionListResponse"];

export async function listDocumentVersions(
  knowledgeBaseId: string,
  cursor?: string,
): Promise<DocumentVersionPage> {
  const { data, error } = await apiClient.GET(
    "/api/v1/knowledge-bases/{knowledge_base_id}/documents",
    {
      params: {
        path: { knowledge_base_id: knowledgeBaseId },
        query: { limit: 20, cursor },
      },
    },
  );
  if (error) {
    throw toApiClientError(error);
  }
  return data;
}

export async function uploadDocument(
  knowledgeBaseId: string,
  file: File,
): Promise<DocumentUpload> {
  const { data, error } = await apiClient.POST(
    "/api/v1/knowledge-bases/{knowledge_base_id}/documents",
    {
      params: { path: { knowledge_base_id: knowledgeBaseId } },
      body: { file: file.name },
      bodySerializer: () => {
        const form = new FormData();
        form.append("file", file);
        return form;
      },
    },
  );
  if (error) {
    throw toApiClientError(error);
  }
  return data;
}
