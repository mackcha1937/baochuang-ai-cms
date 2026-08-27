import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import type { BootstrapData, Brand, BrandAsset } from "./types";

const baseBrand: Brand = {
  id: "brand-demo", companyName: "测试保险服务", productName: "保创AI", primaryColor: "#6D5EF7", secondaryColor: "#19BFA1",
  tone: "专业、亲切", slogan: "让内容更可信", disclaimer: "具体责任以保险合同为准。", revision: 1, updatedAt: "2026-08-27", assets: [],
};

function bootstrap(brand: Brand): BootstrapData {
  return {
    brand, user: { id: "super-demo", name: "超级演示用户", mode: "local_demo", roles: ["管理员"], permissions: ["brand.manage"] },
    stats: { products: 1, pendingReviews: 0, approvedAssets: 0, possiblyOutdated: 0 },
    serviceStatus: { api: "ready", deepseek: "configured", qwen: "configured" }, recentAudit: [],
  };
}

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

describe("Stage 09 brand center", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState({}, "", "/"); });

  it("previews edits, cancels them and saves a revision", async () => {
    window.history.replaceState({}, "", "/brand");
    let current = baseBrand;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap(current));
      if (url.endsWith("/brand") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body)); current = { ...current, ...body, revision: 2 }; return json(current);
      }
      if (url.endsWith("/brand")) return json(current);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const productName = await screen.findByLabelText("平台/产品名称 *");
    fireEvent.change(productName, { target: { value: "星河保创" } });
    expect(screen.getByRole("heading", { name: "星河保创" })).toBeInTheDocument();
    expect(screen.getByText("有未保存修改")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消修改" }));
    expect(screen.getByRole("heading", { name: "保创AI" })).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("企业名称 *"), { target: { value: "星河保险服务" } });
    fireEvent.change(productName, { target: { value: "星河保创" } });
    fireEvent.change(screen.getByLabelText("品牌主色值"), { target: { value: "#123ABC" } });
    fireEvent.click(screen.getByRole("button", { name: "保存品牌" }));
    expect(await screen.findByText("品牌设置已保存")).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([url, init]) => String(url).endsWith("/brand") && init?.method === "PATCH");
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({ companyName: "星河保险服务", productName: "星河保创", primaryColor: "#123ABC", revision: 1 });
    expect(screen.getByText("已保存 · 修订 2")).toBeInTheDocument();
  });

  it("guards unsaved navigation and restores system defaults", async () => {
    window.history.replaceState({}, "", "/brand");
    const defaults = { ...baseBrand, revision: 3 };
    let current = { ...baseBrand, companyName: "已自定义企业", revision: 2 };
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap(current));
      if (url.endsWith("/brand/restore-defaults") && init?.method === "POST") { current = defaults; return json(defaults); }
      if (url.endsWith("/brand")) return json(current);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    }));
    render(<App />);
    fireEvent.change(await screen.findByLabelText("品牌宣传语 *"), { target: { value: "未保存的宣传语" } });
    fireEvent.click(screen.getByRole("link", { name: "工作台" }));
    expect(confirm).toHaveBeenCalledWith("品牌设置尚未保存，确定离开并放弃修改吗？");
    expect(window.location.pathname).toBe("/brand");
    fireEvent.click(screen.getByRole("button", { name: "取消修改" }));
    confirm.mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "恢复系统默认" }));
    expect(await screen.findByText("已恢复系统默认品牌")).toBeInTheDocument();
    expect(screen.getByLabelText("企业名称 *")).toHaveValue(baseBrand.companyName);
  });

  it("previews and removes uploaded visual and writing references", async () => {
    window.history.replaceState({}, "", "/brand");
    const logo: BrandAsset = { id: "logo-1", kind: "logo", name: "logo.png", mimeType: "image/png", sizeBytes: 1024, width: 256, height: 256, previewText: "", createdAt: "2026-08-27" };
    const article: BrandAsset = { id: "article-1", kind: "article", name: "写作样例.docx", mimeType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", sizeBytes: 2048, width: null, height: null, previewText: "专业、温暖、不制造焦虑。", createdAt: "2026-08-27" };
    let current: Brand = { ...baseBrand, revision: 4, assets: [logo, article] };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap(current));
      if (url.includes(`/brand/assets/${article.id}`) && init?.method === "DELETE") { current = { ...current, revision: 5, assets: [logo] }; return json(current); }
      if (url.endsWith("/brand")) return json(current);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(await screen.findByAltText("logo.png")).toHaveAttribute("src", "/api/v1/brand/assets/logo-1/preview");
    expect(screen.getByText("专业、温暖、不制造焦虑。")).toBeInTheDocument();
    const articleCard = screen.getByText("写作样例.docx").closest("article");
    fireEvent.click(within(articleCard as HTMLElement).getByRole("button", { name: "移除" }));
    await waitFor(() => expect(screen.queryByText("写作样例.docx")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.some(([url, init]) => String(url).includes("article-1?revision=4") && init?.method === "DELETE")).toBe(true);
  });

  it("blocks asset upload while fields are dirty then uploads after cancellation", async () => {
    window.history.replaceState({}, "", "/brand");
    const image: BrandAsset = { id: "image-1", kind: "image", name: "reference.png", mimeType: "image/png", sizeBytes: 3, width: 320, height: 320, previewText: "", createdAt: "2026-08-27" };
    let current = baseBrand;
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap")) return json(bootstrap(current));
      if (url.includes("/brand/assets?kind=image") && init?.method === "POST") { current = { ...current, revision: 2, assets: [image] }; return json(current, 201); }
      if (url.endsWith("/brand")) return json(current);
      return json({ error: { message: `unexpected ${url}` } }, 500);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    const input = await screen.findByLabelText("上传品牌参考图");
    const file = new File(["png"], "reference.png", { type: "image/png" });
    fireEvent.change(screen.getByLabelText("品牌宣传语 *"), { target: { value: "未保存" } });
    fireEvent.change(input, { target: { files: [file] } });
    expect(screen.getByRole("alert")).toHaveTextContent("请先保存或取消品牌字段修改");
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes("kind=image"))).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "取消修改" }));
    fireEvent.change(input, { target: { files: [file] } });
    expect(await screen.findByText("参考图已上传")).toBeInTheDocument();
    expect(screen.getByAltText("reference.png")).toBeInTheDocument();
  });
});
