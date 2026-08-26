from __future__ import annotations

import io
import hashlib
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from backend.config import Settings
from backend.main import create_app


def make_client(tmp_path: Path, *, max_bytes: int = 20 * 1024 * 1024) -> TestClient:
    return TestClient(create_app(Settings(
        database_url=f"sqlite:///{tmp_path / 'stage02.db'}",
        frontend_dist=tmp_path / "missing-dist",
        upload_dir=tmp_path / "uploads",
        max_upload_bytes=max_bytes,
    )))


def docx_bytes() -> bytes:
    document = Document()
    document.add_heading("测试保险产品", level=1)
    document.add_paragraph("保障期间为终身，等待期为 90 天。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "字段", "内容"
    table.cell(1, 0).text, table.cell(1, 1).text = "投保年龄", "0-55岁"
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()


def xlsx_bytes() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "费率表"
    sheet.append(["年龄", "年保费"])
    sheet.append([30, 1200])
    rules = workbook.create_sheet("投保规则")
    rules.append(["职业", "1-4类"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def blank_pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def text_pdf_bytes() -> bytes:
    writer = PdfWriter()
    page = writer.add_blank_page(width=595, height=842)
    font = DictionaryObject({
        NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    })
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 72 720 Td (Stage 02 PDF Page Text) Tj ET")
    page[NameObject("/Resources")] = resources
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def create_draft(client: TestClient, name: str = "Stage 02 测试产品") -> dict:
    key = hashlib.sha256(name.encode("utf-8")).hexdigest()
    response = client.post("/api/v1/products", json={"name": name}, headers={"Idempotency-Key": key})
    assert response.status_code == 201, response.text
    return response.json()


def upload(client: TestClient, product_id: str, name: str, content: bytes, mime: str) -> dict:
    response = client.post(f"/api/v1/products/{product_id}/documents", files={"file": (name, content, mime)})
    assert response.status_code == 201, response.text
    return response.json()


def test_draft_duplicate_idempotency_update_confirm_and_restart(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        draft = create_draft(client)
        duplicate = client.post("/api/v1/products", json={"name": draft["name"]})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "DUPLICATE_PRODUCT"

        replay_key = hashlib.sha256(draft["name"].encode("utf-8")).hexdigest()
        replay = client.post("/api/v1/products", json={"name": draft["name"], "allowDuplicate": True}, headers={"Idempotency-Key": replay_key})
        assert replay.status_code == 200
        assert replay.json()["id"] == draft["id"]

        facts = [{**fact, "value": f"已填写-{index}", "confirmed": True} for index, fact in enumerate(draft["facts"], start=1)]
        updated = client.patch(f"/api/v1/products/{draft['id']}", json={
            "name": draft["name"], "insurer": "测试保险公司", "category": "重疾险",
            "summary": "用于验证 Stage 02 产品资料库闭环。", "revision": draft["revision"], "facts": facts,
        })
        assert updated.status_code == 200
        before_source = client.post(f"/api/v1/products/{draft['id']}/confirm", json={"revision": updated.json()["revision"]})
        assert before_source.status_code == 422
        assert "至少一份" in "".join(before_source.json()["error"]["details"]["missing"])

        document = upload(client, draft["id"], "产品说明.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        parsed = client.post(f"/api/v1/products/{draft['id']}/documents/{document['id']}/parse")
        assert parsed.status_code == 200
        assert parsed.json()["status"] == "parsed"
        assert parsed.json()["structure"]["paragraphCount"] == 2
        assert parsed.json()["structure"]["tableCount"] == 1

        latest = client.get(f"/api/v1/products/{draft['id']}").json()
        confirmed = client.post(f"/api/v1/products/{draft['id']}/confirm", json={"revision": latest["revision"]})
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "已确认"
        blocked = client.patch(f"/api/v1/products/{draft['id']}", json={
            "name": draft["name"], "insurer": "覆盖", "category": "重疾险", "summary": "覆盖",
            "revision": confirmed.json()["revision"], "facts": facts,
        })
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "PRODUCT_CONFIRMED"

    with make_client(tmp_path) as restarted:
        restored = restarted.get(f"/api/v1/products/{draft['id']}")
        assert restored.status_code == 200
        assert restored.json()["status"] == "已确认"
        assert restored.json()["documents"][0]["status"] == "parsed"
        assert "保障期间" in restored.json()["documents"][0]["extractedText"]


def test_xlsx_parse_cancel_retry_delete_and_audit(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        draft = create_draft(client, "表格解析产品")
        document = upload(client, draft["id"], "../危险路径/费率 表.xlsx", xlsx_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        assert document["name"] == "费率 表.xlsx"
        assert ".." not in document["name"]
        assert list((tmp_path / "uploads").glob("*.xlsx"))

        cancelled = client.post(f"/api/v1/products/{draft['id']}/documents/{document['id']}/cancel")
        assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
        retried = client.post(f"/api/v1/products/{draft['id']}/documents/{document['id']}/retry")
        assert retried.status_code == 200 and retried.json()["status"] == "parsed"
        assert retried.json()["attempt"] == 1
        assert [sheet["name"] for sheet in retried.json()["structure"]["sheets"]] == ["费率表", "投保规则"]

        removed = client.delete(f"/api/v1/products/{draft['id']}/documents/{document['id']}")
        assert removed.status_code == 204
        assert not list((tmp_path / "uploads").iterdir())
        detail = client.get(f"/api/v1/products/{draft['id']}").json()
        assert all(item.get("id") != document["id"] for item in detail["documents"])
        actions = [item["action"] for item in client.get("/api/v1/audit-logs").json()["items"]]
        for action in ("document.upload", "document.cancel", "document.retry", "document.parse.success", "document.delete"):
            assert action in actions


def test_empty_pdf_manual_fallback_and_delete_draft(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        draft = create_draft(client, "PDF 人工兜底产品")
        document = upload(client, draft["id"], "扫描件.pdf", blank_pdf_bytes(), "application/pdf")
        parsed = client.post(f"/api/v1/products/{draft['id']}/documents/{document['id']}/parse")
        assert parsed.status_code == 200
        assert parsed.json()["status"] == "needs_manual"
        assert "粘贴原文" in parsed.json()["parseError"]
        manual = client.put(f"/api/v1/products/{draft['id']}/documents/{document['id']}/manual-text", json={"text": "人工录入的保险条款原文"})
        assert manual.status_code == 200 and manual.json()["status"] == "manual_ready"
        assert manual.json()["manualText"] == "人工录入的保险条款原文"

        removed = client.delete(f"/api/v1/products/{draft['id']}")
        assert removed.status_code == 204
        assert client.get(f"/api/v1/products/{draft['id']}").status_code == 404
        assert not list((tmp_path / "uploads").iterdir())


def test_pdf_page_number_and_text_are_parsed(tmp_path: Path) -> None:
    with make_client(tmp_path) as client:
        draft = create_draft(client, "PDF 页码解析产品")
        document = upload(client, draft["id"], "条款.pdf", text_pdf_bytes(), "application/pdf")
        parsed = client.post(f"/api/v1/products/{draft['id']}/documents/{document['id']}/parse")
        assert parsed.status_code == 200
        assert parsed.json()["status"] == "parsed"
        assert parsed.json()["structure"]["pageCount"] == 1
        assert parsed.json()["structure"]["pages"][0]["page"] == 1
        assert "Stage 02 PDF Page Text" in parsed.json()["extractedText"]


def test_upload_negative_security_permissions_and_conflict(tmp_path: Path) -> None:
    with make_client(tmp_path, max_bytes=100) as client:
        draft = create_draft(client, "安全测试产品")
        endpoint = f"/api/v1/products/{draft['id']}/documents"
        unsupported = client.post(endpoint, files={"file": ("资料.txt", b"hello", "text/plain")})
        assert unsupported.status_code == 415
        forged = client.post(endpoint, files={"file": ("伪造.pdf", b"not-a-pdf", "application/pdf")})
        assert forged.status_code == 422 and forged.json()["error"]["code"] == "FILE_SIGNATURE_MISMATCH"
        wrong_mime = client.post(endpoint, files={"file": ("资料.pdf", blank_pdf_bytes(), "text/plain")})
        assert wrong_mime.status_code == 415 and wrong_mime.json()["error"]["code"] == "MIME_TYPE_MISMATCH"
        too_large = client.post(endpoint, files={"file": ("资料.pdf", blank_pdf_bytes(), "application/pdf")})
        assert too_large.status_code == 413 and too_large.json()["error"]["code"] == "FILE_TOO_LARGE"
        assert not list((tmp_path / "uploads").iterdir())

        forbidden = client.post("/api/v1/products", json={"name": "越权产品"}, headers={"X-Demo-User": "viewer-demo"})
        assert forbidden.status_code == 403 and forbidden.json()["error"]["code"] == "PERMISSION_DENIED"
        unknown = client.post("/api/v1/products", json={"name": "未知用户"}, headers={"X-Demo-User": "missing"})
        assert unknown.status_code == 401
        permission_audit = client.get("/api/v1/audit-logs").json()["items"]
        assert any(item["action"] == "permission.denied" for item in permission_audit)
        rejected = [item for item in permission_audit if item["action"] == "document.upload.rejected"]
        assert len(rejected) == 4
        assert all(str(tmp_path) not in item["detail"] for item in rejected)

        conflict_payload = {
            "name": draft["name"], "insurer": "测试", "category": "测试", "summary": "测试",
            "revision": draft["revision"] + 1,
            "facts": [{**fact, "value": "测试", "confirmed": True} for fact in draft["facts"]],
        }
        conflict = client.patch(f"/api/v1/products/{draft['id']}", json=conflict_payload)
        assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "REVISION_CONFLICT"
