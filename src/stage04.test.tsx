import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentStudioPage } from "./pages/ContentStudioPage";
import type { ContentItem, GenerationJob, VisualDraft } from "./types";


const productResult = { items: [{ id: "darwin-12", name: "达尔文12号", insurer: "复星", category: "重疾险", version: "V1", status: "已确认", sourceType: "演示", updatedAt: "2026-08-26" }] };

const visual: VisualDraft = {
  assetId: "asset-safe-1",
  status: "draft",
  layout: {
    assetId: "asset-safe-1", imageType: "宣传长图", template: "long-editorial", palette: "brand",
    brandColors: { primary: "#6d5ef7", secondary: "#2ac7b8" },
    blocks: [
      { id: "title", type: "title", text: "家庭保障，从看懂责任开始", required: true },
      { id: "product", type: "product", text: "达尔文12号", required: true },
      { id: "selling-1", type: "selling", text: "保障责任清晰", required: false },
      { id: "disclaimer", type: "disclaimer", text: "具体责任以保险合同为准", required: true },
    ],
    blockOrder: ["title", "product", "selling-1", "disclaimer"], hiddenBlockIds: [],
  },
};

const confirmedItem: ContentItem = {
  id: "image-content", type: "image", title: "Stage 04 图片项目", productId: "darwin-12", status: "draft", revision: 3,
  manualSource: "", settings: { imageType: "宣传长图" }, payload: {
    copy: { title: "家庭保障，从看懂责任开始", sellingPoints: ["保障责任清晰"], blocks: [{ type: "hero", text: "产品介绍" }], disclaimer: "具体责任以保险合同为准" },
    copyStatus: "confirmed",
  }, branches: [], jobs: [], createdAt: "2026-08-26", updatedAt: "2026-08-26",
};

function job(status: GenerationJob["status"], taskType = "image_visual"): GenerationJob {
  return { id: "visual-job", contentId: "image-content", branchId: null, taskType, status, input: {}, output: {}, error: status === "failed" ? "模拟千问限流，输入已保留" : "", errorCode: status === "failed" ? "QWEN_RATE_LIMITED" : "", attempt: status === "queued" ? 0 : 1, provider: "qwen", model: "qwen-image-3.0" };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function baseFetch(item: ContentItem, handler?: (url: string, init?: RequestInit) => Response | undefined) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const handled = handler?.(url, init);
    if (handled) return handled;
    if (url.includes("/products")) return json(productResult);
    if (url.includes("/contents?type=image")) return json({ items: [item], total: 1 });
    if (url.endsWith("/contents/image-content")) return json(item);
    return json({ error: { message: `unexpected ${url}` } }, 500);
  });
}

async function openImageProject() {
  render(<ContentStudioPage type="image" />);
  fireEvent.click(await screen.findByRole("button", { name: /Stage 04 图片项目/ }));
  await screen.findByText("图片文案确认");
}

describe("Stage 04 image generation and deterministic layout", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("generates a visual asset only after copy confirmation and renders program text", async () => {
    const completedItem = { ...confirmedItem, revision: 4, payload: { ...confirmedItem.payload, visual }, jobs: [job("succeeded")] };
    let generated = false;
    const fetchMock = baseFetch(confirmedItem, (url, init) => {
      if (url.endsWith("/contents/image-content/jobs") && init?.method === "POST") return json(job("queued"), 201);
      if (url.endsWith("/jobs/visual-job/run") && init?.method === "POST") { generated = true; return json(job("succeeded")); }
      if (generated && url.endsWith("/contents/image-content")) return json(completedItem);
      return undefined;
    });
    vi.stubGlobal("fetch", fetchMock);
    await openImageProject();
    expect(screen.getByLabelText("image_copy 人工内容")).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "生成视觉素材" }));
    expect(await screen.findByText("视觉素材已保存，关键文字已由程序排入模板")).toBeInTheDocument();
    expect(screen.getByText("图片结构化编辑器")).toBeInTheDocument();
    expect(screen.getByText("家庭保障，从看懂责任开始", { selector: ".visual-block" })).toBeInTheDocument();
    expect(screen.getByText("具体责任以保险合同为准", { selector: ".visual-block" })).toBeInTheDocument();
    const image = screen.getByAltText("AI 生成的无关键文字视觉素材") as HTMLImageElement;
    expect(image.src).toContain("/api/v1/visual-assets/asset-safe-1/preview");
    expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/jobs"), expect.objectContaining({ method: "POST" }));
  });

  it("saves template palette order and optional block visibility", async () => {
    const item = { ...confirmedItem, revision: 4, payload: { ...confirmedItem.payload, visual } };
    const updatedVisual = { ...visual, layout: { ...visual.layout, template: "square-focus" as const, palette: "forest" as const, hiddenBlockIds: ["selling-1"] } };
    const updated = { ...item, revision: 5, payload: { ...item.payload, visual: updatedVisual } };
    let requestBody: any;
    vi.stubGlobal("fetch", baseFetch(item, (url, init) => {
      if (url.endsWith("/visual-layout") && init?.method === "PATCH") { requestBody = JSON.parse(String(init.body)); return json(updated); }
      return undefined;
    }));
    await openImageProject();
    fireEvent.change(screen.getByLabelText("图片模板"), { target: { value: "square-focus" } });
    fireEvent.change(screen.getByLabelText("图片配色"), { target: { value: "forest" } });
    const sellingToggle = screen.getByRole("checkbox", { name: /selling/ });
    fireEvent.click(sellingToggle);
    expect(screen.getByRole("checkbox", { name: /title/ })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: /disclaimer/ })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "保存图片布局" }));
    expect(await screen.findByText("图片模板、配色和区块结构已保存")).toBeInTheDocument();
    expect(requestBody.template).toBe("square-focus");
    expect(requestBody.palette).toBe("forest");
    expect(requestBody.hiddenBlockIds).toEqual(["selling-1"]);
  });

  it("keeps the existing visual but marks it stale after explicit copy unlock", async () => {
    const item = { ...confirmedItem, revision: 4, payload: { ...confirmedItem.payload, visual } };
    const stale = { ...item, revision: 5, payload: { ...item.payload, copyStatus: "draft", visual: { ...visual, status: "needs_relayout" as const } } };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.stubGlobal("fetch", baseFetch(item, (url, init) => {
      if (url.endsWith("/unlock-copy") && init?.method === "POST") return json(stale);
      return undefined;
    }));
    await openImageProject();
    fireEvent.click(screen.getByRole("button", { name: "解锁修改文案" }));
    expect(await screen.findByText("图片文案已解锁，既有图片已标记为需重新排版")).toBeInTheDocument();
    expect(screen.getByText(/旧视觉仍保留/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成视觉素材" })).toBeDisabled();
    expect(screen.getByLabelText("image_copy 人工内容")).toBeEnabled();
  });

  it("shows upload validation errors without losing the confirmed project", async () => {
    vi.stubGlobal("fetch", baseFetch(confirmedItem, (url, init) => {
      if (url.includes("/visual-assets?revision=3") && init?.method === "POST") return json({ error: { code: "VISUAL_FILE_INVALID", message: "替换图内容不是有效图片或尺寸不安全" } }, 422);
      return undefined;
    }));
    await openImageProject();
    const upload = screen.getByLabelText("上传自有替换图") as HTMLInputElement;
    fireEvent.change(upload, { target: { files: [new File(["fake"], "伪造.png", { type: "image/png" })] } });
    expect(await screen.findByRole("alert")).toHaveTextContent("替换图内容不是有效图片或尺寸不安全");
    expect(screen.getByText("Stage 04 图片项目")).toBeInTheDocument();
  });

  it("keeps failed visual jobs retryable and archives locked copy jobs", async () => {
    const failedVisual = job("failed");
    const oldCopy = { ...job("failed", "image_copy"), id: "old-copy", error: "历史文案失败" };
    const item = { ...confirmedItem, jobs: [failedVisual, oldCopy] };
    vi.stubGlobal("fetch", baseFetch(item));
    await openImageProject();
    expect(screen.getByText("模拟千问限流，输入已保留")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重试" })).toBeInTheDocument();
    expect(screen.getByText("节点已确认，历史任务已归档")).toBeInTheDocument();
  });
});
