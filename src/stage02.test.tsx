import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { BootstrapData, ProductDetail } from "./types";

const bootstrap: BootstrapData = {
  brand: { id: "brand-demo", companyName: "测试企业", productName: "保创AI", primaryColor: "#6D5EF7", secondaryColor: "#19BFA1", tone: "专业", slogan: "测试", disclaimer: "测试" },
  user: { id: "super-demo", name: "超级演示用户", mode: "local_demo", roles: ["管理员"], permissions: ["product.create", "product.update", "product.confirm"] },
  stats: { products: 1, pendingReviews: 0, approvedAssets: 0, possiblyOutdated: 0 },
  serviceStatus: { api: "ready", deepseek: "not_configured", qwen: "not_configured" },
  recentAudit: [],
};

const draft: ProductDetail = {
  id: "draft-1", name: "测试重疾险", insurer: "", category: "", version: "V1", status: "草稿",
  sourceType: "用户上传", updatedAt: "2026-08-26T10:00:00+08:00", summary: "", features: [], revision: 1,
  ownerId: "super-demo", createdAt: "2026-08-26T10:00:00+08:00", confirmedAt: null, documents: [],
  facts: [{ label: "投保年龄", value: "", confirmed: false, required: true }],
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), { status, headers: { "Content-Type": "application/json" } });
}

describe("Stage 02 product library UI", () => {
  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
    window.history.replaceState({}, "", "/");
  });

  it("supports cancelling and completing new draft creation", async () => {
    window.history.replaceState({}, "", "/products");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap);
      if (url.includes("/products?") && (!init?.method || init.method === "GET")) return json({ items: [], total: 0, query: "" });
      if (url.endsWith("/products") && init?.method === "POST") return json(draft, 201);
      if (url.endsWith("/products/draft-1")) return json(draft);
      return json({ error: { message: "unexpected" } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const createButton = await screen.findByRole("button", { name: /新建产品/ });
    fireEvent.click(createButton);
    expect(screen.getByRole("dialog", { name: "新建产品草稿" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(createButton);
    fireEvent.change(screen.getByLabelText("产品名称"), { target: { value: "测试重疾险" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并继续" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/products"), expect.objectContaining({ method: "POST" })));
    expect(await screen.findByText("产品基本字段")).toBeInTheDocument();
  });

  it("edits and saves draft fields while preserving the confirmation checklist", async () => {
    window.history.replaceState({}, "", "/products/draft-1");
    const saved = { ...draft, insurer: "测试保险公司", revision: 2 };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap);
      if (url.endsWith("/products/draft-1") && init?.method === "PATCH") return json(saved);
      if (url.endsWith("/products/draft-1")) return json(draft);
      return json({ error: { message: "unexpected" } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const insurer = await screen.findByLabelText("保险公司 *");
    fireEvent.change(insurer, { target: { value: "测试保险公司" } });
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "保存修改" }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/products/draft-1"), expect.objectContaining({ method: "PATCH" })));
    expect(await screen.findByText("产品字段已保存")).toBeInTheDocument();
    expect(screen.getByText("0/1 已确认")).toBeInTheDocument();
  });

  it("keeps a server-side upload validation error visible after refreshing document state", async () => {
    window.history.replaceState({}, "", "/products/draft-1");
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap);
      if (url.endsWith("/products/draft-1/documents") && init?.method === "POST") {
        return json({ error: { code: "FILE_SIGNATURE_MISMATCH", message: "文件内容不是有效 PDF" } }, 422);
      }
      if (url.endsWith("/products/draft-1")) return json(draft);
      return json({ error: { message: "unexpected" } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { container } = render(<App />);
    await screen.findByText("产品基本字段");
    const file = new File(["not a pdf"], "伪造.pdf", { type: "application/pdf" });
    fireEvent.change(container.querySelector('input[type="file"]') as HTMLInputElement, { target: { files: [file] } });
    expect(await screen.findByText("文件内容不是有效 PDF")).toBeInTheDocument();
    expect(screen.getByText("尚未上传源资料")).toBeInTheDocument();
  });
});
