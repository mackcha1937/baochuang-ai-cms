import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

describe("application recovery state", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  it("keeps a visible retry path when bootstrap fails", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ error: { code: "HTTP_ERROR", message: "模拟服务失败" } }),
      { status: 503 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    expect(await screen.findByText("页面暂时无法加载")).toBeInTheDocument();
    expect(screen.getByText("模拟服务失败")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(screen.getByText("页面暂时无法加载")).toBeInTheDocument();
  });
});
