from __future__ import annotations

import json
import os
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Protocol
from uuid import uuid4

import httpx

from backend.database import Database, now_iso
from backend.image_generation import ImageProvider, VisualCancelled, VisualProviderError, VisualResult
from backend.markdown_content import MARKDOWN_TASKS, parse_markdown_output


TASK_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "image_copy": ("title", "sellingPoints", "blocks", "disclaimer"),
    "article_outline": ("title", "sections"),
    "article_generate": ("title", "paragraphs", "disclaimer"),
    "training_outline": ("title", "chapters"),
    "training_script": ("chapters", "questions"),
}

CONTENT_TASKS: dict[str, set[str]] = {
    "image": {"image_copy", "image_visual"},
    "article": {"article_outline", "article_generate"},
    "training": {"training_outline", "training_script"},
}

SYSTEM_PROMPTS = {
    "image_copy": """你是保险营销文案助手。只依据输入事实，不得编造数字。只返回 Markdown，不要 JSON、代码围栏或 HTML。必须严格使用：
# 标题
## 核心卖点
- 至少一条卖点
## 画面区块
### hero
非空区块文案
## 免责声明
非空免责声明
核心卖点至少一项；画面区块至少一个三级标题，每个区块标题是简短英文类型。""",
    "article_outline": """你是保险内容策划。按指定渠道策划，只依据输入事实。只返回 Markdown，不要 JSON、代码围栏或 HTML。必须严格使用：
# 文章标题
## 第一节标题
- 至少一条写作要点
## 第二节标题
- 至少一条写作要点
至少一个二级标题，每节至少一条列表要点。""",
    "article_generate": """你是保险文章编辑。严格按已确认框架写作，不得编造产品事实。只返回 Markdown，不要 JSON、代码围栏或 HTML。必须严格使用：
# 文章标题
## 正文
正文段落，段落之间保留空行。
## 免责声明
非空免责声明
正文至少一个非空段落。""",
    "training_outline": """你是保险培训课程设计师。只返回一个 JSON 对象，不要 Markdown。必须严格使用以下结构和字段类型：
{"title":"非空字符串","chapters":[{"title":"非空字符串","objective":"非空字符串"}]}
chapters 至少有一项。""",
    "training_script": """你是保险培训讲稿编辑。严格按确认框架写作，只返回一个 JSON 对象，不要 Markdown。必须严格使用以下结构和字段类型：
{"chapters":[{"title":"非空字符串","narration":"非空字符串","knowledgeCard":"非空字符串"}],"questions":[{"question":"非空字符串","options":["选项A","选项B"],"answer":"选项A","explanation":"非空字符串"},{"question":"非空字符串","options":["选项A","选项B"],"answer":"选项B","explanation":"非空字符串"},{"question":"非空字符串","options":["选项A","选项B"],"answer":"选项A","explanation":"非空字符串"}]}
chapters 至少一项；questions 必须恰好 3 项；每题 options 至少 2 项；所有值均为非空字符串。测试题的错误选项优先使用非数字描述，绝不能为了干扰项编造白名单外的数字。""",
}


class ProviderError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


class GroundingError(ValueError):
    def __init__(self, unexpected_numbers: set[str]):
        self.unexpected_numbers = unexpected_numbers
        super().__init__("ungrounded numeric claims: " + ", ".join(sorted(unexpected_numbers)))


class AIProvider(Protocol):
    name: str
    model: str

    def generate(self, task_type: str, snapshot: dict[str, Any]) -> dict[str, Any]: ...


class DeepSeekProvider:
    name = "deepseek"

    def __init__(self, *, api_key: Optional[str] = None, model: Optional[str] = None, endpoint: Optional[str] = None):
        self.api_key = api_key if api_key is not None else os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.model = model or os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
        self.endpoint = endpoint or os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")

    def generate(self, task_type: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise ProviderError("CONFIG_AI_KEY_MISSING", "DeepSeek API Key 未配置，已保留全部输入")
        if task_type not in SYSTEM_PROMPTS:
            raise ProviderError("AI_TASK_UNSUPPORTED", "不支持的 AI 任务")
        numeric_whitelist = sorted(grounding_numbers(snapshot))
        prompt_input = {"source": snapshot, "numericWhitelist": numeric_whitelist}
        markdown_task = task_type in MARKDOWN_TASKS
        output_instruction = "必须输出符合模板的 Markdown。" if markdown_task else "必须输出合法 JSON。"
        request_body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPTS[task_type] + " " + output_instruction + "任何阿拉伯数字或百分比都必须逐字匹配 numericWhitelist；枚举顺序使用中文，不得新增白名单外数字。百分比、年龄、天数等单位含义不得互换，例如 60% 不能写成60岁。"},
                {"role": "user", "content": json.dumps(prompt_input, ensure_ascii=False)},
            ],
            "thinking": {"type": "disabled"},
            "max_tokens": 4096,
            "temperature": 0.2,
            "stream": False,
        }
        if not markdown_task:
            request_body["response_format"] = {"type": "json_object"}
        last_grounding_error: Optional[GroundingError] = None
        result: Any = None
        for attempt in range(3):
            try:
                response = httpx.post(
                    self.endpoint,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=request_body,
                    timeout=45,
                )
            except httpx.TimeoutException as exc:
                raise ProviderError("AI_TIMEOUT", "AI 服务响应超时，可稍后重试") from exc
            except httpx.HTTPError as exc:
                raise ProviderError("AI_NETWORK_ERROR", "暂时无法连接 AI 服务，可稍后重试") from exc
            if response.status_code == 429:
                raise ProviderError("AI_RATE_LIMITED", "AI 服务限流或额度不足，请稍后重试")
            if response.status_code in {401, 403}:
                raise ProviderError("AI_AUTH_FAILED", "DeepSeek 配置无效，请检查服务端 Key")
            if response.status_code >= 400:
                raise ProviderError("AI_PROVIDER_ERROR", f"AI 服务暂时不可用（{response.status_code}）")
            content = ""
            try:
                content = response.json()["choices"][0]["message"]["content"]
                result = parse_markdown_output(task_type, content) if markdown_task else (json.loads(content) if content else None)
                validate_output(task_type, result)
                validate_grounding(snapshot, result)
                return result
            except GroundingError as exc:
                last_grounding_error = exc
                if attempt < 2:
                    request_body["messages"].extend([
                        {"role": "assistant", "content": content or ""},
                        {
                            "role": "user",
                            "content": ("上一份 Markdown" if markdown_task else "上一份 JSON") + " 包含白名单外数字或百分比："
                            + "、".join(sorted(exc.unexpected_numbers))
                            + ("。请保持 Markdown 标题结构逐项修订，删除无来源事实，不得新增其他数字，只返回完整修正 Markdown。" if markdown_task else "。请在上一份 JSON 基础上逐项修订：删除无来源事实，数字型错误选项改为不含数字的描述型选项；不得改变字段结构，不得新增其他数字。只返回完整修正 JSON。"),
                        },
                    ])
                    continue
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError):
                if attempt < 2:
                    request_body["messages"].extend([
                        {"role": "assistant", "content": content or ""},
                        {"role": "user", "content": "上一份输出为空、Markdown 标题结构未通过校验或包含不允许的 HTML。请严格使用系统消息规定的 Markdown 模板，只返回完整修正 Markdown。" if markdown_task else "上一份输出为空、不是合法 JSON 或字段结构未通过校验。请逐字使用系统消息规定的字段名和字段类型，只返回完整修正 JSON。"},
                    ])
                    continue
        if last_grounding_error is not None:
            if task_type == "training_script" and isinstance(result, dict):
                repaired = repair_training_script_grounding(snapshot, result)
                validate_output(task_type, repaired)
                validate_grounding(snapshot, repaired)
                return repaired
            detail = "、".join(sorted(last_grounding_error.unexpected_numbers))[:160]
            raise ProviderError("AI_OUTPUT_UNGROUNDED", f"AI 返回包含资料中未确认的数字（{detail}），已保留输入，可重试或人工核对")
        raise ProviderError("AI_OUTPUT_INVALID", "AI 返回内容格式无效，已保留输入，可重试或人工编辑")


def validate_output(task_type: str, result: Any) -> None:
    if not isinstance(result, dict):
        raise ValueError("output must be object")
    required = TASK_REQUIRED_KEYS.get(task_type)
    if not required or any(key not in result for key in required):
        raise ValueError("missing required output fields")

    def text(value: Any) -> bool:
        return isinstance(value, str) and bool(value.strip()) and len(value) <= 10_000

    def texts(value: Any, *, minimum: int = 1, maximum: int = 100) -> bool:
        return isinstance(value, list) and minimum <= len(value) <= maximum and all(text(item) for item in value)

    if task_type == "image_copy":
        blocks = result["blocks"]
        valid = text(result["title"]) and texts(result["sellingPoints"]) and text(result["disclaimer"])
        valid = valid and isinstance(blocks, list) and 1 <= len(blocks) <= 100 and all(
            isinstance(block, dict) and text(block.get("type")) and text(block.get("text")) for block in blocks
        )
    elif task_type == "article_outline":
        sections = result["sections"]
        valid = text(result["title"]) and isinstance(sections, list) and 1 <= len(sections) <= 100 and all(
            isinstance(section, dict) and text(section.get("heading")) and texts(section.get("points")) for section in sections
        )
    elif task_type == "article_generate":
        valid = text(result["title"]) and texts(result["paragraphs"]) and text(result["disclaimer"])
    elif task_type == "training_outline":
        chapters = result["chapters"]
        valid = text(result["title"]) and isinstance(chapters, list) and 1 <= len(chapters) <= 100 and all(
            isinstance(chapter, dict) and text(chapter.get("title")) and text(chapter.get("objective")) for chapter in chapters
        )
    else:
        chapters, questions = result["chapters"], result["questions"]
        valid = isinstance(chapters, list) and 1 <= len(chapters) <= 100 and all(
            isinstance(chapter, dict)
            and text(chapter.get("title"))
            and text(chapter.get("narration"))
            and text(chapter.get("knowledgeCard"))
            for chapter in chapters
        )
        valid = valid and isinstance(questions, list) and len(questions) == 3 and all(
            isinstance(question, dict)
            and text(question.get("question"))
            and texts(question.get("options"), minimum=2, maximum=10)
            and text(question.get("answer"))
            and text(question.get("explanation"))
            for question in questions
        )
    if not valid:
        raise ValueError("invalid output field types")


def extract_numbers(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(re.findall(r"\d+(?:\.\d+)?%?", value))
    if isinstance(value, dict):
        found: set[str] = set()
        for child in value.values():
            found.update(extract_numbers(child))
        return found
    if isinstance(value, list):
        found = set()
        for child in value:
            found.update(extract_numbers(child))
        return found
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return {str(value)}
    return set()


def grounding_numbers(snapshot: dict[str, Any]) -> set[str]:
    grounded_source = {
        key: snapshot.get(key)
        for key in ("product", "manualSource", "settings", "instruction", "confirmedOutline", "currentCopy")
        if snapshot.get(key) is not None
    }
    return extract_numbers(grounded_source)


def validate_grounding(snapshot: dict[str, Any], result: dict[str, Any]) -> None:
    unexpected = extract_numbers(result) - grounding_numbers(snapshot)
    if unexpected:
        raise GroundingError(unexpected)


def repair_training_script_grounding(snapshot: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    product = snapshot.get("product") if isinstance(snapshot.get("product"), dict) else {}
    product_name_numbers = extract_numbers(product.get("name", ""))

    def strip_numeric_claims(value: Any) -> str:
        if not isinstance(value, str):
            return ""
        segments = re.split(r"(?<=[。！？；;])", value)
        kept = [segment for segment in segments if not (extract_numbers(segment) - product_name_numbers)]
        cleaned = "".join(kept).strip()
        return cleaned or "本节内容请结合已确认产品资料进行讲解，具体责任以保险合同为准。"

    repaired_chapters: list[dict[str, str]] = []
    for chapter in result.get("chapters", []):
        if not isinstance(chapter, dict):
            continue
        repaired_chapters.append({
            "title": strip_numeric_claims(chapter.get("title")) or "课程章节",
            "narration": strip_numeric_claims(chapter.get("narration")),
            "knowledgeCard": strip_numeric_claims(chapter.get("knowledgeCard")),
        })
    if not repaired_chapters:
        repaired_chapters = [{
            "title": "课程要点",
            "narration": "请依据已确认资料介绍产品定位与保障要点，具体责任以保险合同为准。",
            "knowledgeCard": "仅使用已确认资料，未经确认的信息不得作为销售依据。",
        }]

    facts = product.get("facts") if isinstance(product.get("facts"), list) else []
    summary = product.get("summary") if isinstance(product.get("summary"), str) else ""
    manual_source = snapshot.get("manualSource") if isinstance(snapshot.get("manualSource"), str) else ""
    source_statements: list[tuple[str, str]] = []
    if summary.strip():
        source_statements.append(("产品摘要", summary.strip()))
    for fact in facts:
        if isinstance(fact, dict) and isinstance(fact.get("label"), str) and isinstance(fact.get("value"), str) and fact["value"].strip():
            source_statements.append((fact["label"].strip(), fact["value"].strip()))
    if manual_source.strip():
        source_statements.append(("人工素材", manual_source.strip()))
    if not source_statements:
        source_statements.append(("课程依据", "具体内容以已确认资料为准"))
    while len(source_statements) < 3:
        source_statements.append(source_statements[-1])

    repaired_questions = []
    for label, grounded_value in source_statements[:3]:
        repaired_questions.append({
            "question": f"关于{label}，哪一项符合已确认资料？",
            "options": [grounded_value, "与已确认资料不符", "当前资料未提供该说法"],
            "answer": grounded_value,
            "explanation": f"已确认资料中，{label}记载为：{grounded_value}",
        })
    return {"chapters": repaired_chapters, "questions": repaired_questions}


def split_narration_sentences(value: str) -> list[str]:
    """Split narration with the same deterministic units used for saved progress."""
    sentences = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*|\n+", value.strip()) if part.strip()]
    return sentences or ([value.strip()] if value.strip() else [])


@dataclass
class ContentRepository:
    database: Database

    def list_items(self, content_type: str = "") -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM content_items", ()
        if content_type:
            sql, params = sql + " WHERE type = ?", (content_type,)
        sql += " ORDER BY updated_at DESC"
        with self.database.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._summary(row) for row in rows]

    def create_item(self, actor_id: str, content_type: str, title: str, product_id: Optional[str], manual_source: str, settings: dict[str, Any]) -> dict[str, Any]:
        timestamp, content_id = now_iso(), uuid4().hex
        with self.database.connect() as connection:
            if product_id:
                product = connection.execute("SELECT status FROM products WHERE id = ?", (product_id,)).fetchone()
                if product is None or product["status"] != "已确认":
                    raise ValueError("product_not_confirmed")
            connection.execute(
                """INSERT INTO content_items
                   (id, type, title, product_id, manual_source, settings_json, payload_json, status, revision, owner_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, '{}', 'draft', 1, ?, ?, ?)""",
                (content_id, content_type, title, product_id, manual_source, json.dumps(settings, ensure_ascii=False), actor_id, timestamp, timestamp),
            )
            if content_type == "article":
                for channel in settings.get("channels", []):
                    connection.execute("INSERT INTO content_branches (id, content_id, channel, updated_at) VALUES (?, ?, ?, ?)", (uuid4().hex, content_id, channel, timestamp))
            Database._audit(connection, actor_id, "content.create", "content", content_id, f"创建 {content_type} 内容草稿")
        item = self.get_item(content_id)
        assert item is not None
        return item

    def get_item(self, content_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if row is None:
                return None
            branches = [self._branch(item) for item in connection.execute("SELECT * FROM content_branches WHERE content_id = ? ORDER BY channel", (content_id,)).fetchall()]
            jobs = [self._job(item) for item in connection.execute("SELECT * FROM generation_jobs WHERE content_id = ? ORDER BY created_at DESC", (content_id,)).fetchall()]
            training_session = connection.execute("SELECT * FROM training_sessions WHERE content_id = ?", (content_id,)).fetchone() if row["type"] == "training" else None
            reviews = [self._review(item) for item in connection.execute(
                """SELECT task.*, version.version_no,
                          (SELECT reason FROM review_records record
                           WHERE record.task_id = task.id AND record.decision = 'reject'
                           ORDER BY record.created_at DESC, record.id DESC LIMIT 1) AS last_reason
                   FROM review_tasks task
                   JOIN content_versions version ON version.id = task.content_version_id
                   WHERE task.content_id = ? ORDER BY task.created_at DESC""",
                (content_id,),
            ).fetchall()]
        item = self._summary(row)
        payload = json.loads(row["payload_json"])
        item.update({"manualSource": row["manual_source"], "settings": json.loads(row["settings_json"]), "payload": payload, "branches": branches, "jobs": jobs, "reviews": reviews, "ownerId": row["owner_id"]})
        if row["type"] == "training":
            fallback_status = "ready" if payload.get("scriptStatus") == "confirmed" else "locked"
            item["trainingSession"] = self._training_session(training_session, fallback_status)
        return item

    def create_job(self, actor_id: str, content_id: str, task_type: str, branch_id: Optional[str], idempotency_key: str, instruction: str = "") -> tuple[dict[str, Any], bool]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if task_type not in CONTENT_TASKS.get(item["type"], set()):
                raise ValueError("invalid_task")
            existing = connection.execute("SELECT * FROM generation_jobs WHERE content_id = ? AND task_type = ? AND idempotency_key = ?", (content_id, task_type, idempotency_key)).fetchone() if idempotency_key else None
            if existing:
                return self._job(existing), False
            branch = connection.execute("SELECT * FROM content_branches WHERE id = ? AND content_id = ?", (branch_id, content_id)).fetchone() if branch_id else None
            if item["type"] == "article" and branch is None:
                raise ValueError("missing")
            if item["type"] != "article" and branch_id is not None:
                raise ValueError("invalid_task")
            self._ensure_task_unlocked(item, branch, task_type)
            snapshot = self._snapshot(connection, item, task_type, branch, instruction)
            job_id = uuid4().hex
            connection.execute("""INSERT INTO generation_jobs (id, content_id, branch_id, task_type, status, input_json, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)""", (job_id, content_id, branch_id, task_type, json.dumps(snapshot, ensure_ascii=False), idempotency_key, timestamp, timestamp))
            Database._audit(connection, actor_id, "generation.create", "job", job_id, f"创建 {task_type} 任务")
        job = self.get_job(job_id)
        assert job is not None
        return job, True

    def run_job(self, actor_id: str, job_id: str, provider: AIProvider) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None: raise ValueError("missing")
            if row["status"] not in {"queued", "failed", "cancelled"}: raise ValueError("state")
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (row["content_id"],)).fetchone()
            branch = connection.execute("SELECT * FROM content_branches WHERE id = ? AND content_id = ?", (row["branch_id"], row["content_id"])).fetchone() if row["branch_id"] else None
            self._ensure_task_unlocked(item, branch, row["task_type"])
            connection.execute("UPDATE generation_jobs SET status = 'running', public_error = '', error_code = '', attempt = attempt + 1, provider = ?, model = ?, cancel_requested = 0, updated_at = ? WHERE id = ?", (provider.name, provider.model, now_iso(), job_id))
            Database._audit(connection, actor_id, "generation.start", "job", job_id, f"开始 {row['task_type']} 任务")
            snapshot, task_type = json.loads(row["input_json"]), row["task_type"]
        try:
            output = provider.generate(task_type, snapshot)
            validate_output(task_type, output)
            validate_grounding(snapshot, output)
            with self.database.connect() as connection:
                current = connection.execute("SELECT cancel_requested FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
                if current and current["cancel_requested"]:
                    connection.execute("UPDATE generation_jobs SET status = 'cancelled', output_json = '{}', updated_at = ? WHERE id = ?", (now_iso(), job_id))
                else:
                    self._apply_output(connection, row["content_id"], row["branch_id"], task_type, output)
                    connection.execute("UPDATE generation_jobs SET status = 'succeeded', output_json = ?, updated_at = ? WHERE id = ?", (json.dumps(output, ensure_ascii=False), now_iso(), job_id))
                    Database._audit(connection, actor_id, "generation.success", "job", job_id, f"完成 {task_type} 任务")
        except ProviderError as exc:
            with self.database.connect() as connection:
                connection.execute("UPDATE generation_jobs SET status = 'failed', error_code = ?, public_error = ?, updated_at = ? WHERE id = ?", (exc.code, exc.message, now_iso(), job_id))
                Database._audit(connection, actor_id, "generation.failed", "job", job_id, f"{task_type} 失败：{exc.code}")
        except GroundingError:
            with self.database.connect() as connection:
                connection.execute("UPDATE generation_jobs SET status = 'failed', error_code = 'AI_OUTPUT_UNGROUNDED', public_error = 'AI 返回包含资料中未确认的数字，已保留输入，可重试或人工核对', updated_at = ? WHERE id = ?", (now_iso(), job_id))
                Database._audit(connection, actor_id, "generation.failed", "job", job_id, f"{task_type} 失败：AI_OUTPUT_UNGROUNDED")
        except (ValueError, TypeError):
            with self.database.connect() as connection:
                connection.execute("UPDATE generation_jobs SET status = 'failed', error_code = 'AI_OUTPUT_INVALID', public_error = 'AI 返回内容格式无效，已保留输入', updated_at = ? WHERE id = ?", (now_iso(), job_id))
                Database._audit(connection, actor_id, "generation.failed", "job", job_id, f"{task_type} 失败：AI_OUTPUT_INVALID")
        job = self.get_job(job_id)
        assert job is not None
        return job

    def run_visual_job(self, actor_id: str, job_id: str, provider: ImageProvider, visual_root: Path) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("missing")
            if row["task_type"] != "image_visual":
                raise ValueError("invalid_task")
            if row["status"] not in {"queued", "failed", "cancelled"}:
                raise ValueError("state")
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (row["content_id"],)).fetchone()
            self._ensure_task_unlocked(item, None, row["task_type"])
            connection.execute(
                """UPDATE generation_jobs SET status = 'running', public_error = '', error_code = '',
                   attempt = attempt + 1, provider = ?, model = ?, provider_task_id = '',
                   cancel_requested = 0, updated_at = ? WHERE id = ?""",
                (provider.name, provider.model, now_iso(), job_id),
            )
            Database._audit(connection, actor_id, "generation.start", "job", job_id, "开始 image_visual 任务")
            snapshot = json.loads(row["input_json"])

        def save_provider_task_id(provider_task_id: str) -> None:
            safe_id = provider_task_id.strip()[:200]
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE generation_jobs SET provider_task_id = ?, updated_at = ? WHERE id = ?",
                    (safe_id, now_iso(), job_id),
                )

        def is_cancelled() -> bool:
            with self.database.connect() as connection:
                current = connection.execute("SELECT cancel_requested FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
            return current is None or bool(current["cancel_requested"])

        try:
            result = provider.generate(snapshot, on_task_created=save_provider_task_id, is_cancelled=is_cancelled)
            if is_cancelled():
                raise VisualCancelled()
            self._save_visual_result(actor_id, row["content_id"], job_id, snapshot, result, visual_root)
        except VisualCancelled:
            with self.database.connect() as connection:
                connection.execute(
                    "UPDATE generation_jobs SET status = 'cancelled', output_json = '{}', updated_at = ? WHERE id = ?",
                    (now_iso(), job_id),
                )
                Database._audit(connection, actor_id, "generation.cancelled_late", "job", job_id, "取消视觉任务并隔离迟到结果")
        except VisualProviderError as exc:
            with self.database.connect() as connection:
                current = connection.execute("SELECT cancel_requested FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
                if current and current["cancel_requested"]:
                    connection.execute("UPDATE generation_jobs SET status = 'cancelled', updated_at = ? WHERE id = ?", (now_iso(), job_id))
                else:
                    connection.execute(
                        "UPDATE generation_jobs SET status = 'failed', error_code = ?, public_error = ?, updated_at = ? WHERE id = ?",
                        (exc.code, exc.message, now_iso(), job_id),
                    )
                    Database._audit(connection, actor_id, "generation.failed", "job", job_id, f"image_visual 失败：{exc.code}")
        except (OSError, ValueError, TypeError):
            with self.database.connect() as connection:
                connection.execute(
                    """UPDATE generation_jobs SET status = 'failed', error_code = 'VISUAL_STORAGE_FAILED',
                       public_error = '视觉素材校验或保存失败，输入已保留，可重试', updated_at = ? WHERE id = ?""",
                    (now_iso(), job_id),
                )
                Database._audit(connection, actor_id, "generation.failed", "job", job_id, "image_visual 失败：VISUAL_STORAGE_FAILED")
        job = self.get_job(job_id)
        assert job is not None
        return job

    def _save_visual_result(
        self,
        actor_id: str,
        content_id: str,
        job_id: str,
        snapshot: dict[str, Any],
        result: VisualResult,
        visual_root: Path,
    ) -> None:
        asset_id, storage_key = uuid4().hex, f"{uuid4().hex}.png"
        path = self._visual_path(visual_root, storage_key)
        visual_root.mkdir(parents=True, exist_ok=True)
        path.write_bytes(result.data)
        checksum = hashlib.sha256(result.data).hexdigest()
        timestamp = now_iso()
        try:
            with self.database.connect() as connection:
                current = connection.execute("SELECT cancel_requested FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
                if current is None or current["cancel_requested"]:
                    raise VisualCancelled()
                connection.execute(
                    """INSERT INTO visual_assets
                       (id, content_id, job_id, source, storage_key, mime_type, size_bytes, checksum, width, height, created_at)
                       VALUES (?, ?, ?, 'qwen', ?, ?, ?, ?, ?, ?, ?)""",
                    (asset_id, content_id, job_id, storage_key, result.mime_type, len(result.data), checksum, result.width, result.height, timestamp),
                )
                item = connection.execute("SELECT payload_json FROM content_items WHERE id = ?", (content_id,)).fetchone()
                payload = json.loads(item["payload_json"])
                layout = self._default_layout(connection, snapshot, asset_id)
                payload["visual"] = {"assetId": asset_id, "status": "draft", "layout": layout}
                connection.execute(
                    "UPDATE content_items SET payload_json = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), timestamp, content_id),
                )
                output = {
                    "assetId": asset_id,
                    "previewUrl": f"/api/v1/visual-assets/{asset_id}/preview",
                    "width": result.width,
                    "height": result.height,
                    "checksum": checksum,
                }
                connection.execute(
                    "UPDATE generation_jobs SET status = 'succeeded', output_json = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(output, ensure_ascii=False), timestamp, job_id),
                )
                Database._audit(connection, actor_id, "generation.success", "job", job_id, "完成 image_visual 任务并保存视觉素材")
        except Exception:
            if path.exists():
                path.unlink()
            raise

    def save_uploaded_visual(
        self,
        actor_id: str,
        content_id: str,
        revision: int,
        original_name: str,
        data: bytes,
        mime_type: str,
        extension: str,
        width: int,
        height: int,
        visual_root: Path,
    ) -> dict[str, Any]:
        asset_id, storage_key = uuid4().hex, f"{uuid4().hex}{extension}"
        path = self._visual_path(visual_root, storage_key)
        visual_root.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        checksum, timestamp = hashlib.sha256(data).hexdigest(), now_iso()
        try:
            with self.database.connect() as connection:
                item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
                if item is None:
                    raise ValueError("missing")
                if item["type"] != "image":
                    raise ValueError("invalid_task")
                if item["revision"] != revision:
                    raise ValueError("conflict")
                if item["status"] != "draft":
                    raise ValueError("review_locked")
                payload = json.loads(item["payload_json"])
                if payload.get("copyStatus") != "confirmed":
                    raise ValueError("copy_not_confirmed")
                connection.execute(
                    """INSERT INTO visual_assets
                       (id, content_id, source, original_name, storage_key, mime_type, size_bytes, checksum, width, height, created_at)
                       VALUES (?, ?, 'upload', ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (asset_id, content_id, original_name, storage_key, mime_type, len(data), checksum, width, height, timestamp),
                )
                snapshot = self._snapshot(connection, item, "image_visual", None, "人工替换视觉素材")
                layout = self._default_layout(connection, snapshot, asset_id)
                payload["visual"] = {"assetId": asset_id, "status": "draft", "layout": layout}
                connection.execute(
                    "UPDATE content_items SET payload_json = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), timestamp, content_id),
                )
                Database._audit(connection, actor_id, "visual.upload", "content", content_id, f"上传替换图 {original_name}")
        except Exception:
            if path.exists():
                path.unlink()
            raise
        result = self.get_item(content_id)
        assert result is not None
        return result

    def update_visual_layout(
        self,
        actor_id: str,
        content_id: str,
        revision: int,
        template: str,
        palette: str,
        block_order: list[str],
        hidden_block_ids: list[str],
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if item["type"] != "image":
                raise ValueError("invalid_task")
            if item["revision"] != revision:
                raise ValueError("conflict")
            if item["status"] != "draft":
                raise ValueError("review_locked")
            payload = json.loads(item["payload_json"])
            visual = payload.get("visual")
            if not isinstance(visual, dict) or not isinstance(visual.get("layout"), dict):
                raise ValueError("visual_missing")
            layout = visual["layout"]
            blocks = layout.get("blocks") if isinstance(layout.get("blocks"), list) else []
            block_ids = [str(block.get("id")) for block in blocks if isinstance(block, dict)]
            if len(block_ids) != len(set(block_ids)) or len(block_order) != len(set(block_order)) or set(block_order) != set(block_ids):
                raise ValueError("layout_invalid")
            if not set(hidden_block_ids).issubset(set(block_ids)):
                raise ValueError("layout_invalid")
            required = {str(block.get("id")) for block in blocks if isinstance(block, dict) and block.get("required")}
            if required.intersection(hidden_block_ids):
                raise ValueError("required_block")
            layout.update({"template": template, "palette": palette, "blockOrder": block_order, "hiddenBlockIds": hidden_block_ids})
            visual["layout"] = layout
            visual["status"] = "draft"
            timestamp = now_iso()
            connection.execute(
                "UPDATE content_items SET payload_json = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), timestamp, content_id),
            )
            Database._audit(connection, actor_id, "visual.layout.update", "content", content_id, "更新图片模板、配色与区块结构")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def get_visual_asset(self, asset_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM visual_assets WHERE id = ? AND status = 'ready'", (asset_id,)).fetchone()
        return dict(row) if row else None

    @staticmethod
    def _visual_path(visual_root: Path, storage_key: str) -> Path:
        root = visual_root.resolve()
        candidate = (root / storage_key).resolve()
        if not candidate.is_relative_to(root) or candidate.name != storage_key:
            raise ValueError("unsafe visual path")
        return candidate

    @staticmethod
    def _default_layout(connection: Any, snapshot: dict[str, Any], asset_id: str) -> dict[str, Any]:
        copy = snapshot.get("confirmedCopy") if isinstance(snapshot.get("confirmedCopy"), dict) else {}
        settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
        product = snapshot.get("product") if isinstance(snapshot.get("product"), dict) else {}
        brand = connection.execute("SELECT primary_color, secondary_color FROM brands LIMIT 1").fetchone()
        image_type = str(settings.get("imageType", "长图"))
        template = {"营销海报": "square-focus", "公众号头图": "header-split"}.get(image_type, "long-editorial")
        blocks: list[dict[str, Any]] = [
            {"id": "title", "type": "title", "text": str(copy.get("title", "")), "required": True},
            {"id": "product", "type": "product", "text": str(product.get("name", "产品保障方案")), "required": True},
        ]
        selling_points = copy.get("sellingPoints") if isinstance(copy.get("sellingPoints"), list) else []
        for index, value in enumerate(selling_points[:6]):
            blocks.append({"id": f"selling-{index + 1}", "type": "selling", "text": str(value), "required": False})
        copy_blocks = copy.get("blocks") if isinstance(copy.get("blocks"), list) else []
        for index, value in enumerate(copy_blocks[:8]):
            if isinstance(value, dict):
                blocks.append({"id": f"copy-{index + 1}", "type": str(value.get("type", "copy")), "text": str(value.get("text", "")), "required": False})
        blocks.append({"id": "disclaimer", "type": "disclaimer", "text": str(copy.get("disclaimer", "具体责任以保险合同为准。")), "required": True})
        return {
            "assetId": asset_id,
            "imageType": image_type,
            "template": template,
            "palette": "brand",
            "brandColors": {"primary": brand["primary_color"], "secondary": brand["secondary_color"]} if brand else {"primary": "#6d5dfc", "secondary": "#2ac7b8"},
            "blocks": blocks,
            "blockOrder": [block["id"] for block in blocks],
            "hiddenBlockIds": [],
        }

    def cancel_job(self, actor_id: str, job_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT status FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None: return None
            if row["status"] == "succeeded": raise ValueError("state")
            connection.execute("UPDATE generation_jobs SET status = 'cancelled', cancel_requested = 1, updated_at = ? WHERE id = ?", (now_iso(), job_id))
            Database._audit(connection, actor_id, "generation.cancel", "job", job_id, "取消生成任务")
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM generation_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def confirm_node(self, actor_id: str, content_id: str, node: str, branch_id: Optional[str], revision: int) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None: raise ValueError("missing")
            if item["revision"] != revision: raise ValueError("conflict")
            if branch_id:
                branch = connection.execute("SELECT * FROM content_branches WHERE id = ? AND content_id = ?", (branch_id, content_id)).fetchone()
                if branch is None: raise ValueError("missing")
                if branch["review_status"] != "draft": raise ValueError("review_locked")
                if node == "outline" and json.loads(branch["outline_json"]):
                    connection.execute("UPDATE content_branches SET outline_status = 'confirmed', updated_at = ? WHERE id = ?", (timestamp, branch_id))
                elif node == "body" and branch["outline_status"] == "confirmed" and json.loads(branch["body_json"]):
                    connection.execute("UPDATE content_branches SET body_status = 'confirmed', updated_at = ? WHERE id = ?", (timestamp, branch_id))
                else: raise ValueError("incomplete")
            else:
                if item["status"] != "draft": raise ValueError("review_locked")
                payload = json.loads(item["payload_json"])
                key = "copyStatus" if node == "copy" else f"{node}Status"
                value_key = "copy" if node == "copy" else node
                if not payload.get(value_key): raise ValueError("incomplete")
                payload[key] = "confirmed"
                if item["type"] == "training" and node == "outline":
                    payload.setdefault("scriptStatus", "empty")
                connection.execute("UPDATE content_items SET payload_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), content_id))
                if item["type"] == "training" and node == "script":
                    connection.execute(
                        """INSERT INTO training_sessions
                           (content_id, chapter_index, sentence_index, playback_rate, mode, answers_json,
                            last_score, results_json, attempt, status, updated_at)
                           VALUES (?, 0, 0, 1.0, 'voice', '{}', NULL, '[]', 0, 'ready', ?)
                           ON CONFLICT(content_id) DO UPDATE SET chapter_index = 0, sentence_index = 0,
                           playback_rate = 1.0, mode = 'voice', answers_json = '{}', last_score = NULL,
                           results_json = '[]', attempt = 0, status = 'ready', updated_at = excluded.updated_at""",
                        (content_id, timestamp),
                    )
            connection.execute("UPDATE content_items SET revision = revision + 1, updated_at = ? WHERE id = ?", (timestamp, content_id))
            Database._audit(connection, actor_id, f"content.confirm.{node}", "content", content_id, f"确认 {node} 节点")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def unlock_image_copy(self, actor_id: str, content_id: str, revision: int) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if item["type"] != "image":
                raise ValueError("invalid_task")
            if item["revision"] != revision:
                raise ValueError("conflict")
            if item["status"] != "draft":
                raise ValueError("review_locked")
            payload = json.loads(item["payload_json"])
            if payload.get("copyStatus") != "confirmed":
                raise ValueError("state")
            payload["copyStatus"] = "draft"
            if isinstance(payload.get("visual"), dict):
                payload["visual"]["status"] = "needs_relayout"
            connection.execute(
                "UPDATE content_items SET payload_json = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), timestamp, content_id),
            )
            Database._audit(connection, actor_id, "content.unlock.copy", "content", content_id, "解锁图片文案并标记既有视觉需重新排版")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def unlock_training_script(self, actor_id: str, content_id: str, revision: int) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if item["type"] != "training":
                raise ValueError("invalid_task")
            if item["revision"] != revision:
                raise ValueError("conflict")
            if item["status"] != "draft":
                raise ValueError("review_locked")
            payload = json.loads(item["payload_json"])
            if payload.get("scriptStatus") != "confirmed":
                raise ValueError("state")
            payload["scriptStatus"] = "draft"
            connection.execute(
                "UPDATE content_items SET payload_json = ?, revision = revision + 1, updated_at = ? WHERE id = ?",
                (json.dumps(payload, ensure_ascii=False), timestamp, content_id),
            )
            connection.execute(
                """INSERT INTO training_sessions (content_id, status, updated_at)
                   VALUES (?, 'stale', ?)
                   ON CONFLICT(content_id) DO UPDATE SET status = 'stale', updated_at = excluded.updated_at""",
                (content_id, timestamp),
            )
            Database._audit(connection, actor_id, "content.unlock.script", "content", content_id, "解锁培训讲稿并标记播放会话已过期")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def save_training_session(
        self,
        actor_id: str,
        content_id: str,
        revision: int,
        chapter_index: int,
        sentence_index: int,
        playback_rate: float,
        mode: str,
        answers: dict[str, str],
    ) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if item["type"] != "training":
                raise ValueError("invalid_task")
            if item["revision"] != revision:
                raise ValueError("conflict")
            payload = json.loads(item["payload_json"])
            if payload.get("scriptStatus") != "confirmed" or not isinstance(payload.get("script"), dict):
                raise ValueError("script_not_confirmed")
            script = payload["script"]
            chapters = script.get("chapters") if isinstance(script.get("chapters"), list) else []
            questions = script.get("questions") if isinstance(script.get("questions"), list) else []
            if not 0 <= chapter_index < len(chapters):
                raise ValueError("progress_invalid")
            narration = str(chapters[chapter_index].get("narration", "")) if isinstance(chapters[chapter_index], dict) else ""
            sentences = split_narration_sentences(narration)
            if not sentences or not 0 <= sentence_index < len(sentences):
                raise ValueError("progress_invalid")
            if mode not in {"voice", "silent"} or not 0.5 <= playback_rate <= 2.0:
                raise ValueError("progress_invalid")
            if len(answers) > 3:
                raise ValueError("answers_invalid")
            for raw_index, answer in answers.items():
                if not raw_index.isdigit():
                    raise ValueError("answers_invalid")
                index = int(raw_index)
                if not 0 <= index < len(questions) or not isinstance(questions[index], dict):
                    raise ValueError("answers_invalid")
                options = questions[index].get("options")
                if not isinstance(options, list) or answer not in options:
                    raise ValueError("answers_invalid")
            connection.execute(
                """INSERT INTO training_sessions
                   (content_id, chapter_index, sentence_index, playback_rate, mode, answers_json,
                    last_score, results_json, attempt, status, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, '[]', 0, 'ready', ?)
                   ON CONFLICT(content_id) DO UPDATE SET chapter_index = excluded.chapter_index,
                   sentence_index = excluded.sentence_index, playback_rate = excluded.playback_rate,
                   mode = excluded.mode, answers_json = excluded.answers_json,
                   updated_at = excluded.updated_at""",
                (content_id, chapter_index, sentence_index, playback_rate, mode, json.dumps(answers, ensure_ascii=False), timestamp),
            )
            Database._audit(connection, actor_id, "training.progress.save", "content", content_id, f"保存培训进度：章节 {chapter_index + 1}")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def submit_training_quiz(self, actor_id: str, content_id: str, revision: int, answers: list[str]) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if item["type"] != "training":
                raise ValueError("invalid_task")
            if item["revision"] != revision:
                raise ValueError("conflict")
            payload = json.loads(item["payload_json"])
            if payload.get("scriptStatus") != "confirmed" or not isinstance(payload.get("script"), dict):
                raise ValueError("script_not_confirmed")
            questions = payload["script"].get("questions")
            if not isinstance(questions, list) or len(questions) != 3 or len(answers) != 3 or any(not answer for answer in answers):
                raise ValueError("answers_incomplete")
            results: list[dict[str, Any]] = []
            score = 0
            for index, (question, answer) in enumerate(zip(questions, answers)):
                if not isinstance(question, dict) or answer not in question.get("options", []):
                    raise ValueError("answers_invalid")
                correct = answer == question.get("answer")
                score += int(correct)
                results.append({
                    "index": index,
                    "question": question.get("question", ""),
                    "selected": answer,
                    "correctAnswer": question.get("answer", ""),
                    "correct": correct,
                    "explanation": question.get("explanation", ""),
                })
            answer_map = {str(index): answer for index, answer in enumerate(answers)}
            connection.execute(
                """INSERT INTO training_sessions
                   (content_id, answers_json, last_score, results_json, attempt, status, updated_at)
                   VALUES (?, ?, ?, ?, 1, 'completed', ?)
                   ON CONFLICT(content_id) DO UPDATE SET answers_json = excluded.answers_json,
                   last_score = excluded.last_score, results_json = excluded.results_json,
                   attempt = training_sessions.attempt + 1, status = 'completed', updated_at = excluded.updated_at""",
                (content_id, json.dumps(answer_map, ensure_ascii=False), score, json.dumps(results, ensure_ascii=False), timestamp),
            )
            Database._audit(connection, actor_id, "training.quiz.submit", "content", content_id, f"提交培训测试：{score}/3")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def reset_training_quiz(self, actor_id: str, content_id: str, revision: int) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if item["type"] != "training":
                raise ValueError("invalid_task")
            if item["revision"] != revision:
                raise ValueError("conflict")
            payload = json.loads(item["payload_json"])
            if payload.get("scriptStatus") != "confirmed":
                raise ValueError("script_not_confirmed")
            connection.execute(
                """INSERT INTO training_sessions (content_id, status, updated_at)
                   VALUES (?, 'ready', ?)
                   ON CONFLICT(content_id) DO UPDATE SET answers_json = '{}', last_score = NULL,
                   results_json = '[]', status = 'ready', updated_at = excluded.updated_at""",
                (content_id, timestamp),
            )
            Database._audit(connection, actor_id, "training.quiz.reset", "content", content_id, "重新开始培训测试")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def save_manual_output(self, actor_id: str, content_id: str, task_type: str, branch_id: Optional[str], revision: int, output: dict[str, Any]) -> dict[str, Any]:
        validate_output(task_type, output)
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None: raise ValueError("missing")
            if item["revision"] != revision: raise ValueError("conflict")
            if task_type not in CONTENT_TASKS.get(item["type"], set()): raise ValueError("invalid_task")
            if item["type"] == "article" and not branch_id: raise ValueError("missing")
            if item["type"] != "article" and branch_id is not None: raise ValueError("invalid_task")
            branch = connection.execute("SELECT * FROM content_branches WHERE id = ? AND content_id = ?", (branch_id, content_id)).fetchone() if branch_id else None
            if branch_id and branch is None:
                raise ValueError("missing")
            self._ensure_task_unlocked(item, branch, task_type)
            if task_type == "article_generate":
                if not branch or branch["outline_status"] != "confirmed": raise ValueError("outline_not_confirmed")
            if task_type == "training_script":
                payload = json.loads(connection.execute("SELECT payload_json FROM content_items WHERE id = ?", (content_id,)).fetchone()[0])
                if payload.get("outlineStatus") != "confirmed": raise ValueError("outline_not_confirmed")
            self._apply_output(connection, content_id, branch_id, task_type, output)
            content_format = "Markdown" if task_type in MARKDOWN_TASKS else "结构化"
            Database._audit(connection, actor_id, "content.manual", "content", content_id, f"人工保存 {task_type} {content_format}内容")
        result = self.get_item(content_id)
        assert result is not None
        return result

    def _snapshot(self, connection: Any, item: Any, task_type: str, branch: Any, instruction: str) -> dict[str, Any]:
        product = None
        if item["product_id"]:
            product_row = connection.execute("SELECT * FROM products WHERE id = ?", (item["product_id"],)).fetchone()
            facts = [dict(row) for row in connection.execute("SELECT label, value FROM product_facts WHERE product_id = ? AND confirmed = 1 ORDER BY position", (item["product_id"],)).fetchall()]
            features = [row[0] for row in connection.execute("SELECT value FROM product_features WHERE product_id = ? ORDER BY position", (item["product_id"],)).fetchall()]
            product = {"name": product_row["name"], "insurer": product_row["insurer"], "category": product_row["category"], "summary": product_row["summary"], "features": features, "facts": facts}
        brand_row = connection.execute(
            "SELECT company_name, product_name, primary_color, secondary_color, tone, slogan, disclaimer, revision FROM brands LIMIT 1"
        ).fetchone()
        brand = {
            "companyName": brand_row["company_name"], "productName": brand_row["product_name"],
            "primaryColor": brand_row["primary_color"], "secondaryColor": brand_row["secondary_color"],
            "tone": brand_row["tone"], "slogan": brand_row["slogan"], "disclaimer": brand_row["disclaimer"],
            "revision": brand_row["revision"],
        } if brand_row else None
        snapshot: dict[str, Any] = {
            "taskType": task_type, "product": product, "brand": brand,
            "manualSource": item["manual_source"], "settings": json.loads(item["settings_json"]), "instruction": instruction,
        }
        payload = json.loads(item["payload_json"])
        if task_type == "image_copy" and payload.get("copy"): snapshot["currentCopy"] = payload["copy"]
        if task_type == "image_visual":
            if payload.get("copyStatus") != "confirmed" or not isinstance(payload.get("copy"), dict):
                raise ValueError("copy_not_confirmed")
            snapshot["confirmedCopy"] = payload["copy"]
        if branch:
            snapshot["channel"] = branch["channel"]
            if task_type == "article_generate":
                if branch["outline_status"] != "confirmed": raise ValueError("outline_not_confirmed")
                snapshot["confirmedOutline"] = json.loads(branch["outline_json"])
        if task_type == "training_script":
            if payload.get("outlineStatus") != "confirmed": raise ValueError("outline_not_confirmed")
            snapshot["confirmedOutline"] = payload.get("outline")
        return snapshot

    @staticmethod
    def _ensure_task_unlocked(item: Any, branch: Any, task_type: str) -> None:
        if branch is not None:
            if branch["review_status"] != "draft":
                raise ValueError("review_locked")
        elif item["status"] != "draft":
            raise ValueError("review_locked")
        if task_type == "image_copy":
            payload = json.loads(item["payload_json"])
            if payload.get("copyStatus") == "confirmed":
                raise ValueError("node_locked")
        if task_type == "image_visual":
            payload = json.loads(item["payload_json"])
            if payload.get("copyStatus") != "confirmed" or not payload.get("copy"):
                raise ValueError("copy_not_confirmed")
        if task_type == "article_outline" and branch and branch["outline_status"] == "confirmed":
            raise ValueError("node_locked")
        if task_type == "article_generate" and branch and branch["body_status"] == "confirmed":
            raise ValueError("node_locked")
        if task_type in {"training_outline", "training_script"}:
            payload = json.loads(item["payload_json"])
            status_key = "outlineStatus" if task_type == "training_outline" else "scriptStatus"
            if payload.get(status_key) == "confirmed":
                raise ValueError("node_locked")

    @staticmethod
    def _apply_output(connection: Any, content_id: str, branch_id: Optional[str], task_type: str, output: dict[str, Any]) -> None:
        timestamp = now_iso()
        if task_type == "article_outline":
            connection.execute("UPDATE content_branches SET outline_json = ?, outline_status = 'draft', updated_at = ? WHERE id = ?", (json.dumps(output, ensure_ascii=False), timestamp, branch_id))
        elif task_type == "article_generate":
            connection.execute("UPDATE content_branches SET body_json = ?, body_status = 'draft', updated_at = ? WHERE id = ?", (json.dumps(output, ensure_ascii=False), timestamp, branch_id))
        else:
            row = connection.execute("SELECT payload_json FROM content_items WHERE id = ?", (content_id,)).fetchone()
            payload = json.loads(row["payload_json"])
            key = {"image_copy": "copy", "training_outline": "outline", "training_script": "script"}[task_type]
            payload[key], payload[f"{key}Status"] = output, "draft"
            connection.execute("UPDATE content_items SET payload_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), content_id))
        connection.execute("UPDATE content_items SET revision = revision + 1, updated_at = ? WHERE id = ?", (timestamp, content_id))

    @staticmethod
    def _summary(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "type": row["type"], "title": row["title"], "productId": row["product_id"], "status": row["status"], "revision": row["revision"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    @staticmethod
    def _branch(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "channel": row["channel"], "outline": json.loads(row["outline_json"]), "outlineStatus": row["outline_status"], "body": json.loads(row["body_json"]), "bodyStatus": row["body_status"], "reviewStatus": row["review_status"], "updatedAt": row["updated_at"]}

    @staticmethod
    def _review(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "branchId": row["branch_id"], "contentVersion": row["version_no"], "stage": row["stage"], "status": row["status"], "startedAt": row["started_at"], "completedAt": row["completed_at"], "createdAt": row["created_at"], "updatedAt": row["updated_at"], "lastReason": row["last_reason"] or ""}

    @staticmethod
    def _job(row: Any) -> dict[str, Any]:
        return {"id": row["id"], "contentId": row["content_id"], "branchId": row["branch_id"], "taskType": row["task_type"], "status": row["status"], "input": json.loads(row["input_json"]), "output": json.loads(row["output_json"]), "error": row["public_error"], "errorCode": row["error_code"], "attempt": row["attempt"], "provider": row["provider"], "model": row["model"], "createdAt": row["created_at"], "updatedAt": row["updated_at"]}

    @staticmethod
    def _training_session(row: Any, fallback_status: str = "locked") -> dict[str, Any]:
        if row is None:
            return {
                "chapterIndex": 0,
                "sentenceIndex": 0,
                "playbackRate": 1.0,
                "mode": "voice",
                "answers": {},
                "lastScore": None,
                "results": [],
                "attempt": 0,
                "status": fallback_status,
                "updatedAt": "",
            }
        return {
            "chapterIndex": row["chapter_index"],
            "sentenceIndex": row["sentence_index"],
            "playbackRate": row["playback_rate"],
            "mode": row["mode"],
            "answers": json.loads(row["answers_json"]),
            "lastScore": row["last_score"],
            "results": json.loads(row["results_json"]),
            "attempt": row["attempt"],
            "status": row["status"],
            "updatedAt": row["updated_at"],
        }
