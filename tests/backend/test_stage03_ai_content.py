from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.ai_content import DeepSeekProvider, GroundingError, ProviderError, repair_training_script_grounding, validate_grounding, validate_output
from backend.config import Settings
from backend.main import create_app
from backend.markdown_content import render_markdown_output


OUTPUTS = {
    "image_copy": {"title": "守护每一步", "sellingPoints": ["保障清晰"], "blocks": [{"type": "hero", "text": "重疾保障"}], "disclaimer": "具体以保险合同为准"},
    "article_outline": {"title": "保险知识框架", "sections": [{"heading": "为什么需要", "points": ["风险准备"]}]},
    "article_generate": {"title": "读懂重疾保障", "paragraphs": ["这是一篇严格依据产品事实的文章。"], "disclaimer": "具体以保险合同为准"},
    "training_outline": {"title": "代理人产品培训", "chapters": [{"title": "产品定位", "objective": "理解核心责任"}]},
    "training_script": {"chapters": [{"title": "产品定位", "narration": "本节介绍产品定位。", "knowledgeCard": "核心责任"}], "questions": [
        {"question": "第一题", "options": ["选项甲", "选项乙"], "answer": "选项甲", "explanation": "第一题解析"},
        {"question": "第二题", "options": ["选项甲", "选项乙"], "answer": "选项乙", "explanation": "第二题解析"},
        {"question": "第三题", "options": ["选项甲", "选项乙"], "answer": "选项甲", "explanation": "第三题解析"},
    ]},
}


class FakeProvider:
    name = "fake-deepseek"
    model = "fake-v4"

    def __init__(self, fail_channel: str = ""):
        self.fail_channel = fail_channel
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def generate(self, task_type: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((task_type, snapshot))
        if snapshot.get("channel") == self.fail_channel:
            raise ProviderError("AI_RATE_LIMITED", "模拟单渠道限流")
        return OUTPUTS[task_type]


def make_client(tmp_path: Path, provider: Any = None) -> TestClient:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'stage03.db'}", frontend_dist=tmp_path / "dist", upload_dir=tmp_path / "uploads")
    return TestClient(create_app(settings, provider))


def create_content(client: TestClient, content_type: str, settings: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post("/api/v1/contents", json={"type": content_type, "title": f"{content_type} 测试", "productId": "darwin-12", "settings": settings or {"audience": "保险代理人"}})
    assert response.status_code == 201, response.text
    return response.json()


def create_job(client: TestClient, content_id: str, task_type: str, key: str, branch_id: str | None = None) -> dict[str, Any]:
    response = client.post(f"/api/v1/contents/{content_id}/jobs", json={"taskType": task_type, "branchId": branch_id}, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def test_image_copy_job_idempotency_confirm_and_restart(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        content = create_content(client, "image")
        job = create_job(client, content["id"], "image_copy", "image-copy-1")
        replay = client.post(f"/api/v1/contents/{content['id']}/jobs", json={"taskType": "image_copy"}, headers={"Idempotency-Key": "image-copy-1"})
        assert replay.status_code == 200 and replay.json()["id"] == job["id"]
        completed = client.post(f"/api/v1/jobs/{job['id']}/run")
        assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
        assert completed.json()["attempt"] == 1
        detail = client.get(f"/api/v1/contents/{content['id']}").json()
        assert detail["payload"]["copy"]["title"] == "守护每一步"
        confirmed = client.post(f"/api/v1/contents/{content['id']}/confirm", json={"node": "copy", "revision": detail["revision"]})
        assert confirmed.status_code == 200
        assert confirmed.json()["payload"]["copyStatus"] == "confirmed"
        assert provider.calls[0][1]["product"]["facts"]

    with make_client(tmp_path, FakeProvider()) as restarted:
        restored = restarted.get(f"/api/v1/contents/{content['id']}").json()
        assert restored["payload"]["copyStatus"] == "confirmed"
        assert restored["jobs"][0]["status"] == "succeeded"


def test_article_channels_are_independent_and_retryable(tmp_path: Path) -> None:
    provider = FakeProvider(fail_channel="xiaohongshu")
    with make_client(tmp_path, provider) as client:
        content = create_content(client, "article", {"channels": ["wechat", "xiaohongshu"], "audience": "家庭用户"})
        branches = {branch["channel"]: branch for branch in content["branches"]}
        wechat_job = create_job(client, content["id"], "article_outline", "wechat-outline", branches["wechat"]["id"])
        xhs_job = create_job(client, content["id"], "article_outline", "xhs-outline", branches["xiaohongshu"]["id"])
        assert client.post(f"/api/v1/jobs/{wechat_job['id']}/run").json()["status"] == "succeeded"
        failed = client.post(f"/api/v1/jobs/{xhs_job['id']}/run").json()
        assert failed["status"] == "failed" and failed["errorCode"] == "AI_RATE_LIMITED"
        detail = client.get(f"/api/v1/contents/{content['id']}").json()
        branches = {branch["channel"]: branch for branch in detail["branches"]}
        assert branches["wechat"]["outlineStatus"] == "draft"
        assert branches["xiaohongshu"]["outlineStatus"] == "empty"

        confirmed = client.post(f"/api/v1/contents/{content['id']}/confirm", json={"node": "outline", "branchId": branches["wechat"]["id"], "revision": detail["revision"]}).json()
        wechat = next(branch for branch in confirmed["branches"] if branch["channel"] == "wechat")
        locked_manual = client.put(f"/api/v1/contents/{content['id']}/manual-output", json={"taskType": "article_outline", "branchId": wechat["id"], "revision": confirmed["revision"], "output": OUTPUTS["article_outline"]})
        assert locked_manual.status_code == 409 and locked_manual.json()["error"]["code"] == "CONTENT_NODE_LOCKED"
        locked_job = client.post(f"/api/v1/contents/{content['id']}/jobs", json={"taskType": "article_outline", "branchId": wechat["id"]}, headers={"Idempotency-Key": "locked-outline"})
        assert locked_job.status_code == 409
        article_job = create_job(client, content["id"], "article_generate", "wechat-article", wechat["id"])
        assert client.post(f"/api/v1/jobs/{article_job['id']}/run").json()["status"] == "succeeded"

        provider.fail_channel = ""
        retried = client.post(f"/api/v1/jobs/{xhs_job['id']}/retry").json()
        assert retried["status"] == "succeeded" and retried["attempt"] == 2


def test_training_order_cancel_permissions_and_audit(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeProvider()) as client:
        content = create_content(client, "training", {"duration": 3, "audience": "代理人"})
        early = client.post(f"/api/v1/contents/{content['id']}/jobs", json={"taskType": "training_script"}, headers={"Idempotency-Key": "early-script"})
        assert early.status_code == 422 and early.json()["error"]["code"] == "OUTLINE_NOT_CONFIRMED"
        outline_job = create_job(client, content["id"], "training_outline", "training-outline")
        cancelled = client.post(f"/api/v1/jobs/{outline_job['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        completed = client.post(f"/api/v1/jobs/{outline_job['id']}/retry")
        assert completed.status_code == 200 and completed.json()["status"] == "succeeded"
        detail = client.get(f"/api/v1/contents/{content['id']}").json()
        confirmed = client.post(f"/api/v1/contents/{content['id']}/confirm", json={"node": "outline", "revision": detail["revision"]}).json()
        assert confirmed["payload"]["scriptStatus"] == "empty"
        stale_script = create_job(client, content["id"], "training_script", "stale-training-script")
        assert client.post(f"/api/v1/jobs/{stale_script['id']}/cancel").status_code == 200
        script_job = create_job(client, content["id"], "training_script", "training-script")
        assert client.post(f"/api/v1/jobs/{script_job['id']}/run").json()["status"] == "succeeded"
        latest = client.get(f"/api/v1/contents/{content['id']}").json()
        assert len(latest["payload"]["script"]["questions"]) == 3
        script_confirmed = client.post(f"/api/v1/contents/{content['id']}/confirm", json={"node": "script", "revision": latest["revision"]})
        assert script_confirmed.status_code == 200
        locked_manual = client.put(f"/api/v1/contents/{content['id']}/manual-output", json={"taskType": "training_script", "revision": script_confirmed.json()["revision"], "output": OUTPUTS["training_script"]})
        assert locked_manual.status_code == 409 and locked_manual.json()["error"]["code"] == "CONTENT_NODE_LOCKED"
        locked_retry = client.post(f"/api/v1/jobs/{stale_script['id']}/retry")
        assert locked_retry.status_code == 409 and locked_retry.json()["error"]["code"] == "CONTENT_NODE_LOCKED"
        locked_create = client.post(f"/api/v1/contents/{content['id']}/jobs", json={"taskType": "training_script"}, headers={"Idempotency-Key": "locked-script"})
        assert locked_create.status_code == 409

        forbidden = client.post("/api/v1/contents", json={"type": "image", "title": "越权", "productId": "darwin-12", "settings": {}}, headers={"X-Demo-User": "viewer-demo"})
        assert forbidden.status_code == 403
        actions = [item["action"] for item in client.get("/api/v1/audit-logs").json()["items"]]
        for action in ("content.create", "generation.create", "generation.cancel", "generation.success", "content.confirm.outline", "content.confirm.script"):
            assert action in actions


def test_missing_key_and_source_validation_preserve_input(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with make_client(tmp_path) as client:
        no_source = client.post("/api/v1/contents", json={"type": "image", "title": "无来源", "settings": {}})
        assert no_source.status_code == 422
        draft_product = client.post("/api/v1/products", json={"name": "未确认产品"}).json()
        unconfirmed = client.post("/api/v1/contents", json={"type": "image", "title": "错误产品", "productId": draft_product["id"], "settings": {}})
        assert unconfirmed.status_code == 422 and unconfirmed.json()["error"]["code"] == "PRODUCT_NOT_CONFIRMED"

        content_response = client.post("/api/v1/contents", json={"type": "image", "title": "人工素材内容", "manualSource": "这段人工素材必须保留", "settings": {"purpose": "科普"}})
        content = content_response.json()
        job = create_job(client, content["id"], "image_copy", "missing-key")
        failed = client.post(f"/api/v1/jobs/{job['id']}/run").json()
        assert failed["status"] == "failed"
        assert failed["errorCode"] == "CONFIG_AI_KEY_MISSING"
        assert failed["input"]["manualSource"] == "这段人工素材必须保留"
        serialized = str(client.get(f"/api/v1/contents/{content['id']}").json())
        assert "Bearer" not in serialized and "DEEPSEEK_API_KEY" not in serialized

        invalid_manual = client.put(f"/api/v1/contents/{content['id']}/manual-output", json={"taskType": "image_copy", "revision": content["revision"], "output": {"title": "不完整"}})
        assert invalid_manual.status_code == 422
        saved_manual = client.put(f"/api/v1/contents/{content['id']}/manual-output", json={"taskType": "image_copy", "revision": content["revision"], "output": OUTPUTS["image_copy"]})
        assert saved_manual.status_code == 200
        assert saved_manual.json()["payload"]["copy"]["title"] == "守护每一步"


def test_task_type_branch_and_manual_schema_guards(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeProvider()) as client:
        image = create_content(client, "image")
        mismatch = client.post(
            f"/api/v1/contents/{image['id']}/jobs",
            json={"taskType": "training_outline"},
            headers={"Idempotency-Key": "wrong-content-type"},
        )
        assert mismatch.status_code == 422 and mismatch.json()["error"]["code"] == "AI_TASK_UNSUPPORTED"

        article = create_content(client, "article", {"channels": ["wechat"]})
        missing_branch = client.post(
            f"/api/v1/contents/{article['id']}/jobs",
            json={"taskType": "article_outline"},
            headers={"Idempotency-Key": "missing-branch"},
        )
        assert missing_branch.status_code == 404
        wrong_manual = client.put(
            f"/api/v1/contents/{image['id']}/manual-output",
            json={"taskType": "article_outline", "revision": image["revision"], "output": OUTPUTS["article_outline"]},
        )
        assert wrong_manual.status_code == 422 and wrong_manual.json()["error"]["code"] == "AI_TASK_UNSUPPORTED"

    invalid_outputs = [
        ("image_copy", {**OUTPUTS["image_copy"], "sellingPoints": "不是数组"}),
        ("article_outline", {**OUTPUTS["article_outline"], "sections": [{"heading": "缺少要点"}]}),
        ("training_script", {**OUTPUTS["training_script"], "questions": OUTPUTS["training_script"]["questions"][:2]}),
    ]
    for task_type, output in invalid_outputs:
        with pytest.raises(ValueError):
            validate_output(task_type, output)


class StubResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code, self.payload = status_code, payload

    def json(self) -> dict[str, Any]:
        return self.payload


def deepseek_payload(content: str) -> dict[str, Any]:
    return {"choices": [{"message": {"content": content}}]}


def test_deepseek_markdown_retry_and_request_security(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []
    responses = [
        StubResponse(200, deepseek_payload("")),
        StubResponse(200, deepseek_payload(render_markdown_output("image_copy", OUTPUTS["image_copy"]))),
    ]

    def fake_post(url: str, **kwargs: Any) -> StubResponse:
        calls.append({"url": url, **kwargs})
        return responses.pop(0)

    monkeypatch.setattr("backend.ai_content.httpx.post", fake_post)
    provider = DeepSeekProvider(api_key="secret-not-for-storage", model="deepseek-v4-flash", endpoint="https://api.deepseek.com/chat/completions")
    result = provider.generate("image_copy", {"manualSource": "严格依据素材"})
    assert result["title"] == "守护每一步" and len(calls) == 2
    assert calls[0]["headers"]["Authorization"].split() == ["Bearer", "secret-not-for-storage"]
    assert "response_format" not in calls[0]["json"]
    assert calls[0]["json"]["thinking"] == {"type": "disabled"}
    assert calls[0]["json"]["temperature"] == 0.2
    assert calls[0]["json"]["messages"][1]["content"].startswith('{"source":')
    assert '"numericWhitelist": []' in calls[0]["json"]["messages"][1]["content"]
    assert "Markdown" in calls[0]["json"]["messages"][0]["content"]
    assert "Markdown 标题结构未通过校验" in calls[1]["json"]["messages"][-1]["content"]
    assert "secret-not-for-storage" not in str(calls[0]["json"])


def test_deepseek_training_output_uses_markdown(monkeypatch) -> None:
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> StubResponse:
        calls.append({"url": url, **kwargs})
        return StubResponse(200, deepseek_payload(render_markdown_output("training_outline", OUTPUTS["training_outline"])))

    monkeypatch.setattr("backend.ai_content.httpx.post", fake_post)
    provider = DeepSeekProvider(api_key="training-key-not-for-storage", model="deepseek-v4-flash", endpoint="https://api.deepseek.com/chat/completions")
    result = provider.generate("training_outline", {"manualSource": "培训素材"})
    assert result == OUTPUTS["training_outline"]
    assert "response_format" not in calls[0]["json"]
    assert "只返回 Markdown" in calls[0]["json"]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("effect", "expected_code"),
    [
        (httpx.ReadTimeout("timeout"), "AI_TIMEOUT"),
        (httpx.ConnectError("network"), "AI_NETWORK_ERROR"),
        (StubResponse(429, {}), "AI_RATE_LIMITED"),
        (StubResponse(401, {}), "AI_AUTH_FAILED"),
        (StubResponse(503, {}), "AI_PROVIDER_ERROR"),
    ],
)
def test_deepseek_public_error_mapping(monkeypatch, effect: Any, expected_code: str) -> None:
    def fake_post(*args: Any, **kwargs: Any) -> StubResponse:
        if isinstance(effect, Exception):
            raise effect
        return effect

    monkeypatch.setattr("backend.ai_content.httpx.post", fake_post)
    with pytest.raises(ProviderError) as caught:
        DeepSeekProvider(api_key="server-only-key").generate("image_copy", {"manualSource": "素材"})
    assert caught.value.code == expected_code
    assert "server-only-key" not in caught.value.message


def test_deepseek_rejects_invalid_markdown_after_retry(monkeypatch) -> None:
    attempts = 0

    def invalid_response(*args: Any, **kwargs: Any) -> StubResponse:
        nonlocal attempts
        attempts += 1
        return StubResponse(200, deepseek_payload("not-json"))

    monkeypatch.setattr(
        "backend.ai_content.httpx.post",
        invalid_response,
    )
    with pytest.raises(ProviderError) as caught:
        DeepSeekProvider(api_key="server-only-key").generate("image_copy", {"manualSource": "素材"})
    assert caught.value.code == "AI_OUTPUT_INVALID"
    assert attempts == 3


def test_numeric_grounding_rejects_unconfirmed_claims() -> None:
    snapshot = {
        "product": {
            "summary": "意外重疾可额外赔付",
            "features": ["中症60%基本保额，最多3次"],
            "facts": [{"label": "等待期", "value": "180天"}],
        },
        "settings": {"duration": 3},
    }
    validate_grounding(snapshot, {"title": "中症赔60%", "paragraphs": ["等待期180天，课程时长3分钟"], "disclaimer": "以合同为准"})
    with pytest.raises(GroundingError) as caught:
        validate_grounding(snapshot, {"title": "无来源", "paragraphs": ["重疾120种，赔付100%"], "disclaimer": "以合同为准"})
    assert caught.value.unexpected_numbers == {"120", "100%"}


def test_deepseek_retries_ungrounded_numbers(monkeypatch) -> None:
    ungrounded = {**OUTPUTS["image_copy"], "sellingPoints": ["额外赔付120%"]}
    responses = [
        StubResponse(200, deepseek_payload(render_markdown_output("image_copy", ungrounded))),
        StubResponse(200, deepseek_payload(render_markdown_output("image_copy", OUTPUTS["image_copy"]))),
    ]
    calls: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> StubResponse:
        calls.append(kwargs)
        return responses.pop(0)

    monkeypatch.setattr("backend.ai_content.httpx.post", fake_post)
    result = DeepSeekProvider(api_key="server-only-key").generate("image_copy", {"manualSource": "保障清晰"})
    assert result == OUTPUTS["image_copy"]
    assert len(calls) == 2 and "120%" in calls[1]["json"]["messages"][-1]["content"]
    assert calls[1]["json"]["messages"][-2]["role"] == "assistant"


def test_training_grounding_fallback_removes_numeric_claims_and_rebuilds_questions() -> None:
    snapshot = {
        "product": {
            "name": "达尔文12号",
            "summary": "重疾赔付后轻中症责任仍可继续",
            "facts": [
                {"label": "投保年龄", "value": "28天至55周岁"},
                {"label": "等待期", "value": "180天"},
            ],
        },
        "settings": {"duration": 3},
    }
    unsafe = {
        "chapters": [{"title": "达尔文12号介绍", "narration": "达尔文12号覆盖120种。核心保障延续。", "knowledgeCard": "赔付100%；以合同为准。"}],
        "questions": OUTPUTS["training_script"]["questions"],
    }
    repaired = repair_training_script_grounding(snapshot, unsafe)
    assert "120" not in repaired["chapters"][0]["narration"]
    assert "核心保障延续" in repaired["chapters"][0]["narration"]
    assert "100%" not in repaired["chapters"][0]["knowledgeCard"]
    assert len(repaired["questions"]) == 3
    validate_output("training_script", repaired)
    validate_grounding(snapshot, repaired)
