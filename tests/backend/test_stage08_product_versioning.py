from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from tests.backend.test_stage02_product_library import docx_bytes, upload
from tests.backend.test_stage03_ai_content import FakeProvider
from tests.backend.test_stage06_review_workflow import (
    article_with_branches,
    ready_image,
    ready_training,
)
from tests.backend.test_stage07_asset_library import approve


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'stage08.db'}",
        frontend_dist=tmp_path / "dist",
        upload_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
    )


def make_client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(settings(tmp_path), FakeProvider()))


def create_version(client: TestClient) -> dict:
    current = client.get("/api/v1/products/darwin-12").json()
    response = client.post(
        "/api/v1/products/darwin-12/new-version",
        json={"revision": current["revision"]},
    )
    assert response.status_code == 201, response.text
    return response.json()


def prepare_version(client: TestClient) -> dict:
    version = create_version(client)
    facts = []
    for fact in version["facts"]:
        facts.append({**fact, "value": "90天" if fact["label"] == "等待期" else fact["value"], "confirmed": True})
    updated = client.patch(
        f"/api/v1/products/{version['id']}",
        json={
            "name": version["name"], "insurer": version["insurer"], "category": version["category"],
            "summary": version["summary"] + " 新增等待期优化。", "revision": version["revision"], "facts": facts,
        },
    )
    assert updated.status_code == 200, updated.text
    document = upload(client, version["id"], "V2条款.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert client.post(f"/api/v1/products/{version['id']}/documents/{document['id']}/parse").status_code == 200
    return client.get(f"/api/v1/products/{version['id']}").json()


def test_product_v2_difference_confirm_marks_assets_and_persists(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings, FakeProvider())) as client:
        image = ready_image(client)
        approve(client, image, "v2-image")
        version = prepare_version(client)

        changes = {item["label"]: item for item in version["changes"]}
        assert changes["等待期"]["before"] == "180天" and changes["等待期"]["after"] == "90天"
        assert "产品摘要" in changes
        assert version["version"] == "V2" and version["previousVersionId"] == "darwin-12"
        assert len(version["versions"]) == 2

        original = client.get("/api/v1/products/darwin-12").json()
        replay = client.post("/api/v1/products/darwin-12/new-version", json={"revision": original["revision"]})
        assert replay.status_code == 200 and replay.json()["id"] == version["id"]
        missing_time = client.post(f"/api/v1/products/{version['id']}/confirm", json={"revision": version["revision"]})
        assert missing_time.status_code == 422
        assert "新版本生效时间" in missing_time.json()["error"]["details"]["missing"]

        confirmed = client.post(
            f"/api/v1/products/{version['id']}/confirm",
            json={"revision": version["revision"], "effectiveAt": "2026-09-01T00:00:00+08:00"},
        )
        assert confirmed.status_code == 200, confirmed.text
        v2 = confirmed.json()
        assert v2["status"] == "已确认" and v2["isCurrent"] is True
        assert v2["effectiveAt"] == "2026-09-01T00:00:00+08:00"
        v1 = client.get("/api/v1/products/darwin-12").json()
        assert v1["status"] == "历史版本" and v1["isCurrent"] is False
        assert client.patch("/api/v1/products/darwin-12", json={
            "name": v1["name"], "insurer": v1["insurer"], "category": v1["category"], "summary": v1["summary"],
            "revision": v1["revision"], "facts": v1["facts"],
        }).status_code == 409
        products = client.get("/api/v1/products").json()["items"]
        assert [item["id"] for item in products] == [v2["id"]]

        asset = client.get("/api/v1/assets?status=possibly_outdated").json()["items"][0]
        assert asset["contentId"] == image["id"]
        assert client.get(f"/api/v1/assets/{asset['id']}/download").status_code == 409
        stats = client.get("/api/v1/bootstrap").json()["stats"]
        assert stats["possiblyOutdated"] == 1 and stats["approvedAssets"] == 0

    with TestClient(create_app(app_settings, FakeProvider())) as restarted:
        restored_v2 = restarted.get(f"/api/v1/products/{v2['id']}").json()
        assert restored_v2["isCurrent"] is True and restored_v2["version"] == "V2"
        assert restarted.get("/api/v1/assets?status=possibly_outdated").json()["total"] == 1
        assert restarted.get("/api/v1/bootstrap").json()["stats"]["possiblyOutdated"] == 1


def test_outdated_dispositions_permissions_audit_and_restart(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings, FakeProvider())) as client:
        image = ready_image(client)
        approve(client, image, "impact-image")
        training = ready_training(client)
        approve(client, training, "impact-training")
        article, branches = article_with_branches(client)
        approve(client, article, "impact-article", branches["wechat"]["id"])
        version = prepare_version(client)
        confirmed = client.post(
            f"/api/v1/products/{version['id']}/confirm",
            json={"revision": version["revision"], "effectiveAt": "2026-09-01"},
        )
        assert confirmed.status_code == 200
        v2_id = confirmed.json()["id"]
        assets = client.get("/api/v1/assets?status=possibly_outdated").json()["items"]
        assert len(assets) == 3
        by_type = {item["type"]: item for item in assets}

        empty = client.post(f"/api/v1/assets/{by_type['image']['id']}/keep-valid", json={"reason": ""})
        assert empty.status_code == 422
        forbidden = client.post(
            f"/api/v1/assets/{by_type['image']['id']}/keep-valid",
            json={"reason": "只读用户尝试处置"}, headers={"X-Demo-User": "viewer-demo"},
        )
        assert forbidden.status_code == 403

        kept = client.post(f"/api/v1/assets/{by_type['image']['id']}/keep-valid", json={"reason": "变化不影响该图片事实"})
        assert kept.status_code == 200 and kept.json()["status"] == "approved"
        assert client.get(f"/api/v1/assets/{by_type['image']['id']}/download").status_code == 200
        assert client.post(f"/api/v1/assets/{by_type['image']['id']}/keep-valid", json={"reason": "重复"}).status_code == 409

        revised = client.post(f"/api/v1/assets/{by_type['training']['id']}/revise")
        assert revised.status_code == 201, revised.text
        draft = revised.json()
        assert draft["productId"] == v2_id and draft["status"] == "draft"
        assert draft["payload"]["scriptStatus"] == "draft"
        assert client.get(f"/api/v1/assets/{by_type['training']['id']}").json()["status"] == "possibly_outdated"

        invalidated = client.post(f"/api/v1/assets/{by_type['article']['id']}/invalidate", json={"reason": "等待期表述已变化"})
        assert invalidated.status_code == 200 and invalidated.json()["status"] == "invalid"
        assert client.get(f"/api/v1/assets/{by_type['article']['id']}/download").status_code == 409
        assert client.get(f"/api/v1/assets/{by_type['article']['id']}").status_code == 200
        restored = client.post(f"/api/v1/assets/{by_type['article']['id']}/restore", json={"reason": "申请重新复核"})
        assert restored.status_code == 200 and restored.json()["status"] == "restore_pending"
        assert client.get(f"/api/v1/assets/{by_type['article']['id']}/download").status_code == 409
        reviewed = client.post(f"/api/v1/assets/{by_type['article']['id']}/keep-valid", json={"reason": "人工复核确认可继续使用"})
        assert reviewed.status_code == 200 and reviewed.json()["status"] == "approved"

        actions = {item["action"] for item in client.get("/api/v1/audit-logs").json()["items"]}
        assert {"product.version.create", "product.version.confirm", "asset.outdated", "asset.keep_valid", "asset.revise", "asset.invalidate", "asset.restore", "permission.denied"}.issubset(actions)

    with TestClient(create_app(app_settings, FakeProvider())) as restarted:
        statuses = {item["type"]: item["status"] for item in restarted.get("/api/v1/assets").json()["items"]}
        assert statuses == {"image": "approved", "article": "approved", "training": "possibly_outdated"}
        assert restarted.get(f"/api/v1/contents/{draft['id']}").json()["productId"] == v2_id


def test_version_cancel_conflict_invalid_time_and_state_guards(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        current = client.get("/api/v1/products/darwin-12").json()
        stale = client.post("/api/v1/products/darwin-12/new-version", json={"revision": current["revision"] + 1})
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
        version = create_version(client)
        invalid_time = client.post(f"/api/v1/products/{version['id']}/confirm", json={"revision": version["revision"], "effectiveAt": "not-a-date"})
        assert invalid_time.status_code == 422
        assert client.post(f"/api/v1/products/{version['id']}/new-version", json={"revision": version["revision"]}).status_code == 409
        assert client.delete(f"/api/v1/products/{version['id']}").status_code == 204
        original = client.get("/api/v1/products/darwin-12").json()
        assert original["status"] == "已确认" and original["isCurrent"] is True
        assert len(original["versions"]) == 1
        assert client.get("/api/v1/assets?status=unknown").status_code == 422
