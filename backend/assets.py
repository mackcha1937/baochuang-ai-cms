from __future__ import annotations

import io
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from docx import Document
from PIL import Image, ImageDraw, ImageFont

from backend.database import Database, now_iso


@dataclass
class AssetRepository:
    database: Database
    generated_root: Path

    def list_assets(self, *, asset_type: str = "", status: str = "", channel: str = "") -> list[dict[str, Any]]:
        conditions: list[str] = []
        values: list[str] = []
        for column, value in (("asset.type", asset_type), ("asset.status", status), ("asset.channel", channel)):
            if value:
                conditions.append(f"{column} = ?")
                values.append(value)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT asset.*, version.version_no FROM assets asset
                    JOIN content_versions version ON version.id = asset.content_version_id
                    {where} ORDER BY asset.updated_at DESC, asset.id DESC""",
                values,
            ).fetchall()
        return [self._summary(row) for row in rows]

    def get_asset(self, asset_id: str) -> Optional[dict[str, Any]]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT asset.*, version.version_no, version.snapshot_json,
                          version.created_by, version.created_at AS submitted_at
                   FROM assets asset JOIN content_versions version ON version.id = asset.content_version_id
                   WHERE asset.id = ?""",
                (asset_id,),
            ).fetchone()
        if row is None:
            return None
        result = self._summary(row)
        result.update({"snapshot": json.loads(row["snapshot_json"]), "submittedBy": row["created_by"], "submittedAt": row["submitted_at"]})
        return result

    def duplicate(
        self,
        actor_id: str,
        asset_id: str,
        *,
        product_id_override: Optional[str] = None,
        allowed_statuses: tuple[str, ...] = ("approved",),
        audit_action: str = "asset.duplicate",
    ) -> str:
        timestamp, content_id = now_iso(), uuid4().hex
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT asset.*, version.snapshot_json FROM assets asset
                   JOIN content_versions version ON version.id = asset.content_version_id
                   WHERE asset.id = ?""",
                (asset_id,),
            ).fetchone()
            if row is None or row["status"] not in allowed_statuses:
                raise ValueError("missing")
            snapshot = json.loads(row["snapshot_json"])
            content = snapshot.get("content") if isinstance(snapshot.get("content"), dict) else {}
            title = f"{str(content.get('title') or row['title'])}（副本）"[:150]
            settings = content.get("settings") if isinstance(content.get("settings"), dict) else {}
            manual_source = str(content.get("manualSource") or "")[:100_000]
            product_id = product_id_override or content.get("productId")
            if product_id and connection.execute("SELECT 1 FROM products WHERE id = ? AND status = '已确认'", (product_id,)).fetchone() is None:
                product_id = None
            payload: dict[str, Any] = {}
            connection.execute(
                """INSERT INTO content_items
                   (id, type, title, product_id, manual_source, settings_json, payload_json, status, revision, owner_id, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, '{}', 'draft', 1, ?, ?, ?)""",
                (content_id, row["type"], title, product_id, manual_source, json.dumps(settings, ensure_ascii=False), actor_id, timestamp, timestamp),
            )
            if row["type"] == "article":
                branch = snapshot.get("branch") if isinstance(snapshot.get("branch"), dict) else {}
                branch_id = uuid4().hex
                connection.execute(
                    """INSERT INTO content_branches
                       (id, content_id, channel, outline_json, outline_status, body_json, body_status, review_status, updated_at)
                       VALUES (?, ?, ?, ?, 'confirmed', ?, 'draft', 'draft', ?)""",
                    (branch_id, content_id, branch.get("channel") or row["channel"], json.dumps(branch.get("outline") or {}, ensure_ascii=False), json.dumps(branch.get("body") or {}, ensure_ascii=False), timestamp),
                )
            else:
                payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
                payload = json.loads(json.dumps(payload, ensure_ascii=False))
                if row["type"] == "image":
                    payload["copyStatus"] = "draft"
                    if isinstance(payload.get("visual"), dict):
                        payload["visual"]["status"] = "needs_relayout"
                elif row["type"] == "training":
                    payload["scriptStatus"] = "draft"
                connection.execute("UPDATE content_items SET payload_json = ? WHERE id = ?", (json.dumps(payload, ensure_ascii=False), content_id))
            Database._audit(connection, actor_id, audit_action, "asset", asset_id, f"复制素材为新草稿 {content_id}" + (f"，关联产品 {product_id}" if product_id else ""))
        return content_id

    def revise_for_current_product(self, actor_id: str, asset_id: str) -> str:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT asset.status, item.product_id, product.root_product_id
                   FROM assets asset JOIN content_items item ON item.id = asset.content_id
                   LEFT JOIN products product ON product.id = item.product_id
                   WHERE asset.id = ?""",
                (asset_id,),
            ).fetchone()
            if row is None or row["status"] != "possibly_outdated" or not row["root_product_id"]:
                raise ValueError("missing")
            replacement = connection.execute(
                """SELECT id FROM products WHERE root_product_id = ? AND status = '已确认'
                   AND is_current = 1 AND id != ? ORDER BY confirmed_at DESC LIMIT 1""",
                (row["root_product_id"], row["product_id"]),
            ).fetchone()
        if replacement is None:
            raise ValueError("replacement_missing")
        return self.duplicate(
            actor_id,
            asset_id,
            product_id_override=replacement["id"],
            allowed_statuses=("possibly_outdated",),
            audit_action="asset.revise",
        )

    def transition(self, actor_id: str, asset_id: str, action: str, reason: str) -> dict[str, Any]:
        transitions = {
            "keep-valid": ({"possibly_outdated", "restore_pending"}, "approved", "asset.keep_valid"),
            "invalidate": ({"approved", "possibly_outdated"}, "invalid", "asset.invalidate"),
            "restore": ({"invalid"}, "restore_pending", "asset.restore"),
        }
        if action not in transitions:
            raise ValueError("action")
        if not reason.strip():
            raise ValueError("reason_required")
        allowed, target, audit_action = transitions[action]
        timestamp = now_iso()
        with self.database.connect() as connection:
            row = connection.execute("SELECT status FROM assets WHERE id = ?", (asset_id,)).fetchone()
            if row is None:
                raise ValueError("missing")
            if row["status"] not in allowed:
                raise ValueError("state")
            connection.execute("UPDATE assets SET status = ?, updated_at = ? WHERE id = ?", (target, timestamp, asset_id))
            Database._audit(connection, actor_id, audit_action, "asset", asset_id, reason.strip())
        result = self.get_asset(asset_id)
        assert result is not None
        return result

    def download(self, actor_id: str, asset_id: str) -> tuple[bytes, str, str]:
        asset = self.get_asset(asset_id)
        if asset is None:
            raise ValueError("missing")
        if asset["status"] != "approved":
            raise ValueError("status")
        snapshot = asset["snapshot"]
        if asset["type"] == "image":
            data = self._render_image(snapshot)
            mime, extension = "image/png", ".png"
        elif asset["type"] == "article":
            data = self._article_docx(snapshot)
            mime, extension = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"
        elif asset["type"] == "training":
            data = self._training_docx(snapshot)
            mime, extension = "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".docx"
        else:
            raise ValueError("type")
        filename = self._filename(asset["title"], asset["contentVersion"], extension)
        self.database.audit(actor_id, "asset.download", "asset", asset_id, f"下载批准素材 {filename}")
        return data, mime, filename

    def _render_image(self, snapshot: dict[str, Any]) -> bytes:
        payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
        visual = payload.get("visual") if isinstance(payload.get("visual"), dict) else {}
        layout = visual.get("layout") if isinstance(visual.get("layout"), dict) else {}
        asset_id = visual.get("assetId")
        with self.database.connect() as connection:
            source = connection.execute("SELECT storage_key FROM visual_assets WHERE id = ? AND status = 'ready'", (asset_id,)).fetchone()
        if source is None:
            raise ValueError("file_missing")
        root = self.generated_root.resolve()
        path = (root / source["storage_key"]).resolve()
        if not path.is_relative_to(root) or path.name != source["storage_key"] or not path.is_file():
            raise ValueError("file_missing")
        image = Image.open(path).convert("RGB")
        draw = ImageDraw.Draw(image, "RGBA")
        width, height = image.size
        blocks = {str(block.get("id")): block for block in layout.get("blocks", []) if isinstance(block, dict)}
        hidden = set(layout.get("hiddenBlockIds", []))
        ordered = [blocks[key] for key in layout.get("blockOrder", []) if key in blocks and key not in hidden]
        draw.rounded_rectangle((width * .055, height * .05, width * .945, height * .95), radius=max(12, width // 35), fill=(15, 18, 36, 185))
        y = int(height * .085)
        for block in ordered:
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            kind = str(block.get("type") or "text")
            size = max(16, int(width * ({"title": .052, "product": .032, "disclaimer": .022}.get(kind, .027))))
            font = self._font(size)
            lines = self._wrap(draw, text, font, int(width * .78))
            line_height = int(size * 1.45)
            if y + line_height * len(lines) > height * .89:
                break
            color = (255, 255, 255, 255) if kind != "disclaimer" else (225, 229, 239, 255)
            for line in lines:
                draw.text((int(width * .1), y), line, font=font, fill=color)
                y += line_height
            y += max(8, size // 2)
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()

    @staticmethod
    def _article_docx(snapshot: dict[str, Any]) -> bytes:
        branch = snapshot.get("branch") if isinstance(snapshot.get("branch"), dict) else {}
        body = branch.get("body") if isinstance(branch.get("body"), dict) else {}
        document = Document()
        document.add_heading(str(body.get("title") or snapshot.get("content", {}).get("title") or "批准文章"), 0)
        document.add_paragraph(f"渠道：{'公众号' if branch.get('channel') == 'wechat' else '小红书'}")
        for paragraph in body.get("paragraphs", []):
            document.add_paragraph(str(paragraph))
        document.add_heading("免责声明", level=1)
        document.add_paragraph(str(body.get("disclaimer") or "具体责任以保险合同为准。"))
        return AssetRepository._save_docx(document)

    @staticmethod
    def _training_docx(snapshot: dict[str, Any]) -> bytes:
        payload = snapshot.get("payload") if isinstance(snapshot.get("payload"), dict) else {}
        script = payload.get("script") if isinstance(payload.get("script"), dict) else {}
        document = Document()
        document.add_heading(str(snapshot.get("content", {}).get("title") or "批准培训课程"), 0)
        for index, chapter in enumerate(script.get("chapters", []), 1):
            document.add_heading(f"第 {index} 章 {chapter.get('title', '')}", level=1)
            document.add_paragraph(str(chapter.get("narration") or ""))
            document.add_heading("知识卡", level=2)
            document.add_paragraph(str(chapter.get("knowledgeCard") or ""))
        document.add_heading("课程测试", level=1)
        for index, question in enumerate(script.get("questions", []), 1):
            document.add_paragraph(f"{index}. {question.get('question', '')}")
            for option in question.get("options", []):
                document.add_paragraph(str(option), style="List Bullet")
            document.add_paragraph(f"正确答案：{question.get('answer', '')}")
            document.add_paragraph(f"解析：{question.get('explanation', '')}")
        return AssetRepository._save_docx(document)

    @staticmethod
    def _save_docx(document: Document) -> bytes:
        stream = io.BytesIO()
        document.save(stream)
        return stream.getvalue()

    @staticmethod
    def _font(size: int):
        candidates = [
            os.getenv("APP_FONT_PATH", ""),
            "/System/Library/Fonts/STHeiti Medium.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
        for value in candidates:
            if value and Path(value).is_file():
                return ImageFont.truetype(value, size=size)
        return ImageFont.load_default(size=size)

    @staticmethod
    def _wrap(draw: ImageDraw.ImageDraw, text: str, font: Any, max_width: int) -> list[str]:
        lines: list[str] = []
        current = ""
        for character in text:
            candidate = current + character
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                lines.append(current)
                current = character
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines[:8]

    @staticmethod
    def _filename(title: str, version: int, extension: str) -> str:
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", title, flags=re.UNICODE).strip("_-")[:80] or "approved_asset"
        return f"{safe}_V{version}{extension}"

    @staticmethod
    def _summary(row: Any) -> dict[str, Any]:
        return {
            "id": row["id"], "contentVersionId": row["content_version_id"], "contentId": row["content_id"],
            "branchId": row["branch_id"], "contentVersion": row["version_no"], "type": row["type"],
            "channel": row["channel"], "title": row["title"], "status": row["status"],
            "createdAt": row["created_at"], "updatedAt": row["updated_at"],
        }
