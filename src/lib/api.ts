const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

export class AppError extends Error {
  constructor(
    message: string,
    public readonly status: number | null = null,
    public readonly code = "REQUEST_FAILED",
  ) {
    super(message);
    this.name = "AppError";
  }
}

export async function apiGet<T>(path: string, timeoutMs = 10_000): Promise<T> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    const payload = (await response.json().catch(() => null)) as
      | { error?: { code?: string; message?: string } }
      | null;
    if (!response.ok) {
      throw new AppError(
        payload?.error?.message ?? `请求失败（${response.status}）`,
        response.status,
        payload?.error?.code ?? "HTTP_ERROR",
      );
    }
    return payload as T;
  } catch (error) {
    if (error instanceof AppError) throw error;
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new AppError("服务响应超时，请稍后重试", null, "REQUEST_TIMEOUT");
    }
    throw new AppError("暂时无法连接本地服务，请确认服务已启动", null, "NETWORK_ERROR");
  } finally {
    window.clearTimeout(timer);
  }
}
