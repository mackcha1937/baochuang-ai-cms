import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentStudioPage } from "./pages/ContentStudioPage";
import { ReviewCenterPage } from "./pages/ReviewCenterPage";
import type { ContentItem, ReviewTask } from "./types";

const products = { items: [{ id: "darwin-12", name: "达尔文12号", insurer: "复星", category: "重疾险", version: "V1", status: "已确认", sourceType: "演示", updatedAt: "2026-08-27" }] };
const body = { title: "读懂重疾保障", paragraphs: ["正文内容"], disclaimer: "具体以保险合同为准" };
const branch = { id: "branch-wechat", channel: "wechat" as const, outline: { title: "框架", sections: [{ heading: "保障", points: ["要点"] }] }, outlineStatus: "confirmed" as const, body, bodyStatus: "confirmed" as const, reviewStatus: "draft" as const };
const article: ContentItem = {
  id: "article-review", type: "article", title: "审核文章", productId: "darwin-12", status: "draft", revision: 5,
  manualSource: "", settings: { channels: ["wechat"] }, payload: {}, branches: [branch], jobs: [], reviews: [],
  createdAt: "2026-08-27", updatedAt: "2026-08-27",
};
const productTask: ReviewTask = {
  id: "review-product", contentId: article.id, branchId: branch.id, contentVersionId: "version-1", contentVersion: 1,
  contentType: "article", contentTitle: article.title, branchChannel: "wechat", stage: "product", status: "pending",
  assigneeId: null, startedAt: null, completedAt: null, createdAt: "2026-08-27", updatedAt: "2026-08-27",
  contentRevision: 5, snapshot: { content: { title: article.title }, branch: { channel: "wechat", body } }, submittedBy: "super-demo", submittedAt: "2026-08-27", records: [],
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("Stage 06 review workflow", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("opens an immutable task, starts it and advances to compliance review", async () => {
    const started = { ...productTask, status: "in_progress" as const, assigneeId: "super-demo", startedAt: "2026-08-27T01:00:00+08:00", records: [{ id: "r1", taskId: productTask.id, actorId: "super-demo", decision: "start" as const, reason: "", createdAt: "2026-08-27" }] };
    const compliance: ReviewTask = { ...productTask, id: "review-compliance", stage: "compliance", status: "pending", assigneeId: null, startedAt: null, records: [] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/reviews") && !init?.method) return json({ items: [productTask], total: 1 });
      if (url.endsWith(`/reviews/${productTask.id}`) && !init?.method) return json(productTask);
      if (url.endsWith(`/reviews/${productTask.id}/start`) && init?.method === "POST") return json(started);
      if (url.endsWith(`/reviews/${productTask.id}/approve`) && init?.method === "POST") return json(compliance);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><ReviewCenterPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /审核文章/ }));
    expect(await screen.findByText(/不可变提交版本 V1/)).toBeInTheDocument();
    expect(screen.getByText(/读懂重疾保障/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "开始审核" }));
    expect(await screen.findByText("审核已开始，提交版本已锁定")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(await screen.findByText("产品审核通过，已进入合规审核")).toBeInTheDocument();
    expect(screen.getByText("合规审核")).toBeInTheDocument();
    const approveCall = fetchMock.mock.calls.find(([url]) => String(url).endsWith("/approve"));
    expect((approveCall?.[1]?.headers as Record<string, string>)["Idempotency-Key"]).toBeTruthy();
  });

  it("requires a rejection reason before sending a decision", async () => {
    const rejected = { ...productTask, status: "rejected" as const, records: [{ id: "r2", taskId: productTask.id, actorId: "super-demo", decision: "reject" as const, reason: "事实表述需修改", createdAt: "2026-08-27" }] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/reviews") && !init?.method) return json({ items: [productTask], total: 1 });
      if (url.endsWith(`/reviews/${productTask.id}`) && !init?.method) return json(productTask);
      if (url.endsWith(`/reviews/${productTask.id}/reject`) && init?.method === "POST") return json(rejected);
      return json({ items: [rejected], total: 1 });
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><ReviewCenterPage /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /审核文章/ }));
    fireEvent.click(await screen.findByRole("button", { name: "驳回修改" }));
    expect(screen.getByRole("alert")).toHaveTextContent("驳回必须填写原因");
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/reject"))).toBe(false);
    fireEvent.change(screen.getByLabelText("审核说明或驳回原因"), { target: { value: "事实表述需修改" } });
    fireEvent.click(screen.getByRole("button", { name: "驳回修改" }));
    expect(await screen.findByText("内容已驳回，创作者可按原因返回修改")).toBeInTheDocument();
  });

  it("submits one article branch, locks it, then withdraws before review starts", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const reviewing: ContentItem = {
      ...article, status: "product_review", revision: 6,
      branches: [{ ...branch, reviewStatus: "product_review" }],
      reviews: [{ id: productTask.id, branchId: branch.id, contentVersion: 1, stage: "product", status: "pending", startedAt: null, completedAt: null, createdAt: "2026-08-27", updatedAt: "2026-08-27" }],
    };
    const withdrawn: ContentItem = { ...article, revision: 7 };
    let detail: ContentItem = article;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/products")) return json(products);
      if (url.includes("/contents?type=article")) return json({ items: [article], total: 1 });
      if (url.endsWith(`/contents/${article.id}`) && !init?.method) return json(detail);
      if (url.endsWith("/submit-review") && init?.method === "POST") { detail = reviewing; return json(productTask, 201); }
      if (url.endsWith("/withdraw-review") && init?.method === "POST") { detail = withdrawn; return json(withdrawn); }
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><ContentStudioPage type="article" /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /审核文章/ }));
    fireEvent.click(await screen.findByRole("button", { name: "提交产品审核" }));
    expect(await screen.findByText(/产品审核中/)).toBeInTheDocument();
    expect(screen.getByLabelText("article_generate 人工内容")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "撤回提交" }));
    expect(await screen.findByText("提交已撤回，可以继续修改后重新提交")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交产品审核" })).toBeInTheDocument();
  });

  it("reopens a rejected branch while preserving its historical review", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const rejected: ContentItem = {
      ...article, status: "rejected_product", revision: 6,
      branches: [{ ...branch, reviewStatus: "rejected_product" }],
      reviews: [{ id: productTask.id, branchId: branch.id, contentVersion: 1, stage: "product", status: "rejected", startedAt: "2026-08-27", completedAt: "2026-08-27", createdAt: "2026-08-27", updatedAt: "2026-08-27", lastReason: "年龄表述需要修正" }],
    };
    const reopened: ContentItem = { ...article, revision: 7, branches: [{ ...branch, bodyStatus: "draft", reviewStatus: "draft" }], reviews: rejected.reviews };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/products")) return json(products);
      if (url.includes("/contents?type=article")) return json({ items: [rejected], total: 1 });
      if (url.endsWith(`/contents/${article.id}`) && !init?.method) return json(rejected);
      if (url.endsWith("/reopen-review") && init?.method === "POST") return json(reopened);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<MemoryRouter><ContentStudioPage type="article" /></MemoryRouter>);
    fireEvent.click(await screen.findByRole("button", { name: /审核文章/ }));
    expect(await screen.findByText("年龄表述需要修正")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "按原因返回修改" }));
    expect(await screen.findByText(/已返回修改/)).toBeInTheDocument();
    expect(screen.getByLabelText("article_generate 人工内容")).not.toBeDisabled();
    expect(screen.getByRole("button", { name: "确认全文" })).toBeInTheDocument();
  });
});
