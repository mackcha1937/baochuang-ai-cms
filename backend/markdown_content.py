from __future__ import annotations

import re
from typing import Any


MARKDOWN_TASKS = {"image_copy", "article_outline", "article_generate", "training_outline", "training_script"}
MAX_MARKDOWN_LENGTH = 100_000
HTML_PATTERN = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")


def _clean(markdown: str) -> list[str]:
    if not isinstance(markdown, str) or not markdown.strip() or len(markdown) > MAX_MARKDOWN_LENGTH:
        raise ValueError("markdown is empty or too long")
    if "\x00" in markdown or HTML_PATTERN.search(markdown):
        raise ValueError("html is not allowed in markdown")
    return [line.rstrip() for line in markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _heading_title(lines: list[str]) -> tuple[str, int]:
    for index, line in enumerate(lines):
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip(), index + 1
        if line.strip():
            break
    raise ValueError("markdown requires a level-one title")


def _sections(lines: list[str], start: int) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    heading = ""
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            if heading:
                result.append((heading, body))
            heading, body = line[3:].strip(), []
            if not heading:
                raise ValueError("markdown section heading is empty")
        elif heading:
            body.append(line)
        elif line.strip():
            raise ValueError("content must be placed under a level-two heading")
    if heading:
        result.append((heading, body))
    if not result:
        raise ValueError("markdown requires level-two sections")
    return result


def _plain(lines: list[str]) -> str:
    return "\n".join(line.strip() for line in lines if line.strip()).strip()


def _bullets(lines: list[str]) -> list[str]:
    content_lines = [line for line in lines if line.strip()]
    if any(not re.match(r"^\s*[-*+]\s+\S", line) for line in content_lines):
        raise ValueError("markdown bullet section contains non-list content")
    values = [re.sub(r"^[-*+]\s+", "", line.strip()).strip() for line in content_lines]
    if not values:
        raise ValueError("markdown section requires bullet items")
    return values


def _subsections(lines: list[str]) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    heading = ""
    body: list[str] = []
    for line in lines:
        if line.startswith("### "):
            if heading:
                result.append((heading, body))
            heading, body = line[4:].strip(), []
            if not heading:
                raise ValueError("markdown subsection heading is empty")
        elif heading:
            body.append(line)
        elif line.strip():
            raise ValueError("content must be placed under a level-three heading")
    if heading:
        result.append((heading, body))
    if not result:
        raise ValueError("markdown requires level-three sections")
    headings = [heading for heading, _ in result]
    if len(headings) != len(set(headings)):
        raise ValueError("markdown contains duplicate subsections")
    return result


def _training_outline(title: str, sections: list[tuple[str, list[str]]]) -> dict[str, Any]:
    headings = [heading for heading, _ in sections]
    if len(headings) != len(set(headings)) or "课程测试" in headings:
        raise ValueError("training outline contains invalid chapters")
    chapters = []
    for heading, body in sections:
        subsections = _subsections(body)
        if [name for name, _ in subsections] != ["学习目标"]:
            raise ValueError("training outline chapter requires an objective")
        objective = _plain(subsections[0][1])
        if not objective:
            raise ValueError("training objective is empty")
        chapters.append({"title": heading, "objective": objective})
    return {"title": title, "chapters": chapters}


def _question(body: list[str]) -> dict[str, Any]:
    question = answer = explanation = ""
    options: list[str] = []
    for raw_line in body:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("**题目：**") and not question:
            question = line.removeprefix("**题目：**").strip()
        elif re.match(r"^[-*+]\s+\S", line):
            options.append(re.sub(r"^[-*+]\s+", "", line).strip())
        elif line.startswith("**答案：**") and not answer:
            answer = line.removeprefix("**答案：**").strip()
        elif line.startswith("**解析：**") and not explanation:
            explanation = line.removeprefix("**解析：**").strip()
        else:
            raise ValueError("training question contains unsupported content")
    if not question or len(options) < 2 or not answer or answer not in options or not explanation:
        raise ValueError("training question is incomplete")
    return {"question": question, "options": options, "answer": answer, "explanation": explanation}


def _training_script(title: str, sections: list[tuple[str, list[str]]]) -> dict[str, Any]:
    if title != "培训讲稿":
        raise ValueError("training script title is invalid")
    headings = [heading for heading, _ in sections]
    if len(headings) != len(set(headings)) or not headings or headings[-1] != "课程测试":
        raise ValueError("training script requires chapters followed by a quiz")
    chapter_sections = sections[:-1]
    if not chapter_sections:
        raise ValueError("training script requires chapters")
    chapters = []
    for heading, body in chapter_sections:
        subsections = _subsections(body)
        if [name for name, _ in subsections] != ["讲解正文", "知识卡"]:
            raise ValueError("training chapter structure is incomplete")
        named = {name: content for name, content in subsections}
        narration, knowledge_card = _plain(named["讲解正文"]), _plain(named["知识卡"])
        if not narration or not knowledge_card:
            raise ValueError("training chapter content is empty")
        chapters.append({"title": heading, "narration": narration, "knowledgeCard": knowledge_card})
    question_sections = _subsections(sections[-1][1])
    if [heading for heading, _ in question_sections] != ["问题一", "问题二", "问题三"]:
        raise ValueError("training script requires exactly three questions")
    return {"chapters": chapters, "questions": [_question(body) for _, body in question_sections]}


def parse_markdown_output(task_type: str, markdown: str) -> dict[str, Any]:
    if task_type not in MARKDOWN_TASKS:
        raise ValueError("task does not accept markdown")
    lines = _clean(markdown)
    title, start = _heading_title(lines)
    sections = _sections(lines, start)

    if task_type == "training_outline":
        return _training_outline(title, sections)
    if task_type == "training_script":
        return _training_script(title, sections)

    if task_type == "article_outline":
        output_sections = []
        for heading, body in sections:
            if heading == "免责声明":
                raise ValueError("outline does not accept disclaimer section")
            output_sections.append({"heading": heading, "points": _bullets(body)})
        return {"title": title, "sections": output_sections}

    headings = [heading for heading, _ in sections]
    if len(headings) != len(set(headings)):
        raise ValueError("markdown contains duplicate sections")
    expected = {"正文", "免责声明"} if task_type == "article_generate" else {"核心卖点", "画面区块", "免责声明"}
    if set(headings) != expected:
        raise ValueError("markdown contains missing or unsupported sections")
    named = {heading: body for heading, body in sections}
    disclaimer = _plain(named.get("免责声明", []))
    if not disclaimer:
        raise ValueError("markdown requires disclaimer")

    if task_type == "article_generate":
        body = named.get("正文")
        if body is None:
            raise ValueError("article markdown requires body section")
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", "\n".join(body).strip()) if part.strip()]
        if not paragraphs:
            raise ValueError("article body is empty")
        return {"title": title, "paragraphs": paragraphs, "disclaimer": disclaimer}

    selling_points = _bullets(named.get("核心卖点", []))
    block_lines = named.get("画面区块")
    if block_lines is None:
        raise ValueError("image markdown requires visual blocks")
    blocks: list[dict[str, str]] = []
    block_type = ""
    block_body: list[str] = []
    for line in block_lines + ["### __END__"]:
        if line.startswith("### "):
            if block_type:
                text = _plain(block_body)
                if not text:
                    raise ValueError("visual block text is empty")
                blocks.append({"type": block_type, "text": text})
            block_type, block_body = line[4:].strip(), []
            if block_type != "__END__" and not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,31}", block_type):
                raise ValueError("visual block type is invalid")
        elif block_type:
            block_body.append(line)
        elif line.strip():
            raise ValueError("visual blocks require level-three headings")
    if not blocks:
        raise ValueError("image markdown requires at least one visual block")
    return {"title": title, "sellingPoints": selling_points, "blocks": blocks, "disclaimer": disclaimer}


def render_markdown_output(task_type: str, output: dict[str, Any]) -> str:
    if task_type == "image_copy":
        selling_points = "\n".join(f"- {item}" for item in output.get("sellingPoints", []))
        blocks = "\n\n".join(
            f"### {block.get('type', 'copy')}\n{block.get('text', '')}" for block in output.get("blocks", []) if isinstance(block, dict)
        )
        return (
            f"# {output.get('title', '')}\n\n## 核心卖点\n{selling_points}\n\n"
            f"## 画面区块\n{blocks}\n\n## 免责声明\n{output.get('disclaimer', '')}"
        ).strip()
    if task_type == "article_outline":
        sections = "\n\n".join(
            f"## {section.get('heading', '')}\n" + "\n".join(f"- {point}" for point in section.get("points", []))
            for section in output.get("sections", []) if isinstance(section, dict)
        )
        return f"# {output.get('title', '')}\n\n{sections}".strip()
    if task_type == "article_generate":
        paragraphs = "\n\n".join(str(item) for item in output.get("paragraphs", []))
        return f"# {output.get('title', '')}\n\n## 正文\n{paragraphs}\n\n## 免责声明\n{output.get('disclaimer', '')}".strip()
    if task_type == "training_outline":
        chapters = "\n\n".join(
            f"## {chapter.get('title', '')}\n### 学习目标\n{chapter.get('objective', '')}"
            for chapter in output.get("chapters", []) if isinstance(chapter, dict)
        )
        return f"# {output.get('title', '')}\n\n{chapters}".strip()
    if task_type == "training_script":
        chapters = "\n\n".join(
            f"## {chapter.get('title', '')}\n### 讲解正文\n{chapter.get('narration', '')}\n\n### 知识卡\n{chapter.get('knowledgeCard', '')}"
            for chapter in output.get("chapters", []) if isinstance(chapter, dict)
        )
        labels = ["一", "二", "三"]
        questions = "\n\n".join(
            f"### 问题{labels[index] if index < len(labels) else index + 1}\n"
            f"**题目：** {question.get('question', '')}\n"
            + "\n".join(f"- {option}" for option in question.get("options", []))
            + f"\n**答案：** {question.get('answer', '')}\n**解析：** {question.get('explanation', '')}"
            for index, question in enumerate(output.get("questions", [])) if isinstance(question, dict)
        )
        return f"# 培训讲稿\n\n{chapters}\n\n## 课程测试\n{questions}".strip()
    raise ValueError("task does not support markdown")
