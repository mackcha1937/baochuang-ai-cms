#!/usr/bin/env python3
"""Local development server for the Baochuang AI demo.

The server intentionally uses Python's standard HTTP stack so the demo can run
without installing a web framework. Document parsing uses the libraries bundled
with the Codex workspace runtime.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
STATE_PATH = DATA_DIR / "state.json"
ENV_PATH = ROOT / ".env"
MAX_UPLOAD_BYTES = 30 * 1024 * 1024


def load_env_file() -> None:
    if not ENV_PATH.exists():
        return
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_state() -> dict[str, Any]:
    return {
        "brand": {
            "companyName": "明远保险服务",
            "productName": "保创AI",
            "primaryColor": "#6D5EF7",
            "secondaryColor": "#1DC8A0",
            "tone": "专业、亲切、克制，不制造焦虑",
            "slogan": "一份产品资料，生成全套营销内容",
            "disclaimer": "本文仅供保险知识交流，具体保障责任、责任免除及投保规则以保险合同条款为准。",
        },
        "products": [
            {
                "id": "darwin-12",
                "name": "复星联合达尔文12号重大疾病保险（互联网）",
                "insurer": "复星联合健康保险",
                "category": "重大疾病保险",
                "version": "V1",
                "status": "已确认",
                "updatedAt": now_iso(),
                "sourceType": "预置产品链接",
                "summary": "重疾赔付后轻中症责任仍可继续赔付，无分组、无间隔期；意外导致重疾可额外赔付；支持多项可选责任组合。",
                "features": [
                    "重疾赔付后轻中症仍可继续赔付",
                    "中症60%基本保额，最多3次",
                    "轻症30%基本保额，最多4次",
                    "可选重疾多次给付与恶性肿瘤治疗津贴",
                    "保障期间可选终身或保至70岁",
                ],
                "facts": {
                    "投保年龄": "28天（含）至55周岁（含）",
                    "保障期间": "终身、保至70岁",
                    "承保职业": "1-4类",
                    "等待期": "180天",
                    "缴费期间": "趸交、5年、10年、20年、30年、35年",
                    "犹豫期": "15日",
                    "销售区域": "中国大陆",
                },
                "documents": [
                    {"name": "产品详情页资料", "type": "网页", "status": "已解析"},
                    {"name": "保险条款", "type": "PDF", "status": "可获取"},
                    {"name": "产品说明书", "type": "PDF", "status": "可获取"},
                    {"name": "费率表", "type": "Excel", "status": "可获取"},
                    {"name": "投保规则", "type": "PDF", "status": "可获取"},
                ],
            }
        ],
        "contents": [],
        "reviews": [],
        "assets": [],
        "audit": [
            {
                "id": "audit-seed",
                "time": now_iso(),
                "actor": "超级演示用户",
                "action": "创建预置产品",
                "target": "复星联合达尔文12号重大疾病保险（互联网）",
            }
        ],
        "settings": {
            "demoMode": True,
            "permissions": ["enterprise_admin", "creator", "product_reviewer", "compliance_reviewer"],
        },
    }


def ensure_data() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    if not STATE_PATH.exists():
        STATE_PATH.write_text(json.dumps(default_state(), ensure_ascii=False, indent=2), encoding="utf-8")


def read_state() -> dict[str, Any]:
    ensure_data()
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def write_state(state: dict[str, Any]) -> None:
    ensure_data()
    temp = STATE_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(STATE_PATH)


def clean_filename(value: str) -> str:
    name = Path(unquote(value)).name
    name = re.sub(r"[^\w\-. ()（）\u4e00-\u9fff]", "_", name)
    return name[:180] or f"upload-{int(time.time())}"


def parse_pdf(path: Path) -> tuple[str, dict[str, Any]]:
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            pages.append(f"\n--- 第{index}页 ---\n{text}")
    return "".join(pages).strip(), {"pages": len(pages)}


def parse_docx(path: Path) -> tuple[str, dict[str, Any]]:
    from docx import Document

    doc = Document(path)
    parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
    table_count = 0
    for table in doc.tables:
        table_count += 1
        for row in table.rows:
            parts.append(" | ".join(cell.text.strip() for cell in row.cells))
    return "\n".join(parts), {"paragraphs": len(doc.paragraphs), "tables": table_count}


def parse_xlsx(path: Path) -> tuple[str, dict[str, Any]]:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    parts: list[str] = []
    row_count = 0
    for sheet in workbook.worksheets:
        parts.append(f"\n--- 工作表：{sheet.title} ---")
        for row in sheet.iter_rows(values_only=True):
            values = ["" if value is None else str(value) for value in row]
            if any(value.strip() for value in values):
                parts.append(" | ".join(values))
                row_count += 1
            if row_count >= 5000:
                parts.append("[为控制演示性能，后续行已省略]")
                break
    return "\n".join(parts).strip(), {"sheets": len(workbook.sheetnames), "rows": row_count}


def parse_uploaded_file(path: Path) -> tuple[str, dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path)
    if suffix == ".docx":
        return parse_docx(path)
    if suffix == ".xlsx":
        return parse_xlsx(path)
    if suffix in {".txt", ".md", ".csv"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return text, {"characters": len(text)}
    raise ValueError("第一版支持 PDF、DOCX、XLSX、TXT、MD 和 CSV 文件")


def deepseek_chat(messages: list[dict[str, str]], *, json_mode: bool = False) -> dict[str, Any]:
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("尚未配置 DEEPSEEK_API_KEY")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.5,
        "max_tokens": 5000,
        "thinking": {"type": "disabled"},
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    request = urllib.request.Request(
        "https://api.deepseek.com/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"DeepSeek接口返回 {exc.code}: {detail[:500]}") from exc
    content = result["choices"][0]["message"].get("content", "")
    return {"content": content, "model": result.get("model", model), "usage": result.get("usage", {})}


def ai_prompt(task: str, data: dict[str, Any]) -> tuple[list[dict[str, str]], bool]:
    common = (
        "你是保创AI的保险内容助手。只能依据用户提供的资料工作，不得虚构保险责任、金额、年龄、期限或赔付结论。"
        "不确定的内容必须明确标注待人工确认。表达专业、亲切、克制，不制造焦虑，不作保证性承诺。"
    )
    product = json.dumps(data.get("product", {}), ensure_ascii=False)
    audience = data.get("audience", "普通客户")
    purpose = data.get("purpose", "产品介绍")
    notes = data.get("notes", "")
    channel = data.get("channel", "")
    if task == "image_copy":
        user = f"基于以下产品资料，为{audience}制作{purpose}的{data.get('format','产品宣传长图')}。先输出可供客户确认的图片文案和区块结构，不生成图片提示词。产品资料：{product}\n补充要求：{notes}"
    elif task == "article_outline":
        user = f"请为{channel}分别生成一套文章框架，面向{audience}，目的为{purpose}。框架要符合渠道特点，只输出框架，不写完整文章。产品资料：{product}\n补充要求：{notes}"
    elif task == "article":
        user = f"根据已经确认的文章框架写完整{channel}文章。面向{audience}，目的为{purpose}。必须附免责声明。产品资料：{product}\n确认框架：{data.get('outline','')}\n补充要求：{notes}"
    elif task == "training_outline":
        user = f"为{audience}设计一门{data.get('duration','3分钟')}保险产品培训，先给课程框架，不写完整讲稿。产品资料：{product}\n补充要求：{notes}"
    elif task == "training_script":
        user = f"根据确认的课程框架生成分章节讲稿，并给出3道单选测试题及答案解析。产品资料：{product}\n课程框架：{data.get('outline','')}"
    elif task == "risk_review":
        user = f"检查以下保险宣传内容。列出事实不一致、夸大承诺、遗漏限制和免责声明问题，并逐项给修改建议：\n{data.get('content','')}\n产品资料：{product}"
    elif task == "product_extract":
        common += "你必须输出合法json对象，不要输出Markdown代码块。"
        user = (
            "请把资料整理成json，字段包括name、insurer、category、summary、features数组、facts对象、uncertain数组。"
            "资料不足的字段放入uncertain，不要猜测。资料如下：\n" + data.get("content", "")[:60000]
        )
        return [{"role": "system", "content": common}, {"role": "user", "content": user}], True
    else:
        user = data.get("prompt", "请根据产品资料生成内容。") + "\n产品资料：" + product
    return [{"role": "system", "content": common}, {"role": "user", "content": user}], False


class AppHandler(SimpleHTTPRequestHandler):
    server_version = "BaochuangAI/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stdout.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("请求内容过大")
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.send_json({"ok": True, "time": now_iso()})
            return
        if path == "/api/config":
            self.send_json(
                {
                    "deepseekConfigured": bool(os.environ.get("DEEPSEEK_API_KEY")),
                    "qwenConfigured": bool(os.environ.get("DASHSCOPE_API_KEY")),
                    "deepseekModel": os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash"),
                }
            )
            return
        if path == "/api/state":
            self.send_json(read_state())
            return
        if path.startswith("/api/"):
            self.send_json({"error": "接口不存在"}, 404)
            return
        if path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                state = self.read_json()
                write_state(state)
                self.send_json({"ok": True})
                return
            if path == "/api/upload":
                self.handle_upload()
                return
            if path == "/api/ai":
                data = self.read_json()
                messages, json_mode = ai_prompt(data.get("task", ""), data)
                result = deepseek_chat(messages, json_mode=json_mode)
                self.send_json({"ok": True, **result})
                return
            self.send_json({"error": "接口不存在"}, 404)
        except Exception as exc:
            traceback.print_exc()
            self.send_json({"error": str(exc)}, 400)

    def handle_upload(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("没有收到文件")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("文件不能超过30MB")
        filename = clean_filename(self.headers.get("X-Filename", "upload.bin"))
        target = UPLOAD_DIR / f"{int(time.time())}-{filename}"
        target.write_bytes(self.rfile.read(length))
        text, metadata = parse_uploaded_file(target)
        self.send_json(
            {
                "ok": True,
                "filename": filename,
                "savedAs": target.name,
                "characters": len(text),
                "metadata": metadata,
                "text": text[:120000],
                "truncated": len(text) > 120000,
            }
        )


def main() -> None:
    load_env_file()
    ensure_data()
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"保创AI已启动：http://127.0.0.1:{port}")
    print("按 Control-C 停止。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止……")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
