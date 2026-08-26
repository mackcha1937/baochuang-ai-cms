import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContentStudioPage } from "./pages/ContentStudioPage";
import type { ContentItem, TrainingSession } from "./types";

const productResult = { items: [{ id: "darwin-12", name: "达尔文12号", insurer: "复星", category: "重疾险", version: "V1", status: "已确认", sourceType: "演示", updatedAt: "2026-08-26" }] };
const script = {
  chapters: [
    { title: "产品定位", narration: "第一句介绍产品定位。第二句说明适用场景。", knowledgeCard: "只依据已确认产品资料。" },
    { title: "保障要点", narration: "本章介绍保障要点。", knowledgeCard: "具体责任以合同为准。" },
  ],
  questions: [
    { question: "第一题", options: ["选项甲", "选项乙"], answer: "选项甲", explanation: "第一题解析" },
    { question: "第二题", options: ["选项甲", "选项乙"], answer: "选项乙", explanation: "第二题解析" },
    { question: "第三题", options: ["选项甲", "选项乙"], answer: "选项甲", explanation: "第三题解析" },
  ],
};

const baseSession: TrainingSession = {
  chapterIndex: 0, sentenceIndex: 0, playbackRate: 1, mode: "voice", answers: {},
  lastScore: null, results: [], attempt: 0, status: "ready", updatedAt: "2026-08-27T00:00:00+08:00",
};

const training: ContentItem = {
  id: "training-1", type: "training", title: "互动培训课程", productId: "darwin-12", status: "draft", revision: 5,
  manualSource: "", settings: { audience: "保险代理人", duration: 3 },
  payload: { outline: { title: "框架", chapters: [{ title: "产品定位", objective: "掌握定位" }] }, outlineStatus: "confirmed", script, scriptStatus: "confirmed" },
  trainingSession: baseSession, branches: [], jobs: [], createdAt: "2026-08-27", updatedAt: "2026-08-27",
};

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
}

function mockStudio(item: ContentItem, handler?: (url: string, init?: RequestInit) => Promise<Response> | Response) {
  return vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (handler) {
      const result = await handler(url, init);
      if (result) return result;
    }
    if (url.includes("/products")) return json(productResult);
    if (url.includes("/contents?type=training")) return json({ items: [item], total: 1 });
    if (url.endsWith(`/contents/${item.id}`)) return json(item);
    return json({ error: { message: `unexpected ${url}` } }, 500);
  });
}

async function openCourse() {
  fireEvent.click(await screen.findByRole("button", { name: /互动培训课程/ }));
  return screen.findByRole("region", { name: "互动培训播放器" });
}

describe("Stage 05 training player", () => {
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

  it("falls back to silent subtitles and persists chapter progress", async () => {
    const fetchMock = mockStudio(training, async (url, init) => {
      if (url.endsWith("/training-session") && init?.method === "PATCH") {
        const body = JSON.parse(String(init.body));
        return json({ ...training, trainingSession: { ...baseSession, ...body, updatedAt: "saved" } });
      }
      return undefined as never;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ContentStudioPage type="training" />);
    await openCourse();

    fireEvent.click(screen.getByRole("button", { name: "播放当前章节" }));
    expect(await screen.findByRole("status")).toHaveTextContent("静默字幕模式");
    expect(screen.getByText("第一句介绍产品定位。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /02\s+保障要点/ }));
    expect(screen.getByText("本章介绍保障要点。")).toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(expect.stringContaining("/training-session"), expect.objectContaining({ method: "PATCH" })));
    const bodies = fetchMock.mock.calls.filter(([url, init]) => String(url).endsWith("/training-session") && init?.method === "PATCH").map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies.some((body) => body.mode === "silent")).toBe(true);
    expect(bodies.some((body) => body.chapterIndex === 1)).toBe(true);
  });

  it("blocks incomplete answers then shows score and explanations", async () => {
    const scored: ContentItem = {
      ...training,
      trainingSession: {
        ...baseSession, answers: { "0": "选项甲", "1": "选项甲", "2": "选项甲" }, lastScore: 2, attempt: 1, status: "completed",
        results: script.questions.map((question, index) => ({ index, question: question.question, selected: "选项甲", correctAnswer: question.answer, correct: index !== 1, explanation: question.explanation })),
      },
    };
    const fetchMock = mockStudio(training, async (url, init) => {
      if (url.endsWith("/training-session") && init?.method === "PATCH") return json(training);
      if (url.endsWith("/training-quiz/submit") && init?.method === "POST") return json(scored);
      return undefined as never;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ContentStudioPage type="training" />);
    await openCourse();

    fireEvent.click(screen.getByRole("button", { name: "提交答案" }));
    expect(screen.getByRole("alert")).toHaveTextContent("请完成全部 3 道题");
    for (const [index, question] of script.questions.entries()) {
      const group = screen.getByRole("group", { name: `${index + 1}. ${question.question}` });
      fireEvent.click(within(group).getByLabelText("选项甲"));
    }
    fireEvent.click(screen.getByRole("button", { name: "提交答案" }));
    expect(await screen.findByText("得分 2 / 3")).toBeInTheDocument();
    expect(screen.getByText("回答错误 · 正确答案：选项乙")).toBeInTheDocument();
    expect(screen.getByText("第二题解析")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新答题" })).toBeInTheDocument();
  });

  it("restores persisted mode, rate, chapter and partial answers", async () => {
    const restored: ContentItem = { ...training, trainingSession: { ...baseSession, chapterIndex: 1, playbackRate: 1.25, mode: "silent", answers: { "0": "选项乙" } } };
    vi.stubGlobal("fetch", mockStudio(restored));
    render(<ContentStudioPage type="training" />);
    await openCourse();
    expect(screen.getByRole("button", { name: /02\s+保障要点/ })).toHaveAttribute("aria-current", "step");
    expect(screen.getByLabelText("培训播放语速")).toHaveValue("1.25");
    expect(screen.getByRole("button", { name: "尝试中文语音" })).toBeInTheDocument();
    expect(within(screen.getByRole("group", { name: "1. 第一题" })).getByLabelText("选项乙")).toBeChecked();
  });

  it("resets a completed quiz and marks the player stale after script unlock", async () => {
    const completed: ContentItem = { ...training, trainingSession: { ...baseSession, status: "completed", lastScore: 3, attempt: 1, answers: { "0": "选项甲", "1": "选项乙", "2": "选项甲" }, results: script.questions.map((question, index) => ({ index, question: question.question, selected: question.answer, correctAnswer: question.answer, correct: true, explanation: question.explanation })) } };
    const reset = { ...training, trainingSession: { ...baseSession, attempt: 1 } };
    const stale = { ...training, revision: 6, payload: { ...training.payload, scriptStatus: "draft" }, trainingSession: { ...baseSession, status: "stale" as const } };
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = mockStudio(completed, async (url, init) => {
      if (url.endsWith("/training-quiz/reset") && init?.method === "POST") return json(reset);
      if (url.endsWith("/unlock-script") && init?.method === "POST") return json(stale);
      return undefined as never;
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<ContentStudioPage type="training" />);
    await openCourse();
    fireEvent.click(screen.getByRole("button", { name: "重新答题" }));
    expect(await screen.findByText("测试已重置，可以重新作答")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "提交答案" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "解锁修改讲稿" }));
    expect(await screen.findByText(/旧播放进度和答题结果仅作历史保留/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "互动培训播放器" })).not.toBeInTheDocument();
  });

  it("does not mount the player for incomplete legacy quiz data", async () => {
    const legacy: ContentItem = { ...training, payload: { ...training.payload, script: { chapters: script.chapters, questions: [{}, {}, {}] } } };
    vi.stubGlobal("fetch", mockStudio(legacy));
    render(<ContentStudioPage type="training" />);
    fireEvent.click(await screen.findByRole("button", { name: /互动培训课程/ }));
    expect(await screen.findByText(/测试题结构不完整/)).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "互动培训播放器" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("training_script 人工内容")).toBeDisabled();
  });
});
