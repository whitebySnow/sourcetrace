import { apiClient, toApiClientError } from "@/shared/api/client";

export async function getReadiness() {
  const { data, error } = await apiClient.GET("/ready");
  if (error) {
    throw toApiClientError(error);
  }
  return data;
}
