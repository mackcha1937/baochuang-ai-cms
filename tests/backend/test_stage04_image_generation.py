from __future__ import annotations

import binascii
import json
import sqlite3
import struct
import zlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.config import Settings
from backend.image_generation import QwenImageProvider, VisualCancelled, VisualProviderError, VisualResult, validate_raster, visual_prompt
from backend.main import create_app


IMAGE_COPY = {
    "title": "家庭保障，从看懂责任开始",
    "sellingPoints": ["保障责任清晰", "重点信息可追溯"],
    "blocks": [{"type": "hero", "text": "依据已确认产品资料介绍保障要点"}],
    "disclaimer": "具体责任以保险合同为准",
}


def png_bytes(width: int = 640, height: int = 960) -> bytes:
    def chunk(kind: bytes, value: bytes) -> bytes:
        return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", binascii.crc32(kind + value) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x88\xaa\xcc" * width for _ in range(height))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


class FakeImageProvider:
    name = "fake-qwen"
    model = "qwen-image-3.0"

    def __init__(self, *, fail_once: bool = False):
        self.fail_once = fail_once
        self.calls: list[dict[str, Any]] = []

    def generate(self, snapshot: dict[str, Any], *, on_task_created: Any, is_cancelled: Any) -> VisualResult:
        self.calls.append(snapshot)
        on_task_created("provider_task_private")
        if is_cancelled():
            raise AssertionError("fresh retry must clear the cancellation flag")
        if self.fail_once:
            self.fail_once = False
            raise VisualProviderError("QWEN_RATE_LIMITED", "模拟千问限流，输入已保留")
        data = png_bytes()
        return VisualResult(data=data, mime_type="image/png", width=640, height=960)


def make_client(tmp_path: Path, provider: Any = None, *, max_visual_bytes: int = 15 * 1024 * 1024) -> TestClient:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'stage04.db'}",
        frontend_dist=tmp_path / "dist",
        upload_dir=tmp_path / "uploads",
        generated_dir=tmp_path / "generated",
        max_visual_bytes=max_visual_bytes,
    )
    return TestClient(create_app(settings, image_provider=provider or FakeImageProvider()))


def create_image_project(client: TestClient, *, confirmed: bool = True) -> dict[str, Any]:
    created = client.post(
        "/api/v1/contents",
        json={
            "type": "image",
            "title": "Stage 04 图片",
            "productId": "darwin-12",
            "settings": {"audience": "家庭用户", "purpose": "产品科普", "imageType": "宣传长图"},
        },
    ).json()
    saved = client.put(
        f"/api/v1/contents/{created['id']}/manual-output",
        json={"taskType": "image_copy", "revision": created["revision"], "output": IMAGE_COPY},
    ).json()
    if not confirmed:
        return saved
    response = client.post(
        f"/api/v1/contents/{created['id']}/confirm",
        json={"node": "copy", "revision": saved["revision"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_visual_job(client: TestClient, content_id: str, key: str = "visual-job-1") -> dict[str, Any]:
    response = client.post(
        f"/api/v1/contents/{content_id}/jobs",
        json={"taskType": "image_visual"},
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_visual_generation_persists_safe_asset_and_layout(tmp_path: Path) -> None:
    provider = FakeImageProvider()
    with make_client(tmp_path, provider) as client:
        content = create_image_project(client)
        job = create_visual_job(client, content["id"])
        replay = client.post(
            f"/api/v1/contents/{content['id']}/jobs",
            json={"taskType": "image_visual"},
            headers={"Idempotency-Key": "visual-job-1"},
        )
        assert replay.status_code == 200 and replay.json()["id"] == job["id"]
        started = client.post(f"/api/v1/jobs/{job['id']}/run")
        assert started.status_code == 200
        completed = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert completed["status"] == "succeeded" and completed["attempt"] == 1
        assert completed["provider"] == "fake-qwen" and completed["model"] == "qwen-image-3.0"
        assert "provider_task_private" not in json.dumps(completed)
        assert "storage" not in json.dumps(completed).lower()

        detail = client.get(f"/api/v1/contents/{content['id']}").json()
        visual = detail["payload"]["visual"]
        assert visual["status"] == "draft"
        assert visual["layout"]["template"] == "long-editorial"
        assert {"title", "product", "disclaimer"}.issubset(set(visual["layout"]["blockOrder"]))
        preview = client.get(f"/api/v1/visual-assets/{visual['assetId']}/preview")
        assert preview.status_code == 200 and preview.headers["content-type"] == "image/png"
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert validate_raster(preview.content) == ("image/png", ".png", 640, 960)
        assert provider.calls[0]["confirmedCopy"]["title"] == IMAGE_COPY["title"]

    with make_client(tmp_path, FakeImageProvider()) as restarted:
        restored = restarted.get(f"/api/v1/contents/{content['id']}").json()
        assert restored["payload"]["visual"]["assetId"] == visual["assetId"]
        assert restarted.get(f"/api/v1/visual-assets/{visual['assetId']}/preview").status_code == 200


def test_visual_requires_confirmed_copy_and_matching_content(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        draft = create_image_project(client, confirmed=False)
        blocked = client.post(
            f"/api/v1/contents/{draft['id']}/jobs",
            json={"taskType": "image_visual"},
            headers={"Idempotency-Key": "blocked-visual"},
        )
        assert blocked.status_code == 422 and blocked.json()["error"]["code"] == "IMAGE_COPY_NOT_CONFIRMED"

        article = client.post(
            "/api/v1/contents",
            json={"type": "article", "title": "错误类型", "productId": "darwin-12", "settings": {"channels": ["wechat"]}},
        ).json()
        mismatch = client.post(
            f"/api/v1/contents/{article['id']}/jobs",
            json={"taskType": "image_visual"},
            headers={"Idempotency-Key": "wrong-type"},
        )
        assert mismatch.status_code == 422 and mismatch.json()["error"]["code"] == "AI_TASK_UNSUPPORTED"


def test_visual_failure_cancel_and_retry_preserve_snapshot(tmp_path: Path) -> None:
    provider = FakeImageProvider(fail_once=True)
    with make_client(tmp_path, provider) as client:
        content = create_image_project(client)
        job = create_visual_job(client, content["id"], "retry-visual")
        client.post(f"/api/v1/jobs/{job['id']}/run")
        failed = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert failed["status"] == "failed" and failed["errorCode"] == "QWEN_RATE_LIMITED"
        assert failed["input"]["confirmedCopy"] == IMAGE_COPY
        retried = client.post(f"/api/v1/jobs/{job['id']}/retry")
        assert retried.status_code == 200
        completed = client.get(f"/api/v1/jobs/{job['id']}").json()
        assert completed["status"] == "succeeded" and completed["attempt"] == 2

        another = create_visual_job(client, content["id"], "cancel-visual")
        cancelled = client.post(f"/api/v1/jobs/{another['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        assert cancelled.json()["input"]["confirmedCopy"] == IMAGE_COPY
        client.post(f"/api/v1/jobs/{another['id']}/retry")
        assert client.get(f"/api/v1/jobs/{another['id']}").json()["status"] == "succeeded"


def test_layout_validation_required_blocks_and_revision(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        content = create_image_project(client)
        job = create_visual_job(client, content["id"])
        client.post(f"/api/v1/jobs/{job['id']}/run")
        detail = client.get(f"/api/v1/contents/{content['id']}").json()
        layout = detail["payload"]["visual"]["layout"]
        reversed_order = list(reversed(layout["blockOrder"]))
        updated = client.patch(
            f"/api/v1/contents/{content['id']}/visual-layout",
            json={"revision": detail["revision"], "template": "square-focus", "palette": "forest", "blockOrder": reversed_order, "hiddenBlockIds": ["selling-1"]},
        )
        assert updated.status_code == 200
        assert updated.json()["payload"]["visual"]["layout"]["blockOrder"] == reversed_order

        stale = client.patch(
            f"/api/v1/contents/{content['id']}/visual-layout",
            json={"revision": detail["revision"], "template": "square-focus", "palette": "forest", "blockOrder": reversed_order, "hiddenBlockIds": []},
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "REVISION_CONFLICT"
        latest = updated.json()
        required = client.patch(
            f"/api/v1/contents/{content['id']}/visual-layout",
            json={"revision": latest["revision"], "template": "header-split", "palette": "ocean", "blockOrder": reversed_order, "hiddenBlockIds": ["disclaimer"]},
        )
        assert required.status_code == 422 and required.json()["error"]["code"] == "VISUAL_REQUIRED_BLOCK"


def test_confirmed_copy_is_locked_until_explicit_unlock_and_marks_visual_stale(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        content = create_image_project(client)
        locked_manual = client.put(
            f"/api/v1/contents/{content['id']}/manual-output",
            json={"taskType": "image_copy", "revision": content["revision"], "output": IMAGE_COPY},
        )
        assert locked_manual.status_code == 409 and locked_manual.json()["error"]["code"] == "CONTENT_NODE_LOCKED"
        locked_job = client.post(
            f"/api/v1/contents/{content['id']}/jobs",
            json={"taskType": "image_copy"},
            headers={"Idempotency-Key": "locked-image-copy"},
        )
        assert locked_job.status_code == 409 and locked_job.json()["error"]["code"] == "CONTENT_NODE_LOCKED"

        visual_job = create_visual_job(client, content["id"])
        client.post(f"/api/v1/jobs/{visual_job['id']}/run")
        with_visual = client.get(f"/api/v1/contents/{content['id']}").json()
        unlocked = client.post(
            f"/api/v1/contents/{content['id']}/unlock-copy",
            json={"revision": with_visual["revision"]},
        )
        assert unlocked.status_code == 200
        assert unlocked.json()["payload"]["copyStatus"] == "draft"
        assert unlocked.json()["payload"]["visual"]["status"] == "needs_relayout"
        blocked_visual = client.post(
            f"/api/v1/contents/{content['id']}/jobs",
            json={"taskType": "image_visual"},
            headers={"Idempotency-Key": "visual-while-unlocked"},
        )
        assert blocked_visual.status_code == 422 and blocked_visual.json()["error"]["code"] == "IMAGE_COPY_NOT_CONFIRMED"


def test_visual_upload_positive_negative_permissions_and_audit(tmp_path: Path) -> None:
    with make_client(tmp_path, max_visual_bytes=200_000) as client:
        content = create_image_project(client)
        invalid = client.post(
            f"/api/v1/contents/{content['id']}/visual-assets",
            params={"revision": content["revision"]},
            files={"file": ("../伪造.png", b"not-an-image", "image/png")},
        )
        assert invalid.status_code == 422 and invalid.json()["error"]["code"] == "VISUAL_FILE_INVALID"
        wrong_type = client.post(
            f"/api/v1/contents/{content['id']}/visual-assets",
            params={"revision": content["revision"]},
            files={"file": ("替换.svg", b"<svg/>", "image/svg+xml")},
        )
        assert wrong_type.status_code == 415
        too_large = client.post(
            f"/api/v1/contents/{content['id']}/visual-assets",
            params={"revision": content["revision"]},
            files={"file": ("过大.png", b"x" * 200_001, "image/png")},
        )
        assert too_large.status_code == 413

        forbidden = client.post(
            f"/api/v1/contents/{content['id']}/visual-assets",
            params={"revision": content["revision"]},
            headers={"X-Demo-User": "viewer-demo"},
            files={"file": ("替换.png", png_bytes(320, 320), "image/png")},
        )
        assert forbidden.status_code == 403

        uploaded = client.post(
            f"/api/v1/contents/{content['id']}/visual-assets",
            params={"revision": content["revision"]},
            files={"file": ("../我的替换图.png", png_bytes(320, 320), "image/png")},
        )
        assert uploaded.status_code == 200, uploaded.text
        visual = uploaded.json()["payload"]["visual"]
        assert client.get(f"/api/v1/visual-assets/{visual['assetId']}/preview").status_code == 200
        serialized = json.dumps(uploaded.json(), ensure_ascii=False)
        assert str(tmp_path) not in serialized and "storage_key" not in serialized and "storageKey" not in serialized
        actions = [item["action"] for item in client.get("/api/v1/audit-logs").json()["items"]]
        assert "visual.upload" in actions and "visual.upload.rejected" in actions and "permission.denied" in actions


def test_running_job_recovers_as_retryable_after_restart(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        content = create_image_project(client)
        job = create_visual_job(client, content["id"], "restart-running")
    with sqlite3.connect(tmp_path / "stage04.db") as connection:
        connection.execute("UPDATE generation_jobs SET status = 'running' WHERE id = ?", (job["id"],))
        connection.commit()
    with make_client(tmp_path) as restarted:
        recovered = restarted.get(f"/api/v1/jobs/{job['id']}").json()
        assert recovered["status"] == "failed"
        assert recovered["errorCode"] == "JOB_INTERRUPTED"
        assert recovered["input"]["confirmedCopy"] == IMAGE_COPY
        restarted.post(f"/api/v1/jobs/{job['id']}/retry")
        assert restarted.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "succeeded"


def test_visual_prompt_and_file_validation_keep_text_out_of_model_image() -> None:
    prompt = visual_prompt({"confirmedCopy": IMAGE_COPY, "settings": {"imageType": "公众号头图"}})
    for forbidden in ("不要在图中绘制任何汉字", "数字", "Logo", "二维码", "表格", "水印"):
        assert forbidden in prompt
    assert validate_raster(png_bytes(640, 320), qwen_output=True) == ("image/png", ".png", 640, 320)
    try:
        validate_raster(b"<svg><script>alert(1)</script></svg>")
    except ValueError:
        pass
    else:
        raise AssertionError("SVG/script content must be rejected")


class FakeHttpResponse:
    def __init__(self, status_code: int, payload: dict[str, Any] | None = None, *, content_type: str = "application/json", data: bytes = b""):
        self.status_code = status_code
        self.payload = payload or {}
        self.headers = {"content-type": content_type}
        self.data = data

    def json(self) -> dict[str, Any]:
        return self.payload

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def iter_bytes(self, _: int):
        yield self.data


class FakeQwenHttpClient:
    def __init__(self, responses: list[FakeHttpResponse], calls: list[dict[str, Any]], **_: Any):
        self.responses, self.calls = responses, calls

    def __enter__(self) -> "FakeQwenHttpClient":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def post(self, url: str, **kwargs: Any) -> FakeHttpResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> FakeHttpResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)

    def stream(self, method: str, url: str, **kwargs: Any) -> FakeHttpResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


def qwen_client_factory(responses: list[FakeHttpResponse], calls: list[dict[str, Any]]):
    return lambda **kwargs: FakeQwenHttpClient(responses, calls, **kwargs)


def test_qwen_async_protocol_poll_download_and_request_security(monkeypatch) -> None:
    monkeypatch.setattr("backend.image_generation.time.sleep", lambda _: None)
    data = png_bytes(1024, 1024)
    responses = [
        FakeHttpResponse(200, {"output": {"task_id": "provider-private-id", "task_status": "PENDING"}}),
        FakeHttpResponse(200, {"output": {"task_id": "provider-private-id", "task_status": "RUNNING"}}),
        FakeHttpResponse(200, {"output": {"task_id": "provider-private-id", "task_status": "SUCCEEDED", "choices": [{"message": {"content": [{"image": "https://dashscope-result.oss-cn-beijing.aliyuncs.com/result.png?Expires=safe"}]}}]}}),
        FakeHttpResponse(200, content_type="image/png", data=data),
    ]
    calls: list[dict[str, Any]] = []
    task_ids: list[str] = []
    provider = QwenImageProvider(
        api_key="unit-test-qwen-key", model="qwen-image-3.0",
        base_url="https://workspace.cn-beijing.maas.aliyuncs.com/api/v1", poll_timeout=10,
        client_factory=qwen_client_factory(responses, calls),
    )
    result = provider.generate(
        {"confirmedCopy": IMAGE_COPY, "settings": {"imageType": "营销海报", "audience": "家庭用户"}},
        on_task_created=task_ids.append, is_cancelled=lambda: False,
    )
    assert result.width == 1024 and result.height == 1024 and result.data == data
    assert task_ids == ["provider-private-id"]
    create_call = calls[0]
    assert create_call["url"].endswith("/services/aigc/image-generation/generation")
    assert create_call["headers"]["X-DashScope-Async"] == "enable"
    assert create_call["headers"]["Authorization"] == "Bearer " + "unit-test-qwen-key"
    assert create_call["json"]["model"] == "qwen-image-3.0"
    assert create_call["json"]["parameters"]["size"] == "1024*1024"
    assert create_call["json"]["parameters"]["n"] == 1
    assert all("unit-test-qwen-key" not in json.dumps(call.get("json", {})) for call in calls)
    assert calls[-1]["url"].startswith("https://dashscope-result.oss-cn-beijing.aliyuncs.com/")


def test_qwen_content_block_invalid_result_host_and_cancel(monkeypatch) -> None:
    monkeypatch.setattr("backend.image_generation.time.sleep", lambda _: None)
    blocked_responses = [
        FakeHttpResponse(200, {"output": {"task_id": "blocked", "task_status": "PENDING"}}),
        FakeHttpResponse(200, {"output": {"task_id": "blocked", "task_status": "FAILED", "code": "DataInspectionFailed"}}),
    ]
    provider = QwenImageProvider(api_key="test", base_url="https://workspace.example/api/v1", client_factory=qwen_client_factory(blocked_responses, []))
    try:
        provider.generate({"confirmedCopy": IMAGE_COPY}, on_task_created=lambda _: None, is_cancelled=lambda: False)
    except VisualProviderError as exc:
        assert exc.code == "QWEN_CONTENT_BLOCKED"
    else:
        raise AssertionError("content policy failure must be mapped")

    unsafe_responses = [
        FakeHttpResponse(200, {"output": {"task_id": "unsafe", "task_status": "PENDING"}}),
        FakeHttpResponse(200, {"output": {"task_id": "unsafe", "task_status": "SUCCEEDED", "choices": [{"message": {"content": [{"image": "https://127.0.0.1/private.png"}]}}]}}),
    ]
    provider = QwenImageProvider(api_key="test", base_url="https://workspace.example/api/v1", client_factory=qwen_client_factory(unsafe_responses, []))
    try:
        provider.generate({"confirmedCopy": IMAGE_COPY}, on_task_created=lambda _: None, is_cancelled=lambda: False)
    except VisualProviderError as exc:
        assert exc.code == "QWEN_OUTPUT_INVALID"
    else:
        raise AssertionError("non-Aliyun result hosts must be rejected")

    cancel_calls: list[dict[str, Any]] = []
    cancel_responses = [FakeHttpResponse(200, {"output": {"task_id": "cancel-me", "task_status": "PENDING"}}), FakeHttpResponse(200, {})]
    provider = QwenImageProvider(api_key="test", base_url="https://workspace.example/api/v1", client_factory=qwen_client_factory(cancel_responses, cancel_calls))
    try:
        provider.generate({"confirmedCopy": IMAGE_COPY}, on_task_created=lambda _: None, is_cancelled=lambda: True)
    except VisualCancelled:
        pass
    else:
        raise AssertionError("cancelled local jobs must stop polling")
    assert cancel_calls[-1]["url"].endswith("/tasks/cancel-me/cancel")
