import { ApiClientError } from "./client";

export function apiErrorText(error: unknown, fallback: string): string {
  if (error instanceof ApiClientError && error.requestId) {
    return `${error.message}（请求 ID：${error.requestId}）`;
  }
  return error instanceof Error ? error.message : fallback;
}
