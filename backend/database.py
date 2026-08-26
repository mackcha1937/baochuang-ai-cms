from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from backend.seed import BRAND, DEMO_USER, PERMISSIONS, PRODUCT


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS brands (
    id TEXT PRIMARY KEY,
    company_name TEXT NOT NULL,
    product_name TEXT NOT NULL,
    primary_color TEXT NOT NULL,
    secondary_color TEXT NOT NULL,
    tone TEXT NOT NULL,
    slogan TEXT NOT NULL,
    disclaimer TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    mode TEXT NOT NULL,
    roles_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_permissions (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission_code TEXT NOT NULL,
    PRIMARY KEY (user_id, permission_code)
);

CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    insurer TEXT NOT NULL,
    category TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL,
    source_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS product_features (
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (product_id, position)
);

CREATE TABLE IF NOT EXISTS product_facts (
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    value TEXT NOT NULL,
    confirmed INTEGER NOT NULL CHECK (confirmed IN (0, 1)),
    PRIMARY KEY (product_id, position)
);

CREATE TABLE IF NOT EXISTS product_documents (
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    PRIMARY KEY (product_id, position)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    action TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
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
            self._seed(connection)

    @staticmethod
    def _seed(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO brands VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                BRAND["id"], BRAND["companyName"], BRAND["productName"],
                BRAND["primaryColor"], BRAND["secondaryColor"], BRAND["tone"],
                BRAND["slogan"], BRAND["disclaimer"],
            ),
        )
        connection.execute(
            "INSERT OR IGNORE INTO users VALUES (?, ?, ?, ?)",
            (DEMO_USER["id"], DEMO_USER["name"], DEMO_USER["mode"], json.dumps(DEMO_USER["roles"], ensure_ascii=False)),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO user_permissions VALUES (?, ?)",
            [(DEMO_USER["id"], permission) for permission in PERMISSIONS],
        )
        connection.execute(
            """INSERT OR IGNORE INTO products
               (id, name, insurer, category, version, status, source_type, summary, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(PRODUCT[key] for key in ("id", "name", "insurer", "category", "version", "status", "sourceType", "summary", "updatedAt")),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO product_features VALUES (?, ?, ?)",
            [(PRODUCT["id"], index, value) for index, value in enumerate(PRODUCT["features"])],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO product_facts VALUES (?, ?, ?, ?, ?)",
            [(PRODUCT["id"], index, item["label"], item["value"], int(item["confirmed"])) for index, item in enumerate(PRODUCT["facts"])],
        )
        connection.executemany(
            "INSERT OR IGNORE INTO product_documents VALUES (?, ?, ?, ?, ?)",
            [(PRODUCT["id"], index, item["name"], item["type"], item["status"]) for index, item in enumerate(PRODUCT["documents"])],
        )
        if connection.execute("SELECT COUNT(*) FROM audit_logs").fetchone()[0] == 0:
            connection.execute(
                """INSERT INTO audit_logs
                   (occurred_at, actor_id, action, object_type, object_id, detail)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (PRODUCT["updatedAt"], DEMO_USER["id"], "seed.create", "product", PRODUCT["id"], "创建预置达尔文12号演示产品"),
            )

    def get_brand(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM brands LIMIT 1").fetchone()
        return {
            "id": row["id"], "companyName": row["company_name"], "productName": row["product_name"],
            "primaryColor": row["primary_color"], "secondaryColor": row["secondary_color"],
            "tone": row["tone"], "slogan": row["slogan"], "disclaimer": row["disclaimer"],
        }

    def get_demo_user(self) -> dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (DEMO_USER["id"],)).fetchone()
            permissions = [item[0] for item in connection.execute(
                "SELECT permission_code FROM user_permissions WHERE user_id = ? ORDER BY permission_code", (DEMO_USER["id"],)
            ).fetchall()]
        return {"id": row["id"], "name": row["name"], "mode": row["mode"], "roles": json.loads(row["roles_json"]), "permissions": permissions}

    def list_products(self, query: str = "") -> list[dict[str, Any]]:
        sql = "SELECT * FROM products"
        params: tuple[str, ...] = ()
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
            features = [item[0] for item in connection.execute(
                "SELECT value FROM product_features WHERE product_id = ? ORDER BY position", (product_id,)
            ).fetchall()]
            facts = [dict(item) for item in connection.execute(
                "SELECT label, value, confirmed FROM product_facts WHERE product_id = ? ORDER BY position", (product_id,)
            ).fetchall()]
            documents = [dict(item) for item in connection.execute(
                "SELECT name, type, status FROM product_documents WHERE product_id = ? ORDER BY position", (product_id,)
            ).fetchall()]
        product = self._product_summary(row)
        product.update({"summary": row["summary"], "features": features, "facts": [{**item, "confirmed": bool(item["confirmed"])} for item in facts], "documents": documents})
        return product

    @staticmethod
    def _product_summary(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"], "name": row["name"], "insurer": row["insurer"],
            "category": row["category"], "version": row["version"], "status": row["status"],
            "sourceType": row["source_type"], "updatedAt": row["updated_at"],
        }

    def get_stats(self) -> dict[str, int]:
        with self.connect() as connection:
            product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        return {"products": product_count, "pendingReviews": 0, "approvedAssets": 0, "possiblyOutdated": 0}

    def get_recent_audit(self, limit: int = 5) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
