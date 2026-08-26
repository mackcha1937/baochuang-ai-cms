from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.backend.test_stage03_ai_content import FakeProvider, OUTPUTS, create_content, create_job, make_client


def confirmed_training(client: TestClient) -> dict:
    content = create_content(client, "training", {"duration": 3, "audience": "保险代理人"})
    outline_job = create_job(client, content["id"], "training_outline", "stage05-outline")
    assert client.post(f"/api/v1/jobs/{outline_job['id']}/run").json()["status"] == "succeeded"
    detail = client.get(f"/api/v1/contents/{content['id']}").json()
    outline_confirmed = client.post(
        f"/api/v1/contents/{content['id']}/confirm",
        json={"node": "outline", "revision": detail["revision"]},
    ).json()
    script_job = create_job(client, content["id"], "training_script", "stage05-script")
    assert client.post(f"/api/v1/jobs/{script_job['id']}/run").json()["status"] == "succeeded"
    detail = client.get(f"/api/v1/contents/{content['id']}").json()
    response = client.post(
        f"/api/v1/contents/{content['id']}/confirm",
        json={"node": "script", "revision": detail["revision"]},
    )
    assert response.status_code == 200, response.text
    assert outline_confirmed["payload"]["outlineStatus"] == "confirmed"
    return response.json()


def test_training_progress_quiz_reset_and_restart(tmp_path: Path) -> None:
    provider = FakeProvider()
    with make_client(tmp_path, provider) as client:
        draft = create_content(client, "training")
        blocked = client.patch(
            f"/api/v1/contents/{draft['id']}/training-session",
            json={"revision": draft["revision"], "chapterIndex": 0, "sentenceIndex": 0, "playbackRate": 1, "mode": "voice", "answers": {}},
        )
        assert blocked.status_code == 422
        assert blocked.json()["error"]["code"] == "TRAINING_SCRIPT_NOT_CONFIRMED"

        content = confirmed_training(client)
        assert content["trainingSession"]["status"] == "ready"
        assert content["trainingSession"]["attempt"] == 0

        saved = client.patch(
            f"/api/v1/contents/{content['id']}/training-session",
            json={
                "revision": content["revision"],
                "chapterIndex": 0,
                "sentenceIndex": 0,
                "playbackRate": 1.25,
                "mode": "silent",
                "answers": {"0": "选项甲"},
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["trainingSession"] | {"updatedAt": ""} == {
            "chapterIndex": 0,
            "sentenceIndex": 0,
            "playbackRate": 1.25,
            "mode": "silent",
            "answers": {"0": "选项甲"},
            "lastScore": None,
            "results": [],
            "attempt": 0,
            "status": "ready",
            "updatedAt": "",
        }

        invalid_progress = client.patch(
            f"/api/v1/contents/{content['id']}/training-session",
            json={"revision": content["revision"], "chapterIndex": 99, "sentenceIndex": 0, "playbackRate": 1, "mode": "voice", "answers": {}},
        )
        assert invalid_progress.status_code == 422
        assert invalid_progress.json()["error"]["code"] == "TRAINING_PROGRESS_INVALID"

        incomplete = client.post(
            f"/api/v1/contents/{content['id']}/training-quiz/submit",
            json={"revision": content["revision"], "answers": ["选项甲", "", "选项甲"]},
        )
        assert incomplete.status_code == 422
        assert incomplete.json()["error"]["code"] == "TRAINING_ANSWERS_INCOMPLETE"

        invalid_answer = client.post(
            f"/api/v1/contents/{content['id']}/training-quiz/submit",
            json={"revision": content["revision"], "answers": ["伪造选项", "选项乙", "选项甲"]},
        )
        assert invalid_answer.status_code == 422
        assert invalid_answer.json()["error"]["code"] == "TRAINING_ANSWERS_INVALID"

        submitted = client.post(
            f"/api/v1/contents/{content['id']}/training-quiz/submit",
            json={"revision": content["revision"], "answers": ["选项甲", "选项甲", "选项甲"]},
        )
        assert submitted.status_code == 200, submitted.text
        session = submitted.json()["trainingSession"]
        assert session["lastScore"] == 2 and session["attempt"] == 1 and session["status"] == "completed"
        assert len(session["results"]) == 3
        assert session["results"][1]["correct"] is False
        assert session["results"][1]["correctAnswer"] == "选项乙"

        reset = client.post(
            f"/api/v1/contents/{content['id']}/training-quiz/reset",
            json={"revision": content["revision"]},
        )
        assert reset.status_code == 200
        assert reset.json()["trainingSession"]["answers"] == {}
        assert reset.json()["trainingSession"]["results"] == []
        assert reset.json()["trainingSession"]["attempt"] == 1
        actions = [item["action"] for item in client.get("/api/v1/audit-logs").json()["items"]]
        for action in ("training.progress.save", "training.quiz.submit", "training.quiz.reset"):
            assert action in actions

    with make_client(tmp_path, FakeProvider()) as restarted:
        restored = restarted.get(f"/api/v1/contents/{content['id']}").json()
        assert restored["trainingSession"]["mode"] == "silent"
        assert restored["trainingSession"]["playbackRate"] == 1.25
        assert restored["trainingSession"]["attempt"] == 1


def test_training_permissions_revision_unlock_and_audit(tmp_path: Path) -> None:
    with make_client(tmp_path, FakeProvider()) as client:
        content = confirmed_training(client)
        session_payload = {"revision": content["revision"], "chapterIndex": 0, "sentenceIndex": 0, "playbackRate": 1, "mode": "voice", "answers": {}}

        forbidden = client.patch(
            f"/api/v1/contents/{content['id']}/training-session",
            json=session_payload,
            headers={"X-Demo-User": "viewer-demo"},
        )
        assert forbidden.status_code == 403
        conflict = client.patch(
            f"/api/v1/contents/{content['id']}/training-session",
            json={**session_payload, "revision": content["revision"] - 1},
        )
        assert conflict.status_code == 409

        image = create_content(client, "image")
        wrong_type = client.patch(
            f"/api/v1/contents/{image['id']}/training-session",
            json={"revision": image["revision"], "chapterIndex": 0, "sentenceIndex": 0, "playbackRate": 1, "mode": "voice", "answers": {}},
        )
        assert wrong_type.status_code == 422
        assert wrong_type.json()["error"]["code"] == "CONTENT_TYPE_INVALID"

        unlocked = client.post(
            f"/api/v1/contents/{content['id']}/unlock-script",
            json={"revision": content["revision"]},
        )
        assert unlocked.status_code == 200
        assert unlocked.json()["payload"]["scriptStatus"] == "draft"
        assert unlocked.json()["trainingSession"]["status"] == "stale"

        blocked = client.patch(
            f"/api/v1/contents/{content['id']}/training-session",
            json={**session_payload, "revision": unlocked.json()["revision"]},
        )
        assert blocked.status_code == 422
        assert blocked.json()["error"]["code"] == "TRAINING_SCRIPT_NOT_CONFIRMED"

        reconfirmed = client.post(
            f"/api/v1/contents/{content['id']}/confirm",
            json={"node": "script", "revision": unlocked.json()["revision"]},
        )
        assert reconfirmed.status_code == 200
        assert reconfirmed.json()["trainingSession"]["status"] == "ready"
        assert reconfirmed.json()["trainingSession"]["attempt"] == 0

        actions = [item["action"] for item in client.get("/api/v1/audit-logs").json()["items"]]
        assert "permission.denied" in actions
        assert "content.unlock.script" in actions
