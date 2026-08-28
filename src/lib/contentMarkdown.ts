const markdownTasks = new Set(["image_copy", "article_outline", "article_generate", "training_outline", "training_script"]);

export function isMarkdownTask(task: string) {
  return markdownTasks.has(task);
}

export const markdownTemplates: Record<string, string> = {
  image_copy: "# 图片标题\n\n## 核心卖点\n- 第一条卖点\n\n## 画面区块\n### hero\n主画面文案\n\n## 免责声明\n具体责任以保险合同为准。",
  article_outline: "# 文章标题\n\n## 第一部分\n- 写作要点",
  article_generate: "# 文章标题\n\n## 正文\n在这里填写正文，段落之间保留空行。\n\n## 免责声明\n具体责任以保险合同为准。",
  training_outline: "# 课程标题\n\n## 第一章节\n### 学习目标\n填写本章学习目标。",
  training_script: "# 培训讲稿\n\n## 第一章节\n### 讲解正文\n填写本章讲解正文。\n\n### 知识卡\n填写本章知识卡。\n\n## 课程测试\n### 问题一\n**题目：** 填写第一道题\n- 正确选项\n- 错误选项\n**答案：** 正确选项\n**解析：** 填写解析\n\n### 问题二\n**题目：** 填写第二道题\n- 正确选项\n- 错误选项\n**答案：** 正确选项\n**解析：** 填写解析\n\n### 问题三\n**题目：** 填写第三道题\n- 正确选项\n- 错误选项\n**答案：** 正确选项\n**解析：** 填写解析",
};

function value(value: unknown) {
  return typeof value === "string" ? value : "";
}

export function renderContentMarkdown(task: string, output: Record<string, any> | undefined): string {
  if (!output || !Object.keys(output).length) return markdownTemplates[task] ?? "";
  if (task === "image_copy") {
    const sellingPoints = Array.isArray(output.sellingPoints) ? output.sellingPoints.map((item: unknown) => `- ${value(item)}`).join("\n") : "";
    const blocks = Array.isArray(output.blocks) ? output.blocks.map((block: any) => `### ${value(block?.type) || "copy"}\n${value(block?.text)}`).join("\n\n") : "";
    return `# ${value(output.title)}\n\n## 核心卖点\n${sellingPoints}\n\n## 画面区块\n${blocks}\n\n## 免责声明\n${value(output.disclaimer)}`.trim();
  }
  if (task === "article_outline") {
    const sections = Array.isArray(output.sections) ? output.sections.map((section: any) => {
      const points = Array.isArray(section?.points) ? section.points.map((point: unknown) => `- ${value(point)}`).join("\n") : "";
      return `## ${value(section?.heading)}\n${points}`;
    }).join("\n\n") : "";
    return `# ${value(output.title)}\n\n${sections}`.trim();
  }
  if (task === "article_generate") {
    const paragraphs = Array.isArray(output.paragraphs) ? output.paragraphs.map(value).join("\n\n") : "";
    return `# ${value(output.title)}\n\n## 正文\n${paragraphs}\n\n## 免责声明\n${value(output.disclaimer)}`.trim();
  }
  if (task === "training_outline") {
    const chapters = Array.isArray(output.chapters) ? output.chapters.map((chapter: any) =>
      `## ${value(chapter?.title)}\n### 学习目标\n${value(chapter?.objective)}`,
    ).join("\n\n") : "";
    return `# ${value(output.title)}\n\n${chapters}`.trim();
  }
  if (task === "training_script") {
    const chapters = Array.isArray(output.chapters) ? output.chapters.map((chapter: any) =>
      `## ${value(chapter?.title)}\n### 讲解正文\n${value(chapter?.narration)}\n\n### 知识卡\n${value(chapter?.knowledgeCard)}`,
    ).join("\n\n") : "";
    const labels = ["一", "二", "三"];
    const questions = Array.isArray(output.questions) ? output.questions.map((question: any, index: number) => {
      const options = Array.isArray(question?.options) ? question.options.map((option: unknown) => `- ${value(option)}`).join("\n") : "";
      return `### 问题${labels[index] ?? index + 1}\n**题目：** ${value(question?.question)}\n${options}\n**答案：** ${value(question?.answer)}\n**解析：** ${value(question?.explanation)}`;
    }).join("\n\n") : "";
    return `# 培训讲稿\n\n${chapters}\n\n## 课程测试\n${questions}`.trim();
  }
  return "";
}
