from __future__ import annotations

import io
import sqlite3
from pathlib import Path
from urllib.parse import unquote

from docx import Document
from fastapi.testclient import TestClient
from PIL import Image

from backend.config import Settings
from backend.main import create_app
from tests.backend.test_stage03_ai_content import FakeProvider
from tests.backend.test_stage06_review_workflow import (
    article_with_branches,
    ready_image,
    ready_training,
    submit_review,
)


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'stage07.db'}",
        frontend_dist=tmp_path / "dist",
        upload_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
    )


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(settings(tmp_path), FakeProvider()))


def approve(client: TestClient, item: dict, key: str, branch_id: str | None = None) -> dict:
    submitted = submit_review(client, item, f"{key}-submit", branch_id)
    assert submitted.status_code == 201, submitted.text
    product = submitted.json()
    first = client.post(
        f"/api/v1/reviews/{product['id']}/approve",
        json={"reason": "产品信息一致"},
        headers={"Idempotency-Key": f"{key}-product"},
    )
    assert first.status_code == 200, first.text
    compliance = first.json()
    final = client.post(
        f"/api/v1/reviews/{compliance['id']}/approve",
        json={"reason": "合规要素完整"},
        headers={"Idempotency-Key": f"{key}-compliance"},
    )
    assert final.status_code == 200, final.text
    return final.json()


def docx_text(data: bytes) -> str:
    document = Document(io.BytesIO(data))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def test_approved_image_enters_library_preview_download_duplicate_and_restart(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings, FakeProvider())) as client:
        original = ready_image(client)
        approve(client, original, "image")

        response = client.get("/api/v1/assets?type=image&status=approved")
        assert response.status_code == 200, response.text
        listed = response.json()
        assert listed["total"] == 1
        asset = listed["items"][0]
        assert asset["contentId"] == original["id"] and asset["contentVersion"] == 1

        detail = client.get(f"/api/v1/assets/{asset['id']}")
        assert detail.status_code == 200
        snapshot = detail.json()["snapshot"]
        assert snapshot["payload"]["copy"]["title"]
        preview_id = snapshot["payload"]["visual"]["assetId"]
        assert client.get(f"/api/v1/visual-assets/{preview_id}/preview").status_code == 200

        download = client.get(f"/api/v1/assets/{asset['id']}/download")
        assert download.status_code == 200
        assert download.headers["content-type"] == "image/png"
        assert download.headers["x-content-type-options"] == "nosniff"
        assert download.headers["cache-control"] == "private, no-store"
        assert "filename*=UTF-8''" in download.headers["content-disposition"]
        with Image.open(io.BytesIO(download.content)) as rendered:
            assert rendered.format == "PNG" and rendered.size == (640, 960)
            assert len(rendered.getcolors(maxcolors=1_000_000) or []) > 2

        copied = client.post(f"/api/v1/assets/{asset['id']}/duplicate")
        assert copied.status_code == 201, copied.text
        draft = copied.json()
        assert draft["id"] != original["id"] and draft["status"] == "draft"
        assert draft["title"].endswith("（副本）")
        assert draft["payload"]["copyStatus"] == "draft"
        assert draft["payload"]["visual"]["status"] == "needs_relayout"
        assert draft["reviews"] == []
        assert client.get("/api/v1/assets").json()["total"] == 1
        assert client.get("/api/v1/bootstrap").json()["stats"]["approvedAssets"] == 1

    with TestClient(create_app(app_settings, FakeProvider())) as restarted:
        restored = restarted.get("/api/v1/assets").json()
        assert restored["total"] == 1 and restored["items"][0]["id"] == asset["id"]
        assert restarted.get(f"/api/v1/assets/{asset['id']}/download").status_code == 200
        assert restarted.get(f"/api/v1/contents/{draft['id']}").json()["status"] == "draft"


def test_article_training_docx_filters_and_independent_drafts(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        article, branches = article_with_branches(client)
        approve(client, article, "article", branches["wechat"]["id"])
        training = ready_training(client)
        approve(client, training, "training")

        article_list = client.get("/api/v1/assets?type=article&channel=wechat").json()
        assert article_list["total"] == 1
        article_asset = article_list["items"][0]
        article_file = client.get(f"/api/v1/assets/{article_asset['id']}/download")
        assert article_file.status_code == 200
        article_text = docx_text(article_file.content)
        assert "渠道：公众号" in article_text and "免责声明" in article_text and "保险合同" in article_text
        assert unquote(article_file.headers["content-disposition"]).endswith("_V1.docx")

        article_copy = client.post(f"/api/v1/assets/{article_asset['id']}/duplicate")
        assert article_copy.status_code == 201
        branch = article_copy.json()["branches"][0]
        assert branch["outlineStatus"] == "confirmed"
        assert branch["bodyStatus"] == "draft" and branch["reviewStatus"] == "draft"

        training_list = client.get("/api/v1/assets?type=training").json()
        assert training_list["total"] == 1
        training_asset = training_list["items"][0]
        training_file = client.get(f"/api/v1/assets/{training_asset['id']}/download")
        training_text = docx_text(training_file.content)
        assert "知识卡" in training_text and "课程测试" in training_text
        assert "正确答案" in training_text and "解析" in training_text
        training_copy = client.post(f"/api/v1/assets/{training_asset['id']}/duplicate").json()
        assert training_copy["status"] == "draft" and training_copy["payload"]["scriptStatus"] == "draft"

        all_items = client.get("/api/v1/assets").json()
        assert all_items["total"] == 2
        assert {item["type"] for item in all_items["items"]} == {"article", "training"}


def test_asset_negative_permissions_validation_safe_filename_missing_file_and_audit(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings, FakeProvider())) as client:
        item = ready_image(client)
        approve(client, item, "negative")
        asset = client.get("/api/v1/assets").json()["items"][0]

        for method, path in (
            ("get", "/api/v1/assets"),
            ("get", f"/api/v1/assets/{asset['id']}"),
            ("get", f"/api/v1/assets/{asset['id']}/download"),
            ("post", f"/api/v1/assets/{asset['id']}/duplicate"),
        ):
            response = getattr(client, method)(path, headers={"X-Demo-User": "viewer-demo"})
            assert response.status_code == 403

        assert client.get("/api/v1/assets?type=video").json()["error"]["code"] == "ASSET_TYPE_INVALID"
        assert client.get("/api/v1/assets?status=invalid").json()["error"]["code"] == "ASSET_STATUS_INVALID"
        assert client.get("/api/v1/assets?channel=email").json()["error"]["code"] == "ASSET_CHANNEL_INVALID"
        assert client.get("/api/v1/assets/not-found").status_code == 404
        assert client.get("/api/v1/assets/not-found/download").status_code == 404
        assert client.post("/api/v1/assets/not-found/duplicate").status_code == 404

        with sqlite3.connect(app_settings.database_path) as connection:
            connection.execute("UPDATE assets SET title = ? WHERE id = ?", ("危险\r\n标题/../../evil", asset["id"]))
        safe = client.get(f"/api/v1/assets/{asset['id']}/download")
        disposition = unquote(safe.headers["content-disposition"])
        assert "\r" not in disposition and "\n" not in disposition and ".." not in disposition and "/" not in disposition

        detail = client.get(f"/api/v1/assets/{asset['id']}").json()
        visual_id = detail["snapshot"]["payload"]["visual"]["assetId"]
        database = client.app.state.database
        with database.connect() as connection:
            storage_key = connection.execute("SELECT storage_key FROM visual_assets WHERE id = ?", (visual_id,)).fetchone()[0]
        (app_settings.generated_dir / storage_key).unlink()
        missing = client.get(f"/api/v1/assets/{asset['id']}/download")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "ASSET_FILE_MISSING"

        audit = client.get("/api/v1/audit-logs").json()["items"]
        actions = {record["action"] for record in audit}
        assert {"asset.create", "asset.download", "permission.denied"}.issubset(actions)
