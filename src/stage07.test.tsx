import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AssetLibraryPage } from "./pages/AssetLibraryPage";
import type { AssetDetail, AssetSummary, ContentItem } from "./types";

const asset: AssetSummary = {
  id: "version-image-1", contentVersionId: "version-image-1", contentId: "image-1", branchId: null,
  contentVersion: 1, type: "image", channel: null, title: "家庭保障长图", status: "approved",
  createdAt: "2026-08-27T08:00:00+08:00", updatedAt: "2026-08-27T08:00:00+08:00",
};

const imageDetail: AssetDetail = {
  ...asset, submittedBy: "super-demo", submittedAt: "2026-08-27T08:00:00+08:00",
  snapshot: { payload: { visual: { assetId: "visual-1", layout: { blocks: [{ id: "title", type: "title", text: "家庭保障，从看懂责任开始" }, { id: "disclaimer", type: "disclaimer", text: "具体责任以保险合同为准" }], blockOrder: ["title", "disclaimer"], hiddenBlockIds: [] } } } },
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("Stage 07 asset library", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("filters, safely previews and downloads an approved image", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.endsWith(`/assets/${asset.id}`)) return json(imageDetail);
      if (url.includes("/assets")) return json({ items: [asset], total: 1 });
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><AssetLibraryPage /></MemoryRouter>);

    expect(await screen.findByRole("button", { name: /家庭保障长图/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("素材类型"), { target: { value: "image" } });
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url).includes("type=image"))).toBe(true));
    fireEvent.click(screen.getByRole("button", { name: /家庭保障长图/ }));
    expect(await screen.findByRole("img", { name: "家庭保障长图批准版本预览" })).toHaveAttribute("src", "/api/v1/visual-assets/visual-1/preview");
    expect(screen.getByText("家庭保障，从看懂责任开始")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载 PNG" })).toHaveAttribute("href", `/api/v1/assets/${asset.id}/download`);
    fireEvent.click(screen.getByRole("button", { name: "← 返回素材库" }));
    expect(screen.getByLabelText("批准素材列表")).toBeInTheDocument();
  });

  it("duplicates an approved asset into an independent draft", async () => {
    const copy: ContentItem = {
      id: "draft-copy", type: "image", title: "家庭保障长图（副本）", productId: "darwin-12", status: "draft", revision: 1,
      manualSource: "", settings: {}, payload: { copyStatus: "draft", visual: { status: "needs_relayout" } }, branches: [], jobs: [], reviews: [], createdAt: "2026-08-27", updatedAt: "2026-08-27",
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/assets/${asset.id}/duplicate`) && init?.method === "POST") return json(copy, 201);
      if (url.endsWith(`/assets/${asset.id}`)) return json(imageDetail);
      if (url.endsWith("/assets")) return json({ items: [asset], total: 1 });
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><AssetLibraryPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /家庭保障长图/ }));
    fireEvent.click(await screen.findByRole("button", { name: "复制为新草稿" }));
    expect(await screen.findByText("已复制为独立草稿，后续修改不会影响已批准素材")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开草稿列表/ })).toHaveAttribute("href", "/create/image");
    expect(fetchMock.mock.calls.find(([url]) => String(url).endsWith("/duplicate"))?.[1]?.method).toBe("POST");
  });

  it("renders article content as a structured read-only preview", async () => {
    const article: AssetSummary = { ...asset, id: "version-article-1", contentVersionId: "version-article-1", contentId: "article-1", branchId: "branch-1", type: "article", channel: "wechat", title: "读懂重疾保障" };
    const detail: AssetDetail = { ...article, submittedBy: "super-demo", submittedAt: article.createdAt, snapshot: { branch: { body: { title: "读懂重疾保障", paragraphs: ["第一段批准正文", "第二段批准正文"], disclaimer: "具体以保险合同为准" } } } };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => String(input).endsWith(`/assets/${article.id}`) ? json(detail) : json({ items: [article], total: 1 })));
    render(<MemoryRouter><AssetLibraryPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /读懂重疾保障/ }));
    expect(await screen.findByText("第一段批准正文")).toBeInTheDocument();
    expect(screen.getByText("免责声明")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载 DOCX" })).toHaveAttribute("href", `/api/v1/assets/${article.id}/download`);
  });
});
