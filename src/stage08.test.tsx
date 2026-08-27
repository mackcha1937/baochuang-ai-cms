import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { AssetLibraryPage } from "./pages/AssetLibraryPage";
import type { AssetDetail, AssetSummary, BootstrapData, ContentItem, ProductDetail } from "./types";

const bootstrap: BootstrapData = {
  brand: { id: "brand", companyName: "测试企业", productName: "保创AI", primaryColor: "#6D5EF7", secondaryColor: "#19BFA1", tone: "专业", slogan: "测试", disclaimer: "测试" },
  user: { id: "super-demo", name: "超级演示用户", mode: "local_demo", roles: ["管理员"], permissions: ["product.update", "product.confirm", "asset.view", "asset.invalidate", "asset.duplicate"] },
  stats: { products: 1, pendingReviews: 0, approvedAssets: 0, possiblyOutdated: 1 },
  serviceStatus: { api: "ready", deepseek: "configured", qwen: "configured" }, recentAudit: [],
};

const facts = [{ label: "等待期", value: "180天", confirmed: true, required: true }];
const v1: ProductDetail = {
  id: "product-v1", name: "测试重疾险", insurer: "测试保险", category: "重疾险", version: "V1", status: "已确认",
  sourceType: "用户上传", updatedAt: "2026-08-27", summary: "V1摘要", features: [], facts, documents: [], revision: 2,
  ownerId: "super-demo", createdAt: "2026-08-27", confirmedAt: "2026-08-27", rootProductId: "product-v1", previousVersionId: null, isCurrent: true, effectiveAt: null,
  changes: [], versions: [],
};
const v2: ProductDetail = {
  ...v1, id: "product-v2", version: "V2", status: "草稿", sourceType: "版本更新", revision: 3, confirmedAt: null,
  previousVersionId: v1.id, isCurrent: false, summary: "V2新增等待期优化", facts: [{ ...facts[0], value: "90天" }],
  changes: [{ field: "summary", label: "产品摘要", before: "V1摘要", after: "V2新增等待期优化" }, { field: "fact:等待期", label: "等待期", before: "180天", after: "90天" }],
  versions: [
    { ...v1, isCurrent: true, effectiveAt: null, confirmedAt: "2026-08-27" },
    { id: "product-v2", name: v1.name, insurer: v1.insurer, category: v1.category, version: "V2", status: "草稿", sourceType: "版本更新", updatedAt: "2026-08-27", isCurrent: false, effectiveAt: null, confirmedAt: null },
  ],
};

const outdated: AssetSummary = {
  id: "asset-old", contentVersionId: "content-version", contentId: "content-old", branchId: null, contentVersion: 1,
  type: "image", channel: null, title: "旧产品宣传图", status: "possibly_outdated", createdAt: "2026-08-27", updatedAt: "2026-08-27",
};
const outdatedDetail: AssetDetail = { ...outdated, submittedBy: "super-demo", submittedAt: "2026-08-27", snapshot: { payload: { visual: { assetId: "visual", layout: { blocks: [], blockOrder: [], hiddenBlockIds: [] } } } } };

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("Stage 08 product versions and asset impact", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

  it("creates V2 from the current confirmed product and shows differences", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.history.replaceState({}, "", `/products/${v1.id}`);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap);
      if (url.endsWith(`/products/${v1.id}/new-version`) && init?.method === "POST") return json(v2, 201);
      if (url.endsWith(`/products/${v2.id}`)) return json(v2);
      if (url.endsWith(`/products/${v1.id}`)) return json(v1);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: "创建新版本" }));
    expect(await screen.findByText("与上一版本的变化")).toBeInTheDocument();
    expect(screen.getByText("180天")).toBeInTheDocument();
    expect(screen.getByText("90天")).toBeInTheDocument();
    expect(screen.getByLabelText("新版本生效时间")).toBeInTheDocument();
    expect(screen.getByText("2 个版本")).toBeInTheDocument();
    expect(fetchMock.mock.calls.find(([url]) => String(url).endsWith("/new-version"))?.[1]?.method).toBe("POST");
  });

  it("confirms V2 with an explicit effective time", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    window.history.replaceState({}, "", `/products/${v2.id}`);
    const confirmed = { ...v2, status: "已确认", isCurrent: true, effectiveAt: "2026-09-01T09:30", revision: 4 };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap);
      if (url.endsWith(`/products/${v2.id}/confirm`) && init?.method === "POST") return json(confirmed);
      if (url.endsWith(`/products/${v2.id}`)) return json(v2);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    fireEvent.change(await screen.findByLabelText("新版本生效时间"), { target: { value: "2026-09-01T09:30" } });
    fireEvent.click(screen.getByRole("button", { name: "确认V2" }));
    expect(await screen.findByText("产品已确认，当前版本进入只读状态")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/confirm"));
    expect(JSON.parse(String(call?.[1]?.body)).effectiveAt).toBe("2026-09-01T09:30");
  });

  it("requires a reason then keeps a possibly outdated asset valid", async () => {
    const approved: AssetDetail = { ...outdatedDetail, status: "approved" };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/assets/${outdated.id}/keep-valid`) && init?.method === "POST") return json(approved);
      if (url.endsWith(`/assets/${outdated.id}`)) return json(outdatedDetail);
      if (url.includes("/assets")) return json({ items: [outdated], total: 1 });
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><AssetLibraryPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /旧产品宣传图/ }));
    expect(screen.queryByRole("link", { name: "下载 PNG" })).not.toBeInTheDocument();
    fireEvent.click(await screen.findByRole("button", { name: "保持有效" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请填写处置原因");
    fireEvent.change(screen.getByLabelText("素材处置原因"), { target: { value: "产品变化不影响图片事实" } });
    fireEvent.click(screen.getByRole("button", { name: "保持有效" }));
    expect(await screen.findByText("处置完成，素材已恢复为可下载的批准状态")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "下载 PNG" })).toBeInTheDocument();
  });

  it("creates a new-version revision draft and restores invalid assets only to pending review", async () => {
    const draft: ContentItem = { id: "revision-draft", type: "image", title: "旧产品宣传图（副本）", productId: "product-v2", status: "draft", revision: 1, manualSource: "", settings: {}, payload: { copyStatus: "draft" }, branches: [], jobs: [], reviews: [], createdAt: "2026-08-27", updatedAt: "2026-08-27" };
    const invalid: AssetDetail = { ...outdatedDetail, status: "invalid" };
    const pending: AssetDetail = { ...outdatedDetail, status: "restore_pending" };
    let detail: AssetDetail = outdatedDetail;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith(`/assets/${outdated.id}/revise`) && init?.method === "POST") return json(draft, 201);
      if (url.endsWith(`/assets/${outdated.id}/restore`) && init?.method === "POST") { detail = pending; return json(pending); }
      if (url.endsWith(`/assets/${outdated.id}`)) return json(detail);
      if (url.includes("/assets")) return json({ items: [{ ...outdated, status: detail.status }], total: 1 });
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    const { unmount } = render(<MemoryRouter><AssetLibraryPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /旧产品宣传图/ }));
    fireEvent.click(await screen.findByRole("button", { name: "关联新版本修改" }));
    expect(await screen.findByText("已基于当前产品版本创建修改草稿，原素材状态未改变")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /打开草稿列表/ })).toHaveAttribute("href", "/create/image");
    unmount(); detail = invalid;
    render(<MemoryRouter><AssetLibraryPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /旧产品宣传图/ }));
    fireEvent.change(await screen.findByLabelText("素材处置原因"), { target: { value: "提交重新复核" } });
    fireEvent.click(screen.getByRole("button", { name: "恢复到待复核" }));
    expect(await screen.findByText("恢复申请已进入待复核，不能直接下载")).toBeInTheDocument();
    expect(screen.getByText("待复核")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "下载 PNG" })).not.toBeInTheDocument();
  });
});
