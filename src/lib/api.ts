const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class AppError extends Error {
  constructor(
    message: string,
    public readonly status: number | null = null,
    public readonly code = "REQUEST_FAILED",
    public readonly details: Record<string, unknown> | null = null,
  ) {
    super(message);
    this.name = "AppError";
  }
}

export async function apiRequest<T>(path: string, init: RequestInit = {}, timeoutMs = 30_000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        Accept: "application/json",
        ...(init.body instanceof FormData ? {} : init.body ? { "Content-Type": "application/json" } : {}),
        ...init.headers,
      },
      signal: init.signal ?? controller.signal,
    });
    if (response.status === 204) return undefined as T;
    const payload = (await response.json().catch(() => null)) as
      | { error?: { code?: string; message?: string; details?: Record<string, unknown> } }
      | null;
    if (!response.ok) {
      throw new AppError(
        payload?.error?.message ?? `请求失败（${response.status}）`,
        response.status,
        payload?.error?.code ?? "HTTP_ERROR",
        payload?.error?.details ?? null,
      );
    }
    return payload as T;
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      if (init.signal?.aborted) throw new AppError("操作已取消", null, "REQUEST_CANCELLED");
      throw new AppError("服务响应超时，请稍后重试", null, "REQUEST_TIMEOUT");
    }
    throw new AppError("暂时无法连接本地服务，请确认服务已启动", null, "NETWORK_ERROR");
  } finally {
    window.clearTimeout(timer);
  }
}

export function apiGet<T>(path: string, timeoutMs = 10_000): Promise<T> {
  return apiRequest<T>(path, {}, timeoutMs);
}

export function apiJson<T>(path: string, method: "POST" | "PATCH" | "PUT" | "DELETE", body?: unknown, timeoutMs = 30_000, headers?: HeadersInit): Promise<T> {
  return apiRequest<T>(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) }, timeoutMs);
}

export function uploadProductFile(productId: string, file: File): { promise: Promise<unknown>; cancel: () => void } {
  const controller = new AbortController();
  const form = new FormData();
  form.append("file", file);
  return {
    promise: apiRequest(`/products/${encodeURIComponent(productId)}/documents`, { method: "POST", body: form, signal: controller.signal }, 120_000),
    cancel: () => controller.abort(),
  };
}

export function uploadVisualFile(contentId: string, revision: number, file: File): Promise<unknown> {
  const form = new FormData();
  form.append("file", file);
  return apiRequest(`/contents/${encodeURIComponent(contentId)}/visual-assets?revision=${revision}`, { method: "POST", body: form }, 120_000);
}
