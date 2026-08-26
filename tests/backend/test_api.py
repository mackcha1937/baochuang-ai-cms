from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app


def make_client(tmp_path: Path) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage01.db'}",
        frontend_dist=tmp_path / "missing-dist",
        upload_dir=tmp_path / "uploads",
    )
    return TestClient(create_app(settings))


def test_health_bootstrap_and_secret_boundary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "unit-test-secret-value")
    monkeypatch.setenv("DASHSCOPE_API_KEY", "unit-test-qwen-secret")
    with make_client(tmp_path) as client:
        health = client.get("/api/v1/health")
        bootstrap = client.get("/api/v1/bootstrap")

    assert health.status_code == 200
    assert health.json()["ok"] is True
    assert bootstrap.status_code == 200
    payload = bootstrap.json()
    assert payload["user"]["name"] == "超级演示用户"
    assert len(payload["user"]["roles"]) == 4
    assert "review.compliance" in payload["user"]["permissions"]
    assert payload["serviceStatus"] == {"api": "ready", "deepseek": "configured", "qwen": "configured"}
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "unit-test-secret-value" not in serialized
    assert "unit-test-qwen-secret" not in serialized


def test_product_list_search_detail_and_404(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        product_list = client.get("/api/v1/products")
        matched = client.get("/api/v1/products", params={"q": "达尔文"})
        empty = client.get("/api/v1/products", params={"q": "不存在的产品"})
        detail = client.get("/api/v1/products/darwin-12")
        missing = client.get("/api/v1/products/missing")
        missing_api = client.get("/api/v1/not-a-route")

    assert product_list.status_code == 200
    assert product_list.json()["total"] == 1
    assert matched.json()["total"] == 1
    assert empty.json() == {"items": [], "total": 0, "query": "不存在的产品"}
    assert detail.status_code == 200
    assert len(detail.json()["facts"]) == 7
    assert all(item["confirmed"] for item in detail.json()["facts"])
    assert missing.status_code == 404
    assert missing.json()["error"]["message"] == "产品不存在或已被移除"
    assert missing_api.status_code == 404


def test_stage00_read_only_endpoints_remain_compatible(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "compatibility-secret")
    with make_client(tmp_path) as client:
        health = client.get("/api/health")
        config = client.get("/api/config")
        state = client.get("/api/state")

    assert health.status_code == 200 and health.json()["ok"] is True
    assert config.status_code == 200 and config.json()["deepseekConfigured"] is True
    assert "compatibility-secret" not in json.dumps(config.json())
    assert state.status_code == 200
    assert state.json()["settings"]["demoMode"] is True
    assert state.json()["products"][0]["id"] == "darwin-12"


def test_seed_is_idempotent_and_sqlite_state_survives_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "stage01.db"
    with make_client(tmp_path) as client:
        assert client.get("/api/v1/products").json()["total"] == 1

    with sqlite3.connect(database_path) as connection:
        connection.execute("UPDATE brands SET company_name = ?", ("持久化验证企业",))
        connection.commit()

    with make_client(tmp_path) as restarted_client:
        payload = restarted_client.get("/api/v1/bootstrap").json()
        products = restarted_client.get("/api/v1/products").json()

    assert payload["brand"]["companyName"] == "持久化验证企业"
    assert products["total"] == 1


def test_frontend_missing_has_recoverable_message(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        response = client.get("/", headers={"accept": "application/json"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "FRONTEND_NOT_BUILT"


def test_all_spa_deep_links_return_the_frontend(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>stage-01-spa</title>", encoding="utf-8")
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage01.db'}", frontend_dist=dist)
    paths = [
        "/", "/products", "/products/darwin-12", "/create/image", "/create/article",
        "/create/training", "/reviews", "/assets", "/brand", "/permissions", "/audit",
    ]
    with TestClient(create_app(settings)) as client:
        responses = [client.get(path) for path in paths]
    assert all(response.status_code == 200 for response in responses)
    assert all("stage-01-spa" in response.text for response in responses)
