from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.seed import PERMISSIONS, ROLE_PERMISSIONS


def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'stage10.db'}",
        frontend_dist=tmp_path / "dist",
        upload_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
    )


def test_permission_matrix_is_read_only_and_denials_are_audited(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings)) as client:
        response = client.get("/api/v1/permissions")
        assert response.status_code == 200, response.text
        matrix = response.json()
        assert matrix["currentUserId"] == "super-demo"
        assert matrix["mode"] == "local_demo" and matrix["mutable"] is False
        assert {user["id"] for user in matrix["users"]} == {"super-demo", "viewer-demo"}
        assert matrix["users"][0]["permissions"] == sorted(PERMISSIONS)
        assert matrix["users"][1]["permissions"] == []
        assert {role["name"] for role in matrix["roles"]} == set(ROLE_PERMISSIONS)
        assert {item["code"] for item in matrix["permissions"]} == set(PERMISSIONS)

        forbidden = client.get("/api/v1/permissions", headers={"X-Demo-User": "viewer-demo"})
        assert forbidden.status_code == 403
        assert forbidden.json()["error"]["code"] == "PERMISSION_DENIED"

        for method in ("post", "patch", "delete"):
            mutation = client.request(method.upper(), "/api/v1/permissions", json={"role": "只读访客"})
            assert mutation.status_code in {404, 405}

        denied = client.get("/api/v1/audit-logs", params={"actorId": "viewer-demo", "action": "permission.denied"}).json()
        assert denied["total"] == 1
        assert denied["items"][0]["object_type"] == "permission"
        assert denied["items"][0]["object_id"] == "user.manage"

    with TestClient(create_app(app_settings)) as restarted:
        matrix = restarted.get("/api/v1/permissions").json()
        assert matrix["mutable"] is False and len(matrix["users"]) == 2
        denied = restarted.get("/api/v1/audit-logs", params={"actorId": "viewer-demo"}).json()
        assert denied["total"] == 1


def test_audit_filters_detail_bounds_immutability_and_restart(tmp_path: Path) -> None:
    app_settings = settings(tmp_path)
    with TestClient(create_app(app_settings)) as client:
        created = client.post(
            "/api/v1/products",
            json={"name": "Stage 10 审计筛选产品"},
            headers={"Idempotency-Key": "stage10-audit-filter"},
        )
        assert created.status_code == 201, created.text
        product_id = created.json()["id"]
        client.get("/api/v1/audit-logs", headers={"X-Demo-User": "viewer-demo"})

        filtered = client.get(
            "/api/v1/audit-logs",
            params={"actorId": "super-demo", "action": "product.create", "objectType": "product", "objectId": product_id},
        )
        assert filtered.status_code == 200, filtered.text
        payload = filtered.json()
        assert payload["total"] == 1 and payload["limit"] == 100 and payload["offset"] == 0
        item = payload["items"][0]
        assert item["object_id"] == product_id
        assert "super-demo" in payload["facets"]["actors"] and "viewer-demo" in payload["facets"]["actors"]
        assert "product.create" in payload["facets"]["actions"]

        exact_time = client.get(
            "/api/v1/audit-logs",
            params={"occurredFrom": item["occurred_at"], "occurredTo": item["occurred_at"], "limit": 1, "offset": 0},
        )
        assert exact_time.status_code == 200 and exact_time.json()["total"] >= 1
        detail = client.get(f"/api/v1/audit-logs/{item['id']}")
        assert detail.status_code == 200 and detail.json() == item
        assert client.get("/api/v1/audit-logs/999999").status_code == 404
        assert client.get("/api/v1/audit-logs", headers={"X-Demo-User": "viewer-demo"}).status_code == 403

        invalid_cases = [
            ({"occurredFrom": "not-a-date"}, "AUDIT_TIME_INVALID"),
            ({"occurredFrom": "2026-08-28T00:00", "occurredTo": "2026-08-27T00:00"}, "AUDIT_TIME_RANGE_INVALID"),
            ({"occurredFrom": "2026-08-27T03:00:00+00:00", "occurredTo": "2026-08-27T09:30:00+08:00"}, "AUDIT_TIME_RANGE_INVALID"),
        ]
        for params, code in invalid_cases:
            response = client.get("/api/v1/audit-logs", params=params)
            assert response.status_code == 422 and response.json()["error"]["code"] == code
        assert client.get("/api/v1/audit-logs", params={"limit": 201}).status_code == 422
        assert client.get("/api/v1/audit-logs", params={"offset": 10001}).status_code == 422

        with sqlite3.connect(app_settings.database_path) as connection:
            before = connection.execute("SELECT COUNT(*), GROUP_CONCAT(detail, '|') FROM audit_logs").fetchone()
        for method in ("post", "patch", "delete"):
            response = client.request(method.upper(), f"/api/v1/audit-logs/{item['id']}", json={"detail": "tampered"})
            assert response.status_code in {404, 405}
        with sqlite3.connect(app_settings.database_path) as connection:
            after = connection.execute("SELECT COUNT(*), GROUP_CONCAT(detail, '|') FROM audit_logs").fetchone()
        assert after == before
        with sqlite3.connect(app_settings.database_path) as connection:
            for statement in ("UPDATE audit_logs SET detail = 'tampered'", "DELETE FROM audit_logs"):
                try:
                    connection.execute(statement)
                except sqlite3.IntegrityError as error:
                    assert "immutable" in str(error)
                else:
                    raise AssertionError("audit trigger allowed a mutation")

        cross_offset = client.get(
            "/api/v1/audit-logs",
            params={"occurredFrom": "2026-08-27T09:00:00+08:00", "occurredTo": "2026-08-27T02:00:00+00:00"},
        )
        assert cross_offset.status_code == 200

    with TestClient(create_app(app_settings)) as restarted:
        result = restarted.get("/api/v1/audit-logs", params={"objectId": product_id, "action": "product.create"}).json()
        assert result["total"] == 1 and result["items"][0]["id"] == item["id"]
        assert restarted.get(f"/api/v1/audit-logs/{item['id']}").json()["detail"] == item["detail"]
