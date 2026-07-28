import createClient from "openapi-fetch";

import type { paths } from "./schema";

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export function apiUrl(path: string): string {
  return `${baseUrl.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
}

export const apiClient = createClient<paths>({
  baseUrl,
  headers: {
    Accept: "application/json",
  },
});

export class ApiClientError extends Error {
  constructor(
    public readonly code: string,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

export function toApiClientError(error: unknown): ApiClientError {
  if (error && typeof error === "object") {
    const body = error as {
      code?: unknown;
      detail?: unknown;
      title?: unknown;
      request_id?: unknown;
    };
    return new ApiClientError(
      typeof body.code === "string" ? body.code : "API_ERROR",
      typeof body.detail === "string"
        ? body.detail
        : typeof body.title === "string"
          ? body.title
          : "请求未成功，请稍后重试。",
      typeof body.request_id === "string" ? body.request_id : undefined,
    );
  }
  return new ApiClientError("NETWORK_ERROR", "无法连接 API 服务。");
}

export async function streamRequest(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(apiUrl(path), {
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (
      error !== null &&
      typeof error === "object" &&
      "name" in error &&
      error.name === "AbortError"
    ) {
      throw error;
    }
    throw new ApiClientError("NETWORK_ERROR", "无法连接 API 服务。");
  }
  if (!response.ok) {
    let error: unknown;
    try {
      error = await response.json();
    } catch {
      error = undefined;
    }
    throw toApiClientError(error);
  }
  return response;
}
