import { afterEach, describe, expect, it, vi } from "vitest";
import { apiGet, AppError } from "./api";

describe("apiGet", () => {
  afterEach(() => vi.restoreAllMocks());

  it("returns JSON for a successful request", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 })));
    await expect(apiGet<{ ok: boolean }>("/health")).resolves.toEqual({ ok: true });
  });

  it("preserves the API error message on failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: { code: "HTTP_ERROR", message: "产品不存在" } }),
      { status: 404 },
    )));
    await expect(apiGet("/products/missing")).rejects.toEqual(
      expect.objectContaining({ message: "产品不存在", status: 404 }),
    );
  });
});
