from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import zipfile
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Optional, Union
from urllib.parse import quote
from uuid import uuid4

from fastapi import BackgroundTasks, Depends, FastAPI, File, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from backend.config import Settings
from backend.ai_content import AIProvider, ContentRepository, DeepSeekProvider
from backend.assets import AssetRepository
from backend.database import Database
from backend.document_parser import EmptyDocumentError, parse_document
from backend.image_generation import ImageProvider, QwenImageProvider, validate_raster
from backend.review import REVIEW_STAGES, ReviewRepository


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


class ProductConfirmRequest(RevisionRequest):
    effectiveAt: Optional[str] = Field(default=None, max_length=40)

    @field_validator("effectiveAt")
    @classmethod
    def valid_effective_at(cls, value: Optional[str]) -> Optional[str]:
        if value is None or not value.strip():
            return None
        cleaned = value.strip()
        try:
            datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("生效时间格式无效") from exc
        return cleaned


class AssetReasonRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("必须填写处置原因")
        return value.strip()


class BrandUpdateRequest(BaseModel):
    companyName: str = Field(min_length=1, max_length=100)
    productName: str = Field(min_length=1, max_length=100)
    primaryColor: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    secondaryColor: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    tone: str = Field(min_length=1, max_length=500)
    slogan: str = Field(min_length=1, max_length=300)
    disclaimer: str = Field(min_length=1, max_length=2000)
    revision: int = Field(ge=1)

    @field_validator("companyName", "productName", "tone", "slogan", "disclaimer")
    @classmethod
    def clean_brand_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("品牌字段不能为空")
        return cleaned

    @field_validator("primaryColor", "secondaryColor")
    @classmethod
    def normalize_color(cls, value: str) -> str:
        return value.upper()


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


class TrainingSessionRequest(BaseModel):
    revision: int = Field(ge=1)
    chapterIndex: int = Field(ge=0, le=99)
    sentenceIndex: int = Field(ge=0, le=999)
    playbackRate: float = Field(ge=0.5, le=2.0)
    mode: str
    answers: dict[str, str] = Field(default_factory=dict)

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        if value not in {"voice", "silent"}:
            raise ValueError("不支持的播放模式")
        return value


class TrainingQuizRequest(BaseModel):
    revision: int = Field(ge=1)
    answers: list[str] = Field(min_length=3, max_length=3)


class ReviewSubmitRequest(BaseModel):
    revision: int = Field(ge=1)
    branchId: Optional[str] = None


class ReviewWithdrawRequest(BaseModel):
    revision: int = Field(ge=1)
    taskId: str = Field(min_length=1, max_length=64)


class ReviewReopenRequest(BaseModel):
    revision: int = Field(ge=1)
    branchId: Optional[str] = None


class ReviewDecisionRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)


ALLOWED_FILES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/octet-stream"},
}

BRAND_IMAGE_FILES = {
    ".png": {"image/png", "application/octet-stream"},
    ".jpg": {"image/jpeg", "application/octet-stream"},
    ".jpeg": {"image/jpeg", "application/octet-stream"},
}
BRAND_ARTICLE_FILES = {
    ".pdf": ALLOWED_FILES[".pdf"],
    ".docx": ALLOWED_FILES[".docx"],
    ".txt": {"text/plain", "application/octet-stream"},
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
    review_repository = ReviewRepository(database)
    provider = ai_provider or DeepSeekProvider()
    visual_provider = image_provider or QwenImageProvider(max_download_bytes=active_settings.max_visual_bytes)
    upload_root = active_settings.upload_dir
    generated_root = active_settings.generated_dir
    asset_repository = AssetRepository(database, generated_root)

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

    def ensure_review_permission(user: dict[str, Any], task: dict[str, Any]) -> None:
        permission = "review.product" if task["stage"] == "product" else "review.compliance"
        if permission not in user["permissions"]:
            database.audit(user["id"], "permission.denied", "permission", permission, f"拒绝访问：缺少 {permission}")
            raise ApiError(403, "PERMISSION_DENIED", f"缺少权限：{permission}")

    async def reviewer(user: dict[str, Any] = Depends(actor)) -> dict[str, Any]:
        if not {"review.product", "review.compliance"}.intersection(user["permissions"]):
            database.audit(user["id"], "permission.denied", "permission", "review.view", "拒绝访问审核中心")
            raise ApiError(403, "PERMISSION_DENIED", "缺少审核权限")
        return user

    def editable_product(product_id: str) -> dict[str, Any]:
        product = database.get_product(product_id)
        if product is None:
            raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        if product["status"] != "草稿":
            raise ApiError(409, "PRODUCT_CONFIRMED", "已确认产品不可直接覆盖，请创建新版本")
        return product

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "baochuang-api", "version": "0.2.0"}

    @app.get("/api/v1/bootstrap")
    def bootstrap() -> dict[str, Any]:
        products = database.list_products()
        featured = next((item for item in products if item["status"] == "已确认" and item["isCurrent"]), None)
        return {"brand": database.get_brand(), "user": database.get_demo_user(), "stats": database.get_stats(), "serviceStatus": {"api": "ready", "deepseek": "configured" if os.getenv("DEEPSEEK_API_KEY", "").strip() else "not_configured", "qwen": "configured" if os.getenv("DASHSCOPE_API_KEY", "").strip() else "not_configured"}, "recentAudit": database.get_recent_audit(5), "featuredProduct": featured}

    @app.get("/api/v1/products")
    def products(q: str = Query(default="", max_length=100)) -> dict[str, Any]:
        items = database.list_products(q.strip())
        return {"items": items, "total": len(items), "query": q.strip()}

    @app.get("/api/v1/brand")
    def get_brand() -> dict[str, Any]:
        return database.get_brand()

    @app.patch("/api/v1/brand")
    def update_brand(payload: BrandUpdateRequest, user: dict[str, Any] = Depends(require("brand.manage"))) -> dict[str, Any]:
        result = database.update_brand(user["id"], payload.revision, payload.model_dump(exclude={"revision"}))
        if result == "missing":
            raise ApiError(404, "BRAND_NOT_FOUND", "品牌设置不存在")
        if result == "conflict":
            raise ApiError(409, "REVISION_CONFLICT", "品牌设置已在其他页面更新，请刷新后重试")
        return database.get_brand()

    @app.post("/api/v1/brand/assets", status_code=201)
    async def upload_brand_asset(
        kind: str,
        revision: int = Query(ge=1),
        file: UploadFile = File(...),
        user: dict[str, Any] = Depends(require("brand.manage")),
    ) -> dict[str, Any]:
        if kind not in {"logo", "image", "article"}:
            raise ApiError(422, "BRAND_ASSET_KIND_INVALID", "品牌素材类型无效")
        display_name = safe_display_name(file.filename or "")
        extension = Path(display_name).suffix.lower()
        allowed = BRAND_ARTICLE_FILES if kind == "article" else BRAND_IMAGE_FILES
        if extension not in allowed:
            database.audit(user["id"], "brand.asset.rejected", "brand", "brand-demo", f"拒绝品牌素材 {display_name}：BRAND_FILE_TYPE_UNSUPPORTED")
            message = "参考文章仅支持 PDF、DOCX、TXT" if kind == "article" else "Logo 和参考图仅支持 PNG、JPG、JPEG"
            raise ApiError(415, "BRAND_FILE_TYPE_UNSUPPORTED", message)
        supplied_mime = (file.content_type or "application/octet-stream").lower()
        if supplied_mime not in allowed[extension]:
            database.audit(user["id"], "brand.asset.rejected", "brand", "brand-demo", f"拒绝品牌素材 {display_name}：BRAND_MIME_MISMATCH")
            raise ApiError(415, "BRAND_MIME_MISMATCH", "品牌素材 MIME 类型与扩展名不匹配")
        asset_id, storage_key = uuid4().hex, f"brand_{uuid4().hex}{extension}"
        path = storage_path(upload_root, storage_key)
        limit = active_settings.max_upload_bytes if kind == "article" else active_settings.max_visual_bytes
        size, digest = 0, hashlib.sha256()
        try:
            with path.open("xb") as target:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise ApiError(413, "BRAND_FILE_TOO_LARGE", "品牌素材超过本地安全上限")
                    digest.update(chunk)
                    target.write(chunk)
            if size == 0:
                raise ApiError(422, "BRAND_FILE_EMPTY", "品牌素材不能为空")
            width = height = None
            preview_text = ""
            if kind in {"logo", "image"}:
                try:
                    detected_mime, _, width, height = validate_raster(path.read_bytes())
                except ValueError as exc:
                    raise ApiError(422, "BRAND_IMAGE_INVALID", "图片内容无效或尺寸不安全") from exc
                if supplied_mime != "application/octet-stream" and supplied_mime != detected_mime:
                    raise ApiError(415, "BRAND_MIME_MISMATCH", "图片实际类型与声明不一致")
                supplied_mime = detected_mime
            elif extension == ".txt":
                try:
                    preview_text = path.read_text(encoding="utf-8")[:20_000].strip()
                except UnicodeDecodeError as exc:
                    raise ApiError(422, "BRAND_ARTICLE_INVALID", "TXT 参考文章必须为 UTF-8") from exc
            else:
                validate_file_signature(path, extension)
                try:
                    preview_text, _ = parse_document(path, extension)
                except EmptyDocumentError as exc:
                    raise ApiError(422, "BRAND_ARTICLE_EMPTY", "参考文章中未解析到文字") from exc
                preview_text = preview_text[:20_000].strip()
            if kind == "article" and not preview_text:
                raise ApiError(422, "BRAND_ARTICLE_EMPTY", "参考文章中未解析到文字")
            result = database.add_brand_asset(user["id"], revision, {
                "id": asset_id, "kind": kind, "original_name": display_name, "storage_key": storage_key,
                "mime_type": supplied_mime, "size_bytes": size, "checksum": digest.hexdigest(),
                "width": width, "height": height, "preview_text": preview_text,
            })
            if result == "conflict":
                raise ApiError(409, "REVISION_CONFLICT", "品牌设置已更新，请刷新后重试")
            if result == "limit":
                label = "Logo" if kind == "logo" else "参考图" if kind == "image" else "参考文章"
                raise ApiError(409, "BRAND_ASSET_LIMIT", f"{label}已达数量上限")
            if result != "created":
                raise ApiError(422, "BRAND_ASSET_INVALID", "品牌素材无法保存")
            return database.get_brand()
        except Exception as exc:
            if path.exists():
                path.unlink()
            if isinstance(exc, ApiError):
                database.audit(user["id"], "brand.asset.rejected", "brand", "brand-demo", f"拒绝品牌素材 {display_name}：{exc.code}")
            raise
        finally:
            await file.close()

    @app.delete("/api/v1/brand/assets/{asset_id}")
    def remove_brand_asset(asset_id: str, revision: int = Query(ge=1), user: dict[str, Any] = Depends(require("brand.manage"))) -> dict[str, Any]:
        result, key = database.remove_brand_asset(user["id"], asset_id, revision)
        if result == "conflict":
            raise ApiError(409, "REVISION_CONFLICT", "品牌设置已更新，请刷新后重试")
        if result != "removed" or key is None:
            raise ApiError(404, "BRAND_ASSET_NOT_FOUND", "品牌素材不存在")
        path = storage_path(upload_root, key)
        if path.exists():
            path.unlink()
        return database.get_brand()

    @app.post("/api/v1/brand/restore-defaults")
    def restore_brand_defaults(payload: RevisionRequest, user: dict[str, Any] = Depends(require("brand.manage"))) -> dict[str, Any]:
        result, keys = database.restore_brand_defaults(user["id"], payload.revision)
        if result == "conflict":
            raise ApiError(409, "REVISION_CONFLICT", "品牌设置已更新，请刷新后重试")
        if result != "restored":
            raise ApiError(404, "BRAND_NOT_FOUND", "品牌设置不存在")
        for key in keys:
            path = storage_path(upload_root, key)
            if path.exists():
                path.unlink()
        return database.get_brand()

    @app.get("/api/v1/brand/assets/{asset_id}/preview", response_model=None)
    def preview_brand_asset(asset_id: str, _: dict[str, Any] = Depends(actor)) -> FileResponse:
        key = database.get_brand_asset_storage_key(asset_id)
        if key is None:
            raise ApiError(404, "BRAND_ASSET_NOT_FOUND", "品牌素材不存在")
        path = storage_path(upload_root, key)
        if not path.is_file():
            raise ApiError(404, "BRAND_FILE_MISSING", "品牌素材文件缺失")
        brand = database.get_brand()
        asset = next((item for item in brand["assets"] if item["id"] == asset_id), None)
        if asset is None or asset["kind"] == "article":
            raise ApiError(415, "BRAND_PREVIEW_UNSUPPORTED", "该品牌素材不支持图片预览")
        return FileResponse(path, media_type=asset["mimeType"], headers={"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"})

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
    def confirm_product(product_id: str, payload: ProductConfirmRequest, user: dict[str, Any] = Depends(require("product.confirm"))) -> dict[str, Any]:
        result, missing = database.confirm_product(user["id"], product_id, payload.revision, payload.effectiveAt)
        if result == "missing": raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        if result == "conflict": raise ApiError(409, "REVISION_CONFLICT", "产品已更新，请刷新后重新确认")
        if result == "confirmed": raise ApiError(409, "PRODUCT_CONFIRMED", "产品已经确认")
        if result == "invalid": raise ApiError(422, "CONFIRMATION_INCOMPLETE", "关键字段或资料来源尚未完成", {"missing": missing})
        product = database.get_product(product_id)
        assert product is not None
        return product

    @app.post("/api/v1/products/{product_id}/new-version", status_code=201)
    def create_product_version(product_id: str, payload: RevisionRequest, user: dict[str, Any] = Depends(require("product.update"))) -> JSONResponse:
        result, product = database.create_product_version(user["id"], product_id, payload.revision)
        if result == "missing":
            raise ApiError(404, "PRODUCT_NOT_FOUND", "产品不存在或已被移除")
        if result == "state":
            raise ApiError(409, "PRODUCT_VERSION_STATE_INVALID", "只能从当前已确认产品创建新版本")
        if result == "conflict":
            raise ApiError(409, "REVISION_CONFLICT", "产品已更新，请刷新后重试")
        assert product is not None
        return JSONResponse(status_code=200 if result == "existing" else 201, content=product)

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
            if code == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，当前版本不可修改") from exc
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
            if str(exc) == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，当前版本不可生成") from exc
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
            if str(exc) == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，历史任务不能重试") from exc
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
        allowed = {"copy", "outline", "body", "script"}
        if payload.node not in allowed:
            raise ApiError(422, "CONTENT_NODE_INVALID", "不支持的确认节点")
        try:
            return content_repository.confirm_node(user["id"], content_id, payload.node, payload.branchId, payload.revision)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容或渠道分支不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，当前版本不可确认") from exc
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
            if code == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，请先撤回或等待审核结果") from exc
            raise ApiError(409, "CONTENT_STATE_CONFLICT", "图片文案当前不需要解锁") from exc

    @app.post("/api/v1/contents/{content_id}/unlock-script")
    def unlock_training_script(content_id: str, payload: RevisionRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            return content_repository.unlock_training_script(user["id"], content_id, payload.revision)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容草稿不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code == "invalid_task": raise ApiError(422, "CONTENT_TYPE_INVALID", "仅培训项目支持解锁讲稿") from exc
            if code == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，请先撤回或等待审核结果") from exc
            raise ApiError(409, "CONTENT_STATE_CONFLICT", "培训讲稿当前不需要解锁") from exc

    @app.patch("/api/v1/contents/{content_id}/training-session")
    def save_training_session(content_id: str, payload: TrainingSessionRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            return content_repository.save_training_session(
                user["id"], content_id, payload.revision, payload.chapterIndex,
                payload.sentenceIndex, payload.playbackRate, payload.mode, payload.answers,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "培训项目不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "培训内容已更新，请刷新后重试") from exc
            if code == "invalid_task": raise ApiError(422, "CONTENT_TYPE_INVALID", "仅培训项目支持保存播放进度") from exc
            if code == "script_not_confirmed": raise ApiError(422, "TRAINING_SCRIPT_NOT_CONFIRMED", "请先确认培训讲稿") from exc
            if code == "answers_invalid": raise ApiError(422, "TRAINING_ANSWERS_INVALID", "答案不属于当前题目选项") from exc
            raise ApiError(422, "TRAINING_PROGRESS_INVALID", "章节、字幕进度或播放设置无效") from exc

    @app.post("/api/v1/contents/{content_id}/training-quiz/submit")
    def submit_training_quiz(content_id: str, payload: TrainingQuizRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            return content_repository.submit_training_quiz(user["id"], content_id, payload.revision, payload.answers)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "培训项目不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "培训内容已更新，请刷新后重试") from exc
            if code == "invalid_task": raise ApiError(422, "CONTENT_TYPE_INVALID", "仅培训项目支持答题") from exc
            if code == "script_not_confirmed": raise ApiError(422, "TRAINING_SCRIPT_NOT_CONFIRMED", "请先确认培训讲稿") from exc
            if code == "answers_incomplete": raise ApiError(422, "TRAINING_ANSWERS_INCOMPLETE", "请完成全部 3 道题后再提交") from exc
            raise ApiError(422, "TRAINING_ANSWERS_INVALID", "答案不属于当前题目选项") from exc

    @app.post("/api/v1/contents/{content_id}/training-quiz/reset")
    def reset_training_quiz(content_id: str, payload: RevisionRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            return content_repository.reset_training_quiz(user["id"], content_id, payload.revision)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "培训项目不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "培训内容已更新，请刷新后重试") from exc
            if code == "invalid_task": raise ApiError(422, "CONTENT_TYPE_INVALID", "仅培训项目支持重新答题") from exc
            raise ApiError(422, "TRAINING_SCRIPT_NOT_CONFIRMED", "请先确认培训讲稿") from exc

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
            if code == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，当前版本不可修改") from exc
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
            if code == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，不能替换视觉素材") from exc
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
            if code == "review_locked": raise ApiError(409, "CONTENT_REVIEW_LOCKED", "内容已提交审核，不能修改图片布局") from exc
            raise ApiError(422, "VISUAL_LAYOUT_INVALID", "图片区块顺序或显隐设置无效") from exc

    @app.post("/api/v1/contents/{content_id}/submit-review", status_code=201)
    def submit_content_review(content_id: str, payload: ReviewSubmitRequest, request: Request, user: dict[str, Any] = Depends(require("content.submit"))) -> dict[str, Any]:
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key or len(idempotency_key) > 100:
            raise ApiError(422, "IDEMPOTENCY_KEY_REQUIRED", "提交审核必须提供有效幂等键")
        try:
            return review_repository.submit(user["id"], content_id, payload.revision, payload.branchId, idempotency_key)
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容或渠道分支不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code in {"branch_required", "branch_invalid"}: raise ApiError(422, "REVIEW_TARGET_INVALID", "文章必须按渠道分支提交，其他类型不能指定渠道") from exc
            if code == "jobs_active": raise ApiError(409, "CONTENT_JOBS_ACTIVE", "仍有生成任务运行中，请完成或取消后再提交") from exc
            if code == "incomplete": raise ApiError(422, "CONTENT_REVIEW_INCOMPLETE", "关键内容尚未确认或视觉素材未准备完成") from exc
            raise ApiError(409, "CONTENT_REVIEW_STATE_CONFLICT", "当前内容状态不能重复提交") from exc

    @app.post("/api/v1/contents/{content_id}/withdraw-review")
    def withdraw_content_review(content_id: str, payload: ReviewWithdrawRequest, user: dict[str, Any] = Depends(require("content.submit"))) -> dict[str, Any]:
        try:
            review_repository.withdraw(user["id"], content_id, payload.taskId, payload.revision)
            item = content_repository.get_item(content_id)
            assert item is not None
            return item
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "REVIEW_TASK_NOT_FOUND", "审核任务或内容不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            raise ApiError(409, "REVIEW_WITHDRAW_FORBIDDEN", "仅未开始的产品审核可以撤回") from exc

    @app.post("/api/v1/contents/{content_id}/reopen-review")
    def reopen_rejected_content(content_id: str, payload: ReviewReopenRequest, user: dict[str, Any] = Depends(require("content.edit"))) -> dict[str, Any]:
        try:
            review_repository.reopen(user["id"], content_id, payload.revision, payload.branchId)
            item = content_repository.get_item(content_id)
            assert item is not None
            return item
        except ValueError as exc:
            code = str(exc)
            if code == "missing": raise ApiError(404, "CONTENT_NOT_FOUND", "内容或渠道分支不存在") from exc
            if code == "conflict": raise ApiError(409, "REVISION_CONFLICT", "内容已更新，请刷新后重试") from exc
            if code in {"branch_required", "branch_invalid"}: raise ApiError(422, "REVIEW_TARGET_INVALID", "返回修改目标无效") from exc
            raise ApiError(409, "CONTENT_REVIEW_STATE_CONFLICT", "仅被驳回内容可以返回修改") from exc

    @app.get("/api/v1/reviews")
    def list_reviews(stage: str = Query(default="", max_length=20), status: str = Query(default="", max_length=30), user: dict[str, Any] = Depends(reviewer)) -> dict[str, Any]:
        if stage and stage not in REVIEW_STAGES:
            raise ApiError(422, "REVIEW_STAGE_INVALID", "审核阶段无效")
        allowed_statuses = {"pending", "in_progress", "approved", "rejected", "cancelled"}
        if status and status not in allowed_statuses:
            raise ApiError(422, "REVIEW_STATUS_INVALID", "审核状态无效")
        allowed_stages = {value for value, permission in (("product", "review.product"), ("compliance", "review.compliance")) if permission in user["permissions"]}
        if stage and stage not in allowed_stages:
            database.audit(user["id"], "permission.denied", "permission", f"review.{stage}", f"拒绝查看 {stage} 审核任务")
            raise ApiError(403, "PERMISSION_DENIED", "缺少对应阶段审核权限")
        effective_stage = stage or (next(iter(allowed_stages)) if len(allowed_stages) == 1 else "")
        items = review_repository.list_tasks(stage=effective_stage, status=status)
        return {"items": items, "total": len(items)}

    @app.get("/api/v1/reviews/{task_id}")
    def get_review(task_id: str, user: dict[str, Any] = Depends(reviewer)) -> dict[str, Any]:
        task = review_repository.get_task(task_id)
        if task is None:
            raise ApiError(404, "REVIEW_TASK_NOT_FOUND", "审核任务不存在")
        ensure_review_permission(user, task)
        return task

    @app.post("/api/v1/reviews/{task_id}/start")
    def start_review(task_id: str, user: dict[str, Any] = Depends(reviewer)) -> dict[str, Any]:
        task = review_repository.get_task(task_id)
        if task is None:
            raise ApiError(404, "REVIEW_TASK_NOT_FOUND", "审核任务不存在")
        ensure_review_permission(user, task)
        try:
            return review_repository.start(user["id"], task_id)
        except ValueError as exc:
            raise ApiError(409, "REVIEW_STATE_CONFLICT", "审核任务当前不能开始") from exc

    def decide_review(task_id: str, decision: str, payload: ReviewDecisionRequest, request: Request, user: dict[str, Any]) -> dict[str, Any]:
        task = review_repository.get_task(task_id)
        if task is None:
            raise ApiError(404, "REVIEW_TASK_NOT_FOUND", "审核任务不存在")
        ensure_review_permission(user, task)
        idempotency_key = request.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key or len(idempotency_key) > 100:
            raise ApiError(422, "IDEMPOTENCY_KEY_REQUIRED", "审核决定必须提供有效幂等键")
        try:
            return review_repository.decide(user["id"], task_id, decision, payload.reason, idempotency_key)
        except ValueError as exc:
            code = str(exc)
            if code == "reason_required": raise ApiError(422, "REVIEW_REASON_REQUIRED", "驳回必须填写原因") from exc
            raise ApiError(409, "REVIEW_STATE_CONFLICT", "审核任务已经处理或当前状态不可操作") from exc

    @app.post("/api/v1/reviews/{task_id}/approve")
    def approve_review(task_id: str, payload: ReviewDecisionRequest, request: Request, user: dict[str, Any] = Depends(reviewer)) -> dict[str, Any]:
        return decide_review(task_id, "approve", payload, request, user)

    @app.post("/api/v1/reviews/{task_id}/reject")
    def reject_review(task_id: str, payload: ReviewDecisionRequest, request: Request, user: dict[str, Any] = Depends(reviewer)) -> dict[str, Any]:
        return decide_review(task_id, "reject", payload, request, user)

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

    @app.get("/api/v1/assets")
    def list_assets(
        type: str = Query(default="", max_length=20),
        status: str = Query(default="", max_length=20),
        channel: str = Query(default="", max_length=20),
        _: dict[str, Any] = Depends(require("asset.view")),
    ) -> dict[str, Any]:
        if type and type not in {"image", "article", "training"}:
            raise ApiError(422, "ASSET_TYPE_INVALID", "素材类型无效")
        if status and status not in {"approved", "possibly_outdated", "invalid", "restore_pending"}:
            raise ApiError(422, "ASSET_STATUS_INVALID", "素材状态无效")
        if channel and channel not in {"wechat", "xiaohongshu"}:
            raise ApiError(422, "ASSET_CHANNEL_INVALID", "素材渠道无效")
        items = asset_repository.list_assets(asset_type=type, status=status, channel=channel)
        return {"items": items, "total": len(items)}

    @app.get("/api/v1/assets/{asset_id}")
    def get_asset(asset_id: str, _: dict[str, Any] = Depends(require("asset.view"))) -> dict[str, Any]:
        asset = asset_repository.get_asset(asset_id)
        if asset is None:
            raise ApiError(404, "ASSET_NOT_FOUND", "批准素材不存在")
        return asset

    @app.post("/api/v1/assets/{asset_id}/duplicate", status_code=201)
    def duplicate_asset(asset_id: str, user: dict[str, Any] = Depends(require("asset.duplicate"))) -> dict[str, Any]:
        try:
            content_id = asset_repository.duplicate(user["id"], asset_id)
        except ValueError as exc:
            raise ApiError(404, "ASSET_NOT_FOUND", "批准素材不存在或当前不可复制") from exc
        item = content_repository.get_item(content_id)
        assert item is not None
        return item

    @app.post("/api/v1/assets/{asset_id}/revise", status_code=201)
    def revise_asset(asset_id: str, user: dict[str, Any] = Depends(require("asset.duplicate"))) -> dict[str, Any]:
        try:
            content_id = asset_repository.revise_for_current_product(user["id"], asset_id)
        except ValueError as exc:
            if str(exc) == "replacement_missing":
                raise ApiError(409, "PRODUCT_REPLACEMENT_MISSING", "当前产品新版本不存在，无法创建修改草稿") from exc
            raise ApiError(409, "ASSET_STATUS_INVALID", "仅可能过期素材可以关联新产品版本修改") from exc
        item = content_repository.get_item(content_id)
        assert item is not None
        return item

    def transition_asset(asset_id: str, action: str, payload: AssetReasonRequest, user: dict[str, Any]) -> dict[str, Any]:
        try:
            return asset_repository.transition(user["id"], asset_id, action, payload.reason)
        except ValueError as exc:
            if str(exc) == "missing":
                raise ApiError(404, "ASSET_NOT_FOUND", "素材不存在") from exc
            raise ApiError(409, "ASSET_STATE_CONFLICT", "素材当前状态不能执行该处置") from exc

    @app.post("/api/v1/assets/{asset_id}/keep-valid")
    def keep_asset_valid(asset_id: str, payload: AssetReasonRequest, user: dict[str, Any] = Depends(require("asset.invalidate"))) -> dict[str, Any]:
        return transition_asset(asset_id, "keep-valid", payload, user)

    @app.post("/api/v1/assets/{asset_id}/invalidate")
    def invalidate_asset(asset_id: str, payload: AssetReasonRequest, user: dict[str, Any] = Depends(require("asset.invalidate"))) -> dict[str, Any]:
        return transition_asset(asset_id, "invalidate", payload, user)

    @app.post("/api/v1/assets/{asset_id}/restore")
    def restore_asset(asset_id: str, payload: AssetReasonRequest, user: dict[str, Any] = Depends(require("asset.invalidate"))) -> dict[str, Any]:
        return transition_asset(asset_id, "restore", payload, user)

    @app.get("/api/v1/assets/{asset_id}/download", response_model=None)
    def download_asset(asset_id: str, user: dict[str, Any] = Depends(require("asset.download"))) -> Response:
        try:
            data, media_type, filename = asset_repository.download(user["id"], asset_id)
        except ValueError as exc:
            code = str(exc)
            if code == "missing":
                raise ApiError(404, "ASSET_NOT_FOUND", "批准素材不存在") from exc
            if code == "status":
                raise ApiError(409, "ASSET_STATUS_INVALID", "仅批准素材可以下载") from exc
            if code == "file_missing":
                raise ApiError(404, "ASSET_FILE_MISSING", "批准图片的源文件缺失") from exc
            raise ApiError(422, "ASSET_TYPE_INVALID", "素材类型无法生成正式文件") from exc
        disposition = f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
        return Response(
            content=data,
            media_type=media_type,
            headers={
                "Content-Disposition": disposition,
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/api/health", include_in_schema=False)
    def legacy_health() -> dict[str, Any]: return {"ok": True, "service": "baochuang-api", "version": "0.2.0"}

    @app.get("/api/config", include_in_schema=False)
    def legacy_config() -> dict[str, Any]: return {"deepseekConfigured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()), "qwenConfigured": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()), "deepseekModel": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"), "qwenModel": os.getenv("QWEN_IMAGE_MODEL", "qwen-image-3.0")}

    @app.get("/api/state", include_in_schema=False)
    def legacy_state() -> dict[str, Any]:
        products = [database.get_product(item["id"]) for item in database.list_products()]
        return {"brand": database.get_brand(), "products": [item for item in products if item], "contents": [], "reviews": [], "assets": asset_repository.list_assets(), "audit": database.get_recent_audit(), "settings": {"demoMode": True, "permissions": database.get_demo_user()["permissions"]}}

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
