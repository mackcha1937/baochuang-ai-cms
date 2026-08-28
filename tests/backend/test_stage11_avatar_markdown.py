from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.markdown_content import parse_markdown_output, render_markdown_output


OUTPUTS = {
    "image_copy": {"title": "守护每一步", "sellingPoints": ["保障清晰", "责任明确"], "blocks": [{"type": "hero", "text": "重疾保障"}], "disclaimer": "具体以保险合同为准。"},
    "article_outline": {"title": "读懂重疾保障", "sections": [{"heading": "保障重点", "points": ["理解等待期", "核对保障期间"]}]},
    "article_generate": {"title": "读懂重疾保障", "paragraphs": ["先从保障需求出发。", "再核对合同中的责任与限制。"], "disclaimer": "具体以保险合同为准。"},
    "training_outline": {"title": "代理人产品培训", "chapters": [{"title": "产品定位", "objective": "理解核心保障责任。"}]},
    "training_script": {"chapters": [{"title": "产品定位", "narration": "本节介绍产品定位。", "knowledgeCard": "核心责任以保险合同为准。"}], "questions": [
        {"question": "第一题", "options": ["选项甲", "选项乙"], "answer": "选项甲", "explanation": "第一题解析"},
        {"question": "第二题", "options": ["选项甲", "选项乙"], "answer": "选项乙", "explanation": "第二题解析"},
        {"question": "第三题", "options": ["选项甲", "选项乙"], "answer": "选项甲", "explanation": "第三题解析"},
    ]},
}


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'stage11.db'}",
        frontend_dist=tmp_path / "dist",
        upload_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
    )


@pytest.mark.parametrize("task_type", ["image_copy", "article_outline", "article_generate", "training_outline", "training_script"])
def test_markdown_round_trip(task_type: str) -> None:
    markdown = render_markdown_output(task_type, OUTPUTS[task_type])
    assert markdown.startswith("# ") and "{" not in markdown
    assert parse_markdown_output(task_type, markdown) == OUTPUTS[task_type]


@pytest.mark.parametrize(
    ("task_type", "markdown"),
    [
        ("image_copy", '{"title":"仍是 JSON"}'),
        ("image_copy", "# 标题\n\n## 核心卖点\n- 卖点\n\n## 画面区块\n### hero\n文案"),
        ("article_outline", "# 标题\n\n## 第一节\n没有列表"),
        ("article_outline", "# 标题\n\n## 第一节\n- 要点\n遗漏文本"),
        ("article_generate", "# 标题\n\n## 正文\n正文\n\n## 免责声明\n<script>alert(1)</script>"),
        ("article_generate", "# 标题\n\n## 正文\n正文\n\n## 正文\n重复\n\n## 免责声明\n以合同为准"),
        ("image_copy", "# 标题\n\n## 核心卖点\n- 卖点\n\n## 画面区块\n### 非法 类型\n文案\n\n## 免责声明\n以合同为准"),
        ("image_copy", "# 标题\n\n## 核心卖点\n- 卖点\n\n## 画面区块\n### hero\n文案\n\n## 额外区块\n内容\n\n## 免责声明\n以合同为准"),
        ("training_outline", "# 培训\n\n## 第一章\n没有学习目标标题"),
        ("training_script", "# 培训讲稿\n\n## 第一章\n### 讲解正文\n正文\n### 知识卡\n知识\n\n## 课程测试\n### 问题一\n**题目：** 问题\n- 甲\n- 乙\n**答案：** 丙\n**解析：** 解析"),
        ("training_script", render_markdown_output("training_script", OUTPUTS["training_script"]).replace("### 问题二", "### 问题四")),
        ("unknown", "# 不支持的任务"),
    ],
)
def test_markdown_rejects_json_missing_sections_html_and_wrong_tasks(task_type: str, markdown: str) -> None:
    with pytest.raises(ValueError):
        parse_markdown_output(task_type, markdown)


def test_image_markdown_manual_save_permissions_lock_audit_and_legacy_compatibility(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    markdown = render_markdown_output("image_copy", OUTPUTS["image_copy"])
    with TestClient(create_app(app_settings)) as client:
        created = client.post("/api/v1/contents", json={"type": "image", "title": "Markdown 图片文案", "productId": "darwin-12", "settings": {}}).json()
        invalid = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            json={"taskType": "image_copy", "revision": created["revision"], "markdown": "# 不完整"},
        )
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "MARKDOWN_OUTPUT_INVALID"
        forbidden = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            headers={"X-Demo-User": "viewer-demo"},
            json={"taskType": "image_copy", "revision": created["revision"], "markdown": markdown},
        )
        assert forbidden.status_code == 403
        saved = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            json={"taskType": "image_copy", "revision": created["revision"], "markdown": markdown},
        )
        assert saved.status_code == 200, saved.text
        detail = saved.json()
        assert detail["payload"]["copy"] == OUTPUTS["image_copy"]
        confirmed = client.post(f"/api/v1/contents/{created['id']}/confirm", json={"node": "copy", "revision": detail["revision"]})
        assert confirmed.status_code == 200
        locked = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            json={"taskType": "image_copy", "revision": confirmed.json()["revision"], "markdown": markdown},
        )
        assert locked.status_code == 409 and locked.json()["error"]["code"] == "CONTENT_NODE_LOCKED"

        legacy = client.post("/api/v1/contents", json={"type": "image", "title": "旧结构兼容", "productId": "darwin-12", "settings": {}}).json()
        legacy_saved = client.put(
            f"/api/v1/contents/{legacy['id']}/manual-output",
            json={"taskType": "image_copy", "revision": legacy["revision"], "output": OUTPUTS["image_copy"]},
        )
        assert legacy_saved.status_code == 200 and legacy_saved.json()["payload"]["copy"]["title"] == "守护每一步"
        audit = client.get("/api/v1/audit-logs", params={"action": "content.manual", "objectId": created["id"]}).json()
        assert audit["total"] == 1 and "Markdown" in audit["items"][0]["detail"]

    with TestClient(create_app(app_settings)) as restarted:
        restored = restarted.get(f"/api/v1/contents/{created['id']}").json()
        assert restored["payload"]["copyStatus"] == "confirmed"
        assert restored["payload"]["copy"] == OUTPUTS["image_copy"]


def test_article_markdown_outline_body_and_restart(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings)) as client:
        created = client.post(
            "/api/v1/contents",
            json={"type": "article", "title": "Markdown 文章", "productId": "darwin-12", "settings": {"channels": ["wechat"]}},
        ).json()
        branch = created["branches"][0]
        outline = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            json={"taskType": "article_outline", "branchId": branch["id"], "revision": created["revision"], "markdown": render_markdown_output("article_outline", OUTPUTS["article_outline"])},
        )
        assert outline.status_code == 200, outline.text
        confirmed = client.post(
            f"/api/v1/contents/{created['id']}/confirm",
            json={"node": "outline", "branchId": branch["id"], "revision": outline.json()["revision"]},
        ).json()
        body = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            json={"taskType": "article_generate", "branchId": branch["id"], "revision": confirmed["revision"], "markdown": render_markdown_output("article_generate", OUTPUTS["article_generate"])},
        )
        assert body.status_code == 200, body.text
        result_branch = body.json()["branches"][0]
        assert result_branch["outline"] == OUTPUTS["article_outline"]
        assert result_branch["body"] == OUTPUTS["article_generate"]

    with TestClient(create_app(app_settings)) as restarted:
        restored = restarted.get(f"/api/v1/contents/{created['id']}").json()["branches"][0]
        assert restored["outlineStatus"] == "confirmed" and restored["bodyStatus"] == "draft"
        assert render_markdown_output("article_generate", restored["body"]).startswith("# 读懂重疾保障")


def test_training_markdown_outline_script_player_data_and_restart(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings)) as client:
        created = client.post(
            "/api/v1/contents",
            json={"type": "training", "title": "Markdown 培训", "productId": "darwin-12", "settings": {"audience": "保险代理人"}},
        ).json()
        outline = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            json={"taskType": "training_outline", "revision": created["revision"], "markdown": render_markdown_output("training_outline", OUTPUTS["training_outline"])},
        )
        assert outline.status_code == 200, outline.text
        confirmed = client.post(
            f"/api/v1/contents/{created['id']}/confirm",
            json={"node": "outline", "revision": outline.json()["revision"]},
        ).json()
        script = client.put(
            f"/api/v1/contents/{created['id']}/manual-output",
            json={"taskType": "training_script", "revision": confirmed["revision"], "markdown": render_markdown_output("training_script", OUTPUTS["training_script"])},
        )
        assert script.status_code == 200, script.text
        assert script.json()["payload"]["script"] == OUTPUTS["training_script"]

    with TestClient(create_app(app_settings)) as restarted:
        restored = restarted.get(f"/api/v1/contents/{created['id']}").json()
        assert restored["payload"]["outline"] == OUTPUTS["training_outline"]
        assert restored["payload"]["script"]["questions"][2]["answer"] == "选项甲"
        assert render_markdown_output("training_script", restored["payload"]["script"]).startswith("# 培训讲稿")


def test_generated_presenter_asset_is_project_bound() -> None:
    asset = Path(__file__).resolve().parents[2] / "public" / "training-presenter-v2.png"
    assert asset.is_file() and asset.stat().st_size > 100_000
    assert asset.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
