import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentStudioPage } from "./pages/ContentStudioPage";
import type { ContentItem } from "./types";

const productResult = { items: [{ id: "darwin-12", name: "达尔文12号", insurer: "复星", category: "重疾险", version: "V1", status: "已确认", sourceType: "演示", updatedAt: "2026-08-26" }] };

const baseItem: ContentItem = {
  id: "content-1", type: "image", title: "图片文案项目", productId: "darwin-12", status: "draft", revision: 1,
  manualSource: "补充素材", settings: {}, payload: {}, branches: [], jobs: [], createdAt: "2026-08-26", updatedAt: "2026-08-26",
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("Stage 03 AI content studio", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("keeps a missing-key generation failure visible without losing the draft", async () => {
    const failedJob = { id: "job-1", contentId: "content-1", branchId: null, taskType: "image_copy", status: "failed", input: { product: { name: "达尔文12号" } }, output: {}, error: "DeepSeek API Key 未配置，已保留全部输入", errorCode: "CONFIG_AI_KEY_MISSING", attempt: 1, provider: "deepseek", model: "deepseek-v4-flash" };
    const restored = { ...baseItem, jobs: [failedJob] };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/products")) return json(productResult);
      if (url.includes("/contents?type=image")) return json({ items: [], total: 0 });
      if (url.endsWith("/contents") && init?.method === "POST") return json(baseItem, 201);
      if (url.endsWith("/contents/content-1/jobs") && init?.method === "POST") return json({ ...failedJob, status: "queued", attempt: 0 }, 201);
      if (url.endsWith("/jobs/job-1/run")) return json(failedJob);
      if (url.endsWith("/contents/content-1")) return json(restored);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ContentStudioPage type="image" />);
    fireEvent.click(await screen.findByRole("button", { name: /新建第一个项目/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /新建创作/ }));
    fireEvent.change(screen.getByLabelText("项目标题 *"), { target: { value: "图片文案项目" } });
    fireEvent.change(screen.getByLabelText("人工补充素材"), { target: { value: "补充素材" } });
    fireEvent.click(screen.getByRole("button", { name: "创建草稿" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成图片文案" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("DeepSeek API Key 未配置，已保留全部输入");
    expect(screen.getByText("图片文案项目")).toBeInTheDocument();
    expect(screen.getByText("image_copy")).toBeInTheDocument();
  });

  it("renders independent WeChat and Xiaohongshu outline controls", async () => {
    const article: ContentItem = {
      ...baseItem, type: "article", title: "双渠道文章", branches: [
        { id: "w", channel: "wechat", outline: {}, outlineStatus: "empty", body: {}, bodyStatus: "empty" },
        { id: "x", channel: "xiaohongshu", outline: {}, outlineStatus: "empty", body: {}, bodyStatus: "empty" },
      ],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/products")) return json(productResult);
      if (url.includes("/contents?type=article")) return json({ items: [article], total: 1 });
      if (url.endsWith("/contents/content-1")) return json(article);
      return json({ error: { message: "unexpected" } }, 500);
    }));
    render(<ContentStudioPage type="article" />);
    fireEvent.click(await screen.findByRole("button", { name: /双渠道文章/ }));
    expect(await screen.findByText("公众号分支")).toBeInTheDocument();
    expect(screen.getByText("小红书分支")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "生成 Markdown 框架" })).toHaveLength(2);
    expect(screen.getAllByLabelText("article_outline 人工内容").every((editor) => !editor.hasAttribute("disabled"))).toBe(true);
    await waitFor(() => expect(screen.getByText(/修订 1/)).toBeInTheDocument());
  });

  it("saves manual fallback output and refreshes the editor from the persisted result", async () => {
    const persisted = {
      ...baseItem,
      revision: 2,
      payload: { copy: { title: "人工文案", sellingPoints: ["事实卖点"], blocks: [{ type: "hero", text: "保障介绍" }], disclaimer: "以合同为准" }, copyStatus: "draft" },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes("/products")) return json(productResult);
      if (url.includes("/contents?type=image")) return json({ items: [baseItem], total: 1 });
      if (url.endsWith("/contents/content-1/manual-output") && init?.method === "PUT") return json(persisted);
      if (url.endsWith("/contents/content-1")) return json(baseItem);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ContentStudioPage type="image" />);
    fireEvent.click(await screen.findByRole("button", { name: /图片文案项目/ }));
    const editor = await screen.findByLabelText("image_copy 人工内容");
    fireEvent.change(editor, { target: { value: "# 人工文案\n\n## 核心卖点\n- 事实卖点\n\n## 画面区块\n### hero\n保障介绍\n\n## 免责声明\n以合同为准" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Markdown" }));
    expect(await screen.findByText("人工内容已保存，可继续确认")).toBeInTheDocument();
    expect(document.querySelector(".structured-output")).toHaveTextContent("# 人工文案");
    expect((screen.getByLabelText("image_copy 人工内容") as HTMLTextAreaElement).value).toContain("## 核心卖点");
    const request = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/manual-output") && init?.method === "PUT");
    expect(JSON.parse(String(request?.[1]?.body))).toMatchObject({ markdown: expect.stringContaining("# 人工文案") });
    expect(screen.getByRole("button", { name: "确认文案" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/manual-output"), expect.objectContaining({ method: "PUT" }));
  });

  it("locks the training script editor until the outline is confirmed", async () => {
    const training: ContentItem = {
      ...baseItem,
      type: "training",
      title: "顺序门禁课程",
      payload: { outlineStatus: "empty", scriptStatus: "locked" },
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/products")) return json(productResult);
      if (url.includes("/contents?type=training")) return json({ items: [training], total: 1 });
      if (url.endsWith("/contents/content-1")) return json(training);
      return json({ error: { message: "unexpected" } }, 500);
    }));
    render(<ContentStudioPage type="training" />);
    fireEvent.click(await screen.findByRole("button", { name: /顺序门禁课程/ }));
    expect(await screen.findByLabelText("training_outline 人工内容")).toBeEnabled();
    expect(screen.getByLabelText("training_script 人工内容")).toBeDisabled();
    expect(screen.getByRole("button", { name: "生成培训讲稿" })).toBeDisabled();
  });

  it("archives retries and locks editors after training nodes are confirmed", async () => {
    const confirmedTraining: ContentItem = {
      ...baseItem,
      type: "training",
      title: "已确认课程",
      payload: {
        outline: { title: "课程框架", chapters: [{ title: "第一章", objective: "掌握知识" }] },
        outlineStatus: "confirmed",
        script: { chapters: [{ title: "第一章", narration: "讲稿", knowledgeCard: "知识卡" }], questions: [{}, {}, {}] },
        scriptStatus: "confirmed",
      },
      jobs: [{ id: "job-old", contentId: "content-1", branchId: null, taskType: "training_script", status: "failed", input: {}, output: {}, error: "历史失败", errorCode: "AI_TIMEOUT", attempt: 1, provider: "deepseek", model: "deepseek-v4-flash" }],
    };
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/products")) return json(productResult);
      if (url.includes("/contents?type=training")) return json({ items: [confirmedTraining], total: 1 });
      if (url.endsWith("/contents/content-1")) return json(confirmedTraining);
      return json({ error: { message: "unexpected" } }, 500);
    }));
    render(<ContentStudioPage type="training" />);
    fireEvent.click(await screen.findByRole("button", { name: /已确认课程/ }));
    expect(await screen.findByLabelText("training_outline 人工内容")).toBeDisabled();
    expect(screen.getByLabelText("training_script 人工内容")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "重试" })).not.toBeInTheDocument();
    expect(screen.getByText("节点已确认，历史任务已归档")).toBeInTheDocument();
  });
});
