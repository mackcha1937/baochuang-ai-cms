from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from tests.backend.test_stage03_ai_content import FakeProvider, OUTPUTS
from tests.backend.test_stage04_image_generation import IMAGE_COPY, png_bytes


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage06.db'}",
        frontend_dist=tmp_path / "dist",
        upload_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
    )
    return TestClient(create_app(settings, FakeProvider()))


def create(client: TestClient, content_type: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post(
        "/api/v1/contents",
        json={"type": content_type, "title": f"Stage 06 {content_type}", "productId": "darwin-12", "settings": settings or {}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def save_manual(client: TestClient, item: dict[str, Any], task: str, output: dict[str, Any], branch_id: str | None = None) -> dict[str, Any]:
    response = client.put(
        f"/api/v1/contents/{item['id']}/manual-output",
        json={"taskType": task, "branchId": branch_id, "revision": item["revision"], "output": output},
    )
    assert response.status_code == 200, response.text
    return response.json()


def confirm(client: TestClient, item: dict[str, Any], node: str, branch_id: str | None = None) -> dict[str, Any]:
    response = client.post(
        f"/api/v1/contents/{item['id']}/confirm",
        json={"node": node, "branchId": branch_id, "revision": item["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def ready_image(client: TestClient) -> dict[str, Any]:
    item = create(client, "image", {"imageType": "宣传长图"})
    item = confirm(client, save_manual(client, item, "image_copy", IMAGE_COPY), "copy")
    response = client.post(
        f"/api/v1/contents/{item['id']}/visual-assets?revision={item['revision']}",
        files={"file": ("review.png", png_bytes(), "image/png")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def ready_training(client: TestClient) -> dict[str, Any]:
    item = create(client, "training", {"duration": 3})
    item = confirm(client, save_manual(client, item, "training_outline", OUTPUTS["training_outline"]), "outline")
    return confirm(client, save_manual(client, item, "training_script", OUTPUTS["training_script"]), "script")


def article_with_branches(client: TestClient) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    item = create(client, "article", {"channels": ["wechat", "xiaohongshu"]})
    for channel in ("wechat", "xiaohongshu"):
        branch = next(value for value in item["branches"] if value["channel"] == channel)
        item = confirm(client, save_manual(client, item, "article_outline", OUTPUTS["article_outline"], branch["id"]), "outline", branch["id"])
        item = save_manual(client, item, "article_generate", OUTPUTS["article_generate"], branch["id"])
        if channel == "wechat":
            item = confirm(client, item, "body", branch["id"])
    return item, {value["channel"]: value for value in item["branches"]}


def submit_review(client: TestClient, item: dict[str, Any], key: str, branch_id: str | None = None, headers: dict[str, str] | None = None):
    merged = {"Idempotency-Key": key, **(headers or {})}
    return client.post(
        f"/api/v1/contents/{item['id']}/submit-review",
        json={"revision": item["revision"], "branchId": branch_id},
        headers=merged,
    )


def test_image_two_level_review_immutable_idempotent_and_restart(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        item = ready_image(client)
        submitted = submit_review(client, item, "image-submit-1")
        assert submitted.status_code == 201, submitted.text
        product_task = submitted.json()
        assert product_task["stage"] == "product" and product_task["status"] == "pending"
        assert product_task["snapshot"]["payload"]["copy"]["title"] == IMAGE_COPY["title"]
        replay = submit_review(client, item, "image-submit-1")
        assert replay.status_code == 201 and replay.json()["id"] == product_task["id"]

        locked = client.patch(
            f"/api/v1/contents/{item['id']}/visual-layout",
            json={
                "revision": item["revision"] + 1,
                "template": "long-editorial", "palette": "brand",
                "blockOrder": item["payload"]["visual"]["layout"]["blockOrder"], "hiddenBlockIds": [],
            },
        )
        assert locked.status_code == 409 and locked.json()["error"]["code"] == "CONTENT_REVIEW_LOCKED"

        started = client.post(f"/api/v1/reviews/{product_task['id']}/start")
        assert started.status_code == 200 and started.json()["status"] == "in_progress"
        approved = client.post(
            f"/api/v1/reviews/{product_task['id']}/approve",
            json={"reason": "产品事实与来源一致"}, headers={"Idempotency-Key": "product-approve-1"},
        )
        assert approved.status_code == 200
        compliance_task = approved.json()
        assert compliance_task["stage"] == "compliance" and compliance_task["status"] == "pending"
        replay_decision = client.post(
            f"/api/v1/reviews/{product_task['id']}/approve",
            json={"reason": "重复点击"}, headers={"Idempotency-Key": "product-approve-1"},
        )
        assert replay_decision.status_code == 200 and replay_decision.json()["id"] == compliance_task["id"]
        assert client.post(f"/api/v1/reviews/{compliance_task['id']}/start").status_code == 200
        final = client.post(
            f"/api/v1/reviews/{compliance_task['id']}/approve",
            json={"reason": "声明完整，风险可接受"}, headers={"Idempotency-Key": "compliance-approve-1"},
        )
        assert final.status_code == 200 and final.json()["status"] == "approved"
        detail = client.get(f"/api/v1/contents/{item['id']}").json()
        assert detail["status"] == "approved"
        assert len(detail["reviews"]) == 2
        assert client.get("/api/v1/bootstrap").json()["stats"]["pendingReviews"] == 0

        other = ready_training(client)
        cross_target = submit_review(client, other, "image-submit-1")
        assert cross_target.status_code == 201
        assert cross_target.json()["id"] != product_task["id"]
        other_current = client.get(f"/api/v1/contents/{other['id']}").json()
        assert client.post(
            f"/api/v1/contents/{other['id']}/withdraw-review",
            json={"revision": other_current["revision"], "taskId": cross_target.json()["id"]},
        ).status_code == 200

    with make_client(tmp_path) as restarted:
        restored = restarted.get(f"/api/v1/contents/{item['id']}").json()
        assert restored["status"] == "approved" and len(restored["reviews"]) == 2
        task = restarted.get(f"/api/v1/reviews/{compliance_task['id']}").json()
        assert task["snapshot"]["payload"]["copy"]["title"] == IMAGE_COPY["title"]
        assert [record["decision"] for record in task["records"]] == ["start", "approve"]


def test_article_channels_reject_reopen_resubmit_and_withdraw(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        item, branches = article_with_branches(client)
        wechat = branches["wechat"]
        submitted = submit_review(client, item, "wechat-submit-1", wechat["id"])
        assert submitted.status_code == 201, submitted.text
        task = submitted.json()

        current = client.get(f"/api/v1/contents/{item['id']}").json()
        branch_map = {value["channel"]: value for value in current["branches"]}
        assert branch_map["wechat"]["reviewStatus"] == "product_review"
        assert branch_map["xiaohongshu"]["reviewStatus"] == "draft"
        xhs = branch_map["xiaohongshu"]
        current = confirm(client, current, "body", xhs["id"])
        assert next(value for value in current["branches"] if value["id"] == xhs["id"])["bodyStatus"] == "confirmed"

        no_reason = client.post(
            f"/api/v1/reviews/{task['id']}/reject", json={"reason": ""}, headers={"Idempotency-Key": "reject-empty"},
        )
        assert no_reason.status_code == 422 and no_reason.json()["error"]["code"] == "REVIEW_REASON_REQUIRED"
        rejected = client.post(
            f"/api/v1/reviews/{task['id']}/reject",
            json={"reason": "第二段产品年龄表述需要修正"}, headers={"Idempotency-Key": "reject-wechat-1"},
        )
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        current = client.get(f"/api/v1/contents/{item['id']}").json()
        rejected_branch = next(value for value in current["branches"] if value["id"] == wechat["id"])
        assert rejected_branch["reviewStatus"] == "rejected_product"

        reopened = client.post(
            f"/api/v1/contents/{item['id']}/reopen-review",
            json={"revision": current["revision"], "branchId": wechat["id"]},
        )
        assert reopened.status_code == 200
        current = reopened.json()
        reopened_branch = next(value for value in current["branches"] if value["id"] == wechat["id"])
        assert reopened_branch["reviewStatus"] == "draft" and reopened_branch["bodyStatus"] == "draft"
        changed = {**OUTPUTS["article_generate"], "title": "修订后的重疾保障文章"}
        current = confirm(client, save_manual(client, current, "article_generate", changed, wechat["id"]), "body", wechat["id"])
        resubmitted = submit_review(client, current, "wechat-submit-2", wechat["id"])
        assert resubmitted.status_code == 201 and resubmitted.json()["contentVersion"] == 2

        current = client.get(f"/api/v1/contents/{item['id']}").json()
        withdrawn = client.post(
            f"/api/v1/contents/{item['id']}/withdraw-review",
            json={"revision": current["revision"], "taskId": resubmitted.json()["id"]},
        )
        assert withdrawn.status_code == 200
        branch = next(value for value in withdrawn.json()["branches"] if value["id"] == wechat["id"])
        assert branch["reviewStatus"] == "draft"


def test_review_negative_permissions_conflict_started_withdraw_and_audit(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        incomplete = create(client, "training", {"duration": 3})
        blocked = submit_review(client, incomplete, "incomplete-training")
        assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "CONTENT_REVIEW_INCOMPLETE"

        item = ready_training(client)
        forbidden = submit_review(client, item, "viewer-submit", headers={"X-Demo-User": "viewer-demo"})
        assert forbidden.status_code == 403
        task = submit_review(client, item, "training-submit").json()
        current = client.get(f"/api/v1/contents/{item['id']}").json()
        stale = client.post(
            f"/api/v1/contents/{item['id']}/withdraw-review",
            json={"revision": current["revision"] - 1, "taskId": task["id"]},
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
        assert client.get("/api/v1/reviews", headers={"X-Demo-User": "viewer-demo"}).status_code == 403
        assert client.post(f"/api/v1/reviews/{task['id']}/start").status_code == 200
        started_withdraw = client.post(
            f"/api/v1/contents/{item['id']}/withdraw-review",
            json={"revision": current["revision"], "taskId": task["id"]},
        )
        assert started_withdraw.status_code == 409 and started_withdraw.json()["error"]["code"] == "REVIEW_WITHDRAW_FORBIDDEN"
        rejected = client.post(
            f"/api/v1/reviews/{task['id']}/reject",
            json={"reason": "培训声明需要补充"}, headers={"Idempotency-Key": "training-reject"},
        )
        assert rejected.status_code == 200
        illegal_repeat = client.post(
            f"/api/v1/reviews/{task['id']}/approve",
            json={"reason": ""}, headers={"Idempotency-Key": "different-decision"},
        )
        assert illegal_repeat.status_code == 409
        actions = {entry["action"] for entry in client.get("/api/v1/audit-logs").json()["items"]}
        assert {"content.submit", "review.product.start", "review.product.reject", "permission.denied"}.issubset(actions)
