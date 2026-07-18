import { describe, expect, it } from "vitest";

import { toApiClientError } from "./client";

describe("toApiClientError", () => {
  it("preserves structured API error fields", () => {
    const error = toApiClientError({
      code: "VALIDATION_ERROR",
      detail: "Request validation failed",
      request_id: "request-1",
    });

    expect(error.code).toBe("VALIDATION_ERROR");
    expect(error.requestId).toBe("request-1");
  });

  it("maps transport failures to a safe message", () => {
    const error = toApiClientError(undefined);

    expect(error.code).toBe("NETWORK_ERROR");
    expect(error.message).toBe("无法连接 API 服务。");
  });
});
