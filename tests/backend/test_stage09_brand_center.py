from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.seed import BRAND
from tests.backend.test_stage02_product_library import docx_bytes
from tests.backend.test_stage04_image_generation import png_bytes


def settings(tmp_path: Path, *, max_visual_bytes: int = 15 * 1024 * 1024) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'stage09.db'}",
        frontend_dist=tmp_path / "dist",
        upload_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
        max_visual_bytes=max_visual_bytes,
    )


def brand_payload(brand: dict, **overrides: str) -> dict:
    fields = {key: brand[key] for key in ("companyName", "productName", "primaryColor", "secondaryColor", "tone", "slogan", "disclaimer")}
    return {**fields, **overrides, "revision": brand["revision"]}


def upload(client: TestClient, kind: str, revision: int, name: str, data: bytes, mime: str):
    return client.post(f"/api/v1/brand/assets?kind={kind}&revision={revision}", files={"file": (name, data, mime)})


def test_brand_fields_permissions_conflicts_audit_and_restart(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings)) as client:
        current = client.get("/api/v1/brand").json()
        assert current["revision"] == 1 and current["assets"] == []
        payload = brand_payload(
            current,
            companyName="星河保险服务",
            productName="星河保创",
            primaryColor="#123ABC",
            secondaryColor="#45CDEF",
            tone="专业、温暖、清晰",
            slogan="让保险内容更可信",
            disclaimer="本内容仅供交流，具体责任以保险合同为准。",
        )
        forbidden = client.patch("/api/v1/brand", json=payload, headers={"X-Demo-User": "viewer-demo"})
        assert forbidden.status_code == 403
        updated = client.patch("/api/v1/brand", json=payload)
        assert updated.status_code == 200, updated.text
        brand = updated.json()
        assert brand["companyName"] == "星河保险服务" and brand["revision"] == 2
        assert brand["primaryColor"] == "#123ABC"
        content = client.post("/api/v1/contents", json={"type": "image", "title": "品牌快照验证", "productId": "darwin-12", "settings": {}}).json()
        job = client.post(f"/api/v1/contents/{content['id']}/jobs", json={"taskType": "image_copy"}, headers={"Idempotency-Key": "brand-snapshot"}).json()
        assert job["input"]["brand"] == {
            "companyName": "星河保险服务", "productName": "星河保创", "primaryColor": "#123ABC",
            "secondaryColor": "#45CDEF", "tone": "专业、温暖、清晰", "slogan": "让保险内容更可信",
            "disclaimer": "本内容仅供交流，具体责任以保险合同为准。", "revision": 2,
        }

        stale = client.patch("/api/v1/brand", json=payload)
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
        invalid_color = client.patch("/api/v1/brand", json={**brand_payload(brand), "primaryColor": "red"})
        assert invalid_color.status_code == 422
        blank = client.patch("/api/v1/brand", json={**brand_payload(brand), "companyName": "   "})
        assert blank.status_code == 422
        actions = {item["action"] for item in client.get("/api/v1/audit-logs").json()["items"]}
        assert {"brand.update", "permission.denied"}.issubset(actions)

    with TestClient(create_app(app_settings)) as restarted:
        brand = restarted.get("/api/v1/brand").json()
        assert brand["companyName"] == "星河保险服务" and brand["revision"] == 2
        assert restarted.get("/api/v1/bootstrap").json()["brand"]["slogan"] == "让保险内容更可信"


def test_brand_assets_limits_security_remove_restore_and_persistence(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings)) as client:
        brand = client.get("/api/v1/brand").json()
        logo = upload(client, "logo", brand["revision"], "../企业 Logo.png", png_bytes(256, 256), "image/png")
        assert logo.status_code == 201, logo.text
        brand = logo.json()
        logo_asset = brand["assets"][0]
        assert logo_asset["name"] == "企业 Logo.png" and logo_asset["width"] == 256
        preview = client.get(f"/api/v1/brand/assets/{logo_asset['id']}/preview")
        assert preview.status_code == 200 and preview.headers["content-type"].startswith("image/png")
        assert preview.headers["x-content-type-options"] == "nosniff"

        for index in range(3):
            response = upload(client, "image", brand["revision"], f"参考图{index + 1}.png", png_bytes(320 + index, 320), "image/png")
            assert response.status_code == 201, response.text
            brand = response.json()
        limited = upload(client, "image", brand["revision"], "超限.png", png_bytes(320, 320), "image/png")
        assert limited.status_code == 409 and limited.json()["error"]["code"] == "BRAND_ASSET_LIMIT"

        article = upload(client, "article", brand["revision"], "品牌写作样例.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        assert article.status_code == 201, article.text
        brand = article.json()
        article_asset = next(item for item in brand["assets"] if item["kind"] == "article")
        assert "保障期间" in article_asset["previewText"]
        assert client.get(f"/api/v1/brand/assets/{article_asset['id']}/preview").status_code == 415

        unsupported = upload(client, "image", brand["revision"], "恶意.svg", b"<svg/>", "image/svg+xml")
        assert unsupported.status_code == 415
        mismatch = upload(client, "image", brand["revision"], "伪装.png", b"not-png", "image/png")
        assert mismatch.status_code == 422
        mime_mismatch = upload(client, "image", brand["revision"], "错误.png", png_bytes(128, 128), "image/jpeg")
        assert mime_mismatch.status_code == 415
        stale_remove = client.delete(f"/api/v1/brand/assets/{article_asset['id']}?revision={brand['revision'] - 1}")
        assert stale_remove.status_code == 409
        removed = client.delete(f"/api/v1/brand/assets/{article_asset['id']}?revision={brand['revision']}")
        assert removed.status_code == 200
        brand = removed.json()
        assert all(item["id"] != article_asset["id"] for item in brand["assets"])

        upload_files = list(app_settings.upload_dir.glob("brand_*"))
        assert len(upload_files) == 4
        with sqlite3.connect(app_settings.database_path) as connection:
            keys = [item[0] for item in connection.execute("SELECT storage_key FROM brand_assets")]
        assert all("/" not in key and "\\" not in key for key in keys)

    with TestClient(create_app(app_settings)) as restarted:
        brand = restarted.get("/api/v1/brand").json()
        assert len(brand["assets"]) == 4
        forbidden = restarted.post("/api/v1/brand/restore-defaults", json={"revision": brand["revision"]}, headers={"X-Demo-User": "viewer-demo"})
        assert forbidden.status_code == 403
        restored = restarted.post("/api/v1/brand/restore-defaults", json={"revision": brand["revision"]})
        assert restored.status_code == 200
        result = restored.json()
        assert result["companyName"] == BRAND["companyName"] and result["assets"] == []
        assert list(app_settings.upload_dir.glob("brand_*")) == []
        actions = {item["action"] for item in restarted.get("/api/v1/audit-logs").json()["items"]}
        assert {"brand.asset.upload", "brand.asset.remove", "brand.asset.rejected", "brand.restore_defaults"}.issubset(actions)


def test_brand_size_empty_kind_and_missing_guards(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path, max_visual_bytes=100))) as client:
        brand = client.get("/api/v1/brand").json()
        assert upload(client, "unknown", brand["revision"], "x.png", png_bytes(64, 64), "image/png").status_code == 422
        assert upload(client, "logo", brand["revision"], "empty.png", b"", "image/png").status_code == 422
        assert upload(client, "article", brand["revision"], "blank.txt", b"   ", "text/plain").status_code == 422
        too_large = upload(client, "logo", brand["revision"], "large.png", png_bytes(64, 64), "image/png")
        assert too_large.status_code == 413
        assert client.delete(f"/api/v1/brand/assets/missing?revision={brand['revision']}").status_code == 404
        assert client.get("/api/v1/brand/assets/missing/preview").status_code == 404
