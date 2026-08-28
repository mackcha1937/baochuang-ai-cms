const markdownTasks = new Set(["image_copy", "article_outline", "article_generate"]);

export function isMarkdownTask(task: string) {
  return markdownTasks.has(task);
}

export const markdownTemplates: Record<string, string> = {
  image_copy: "# 图片标题\n\n## 核心卖点\n- 第一条卖点\n\n## 画面区块\n### hero\n主画面文案\n\n## 免责声明\n具体责任以保险合同为准。",
  article_outline: "# 文章标题\n\n## 第一部分\n- 写作要点",
  article_generate: "# 文章标题\n\n## 正文\n在这里填写正文，段落之间保留空行。\n\n## 免责声明\n具体责任以保险合同为准。",
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
  return "";
}
