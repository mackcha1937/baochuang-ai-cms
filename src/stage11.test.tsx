import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentStudioPage } from "./pages/ContentStudioPage";
import type { ContentItem } from "./types";

const products = { items: [{ id: "darwin-12", name: "达尔文12号", insurer: "复星", category: "重疾险", version: "V1", status: "已确认", sourceType: "演示", updatedAt: "2026-08-28" }] };

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function mockStudio(type: string, item: ContentItem, saved: ContentItem = item) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/products")) return json(products);
    if (url.includes(`/contents?type=${type}`)) return json({ items: [item], total: 1 });
    if (url.endsWith(`/contents/${item.id}/manual-output`) && init?.method === "PUT") return json(saved);
    if (url.endsWith(`/contents/${item.id}`)) return json(item);
    return json({ error: { message: `unexpected ${url}` } }, 500);
  });
}

describe("Stage 11 Markdown content and presenter", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("renders legacy image copy as Markdown and saves Markdown instead of JSON", async () => {
    const copy = { title: "守护每一步", sellingPoints: ["保障清晰"], blocks: [{ type: "hero", text: "重疾保障" }], disclaimer: "具体以合同为准" };
    const item: ContentItem = { id: "image-md", type: "image", title: "Markdown 图片", productId: "darwin-12", status: "draft", revision: 1, manualSource: "", settings: {}, payload: { copy, copyStatus: "draft" }, branches: [], jobs: [], reviews: [], createdAt: "2026-08-28", updatedAt: "2026-08-28" };
    const fetchMock = mockStudio("image", item, { ...item, revision: 2 });
    vi.stubGlobal("fetch", fetchMock);
    render(<ContentStudioPage type="image" />);
    fireEvent.click(await screen.findByRole("button", { name: /Markdown 图片/ }));
    const editor = await screen.findByLabelText("image_copy 人工内容");
    expect((editor as HTMLTextAreaElement).value).toContain("# 守护每一步");
    expect((editor as HTMLTextAreaElement).value).not.toContain('{"title"');
    fireEvent.change(editor, { target: { value: "# 修改标题\n\n## 核心卖点\n- 保障清晰\n\n## 画面区块\n### hero\n保障介绍\n\n## 免责声明\n以合同为准" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Markdown" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([url, init]) => {
      if (!String(url).endsWith("/manual-output") || init?.method !== "PUT") return false;
      const body = JSON.parse(String(init.body)); return typeof body.markdown === "string" && body.output === undefined;
    })).toBe(true));
  });

  it("blocks HTML locally before a Markdown request is sent", async () => {
    const item: ContentItem = { id: "image-empty", type: "image", title: "安全 Markdown", productId: "darwin-12", status: "draft", revision: 1, manualSource: "", settings: {}, payload: {}, branches: [], jobs: [], reviews: [], createdAt: "2026-08-28", updatedAt: "2026-08-28" };
    const fetchMock = mockStudio("image", item);
    vi.stubGlobal("fetch", fetchMock);
    render(<ContentStudioPage type="image" />);
    fireEvent.click(await screen.findByRole("button", { name: /安全 Markdown/ }));
    fireEvent.change(await screen.findByLabelText("image_copy 人工内容"), { target: { value: "# 标题\n<script>alert(1)</script>" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Markdown" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Markdown 不允许包含 HTML 标签");
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/manual-output"))).toBe(false);
  });

  it("renders article outline and body as Markdown", async () => {
    const item: ContentItem = {
      id: "article-md", type: "article", title: "Markdown 文章", productId: "darwin-12", status: "draft", revision: 4, manualSource: "", settings: {}, payload: {}, jobs: [], reviews: [], createdAt: "2026-08-28", updatedAt: "2026-08-28",
      branches: [{ id: "wechat", channel: "wechat", outline: { title: "文章框架", sections: [{ heading: "保障重点", points: ["核对等待期"] }] }, outlineStatus: "confirmed", body: { title: "完整文章", paragraphs: ["第一段正文。", "第二段正文。"], disclaimer: "以合同为准。" }, bodyStatus: "draft", reviewStatus: "draft" }],
    };
    vi.stubGlobal("fetch", mockStudio("article", item));
    render(<ContentStudioPage type="article" />);
    fireEvent.click(await screen.findByRole("button", { name: /Markdown 文章/ }));
    const articleEditor = await screen.findByLabelText("article_generate 人工内容");
    const markdownOutputs = document.querySelectorAll(".structured-output--markdown");
    expect(markdownOutputs[0]).toHaveTextContent("# 文章框架");
    expect(markdownOutputs[1]).toHaveTextContent("# 完整文章");
    expect((articleEditor as HTMLTextAreaElement).value).toContain("## 正文");
    expect(screen.queryByText(/"paragraphs"/)).not.toBeInTheDocument();
  });

  it("uses the generated yellow 3D presenter in the training player", async () => {
    const item: ContentItem = {
      id: "training-avatar", type: "training", title: "数字人课程", productId: "darwin-12", status: "draft", revision: 5, manualSource: "", settings: {}, branches: [], jobs: [], reviews: [], createdAt: "2026-08-28", updatedAt: "2026-08-28",
      payload: { outlineStatus: "confirmed", scriptStatus: "confirmed", script: { chapters: [{ title: "第一章", narration: "欢迎学习本章。", knowledgeCard: "依据已确认资料。" }], questions: [0, 1, 2].map((index) => ({ question: `问题${index}`, options: ["正确", "错误"], answer: "正确", explanation: "依据资料" })) } },
      trainingSession: { chapterIndex: 0, sentenceIndex: 0, playbackRate: 1, mode: "silent", answers: {}, lastScore: null, results: [], attempt: 0, status: "ready", updatedAt: "2026-08-28" },
    };
    vi.stubGlobal("fetch", mockStudio("training", item));
    render(<ContentStudioPage type="training" />);
    fireEvent.click(await screen.findByRole("button", { name: /数字人课程/ }));
    const presenter = await screen.findByLabelText("黄色三维吉祥物数字讲师待机");
    expect(presenter.querySelector("img")).toHaveAttribute("src", "/training-presenter-v2.png");
    expect(screen.queryByLabelText(/二维动画讲师/)).not.toBeInTheDocument();
  });
});
