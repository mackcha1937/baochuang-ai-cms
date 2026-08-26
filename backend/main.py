from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Union
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.config import Settings
from backend.ai_content import AIProvider, ContentRepository, DeepSeekProvider
from backend.database import Database
from backend.document_parser import EmptyDocumentError, parse_document
from backend.image_generation import ImageProvider, QwenImageProvider, validate_raster


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Optional[dict[str, Any]] = None):
        self.status_code, self.code, self.message, self.details = status_code, code, message, details


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    allowDuplicate: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("产品名称不能为空")
        return value.strip()


class FactUpdate(BaseModel):
    label: str = Field(min_length=1, max_length=50)
    value: str = Field(max_length=500)
    confirmed: bool
    required: bool = True


class ProductUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    insurer: str = Field(max_length=100)
    category: str = Field(max_length=100)
    summary: str = Field(max_length=3000)
    revision: int = Field(ge=1)
    facts: list[FactUpdate] = Field(min_length=1, max_length=30)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=1)


class ManualTextRequest(BaseModel):
    text: str = Field(max_length=500_000)


class ContentCreateRequest(BaseModel):
    type: str
    title: str = Field(min_length=1, max_length=150)
    productId: Optional[str] = None
    manualSource: str = Field(default="", max_length=100_000)
    settings: dict[str, Any] = Field(default_factory=dict)

    @field_validator("type")
    @classmethod
    def valid_type(cls, value: str) -> str:
        if value not in {"image", "article", "training"}:
            raise ValueError("不支持的内容类型")
        return value


class JobCreateRequest(BaseModel):
    taskType: str
    branchId: Optional[str] = None
    instruction: str = Field(default="", max_length=2000)


class ContentConfirmRequest(BaseModel):
    node: str
    branchId: Optional[str] = None
    revision: int = Field(ge=1)


class ManualOutputRequest(BaseModel):
    taskType: str
    branchId: Optional[str] = None
    revision: int = Field(ge=1)
    output: dict[str, Any]


class VisualLayoutRequest(BaseModel):
    revision: int = Field(ge=1)
    template: str
    palette: str
    blockOrder: list[str] = Field(min_length=3, max_length=30)
    hiddenBlockIds: list[str] = Field(default_factory=list, max_length=27)

    @field_validator("template")
    @classmethod
    def valid_template(cls, value: str) -> str:
        if value not in {"long-editorial", "square-focus", "header-split"}:
            raise ValueError("不支持的图片模板")
        return value

    @field_validator("palette")
    @classmethod
    def valid_palette(cls, value: str) -> str:
        if value not in {"brand", "ocean", "forest", "sunset"}:
            raise ValueError("不支持的配色")
        return value


ALLOWED_FILES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"},
}


def safe_display_name(raw_name: str) -> str:
    normalized = unicodedata.normalize("NFKC", raw_name).replace("\\", "/")
    basename = normalized.rsplit("/", 1)[-1].strip()
    basename = re.sub(r"[\x00-\x1f\x7f]", "", basename)
    basename = re.sub(r"[^\w\u4e00-\u9fff .()（）-]", "_", basename, flags=re.UNICODE)
    basename = re.sub(r"\s+", " ", basename).strip(" .")
    if not basename:
        raise ApiError(422, "INVALID_FILENAME", "文件名无效")
    if len(basename) > 180:
        stem, suffix = Path(basename).stem[:140], Path(basename).suffix[:20]
        basename = f"{stem}{suffix}"
    return basename


def validate_file_signature(path: Path, extension: str) -> None:
    if extension == ".pdf":
        if not path.read_bytes()[:5] == b"%PDF-":
            raise ApiError(422, "FILE_SIGNATURE_MISMATCH", "文件内容不是有效 PDF")
        return
    if not zipfile.is_zipfile(path):
        raise ApiError(422, "FILE_SIGNATURE_MISMATCH", f"文件内容不是有效 {extension[1:].upper()}")
    required = "word/document.xml" if extension == ".docx" else "xl/workbook.xml"
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    if "[Content_Types].xml" not in names or required not in names:
        raise ApiError(422, "FILE_SIGNATURE_MISMATCH", f"文件内容与 {extension[1:].upper()} 扩展名不一致")


def storage_path(upload_root: Path, storage_key: str) -> Path:
    root = upload_root.resolve()
    candidate = (root / storage_key).resolve()
    if not candidate.is_relative_to(root) or candidate.name != storage_key:
        raise ApiError(400, "UNSAFE_STORAGE_PATH", "文件存储路径不安全")
    return candidate


def create_app(
    settings: Optional[Settings] = None,
    ai_provider: Optional[AIProvider] = None,
    image_provider: Optional[ImageProvider] = None,
) -> FastAPI:
    active_settings = settings or Settings.from_environment()
    database = Database(active_settings.database_path)
    content_repository = ContentRepository(database)
    provider = ai_provider or DeepSeekProvider()
    visual_provider = image_provider or QwenImageProvider(max_download_bytes=active_settings.max_visual_bytes)
    upload_root = active_settings.upload_dir
    generated_root = active_settings.generated_dir

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        upload_root.mkdir(parents=True, exist_ok=True)
        generated_root.mkdir(parents=True, exist_ok=True)
        yield

    app = FastAPI(title="保创AI API", version="0.2.0", docs_url="/api/docs" if os.getenv("APP_ENV") != "production" else None, redoc_url=None, lifespan=lifespan)
    app.state.database, app.state.settings, app.state.ai_provider, app.state.image_provider = database, active_settings, provider, visual_provider

    @app.exception_handler(ApiError)
    async def api_exception_handler(_: Request, exc: ApiError) -> JSONResponse:
        payload: dict[str, Any] = {"error": {"code": exc.code, "message": exc.message}}
        if exc.details:
            payload["error"]["details"] = exc.details
        return JSONResponse(status_code=exc.status_code, content=payload)

    async def actor(x_demo_user: str = Header(default="super-demo")) -> dict[str, Any]:
        user = database.get_user(x_demo_user)
        if user is None:
            raise ApiError(401, "UNAUTHENTICATED", "演示用户不存在")
        return user

    def require(permission: str) -> Callable[..., Any]:
        async def dependency(user: dict[str, Any] = Depends(actor)) -> dict[str, Any]:
            if permission not in user["permissions"]:
                database.audit(user["id"], "permission.denied", "permission", permission, f"拒绝访问：缺少 {permission}")
                raise ApiError(403, "PERMISSION_DENIED", f"缺少权限：{permission}")
            return user
        return dependency

    def editable_product(product_id: str) -> dict[str, Any]:
        product = database.get_product(product_id)
        if product is None:
            raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        if product["status"] == "已确认":
            raise ApiError(409, "PRODUCT_CONFIRMED", "已确认产品不可直接覆盖，请创建新版本")
        return product

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "baochuang-api", "version": "0.2.0"}

    @app.get("/api/v1/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return {"brand": database.get_brand(), "user": database.get_demo_user(), "stats": database.get_stats(), "serviceStatus": {"api": "ready", "deepseek": "configured" if os.getenv("DEEPSEEK_API_KEY", "").strip() else "not_configured", "qwen": "configured" if os.getenv("DASHSCOPE_API_KEY", "").strip() else "not_configured"}, "recentAudit": database.get_recent_audit(5)}

    @app.get("/api/v1/products")
    def products(q: str = Query(default="", max_length=100)) -> dict[str, Any]:
        items = database.list_products(q.strip())
        return {"items": items, "total": len(items), "query": q.strip()}

    @app.post("/api/v1/products", status_code=201)
    def create_product(payload: ProductCreate, request: Request, user: dict[str, Any] = Depends(require("product.create"))) -> JSONResponse:
        if database.product_name_exists(payload.name) and not payload.allowDuplicate:
            raise ApiError(409, "DUPLICATE_PRODUCT", "已存在同名产品，请确认是否仍要创建")
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if len(idempotency_key) > 100:
            raise ApiError(422, "INVALID_IDEMPOTENCY_KEY", "幂等键过长")
        product, created = database.create_product(user["id"], payload.name, idempotency_key)
        return JSONResponse(status_code=201 if created else 200, content=product)

    @app.get("/api/v1/products/{product_id}")
    def product_detail(product_id: str) -> dict[str, Any]:
        product = database.get_product(product_id)
        if product is None:
            raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        return product

    @app.patch("/api/v1/products/{product_id}")
    def update_product(product_id: str, payload: ProductUpdate, user: dict[str, Any] = Depends(require("product.update"))) -> dict[str, Any]:
        result = database.update_product(user["id"], product_id, payload.revision, {"name": payload.name.strip(), "insurer": payload.insurer.strip(), "category": payload.category.strip(), "summary": payload.summary.strip()}, [item.model_dump() for item in payload.facts])
        if result == "missing": raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        if result == "confirmed": raise ApiError(409, "PRODUCT_CONFIRMED", "已确认产品不可直接覆盖，请创建新版本")
        if result == "conflict": raise ApiError(409, "REVISION_CONFLICT", "产品已在其他页面更新，请刷新后重试")
        product = database.get_product(product_id)
        assert product is not None
        return product

    @app.post("/api/v1/products/{product_id}/confirm")
    def confirm_product(product_id: str, payload: RevisionRequest, user: dict[str, Any] = Depends(require("product.confirm"))) -> dict[str, Any]:
        result, missing = database.confirm_product(user["id"], product_id, payload.revision)
        if result == "missing": raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        if result == "conflict": raise ApiError(409, "REVISION_CONFLICT", "产品已更新，请刷新后重新确认")
        if result == "confirmed": raise ApiError(409, "PRODUCT_CONFIRMED", "产品已经确认")
        if result == "invalid": raise ApiError(422, "CONFIRMATION_INCOMPLETE", "关键字段或资料来源尚未完成", {"missing": missing})
        product = database.get_product(product_id)
        assert product is not None
        return product

    @app.delete("/api/v1/products/{product_id}", status_code=204, response_class=Response)
    def delete_product(product_id: str, user: dict[str, Any] = Depends(require("product.update"))) -> Response:
        result, keys = database.delete_draft(user["id"], product_id)
        if result == "missing": raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        if result == "confirmed": raise ApiError(409, "PRODUCT_CONFIRMED", "已确认产品不可删除")
        for key in keys:
            path = storage_path(upload_root, key)
            if path.exists(): path.unlink()
        return Response(status_code=204)

    @app.post("/api/v1/products/{product_id}/documents", status_code=201)
    async def upload_document(product_id: str, file: UploadFile = File(...), user: dict[str, Any] = Depends(require("product.update"))) -> dict[str, Any]:
        editable_product(product_id)
        display_name = safe_display_name(file.filename or "")
        extension = Path(display_name).suffix.lower()
        if extension not in ALLOWED_FILES:
            database.audit(user["id"], "document.upload.rejected", "product", product_id, f"拒绝文件 {display_name}：UNSUPPORTED_FILE_TYPE")
            raise ApiError(415, "UNSUPPORTED_FILE_TYPE", "仅支持 PDF、DOCX、XLSX")
        mime_type = (file.content_type or "application/octet-stream").lower()
        if mime_type not in ALLOWED_FILES[extension]:
            database.audit(user["id"], "document.upload.rejected", "product", product_id, f"拒绝文件 {display_name}：MIME_TYPE_MISMATCH")
            raise ApiError(415, "MIME_TYPE_MISMATCH", "文件 MIME 类型与扩展名不匹配")
        document_id = uuid4().hex
        storage_key = f"{uuid4().hex}{extension}"
        path = storage_path(upload_root, storage_key)
        size, digest = 0, hashlib.sha256()
        try:
            with path.open("xb") as target:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > active_settings.max_upload_bytes:
                        raise ApiError(413, "FILE_TOO_LARGE", f"文件不得超过 {active_settings.max_upload_bytes // (1024 * 1024)} MB")
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise ApiError(422, "EMPTY_FILE", "不能上传空文件")
            validate_file_signature(path, extension)
            document = database.create_document(user["id"], product_id, {"id": document_id, "original_name": display_name, "safe_name": display_name, "storage_key": storage_key, "extension": extension, "mime_type": mime_type, "size_bytes": size, "checksum": digest.hexdigest()})
            if document is None:
                raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
            return document
        except Exception as exc:
            if path.exists(): path.unlink()
            if isinstance(exc, ApiError):
                database.audit(user["id"], "document.upload.rejected", "product", product_id, f"拒绝文件 {display_name}：{exc.code}")
            raise
        finally:
            await file.close()

    def run_parse(product_id: str, document_id: str, user_id: str) -> dict[str, Any]:
        editable_product(product_id)
        document = database.get_document(product_id, document_id)
        if document is None: raise ApiError(404, "DOCUMENT_NOT_FOUND", "文件不存在或已被删除")
        if document["status"] not in {"queued", "failed", "needs_manual", "cancelled"}:
            raise ApiError(409, "INVALID_DOCUMENT_STATE", "当前文件状态不能开始解析")
        database.set_document_status(user_id, product_id, document_id, "parsing", increment_attempt=True, audit_action="document.parse.start")
        storage_key = database.get_document_storage_key(product_id, document_id)
        if storage_key is None:
            raise ApiError(404, "DOCUMENT_NOT_FOUND", "文件不存在或已被删除")
        path = storage_path(upload_root, storage_key)
        try:
            text, structure = parse_document(path, f".{document['type'].lower()}")
            current = database.get_document(product_id, document_id)
            if current and current["status"] == "cancelled": return current
            result = database.set_document_status(user_id, product_id, document_id, "parsed", extracted_text=text, structure=structure, audit_action="document.parse.success")
        except EmptyDocumentError as exc:
            result = database.set_document_status(user_id, product_id, document_id, "needs_manual", parse_error=str(exc), audit_action="document.parse.empty")
        except Exception:
            result = database.set_document_status(user_id, product_id, document_id, "failed", parse_error="文件解析失败，请重试或粘贴原文", audit_action="document.parse.failed")
        assert result is not None
        return result

    @app.post("/api/v1/products/{product_id}/documents/{document_id}/parse")
    def parse_uploaded_document(product_id: str, document_id: str, user: dict[str, Any] = Depends(require("product.update"))) -> dict[str, Any]:
        return run_parse(product_id, document_id, user["id"])

    @app.post("/api/v1/products/{product_id}/documents/{document_id}/retry")
    def retry_document(product_id: str, document_id: str, user: dict[str, Any] = Depends(require("product.update"))) -> dict[str, Any]:
        editable_product(product_id)
        document = database.get_document(product_id, document_id)
        if document is None: raise ApiError(404, "DOCUMENT_NOT_FOUND", "文件不存在或已被删除")
        if document["status"] not in {"failed", "needs_manual", "cancelled"}: raise ApiError(409, "INVALID_DOCUMENT_STATE", "仅失败、待人工处理或已取消的文件可重试")
        database.set_document_status(user["id"], product_id, document_id, "queued", audit_action="document.retry")
        return run_parse(product_id, document_id, user["id"])

    @app.post("/api/v1/products/{product_id}/documents/{document_id}/cancel")
    def cancel_document(product_id: str, document_id: str, user: dict[str, Any] = Depends(require("product.update"))) -> dict[str, Any]:
        editable_product(product_id)
        document = database.get_document(product_id, document_id)
        if document is None: raise ApiError(404, "DOCUMENT_NOT_FOUND", "文件不存在或已被删除")
        if document["status"] in {"parsed", "manual_ready"}: raise ApiError(409, "INVALID_DOCUMENT_STATE", "已完成文件无需取消")
        result = database.set_document_status(user["id"], product_id, document_id, "cancelled", audit_action="document.cancel")
        assert result is not None
        return result

    @app.put("/api/v1/products/{product_id}/documents/{document_id}/manual-text")
    def manual_text(product_id: str, document_id: str, payload: ManualTextRequest, user: dict[str, Any] = Depends(require("product.update"))) -> dict[str, Any]:
        editable_product(product_id)
        result = database.save_manual_text(user["id"], product_id, document_id, payload.text.strip())
        if result is None: raise ApiError(404, "DOCUMENT_NOT_FOUND", "文件不存在或已被删除")
        return result

    @app.delete("/api/v1/products/{product_id}/documents/{document_id}", status_code=204, response_class=Response)
    def delete_document(product_id: str, document_id: str, user: dict[str, Any] = Depends(require("product.update"))) -> Response:
        editable_product(product_id)
        key = database.delete_document(user["id"], product_id, document_id)
        if key is None: raise ApiError(404, "DOCUMENT_NOT_FOUND", "文件不存在或已被删除")
        path = storage_path(upload_root, key)
        if path.exists(): path.unlink()
        return Response(status_code=204)

    @app.get("/api/v1/audit-logs")
    def audit_logs(user: dict[str, Any] = Depends(require("audit.view"))) -> dict[str, Any]:
        items = database.get_recent_audit(100)
        return {"items": items, "total": len(items)}

    @app.get("/api/v1/contents")
    def list_contents(type: str = Query(default="", max_length=20)) -> dict[str, Any]:
        if type and type not in {"image", "article", "training"}:
            raise ApiError(422, "CONTENT_TYPE_INVALID", "不支持的内容类型")
        items = content_repository.list_items(type)
        return {"items": items, "total": len(items)}

    @app.post("/api/v1/contents", status_code=201)
    def create_content(payload: ContentCreateRequest, user: dict[str, Any] = Depends(require("content.create"))) -> dict[str, Any]:
        if not payload.productId and not payload.manualSource.strip():
            raise ApiError(422, "CONTENT_SOURCE_REQUIRED", "请选择已确认产品或填写人工素材")
        if payload.type == "article":
            channels = payload.settings.get("channels", [])
            if not isinstance(channels, list) or not channels or any(channel not in {"wechat", "xiaohongshu"} for channel in channels):
                raise ApiError(422, "ARTICLE_CHANNEL_REQUIRED", "至少选择公众号或小红书一个渠道")
        try:
            return content_repository.create_item(user["id"], payload.type, payload.title.strip(), payload.productId, payload.manualSource.strip(), payload.settings)
        except ValueError as exc:
            if str(exc) == "product_not_confirmed":
                raise ApiError(422, "PRODUCT_NOT_CONFIRMED", "只能使用已确认产品") from exc
            raise

    @app.get("/api/v1/contents/{content_id}")
    def get_content(content_id: str) -> dict[str, Any]:
        item = content_repository.get_item(content_id)
        if item is None:
            raise ApiError(404, "CONTENT_NOT_FOUND", "内容草稿不存在或已删除")
        return item

    @app.post("/api/v1/contents/{content_id}/jobs", status_code=201)
    def create_generation_job(content_id: str, payload: JobCreateRequest, request: Request, user: dict[str, Any] = Depends(require("content.edit"))) -> JSONResponse:
        if payload.taskType not in {"image_copy", "image_visual", "article_outline", "article_generate", "training_outline", "training_script"}:
            raise ApiError(422, "AI_TASK_UNSUPPORTED", "不支持的生成任务")
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key or len(idempotency_key) > 100:
            raise ApiError(422, "IDEMPOTENCY_KEY_REQUIRED", "生成任务必须提供有效幂等键")
        try:
            job, created = content_repository.create_job(user["id"], content_id, payload.taskType, payload.branchId, idempotency_key, payload.instruction.strip())
            return JSONResponse(status_code=201 if created else 200, content=job)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容或渠道分支不存在") from exc
            if code == "invalid_task": raise ApiError(422, "AI_TASK_UNSUPPORTED", "生成任务与内容类型不匹配") from exc
            if code == "node_locked": raise ApiError(409, "CONTENT_NODE_LOCKED", "节点已确认，不能重新生成") from exc
            if code == "outline_not_confirmed": raise ApiError(422, "OUTLINE_NOT_CONFIRMED", "请先确认框架") from exc
            if code == "copy_not_confirmed": raise ApiError(422, "IMAGE_COPY_NOT_CONFIRMED", "请先确认图片文案") from exc
            raise

    @app.post("/api/v1/jobs/{job_id}/run")
    def run_generation_job(job_id: str, background_tasks: BackgroundTasks, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        existing = content_repository.get_job(job_id)
        if existing is None:
            raise ApiError(404, "JOB_NOT_FOUND", "生成任务不存在")
        if existing["taskType"] == "image_visual":
            if existing["status"] not in {"queued", "failed", "cancelled"}:
                raise ApiError(409, "JOB_STATE_CONFLICT", "当前任务状态不能执行")
            background_tasks.add_task(content_repository.run_visual_job, user["id"], job_id, visual_provider, generated_root)
            return existing
        try:
            return content_repository.run_job(user["id"], job_id, provider)
        except ValueError as exc:
            if str(exc) == "missing": raise ApiError(404, "JOB_NOT_FOUND", "生成任务不存在") from exc
            if str(exc) == "node_locked": raise ApiError(409, "CONTENT_NODE_LOCKED", "节点已确认，不能执行历史任务") from exc
            raise ApiError(409, "JOB_STATE_CONFLICT", "当前任务状态不能执行") from exc

    @app.post("/api/v1/jobs/{job_id}/retry")
    def retry_generation_job(job_id: str, background_tasks: BackgroundTasks, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        existing = content_repository.get_job(job_id)
        if existing is None:
            raise ApiError(404, "JOB_NOT_FOUND", "生成任务不存在")
        if existing["taskType"] == "image_visual":
            if existing["status"] not in {"failed", "cancelled"}:
                raise ApiError(409, "JOB_STATE_CONFLICT", "仅失败或已取消任务可重试")
            background_tasks.add_task(content_repository.run_visual_job, user["id"], job_id, visual_provider, generated_root)
            return existing
        try:
            return content_repository.run_job(user["id"], job_id, provider)
        except ValueError as exc:
            if str(exc) == "missing": raise ApiError(404, "JOB_NOT_FOUND", "生成任务不存在") from exc
            if str(exc) == "node_locked": raise ApiError(409, "CONTENT_NODE_LOCKED", "节点已确认，不能重试历史任务") from exc
            raise ApiError(409, "JOB_STATE_CONFLICT", "仅失败或已取消任务可重试") from exc

    @app.get("/api/v1/jobs/{job_id}")
    def get_generation_job(job_id: str) -> dict[str, Any]:
        job = content_repository.get_job(job_id)
        if job is None: raise ApiError(404, "JOB_NOT_FOUND", "生成任务不存在")
        return job

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_generation_job(job_id: str, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            job = content_repository.cancel_job(user["id"], job_id)
        except ValueError as exc:
            raise ApiError(409, "JOB_STATE_CONFLICT", "已完成任务不能取消") from exc
        if job is None: raise ApiError(404, "JOB_NOT_FOUND", "生成任务不存在")
        return job

    @app.post("/api/v1/contents/{content_id}/confirm")
    def confirm_content_node(content_id: str, payload: ContentConfirmRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        allowed = {"copy", "outline", "script"}
        if payload.node not in allowed:
            raise ApiError(422, "CONTENT_NODE_INVALID", "不支持的确认节点")
        try:
            return content_repository.confirm_node(user["id"], content_id, payload.node, payload.branchId, payload.revision)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容或渠道分支不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            raise ApiError(422, "CONTENT_NODE_INCOMPLETE", "当前节点没有可确认内容") from exc

    @app.post("/api/v1/contents/{content_id}/unlock-copy")
    def unlock_image_copy(content_id: str, payload: RevisionRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            return content_repository.unlock_image_copy(user["id"], content_id, payload.revision)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容草稿不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code == "invalid_task": raise ApiError(422, "CONTENT_TYPE_INVALID", "仅图片项目支持解锁图片文案") from exc
            raise ApiError(409, "CONTENT_STATE_CONFLICT", "图片文案当前不需要解锁") from exc

    @app.put("/api/v1/contents/{content_id}/manual-output")
    def save_manual_output(content_id: str, payload: ManualOutputRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        if payload.taskType not in {"image_copy", "article_outline", "article_generate", "training_outline", "training_script"}:
            raise ApiError(422, "AI_TASK_UNSUPPORTED", "不支持的内容节点")
        try:
            return content_repository.save_manual_output(user["id"], content_id, payload.taskType, payload.branchId, payload.revision, payload.output)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容或渠道分支不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code == "invalid_task": raise ApiError(422, "AI_TASK_UNSUPPORTED", "内容节点与项目类型不匹配") from exc
            if code == "node_locked": raise ApiError(409, "CONTENT_NODE_LOCKED", "节点已确认，不能覆盖") from exc
            if code == "outline_not_confirmed": raise ApiError(422, "OUTLINE_NOT_CONFIRMED", "请先确认框架") from exc
            raise ApiError(422, "MANUAL_OUTPUT_INVALID", "人工内容结构不完整，请核对必填字段") from exc

    @app.post("/api/v1/contents/{content_id}/visual-assets")
    async def upload_visual_asset(
        content_id: str,
        revision: int,
        file: UploadFile = File(...),
        user: dict[str, Any] = Depends(require("content.edit")),
    ) -> dict[str, Any]:
        display_name = safe_display_name(file.filename or "")
        extension = Path(display_name).suffix.lower()
        allowed_mimes = {
            ".png": {"image/png", "application/octet-stream"},
            ".jpg": {"image/jpeg", "application/octet-stream"},
            ".jpeg": {"image/jpeg", "application/octet-stream"},
        }
        if extension not in allowed_mimes:
            database.audit(user["id"], "visual.upload.rejected", "content", content_id, f"拒绝替换图 {display_name}：VISUAL_FILE_TYPE_UNSUPPORTED")
            raise ApiError(415, "VISUAL_FILE_TYPE_UNSUPPORTED", "替换图仅支持 PNG、JPG、JPEG")
        supplied_mime = (file.content_type or "application/octet-stream").lower()
        if supplied_mime not in allowed_mimes[extension]:
            database.audit(user["id"], "visual.upload.rejected", "content", content_id, f"拒绝替换图 {display_name}：VISUAL_MIME_MISMATCH")
            raise ApiError(415, "VISUAL_MIME_MISMATCH", "替换图 MIME 类型与扩展名不匹配")
        chunks: list[bytes] = []
        size = 0
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > active_settings.max_visual_bytes:
                database.audit(user["id"], "visual.upload.rejected", "content", content_id, f"拒绝替换图 {display_name}：VISUAL_FILE_TOO_LARGE")
                raise ApiError(413, "VISUAL_FILE_TOO_LARGE", "替换图超过本地安全上限")
            chunks.append(chunk)
        data = b"".join(chunks)
        if not data:
            raise ApiError(422, "VISUAL_FILE_EMPTY", "替换图不能为空")
        try:
            detected_mime, detected_extension, width, height = validate_raster(data)
        except ValueError as exc:
            database.audit(user["id"], "visual.upload.rejected", "content", content_id, f"拒绝替换图 {display_name}：VISUAL_FILE_INVALID")
            raise ApiError(422, "VISUAL_FILE_INVALID", "替换图内容不是有效图片或尺寸不安全") from exc
        if detected_mime != supplied_mime and supplied_mime != "application/octet-stream":
            raise ApiError(415, "VISUAL_MIME_MISMATCH", "替换图实际类型与声明不一致")
        try:
            return content_repository.save_uploaded_visual(
                user["id"], content_id, revision, display_name, data, detected_mime,
                detected_extension, width, height, generated_root,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容草稿不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code == "copy_not_confirmed": raise ApiError(422, "IMAGE_COPY_NOT_CONFIRMED", "请先确认图片文案") from exc
            raise ApiError(422, "VISUAL_UPLOAD_INVALID", "当前内容不能保存替换图") from exc

    @app.patch("/api/v1/contents/{content_id}/visual-layout")
    def update_visual_layout(content_id: str, payload: VisualLayoutRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            return content_repository.update_visual_layout(
                user["id"], content_id, payload.revision, payload.template, payload.palette,
                payload.blockOrder, payload.hiddenBlockIds,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容草稿不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code == "visual_missing": raise ApiError(422, "VISUAL_NOT_READY", "请先生成或上传视觉素材") from exc
            if code == "required_block": raise ApiError(422, "VISUAL_REQUIRED_BLOCK", "标题、产品主体和免责声明不可隐藏") from exc
            raise ApiError(422, "VISUAL_LAYOUT_INVALID", "图片区块顺序或显隐设置无效") from exc

    @app.get("/api/v1/visual-assets/{asset_id}/preview", response_model=None)
    def preview_visual_asset(asset_id: str, _: dict[str, Any] = Depends(require("asset.view"))) -> FileResponse:
        asset = content_repository.get_visual_asset(asset_id)
        if asset is None:
            raise ApiError(404, "VISUAL_ASSET_NOT_FOUND", "视觉素材不存在或已失效")
        try:
            path = content_repository._visual_path(generated_root, asset["storage_key"])
        except ValueError as exc:
            raise ApiError(400, "UNSAFE_STORAGE_PATH", "视觉素材路径不安全") from exc
        if not path.is_file():
            raise ApiError(404, "VISUAL_FILE_MISSING", "视觉文件缺失，可重新生成或上传替换图")
        return FileResponse(path, media_type=asset["mime_type"], headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"})

    @app.get("/api/health", include_in_schema=False)
    def legacy_health() -> dict[str, Any]: return {"ok": True, "service": "baochuang-api", "version": "0.2.0"}

    @app.get("/api/config", include_in_schema=False)
    def legacy_config() -> dict[str, Any]: return {"deepseekConfigured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()), "qwenConfigured": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()), "deepseekModel": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"), "qwenModel": os.getenv("QWEN_IMAGE_MODEL", "qwen-image-3.0")}

    @app.get("/api/state", include_in_schema=False)
    def legacy_state() -> dict[str, Any]:
        products = [database.get_product(item["id"]) for item in database.list_products()]
        return {"brand": database.get_brand(), "products": [item for item in products if item], "contents": [], "reviews": [], "assets": [], "audit": database.get_recent_audit(), "settings": {"demoMode": True, "permissions": database.get_demo_user()["permissions"]}}

    @app.get("/api/v1/{path:path}")
    def api_not_found(path: str) -> None: raise ApiError(404, "API_NOT_FOUND", f"接口 /api/v1/{path} 不存在")

    dist_dir, static_dir = active_settings.frontend_dist, active_settings.frontend_dist / "static"
    if static_dir.exists(): app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa_fallback(path: str) -> Union[FileResponse, JSONResponse]:
        candidate = (dist_dir / path).resolve()
        if dist_dir.exists() and candidate.is_relative_to(dist_dir.resolve()) and candidate.is_file(): return FileResponse(candidate)
        index = dist_dir / "index.html"
        if index.exists(): return FileResponse(index)
        return JSONResponse(status_code=503, content={"error": {"code": "FRONTEND_NOT_BUILT", "message": "前端尚未构建，请先运行 npm run build"}})

    return app


app = create_app()
