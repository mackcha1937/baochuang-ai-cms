from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Optional
from uuid import uuid4

from backend.database import Database, now_iso


REVIEW_STAGES = {"product", "compliance"}
OPEN_REVIEW_STATUSES = {"pending", "in_progress"}


@dataclass
class ReviewRepository:
    database: Database

    def list_tasks(self, *, stage: str = "", status: str = "") -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[Any] = []
        if stage:
            conditions.append("task.stage = ?")
            values.append(stage)
        if status:
            conditions.append("task.status = ?")
            values.append(status)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT task.*, item.type AS content_type, item.title AS content_title,
                           branch.channel AS branch_channel, version.version_no
                    FROM review_tasks task
                    JOIN content_items item ON item.id = task.content_id
                    JOIN content_versions version ON version.id = task.content_version_id
                    LEFT JOIN content_branches branch ON branch.id = task.branch_id
                    {where}
                    ORDER BY CASE task.status WHEN 'in_progress' THEN 0 WHEN 'pending' THEN 1 ELSE 2 END,
                             task.updated_at DESC""",
                values,
            ).fetchall()
        return [self._task_summary(row) for row in rows]

    def get_task(self, task_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT task.*, item.type AS content_type, item.title AS content_title,
                          branch.channel AS branch_channel, version.version_no, version.content_revision,
                          version.snapshot_json, version.created_by AS submitted_by,
                          version.created_at AS submitted_at
                   FROM review_tasks task
                   JOIN content_items item ON item.id = task.content_id
                   JOIN content_versions version ON version.id = task.content_version_id
                   LEFT JOIN content_branches branch ON branch.id = task.branch_id
                   WHERE task.id = ?""",
                (task_id,),
            ).fetchone()
            if row is None:
                return None
            records = connection.execute(
                "SELECT * FROM review_records WHERE task_id = ? ORDER BY created_at, rowid",
                (task_id,),
            ).fetchall()
        task = self._task_summary(row)
        task.update({
            "contentRevision": row["content_revision"],
            "snapshot": json.loads(row["snapshot_json"]),
            "submittedBy": row["submitted_by"],
            "submittedAt": row["submitted_at"],
            "records": [self._record(item) for item in records],
        })
        return task

    def submit(
        self,
        actor_id: str,
        content_id: str,
        revision: int,
        branch_id: Optional[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        timestamp = now_iso()
        action = f"content.submit:{content_id}:{branch_id or 'content'}"
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT object_id FROM idempotency_keys WHERE actor_id = ? AND action = ? AND key = ?",
                (actor_id, action, idempotency_key),
            ).fetchone()
            if existing:
                existing_id = existing["object_id"]
            else:
                existing_id = ""
            if existing_id:
                pass
            else:
                item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
                if item is None:
                    raise ValueError("missing")
                if item["revision"] != revision:
                    raise ValueError("conflict")
                branch = None
                if item["type"] == "article":
                    if not branch_id:
                        raise ValueError("branch_required")
                    branch = connection.execute(
                        "SELECT * FROM content_branches WHERE id = ? AND content_id = ?",
                        (branch_id, content_id),
                    ).fetchone()
                    if branch is None:
                        raise ValueError("missing")
                    if branch["review_status"] != "draft":
                        raise ValueError("state")
                elif branch_id is not None:
                    raise ValueError("branch_invalid")
                elif item["status"] != "draft":
                    raise ValueError("state")
                self._validate_submittable(connection, item, branch)
                version_no = connection.execute(
                    "SELECT COALESCE(MAX(version_no), 0) + 1 FROM content_versions WHERE content_id = ?",
                    (content_id,),
                ).fetchone()[0]
                version_id = uuid4().hex
                task_id = uuid4().hex
                snapshot = self._snapshot(connection, item, branch)
                connection.execute(
                    """INSERT INTO content_versions
                       (id, content_id, branch_id, version_no, content_revision, snapshot_json, created_by, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (version_id, content_id, branch_id, version_no, revision, json.dumps(snapshot, ensure_ascii=False), actor_id, timestamp),
                )
                connection.execute(
                    """INSERT INTO review_tasks
                       (id, content_id, branch_id, content_version_id, stage, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'product', 'pending', ?, ?)""",
                    (task_id, content_id, branch_id, version_id, timestamp, timestamp),
                )
                if branch:
                    connection.execute("UPDATE content_branches SET review_status = 'product_review', updated_at = ? WHERE id = ?", (timestamp, branch_id))
                    self._refresh_article_status(connection, content_id, timestamp)
                else:
                    connection.execute(
                        "UPDATE content_items SET status = 'product_review', revision = revision + 1, updated_at = ? WHERE id = ?",
                        (timestamp, content_id),
                    )
                if branch:
                    connection.execute("UPDATE content_items SET revision = revision + 1, updated_at = ? WHERE id = ?", (timestamp, content_id))
                connection.execute(
                    "INSERT INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
                    (actor_id, action, idempotency_key, task_id, timestamp),
                )
                Database._audit(connection, actor_id, "content.submit", "content", content_id, f"提交第 {version_no} 版进入产品审核")
                existing_id = task_id
        result = self.get_task(existing_id)
        assert result is not None
        return result

    def start(self, actor_id: str, task_id: str) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            task = connection.execute("SELECT * FROM review_tasks WHERE id = ?", (task_id,)).fetchone()
            if task is None:
                raise ValueError("missing")
            if task["status"] == "in_progress" and task["assignee_id"] == actor_id:
                pass
            elif task["status"] != "pending":
                raise ValueError("state")
            else:
                connection.execute(
                    "UPDATE review_tasks SET status = 'in_progress', assignee_id = ?, started_at = ?, updated_at = ? WHERE id = ?",
                    (actor_id, timestamp, timestamp, task_id),
                )
                connection.execute(
                    "INSERT INTO review_records VALUES (?, ?, ?, 'start', '', ?)",
                    (uuid4().hex, task_id, actor_id, timestamp),
                )
                Database._audit(connection, actor_id, f"review.{task['stage']}.start", "review", task_id, "开始审核提交版本")
        result = self.get_task(task_id)
        assert result is not None
        return result

    def decide(self, actor_id: str, task_id: str, decision: str, reason: str, idempotency_key: str) -> dict[str, Any]:
        if decision not in {"approve", "reject"}:
            raise ValueError("decision")
        if decision == "reject" and not reason.strip():
            raise ValueError("reason_required")
        timestamp = now_iso()
        action = f"review.decision:{task_id}"
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT object_id FROM idempotency_keys WHERE actor_id = ? AND action = ? AND key = ?",
                (actor_id, action, idempotency_key),
            ).fetchone()
            if existing:
                result_task_id = existing["object_id"]
            else:
                task = connection.execute("SELECT * FROM review_tasks WHERE id = ?", (task_id,)).fetchone()
                if task is None:
                    raise ValueError("missing")
                if task["status"] not in OPEN_REVIEW_STATUSES:
                    raise ValueError("state")
                if task["status"] == "pending":
                    connection.execute(
                        "UPDATE review_tasks SET status = 'in_progress', assignee_id = ?, started_at = ?, updated_at = ? WHERE id = ?",
                        (actor_id, timestamp, timestamp, task_id),
                    )
                    connection.execute(
                        "INSERT INTO review_records VALUES (?, ?, ?, 'start', '', ?)",
                        (uuid4().hex, task_id, actor_id, timestamp),
                    )
                final_status = "approved" if decision == "approve" else "rejected"
                connection.execute(
                    "UPDATE review_tasks SET status = ?, assignee_id = ?, completed_at = ?, updated_at = ? WHERE id = ?",
                    (final_status, actor_id, timestamp, timestamp, task_id),
                )
                connection.execute(
                    "INSERT INTO review_records VALUES (?, ?, ?, ?, ?, ?)",
                    (uuid4().hex, task_id, actor_id, decision, reason.strip(), timestamp),
                )
                if decision == "approve" and task["stage"] == "product":
                    next_task_id = uuid4().hex
                    connection.execute(
                        """INSERT INTO review_tasks
                           (id, content_id, branch_id, content_version_id, stage, status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, 'compliance', 'pending', ?, ?)""",
                        (next_task_id, task["content_id"], task["branch_id"], task["content_version_id"], timestamp, timestamp),
                    )
                    self._set_content_review_status(connection, task, "compliance_review", timestamp)
                    result_task_id = next_task_id
                else:
                    target_status = "approved" if decision == "approve" else f"rejected_{task['stage']}"
                    self._set_content_review_status(connection, task, target_status, timestamp)
                    result_task_id = task_id
                connection.execute(
                    "INSERT INTO idempotency_keys VALUES (?, ?, ?, ?, ?)",
                    (actor_id, action, idempotency_key, result_task_id, timestamp),
                )
                Database._audit(
                    connection,
                    actor_id,
                    f"review.{task['stage']}.{decision}",
                    "review",
                    task_id,
                    "审核通过" if decision == "approve" else f"审核驳回：{reason.strip()}",
                )
        result = self.get_task(result_task_id)
        assert result is not None
        return result

    def withdraw(self, actor_id: str, content_id: str, task_id: str, revision: int) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            task = connection.execute("SELECT * FROM review_tasks WHERE id = ? AND content_id = ?", (task_id, content_id)).fetchone()
            if item is None or task is None:
                raise ValueError("missing")
            if item["revision"] != revision:
                raise ValueError("conflict")
            if task["stage"] != "product" or task["status"] != "pending" or task["started_at"]:
                raise ValueError("state")
            connection.execute("UPDATE review_tasks SET status = 'cancelled', completed_at = ?, updated_at = ? WHERE id = ?", (timestamp, timestamp, task_id))
            connection.execute("INSERT INTO review_records VALUES (?, ?, ?, 'withdraw', '', ?)", (uuid4().hex, task_id, actor_id, timestamp))
            self._set_content_review_status(connection, task, "draft", timestamp)
            connection.execute("UPDATE content_items SET revision = revision + 1, updated_at = ? WHERE id = ?", (timestamp, content_id))
            Database._audit(connection, actor_id, "content.review.withdraw", "content", content_id, "撤回尚未开始的产品审核")
        result = self.get_task(task_id)
        assert result is not None
        return result

    def reopen(self, actor_id: str, content_id: str, revision: int, branch_id: Optional[str]) -> None:
        timestamp = now_iso()
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM content_items WHERE id = ?", (content_id,)).fetchone()
            if item is None:
                raise ValueError("missing")
            if item["revision"] != revision:
                raise ValueError("conflict")
            payload = json.loads(item["payload_json"])
            if item["type"] == "article":
                if not branch_id:
                    raise ValueError("branch_required")
                branch = connection.execute("SELECT * FROM content_branches WHERE id = ? AND content_id = ?", (branch_id, content_id)).fetchone()
                if branch is None:
                    raise ValueError("missing")
                if branch["review_status"] not in {"rejected_product", "rejected_compliance"}:
                    raise ValueError("state")
                connection.execute(
                    "UPDATE content_branches SET body_status = 'draft', review_status = 'draft', updated_at = ? WHERE id = ?",
                    (timestamp, branch_id),
                )
                self._refresh_article_status(connection, content_id, timestamp)
            else:
                if branch_id is not None:
                    raise ValueError("branch_invalid")
                if item["status"] not in {"rejected_product", "rejected_compliance"}:
                    raise ValueError("state")
                if item["type"] == "image":
                    payload["copyStatus"] = "draft"
                    if isinstance(payload.get("visual"), dict):
                        payload["visual"]["status"] = "needs_relayout"
                elif item["type"] == "training":
                    payload["scriptStatus"] = "draft"
                    connection.execute("UPDATE training_sessions SET status = 'stale', updated_at = ? WHERE content_id = ?", (timestamp, content_id))
                connection.execute("UPDATE content_items SET status = 'draft', payload_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), content_id))
            connection.execute("UPDATE content_items SET revision = revision + 1, updated_at = ? WHERE id = ?", (timestamp, content_id))
            Database._audit(connection, actor_id, "content.review.reopen", "content", content_id, "驳回后返回修改并创建新修订")

    def _validate_submittable(self, connection: Any, item: Any, branch: Any) -> None:
        active = connection.execute(
            "SELECT 1 FROM generation_jobs WHERE content_id = ? AND status IN ('queued', 'running') AND (? IS NULL OR branch_id = ?) LIMIT 1",
            (item["id"], branch["id"] if branch else None, branch["id"] if branch else None),
        ).fetchone()
        if active:
            raise ValueError("jobs_active")
        payload = json.loads(item["payload_json"])
        if item["type"] == "image":
            visual = payload.get("visual")
            if payload.get("copyStatus") != "confirmed" or not isinstance(visual, dict) or visual.get("status") != "draft":
                raise ValueError("incomplete")
            asset = connection.execute(
                "SELECT 1 FROM visual_assets WHERE id = ? AND content_id = ? AND status = 'ready'",
                (visual.get("assetId"), item["id"]),
            ).fetchone()
            if not asset:
                raise ValueError("incomplete")
        elif item["type"] == "article":
            if branch is None or branch["outline_status"] != "confirmed" or branch["body_status"] != "confirmed":
                raise ValueError("incomplete")
            if not json.loads(branch["body_json"]):
                raise ValueError("incomplete")
        elif item["type"] == "training":
            script = payload.get("script")
            if payload.get("scriptStatus") != "confirmed" or not isinstance(script, dict):
                raise ValueError("incomplete")
            chapters, questions = script.get("chapters"), script.get("questions")
            if not isinstance(chapters, list) or not chapters or not isinstance(questions, list) or len(questions) != 3:
                raise ValueError("incomplete")
            if any(
                not isinstance(chapter, dict)
                or not all(isinstance(chapter.get(key), str) and chapter[key].strip() for key in ("title", "narration", "knowledgeCard"))
                for chapter in chapters
            ):
                raise ValueError("incomplete")
            if any(
                not isinstance(question, dict)
                or not isinstance(question.get("options"), list)
                or len(question["options"]) < 2
                or not all(isinstance(option, str) and option.strip() for option in question["options"])
                or not isinstance(question.get("answer"), str)
                or question["answer"] not in question["options"]
                or not isinstance(question.get("question"), str)
                or not question["question"].strip()
                or not isinstance(question.get("explanation"), str)
                or not question["explanation"].strip()
                for question in questions
            ):
                raise ValueError("incomplete")
        else:
            raise ValueError("type")

    @staticmethod
    def _snapshot(connection: Any, item: Any, branch: Any) -> dict[str, Any]:
        product = None
        if item["product_id"]:
            row = connection.execute("SELECT id, name, insurer, category, version, status FROM products WHERE id = ?", (item["product_id"],)).fetchone()
            product = dict(row) if row else None
        snapshot: dict[str, Any] = {
            "content": {
                "id": item["id"], "type": item["type"], "title": item["title"],
                "productId": item["product_id"], "manualSource": item["manual_source"],
                "settings": json.loads(item["settings_json"]), "revision": item["revision"],
            },
            "sourceProduct": product,
        }
        if branch:
            snapshot["branch"] = {
                "id": branch["id"], "channel": branch["channel"],
                "outline": json.loads(branch["outline_json"]), "body": json.loads(branch["body_json"]),
            }
        else:
            snapshot["payload"] = json.loads(item["payload_json"])
        return snapshot

    def _set_content_review_status(self, connection: Any, task: Any, status: str, timestamp: str) -> None:
        if task["branch_id"]:
            connection.execute("UPDATE content_branches SET review_status = ?, updated_at = ? WHERE id = ?", (status, timestamp, task["branch_id"]))
            self._refresh_article_status(connection, task["content_id"], timestamp)
        else:
            connection.execute("UPDATE content_items SET status = ?, updated_at = ? WHERE id = ?", (status, timestamp, task["content_id"]))

    @staticmethod
    def _refresh_article_status(connection: Any, content_id: str, timestamp: str) -> None:
        statuses = [row[0] for row in connection.execute("SELECT review_status FROM content_branches WHERE content_id = ?", (content_id,)).fetchall()]
        if statuses and all(status == "approved" for status in statuses):
            aggregate = "approved"
        elif "compliance_review" in statuses:
            aggregate = "compliance_review"
        elif "product_review" in statuses:
            aggregate = "product_review"
        elif "rejected_compliance" in statuses:
            aggregate = "rejected_compliance"
        elif "rejected_product" in statuses:
            aggregate = "rejected_product"
        else:
            aggregate = "draft"
        connection.execute("UPDATE content_items SET status = ?, updated_at = ? WHERE id = ?", (aggregate, timestamp, content_id))

    @staticmethod
    def _task_summary(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "contentId": row["content_id"], "branchId": row["branch_id"],
            "contentVersionId": row["content_version_id"], "contentVersion": row["version_no"],
            "contentType": row["content_type"], "contentTitle": row["content_title"],
            "branchChannel": row["branch_channel"], "stage": row["stage"], "status": row["status"],
            "assigneeId": row["assignee_id"], "startedAt": row["started_at"],
            "completedAt": row["completed_at"], "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _record(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "taskId": row["task_id"], "actorId": row["actor_id"],
            "decision": row["decision"], "reason": row["reason"], "createdAt": row["created_at"],
        }
