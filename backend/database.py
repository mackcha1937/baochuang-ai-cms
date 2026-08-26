from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from backend.seed import BRAND, DEMO_USER, PERMISSIONS, PRODUCT, VIEWER_USER


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS brands (id TEXT PRIMARY KEY, company_name TEXT NOT NULL, product_name TEXT NOT NULL, primary_color TEXT NOT NULL, secondary_color TEXT NOT NULL, tone TEXT NOT NULL, slogan TEXT NOT NULL, disclaimer TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL, roles_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user_permissions (user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE, permission_code TEXT NOT NULL, PRIMARY KEY (user_id, permission_code));
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, insurer TEXT NOT NULL, category TEXT NOT NULL,
    version TEXT NOT NULL, status TEXT NOT NULL, source_type TEXT NOT NULL, summary TEXT NOT NULL,
    updated_at TEXT NOT NULL, owner_id TEXT NOT NULL DEFAULT 'super-demo', revision INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT '', confirmed_at TEXT
);
CREATE TABLE IF NOT EXISTS product_features (product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE, position INTEGER NOT NULL, value TEXT NOT NULL, PRIMARY KEY (product_id, position));
CREATE TABLE IF NOT EXISTS product_facts (
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE, position INTEGER NOT NULL,
    label TEXT NOT NULL, value TEXT NOT NULL, confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    required INTEGER NOT NULL DEFAULT 1 CHECK (required IN (0, 1)), PRIMARY KEY (product_id, position)
);
CREATE TABLE IF NOT EXISTS product_documents (product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE, position INTEGER NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL, PRIMARY KEY (product_id, position));
CREATE TABLE IF NOT EXISTS source_documents (
    id TEXT PRIMARY KEY, product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    original_name TEXT NOT NULL, safe_name TEXT NOT NULL, storage_key TEXT NOT NULL UNIQUE,
    extension TEXT NOT NULL, mime_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, checksum TEXT NOT NULL,
    status TEXT NOT NULL, parse_error TEXT NOT NULL DEFAULT '', extracted_text TEXT NOT NULL DEFAULT '',
    structure_json TEXT NOT NULL DEFAULT '{}', manual_text TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (actor_id TEXT NOT NULL, action TEXT NOT NULL, key TEXT NOT NULL, object_id TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY (actor_id, action, key));
CREATE TABLE IF NOT EXISTS audit_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, actor_id TEXT NOT NULL, action TEXT NOT NULL, object_type TEXT NOT NULL, object_id TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS content_items (
    id TEXT PRIMARY KEY, type TEXT NOT NULL, title TEXT NOT NULL,
    product_id TEXT REFERENCES products(id), manual_source TEXT NOT NULL DEFAULT '',
    settings_json TEXT NOT NULL DEFAULT '{}', payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, owner_id TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_branches (
    id TEXT PRIMARY KEY, content_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    channel TEXT NOT NULL, outline_json TEXT NOT NULL DEFAULT '{}', outline_status TEXT NOT NULL DEFAULT 'empty',
    body_json TEXT NOT NULL DEFAULT '{}', body_status TEXT NOT NULL DEFAULT 'empty', updated_at TEXT NOT NULL,
    UNIQUE(content_id, channel)
);
CREATE TABLE IF NOT EXISTS generation_jobs (
    id TEXT PRIMARY KEY, content_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    branch_id TEXT REFERENCES content_branches(id) ON DELETE CASCADE, task_type TEXT NOT NULL,
    status TEXT NOT NULL, input_json TEXT NOT NULL, output_json TEXT NOT NULL DEFAULT '{}',
    public_error TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT '', attempt INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL DEFAULT '', provider TEXT NOT NULL DEFAULT 'deepseek', model TEXT NOT NULL DEFAULT '',
    provider_task_id TEXT NOT NULL DEFAULT '', cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(content_id, task_type, idempotency_key)
);
CREATE TABLE IF NOT EXISTS visual_assets (
    id TEXT PRIMARY KEY, content_id TEXT NOT NULL REFERENCES content_items(id) ON DELETE CASCADE,
    job_id TEXT REFERENCES generation_jobs(id) ON DELETE SET NULL, source TEXT NOT NULL,
    original_name TEXT NOT NULL DEFAULT '', storage_key TEXT NOT NULL UNIQUE, mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL, checksum TEXT NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready', created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_products_updated ON products(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_documents_product ON source_documents(product_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_logs_time ON audit_logs(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_content_items_updated ON content_items(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_content ON generation_jobs(content_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_visual_assets_content ON visual_assets(content_id, created_at DESC);
"""

DEFAULT_FACTS = ["投保年龄", "保障期间", "等待期", "缴费期间"]


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_stage01(connection)
            self._migrate_stage04(connection)
            self._seed(connection)

    @staticmethod
    def _migrate_stage01(connection: sqlite3.Connection) -> None:
        product_columns = {row[1] for row in connection.execute("PRAGMA table_info(products)")}
        migrations = {
            "owner_id": "ALTER TABLE products ADD COLUMN owner_id TEXT NOT NULL DEFAULT 'super-demo'",
            "revision": "ALTER TABLE products ADD COLUMN revision INTEGER NOT NULL DEFAULT 1",
            "created_at": "ALTER TABLE products ADD COLUMN created_at TEXT NOT NULL DEFAULT ''",
            "confirmed_at": "ALTER TABLE products ADD COLUMN confirmed_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in product_columns:
                connection.execute(statement)
        fact_columns = {row[1] for row in connection.execute("PRAGMA table_info(product_facts)")}
        if "required" not in fact_columns:
            connection.execute("ALTER TABLE product_facts ADD COLUMN required INTEGER NOT NULL DEFAULT 1")
        connection.execute("UPDATE products SET created_at = updated_at WHERE created_at = ''")

    @staticmethod
    def _migrate_stage04(connection: sqlite3.Connection) -> None:
        job_columns = {row[1] for row in connection.execute("PRAGMA table_info(generation_jobs)")}
        if "provider_task_id" not in job_columns:
            connection.execute("ALTER TABLE generation_jobs ADD COLUMN provider_task_id TEXT NOT NULL DEFAULT ''")
        connection.execute(
            """UPDATE generation_jobs
               SET status = 'failed', error_code = 'JOB_INTERRUPTED',
                   public_error = '服务重启中断了生成任务，输入已保留，可重新尝试', updated_at = ?
               WHERE status = 'running'""",
            (now_iso(),),
        )

    @staticmethod
    def _seed(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO brands VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (BRAND["id"], BRAND["companyName"], BRAND["productName"], BRAND["primaryColor"], BRAND["secondaryColor"], BRAND["tone"], BRAND["slogan"], BRAND["disclaimer"]),
        )
        for user in (DEMO_USER, VIEWER_USER):
            connection.execute("INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)", (user["id"], user["name"], user["mode"], json.dumps(user["roles"], ensure_ascii=False)))
        connection.executemany("INSERT OR IGNORE INTO user_permissions VALUES (?, ?)", [(DEMO_USER["id"], permission) for permission in PERMISSIONS])
        connection.execute(
            """INSERT OR IGNORE INTO products
               (id, name, insurer, category, version, status, source_type, summary, updated_at, owner_id, revision, created_at, confirmed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(PRODUCT[key] for key in ("id", "name", "insurer", "category", "version", "status", "sourceType", "summary", "updatedAt")) + (DEMO_USER["id"], 1, PRODUCT["updatedAt"], PRODUCT["updatedAt"]),
        )
        connection.executemany("INSERT OR IGNORE INTO product_features VALUES (?, ?, ?)", [(PRODUCT["id"], index, value) for index, value in enumerate(PRODUCT["features"])])
        connection.executemany(
            "INSERT OR IGNORE INTO product_facts (product_id, position, label, value, confirmed, required) VALUES (?, ?, ?, ?, ?, ?)",
            [(PRODUCT["id"], index, item["label"], item["value"], int(item["confirmed"]), 1) for index, item in enumerate(PRODUCT["facts"])],
        )
        connection.executemany("INSERT OR IGNORE INTO product_documents VALUES (?, ?, ?, ?, ?)", [(PRODUCT["id"], index, item["name"], item["type"], item["status"]) for index, item in enumerate(PRODUCT["documents"])])
        if connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0:
            Database._audit(connection, DEMO_USER["id"], "seed.create", "product", PRODUCT["id"], "创建预置达尔文12号演示产品")

    @staticmethod
    def _audit(connection: sqlite3.Connection, actor_id: str, action: str, object_type: str, object_id: str, detail: str) -> None:
        connection.execute("INSERT INTO audit_logs (occurred_at, actor_id, action, object_type, object_id, detail) VALUES (?, ?, ?, ?, ?, ?)", (now_iso(), actor_id, action, object_type, object_id, detail[:500]))

    def audit(self, actor_id: str, action: str, object_type: str, object_id: str, detail: str) -> None:
        with self.connect() as connection:
            self._audit(connection, actor_id, action, object_type, object_id, detail)

    def get_brand(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM brands LIMIT 1").fetchone()
        assert row is not None
        return {"id": row["id"], "companyName": row["company_name"], "productName": row["product_name"], "primaryColor": row["primary_color"], "secondaryColor": row["secondary_color"], "tone": row["tone"], "slogan": row["slogan"], "disclaimer": row["disclaimer"]}

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            if row is None:
                return None
            permissions = [item[0] for item in connection.execute("SELECT permission_code FROM user_permissions WHERE user_id = ? ORDER BY permission_code", (user_id,)).fetchall()]
        return {"id": row["id"], "name": row["name"], "mode": row["mode"], "roles": json.loads(row["roles_json"]), "permissions": permissions}

    def get_demo_user(self) -> dict[str, Any]:
        user = self.get_user(DEMO_USER["id"])
        assert user is not None
        return user

    def list_products(self, query: str = "") -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM products", ()
        if query:
            sql += " WHERE name LIKE ? OR insurer LIKE ? OR category LIKE ?"
            wildcard = f"%{query}%"
            params = (wildcard, wildcard, wildcard)
        sql += " ORDER BY updated_at DESC, name ASC"
        with self.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [self._product_summary(row) for row in rows]

    def get_product(self, product_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if row is None:
                return None
            features = [item[0] for item in connection.execute("SELECT value FROM product_features WHERE product_id = ? ORDER BY position", (product_id,)).fetchall()]
            facts = [dict(item) for item in connection.execute("SELECT label, value, confirmed, required FROM product_facts WHERE product_id = ? ORDER BY position", (product_id,)).fetchall()]
            legacy_documents = [dict(item) for item in connection.execute("SELECT name, type, status FROM product_documents WHERE product_id = ? ORDER BY position", (product_id,)).fetchall()]
            source_documents = [self._document_dict(item) for item in connection.execute("SELECT * FROM source_documents WHERE product_id = ? ORDER BY created_at DESC", (product_id,)).fetchall()]
        product = self._product_summary(row)
        product.update({"summary": row["summary"], "features": features, "facts": [{**item, "confirmed": bool(item["confirmed"]), "required": bool(item["required"])} for item in facts], "documents": [{**item, "id": None, "legacy": True} for item in legacy_documents] + source_documents, "revision": row["revision"], "ownerId": row["owner_id"], "createdAt": row["created_at"], "confirmedAt": row["confirmed_at"]})
        return product

    def create_product(self, actor_id: str, name: str, idempotency_key: str = "") -> tuple[dict[str, Any], bool]:
        timestamp = now_iso()
        with self.connect() as connection:
            existing = connection.execute("SELECT object_id FROM idempotency_keys WHERE actor_id = ? AND action = 'product.create' AND key = ?", (actor_id, idempotency_key)).fetchone() if idempotency_key else None
            object_id, created = (existing["object_id"], False) if existing else (uuid4().hex, True)
            if created:
                connection.execute("""INSERT INTO products (id, name, insurer, category, version, status, source_type, summary, updated_at, owner_id, revision, created_at, confirmed_at) VALUES (?, ?, '', '', 'V1', '草稿', '用户上传', '', ?, ?, 1, ?, NULL)""", (object_id, name, timestamp, actor_id, timestamp))
                connection.executemany("INSERT INTO product_facts (product_id, position, label, value, confirmed, required) VALUES (?, ?, ?, '', 0, 1)", [(object_id, index, label) for index, label in enumerate(DEFAULT_FACTS)])
                if idempotency_key:
                    connection.execute("INSERT INTO idempotency_keys VALUES (?, 'product.create', ?, ?, ?)", (actor_id, idempotency_key, object_id, timestamp))
                self._audit(connection, actor_id, "product.create", "product", object_id, f"创建产品草稿：{name}")
        product = self.get_product(object_id)
        assert product is not None
        return product, created

    def product_name_exists(self, name: str) -> bool:
        with self.connect() as connection:
            return connection.execute("SELECT 1 FROM products WHERE lower(name) = lower(?) LIMIT 1", (name,)).fetchone() is not None

    def update_product(self, actor_id: str, product_id: str, revision: int, fields: dict[str, str], facts: list[dict[str, Any]]) -> str:
        timestamp = now_iso()
        with self.connect() as connection:
            row = connection.execute("SELECT revision, status FROM products WHERE id = ?", (product_id,)).fetchone()
            if row is None: return "missing"
            if row["status"] == "已确认": return "confirmed"
            if row["revision"] != revision: return "conflict"
            connection.execute("UPDATE products SET name = ?, insurer = ?, category = ?, summary = ?, updated_at = ?, revision = revision + 1 WHERE id = ?", (fields["name"], fields["insurer"], fields["category"], fields["summary"], timestamp, product_id))
            connection.execute("DELETE FROM product_facts WHERE product_id = ?", (product_id,))
            connection.executemany("INSERT INTO product_facts (product_id, position, label, value, confirmed, required) VALUES (?, ?, ?, ?, ?, ?)", [(product_id, index, item["label"], item["value"], int(item["confirmed"]), int(item.get("required", True))) for index, item in enumerate(facts)])
            self._audit(connection, actor_id, "product.update", "product", product_id, "更新产品字段和关键事实")
        return "updated"

    def confirm_product(self, actor_id: str, product_id: str, revision: int) -> tuple[str, list[str]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if row is None: return "missing", []
            if row["status"] == "已确认": return "confirmed", []
            if row["revision"] != revision: return "conflict", []
            missing = [label for label, column in (("产品名称", "name"), ("保险公司", "insurer"), ("产品类别", "category"), ("产品摘要", "summary")) if not row[column].strip()]
            for fact in connection.execute("SELECT label, value, confirmed FROM product_facts WHERE product_id = ? AND required = 1 ORDER BY position", (product_id,)).fetchall():
                if not fact["value"].strip() or not fact["confirmed"]: missing.append(f"关键字段：{fact['label']}")
            if connection.execute("SELECT 1 FROM source_documents WHERE product_id = ? AND (status = 'parsed' OR length(trim(manual_text)) > 0) LIMIT 1", (product_id,)).fetchone() is None:
                missing.append("至少一份已解析资料或人工粘贴原文")
            if missing: return "invalid", missing
            timestamp = now_iso()
            connection.execute("UPDATE products SET status = '已确认', confirmed_at = ?, updated_at = ?, revision = revision + 1 WHERE id = ?", (timestamp, timestamp, product_id))
            self._audit(connection, actor_id, "product.confirm", "product", product_id, "确认产品关键字段与资料来源")
        return "confirmed_now", []

    def delete_draft(self, actor_id: str, product_id: str) -> tuple[str, list[str]]:
        with self.connect() as connection:
            row = connection.execute("SELECT status FROM products WHERE id = ?", (product_id,)).fetchone()
            if row is None: return "missing", []
            if row["status"] == "已确认": return "confirmed", []
            storage_keys = [item[0] for item in connection.execute("SELECT storage_key FROM source_documents WHERE product_id = ?", (product_id,)).fetchall()]
            self._audit(connection, actor_id, "product.delete", "product", product_id, "删除未确认产品草稿")
            connection.execute("DELETE FROM products WHERE id = ?", (product_id,))
        return "deleted", storage_keys

    def create_document(self, actor_id: str, product_id: str, values: dict[str, Any]) -> Optional[dict[str, Any]]:
        timestamp = now_iso()
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone() is None: return None
            connection.execute("""INSERT INTO source_documents (id, product_id, original_name, safe_name, storage_key, extension, mime_type, size_bytes, checksum, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)""", (values["id"], product_id, values["original_name"], values["safe_name"], values["storage_key"], values["extension"], values["mime_type"], values["size_bytes"], values["checksum"], timestamp, timestamp))
            connection.execute("UPDATE products SET updated_at = ?, revision = revision + 1 WHERE id = ?", (timestamp, product_id))
            self._audit(connection, actor_id, "document.upload", "document", values["id"], f"上传 {values['extension'][1:].upper()} 文件：{values['safe_name']}（{values['size_bytes']} bytes）")
        return self.get_document(product_id, values["id"])

    def get_document(self, product_id: str, document_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM source_documents WHERE id = ? AND product_id = ?", (document_id, product_id)).fetchone()
        return self._document_dict(row) if row else None

    def get_document_storage_key(self, product_id: str, document_id: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT storage_key FROM source_documents WHERE id = ? AND product_id = ?",
                (document_id, product_id),
            ).fetchone()
        return row["storage_key"] if row else None

    def set_document_status(self, actor_id: str, product_id: str, document_id: str, status: str, *, parse_error: str = "", extracted_text: str = "", structure: Optional[dict[str, Any]] = None, increment_attempt: bool = False, audit_action: Optional[str] = None) -> Optional[dict[str, Any]]:
        timestamp = now_iso()
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM source_documents WHERE id = ? AND product_id = ?", (document_id, product_id)).fetchone() is None: return None
            connection.execute("UPDATE source_documents SET status = ?, parse_error = ?, extracted_text = ?, structure_json = ?, attempt = attempt + ?, updated_at = ? WHERE id = ? AND product_id = ?", (status, parse_error[:500], extracted_text, json.dumps(structure or {}, ensure_ascii=False), int(increment_attempt), timestamp, document_id, product_id))
            connection.execute("UPDATE products SET updated_at = ?, revision = revision + 1 WHERE id = ?", (timestamp, product_id))
            if audit_action: self._audit(connection, actor_id, audit_action, "document", document_id, f"文件状态更新为 {status}")
        return self.get_document(product_id, document_id)

    def save_manual_text(self, actor_id: str, product_id: str, document_id: str, text: str) -> Optional[dict[str, Any]]:
        timestamp = now_iso()
        with self.connect() as connection:
            if connection.execute("SELECT 1 FROM source_documents WHERE id = ? AND product_id = ?", (document_id, product_id)).fetchone() is None: return None
            status = "manual_ready" if text.strip() else "needs_manual"
            connection.execute("UPDATE source_documents SET manual_text = ?, status = ?, parse_error = '', updated_at = ? WHERE id = ?", (text, status, timestamp, document_id))
            connection.execute("UPDATE products SET updated_at = ?, revision = revision + 1 WHERE id = ?", (timestamp, product_id))
            self._audit(connection, actor_id, "document.manual", "document", document_id, "保存人工粘贴原文兜底")
        return self.get_document(product_id, document_id)

    def delete_document(self, actor_id: str, product_id: str, document_id: str) -> Optional[str]:
        with self.connect() as connection:
            row = connection.execute("SELECT storage_key FROM source_documents WHERE id = ? AND product_id = ?", (document_id, product_id)).fetchone()
            if row is None: return None
            storage_key = row["storage_key"]
            self._audit(connection, actor_id, "document.delete", "document", document_id, "删除产品源文件及解析记录")
            connection.execute("DELETE FROM source_documents WHERE id = ?", (document_id,))
            connection.execute("UPDATE products SET updated_at = ?, revision = revision + 1 WHERE id = ?", (now_iso(), product_id))
        return storage_key

    def get_stats(self) -> dict[str, int]:
        with self.connect() as connection: product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        return {"products": product_count, "pendingReviews": 0, "approvedAssets": 0, "possiblyOutdated": 0}

    def get_recent_audit(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection: rows = connection.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _product_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "name": row["name"], "insurer": row["insurer"], "category": row["category"], "version": row["version"], "status": row["status"], "sourceType": row["source_type"], "updatedAt": row["updated_at"]}

    @staticmethod
    def _document_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "name": row["safe_name"], "originalName": row["original_name"], "type": row["extension"][1:].upper(), "mimeType": row["mime_type"], "sizeBytes": row["size_bytes"], "status": row["status"], "parseError": row["parse_error"], "attempt": row["attempt"], "extractedText": row["extracted_text"], "structure": json.loads(row["structure_json"] or "{}"), "manualText": row["manual_text"], "createdAt": row["created_at"], "updatedAt": row["updated_at"], "legacy": False}
