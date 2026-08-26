from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.config import Settings
from backend.database import Database


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    active_settings = settings or Settings.from_environment()
    database = Database(active_settings.database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.initialize()
        yield

    app = FastAPI(
        title="保创AI API",
        version="0.1.0",
        docs_url="/api/docs" if os.getenv("APP_ENV") != "production" else None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.database = database
    app.state.settings = active_settings

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else "请求无法处理"
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": "HTTP_ERROR", "message": detail}})

    @app.get("/api/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "service": "baochuang-api", "version": "0.1.0"}

    @app.get("/api/v1/bootstrap")
    def bootstrap() -> dict[str, Any]:
        return {
            "brand": database.get_brand(),
            "user": database.get_demo_user(),
            "stats": database.get_stats(),
            "serviceStatus": {
                "api": "ready",
                "deepseek": "configured" if os.getenv("DEEPSEEK_API_KEY", "").strip() else "not_configured",
                "qwen": "configured" if os.getenv("DASHSCOPE_API_KEY", "").strip() else "not_configured",
            },
            "recentAudit": database.get_recent_audit(),
        }

    @app.get("/api/v1/products")
    def products(q: str = Query(default="", max_length=100)) -> dict[str, Any]:
        items = database.list_products(q.strip())
        return {"items": items, "total": len(items), "query": q.strip()}

    @app.get("/api/v1/products/{product_id}")
    def product_detail(product_id: str) -> dict[str, Any]:
        product = database.get_product(product_id)
        if product is None:
            raise HTTPException(status_code=404, detail="产品不存在或已被移除")
        return product

    # Stage 00 compatibility endpoints remain read-only so the previously accepted
    # local health/config/state checks continue to work during Stage 01.
    @app.get("/api/health", include_in_schema=False)
    def legacy_health() -> dict[str, Any]:
        return {"ok": True, "service": "baochuang-api", "version": "0.1.0"}

    @app.get("/api/config", include_in_schema=False)
    def legacy_config() -> dict[str, Any]:
        return {
            "deepseekConfigured": bool(os.getenv("DEEPSEEK_API_KEY", "").strip()),
            "qwenConfigured": bool(os.getenv("DASHSCOPE_API_KEY", "").strip()),
            "deepseekModel": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
        }

    @app.get("/api/state", include_in_schema=False)
    def legacy_state() -> dict[str, Any]:
        products = [database.get_product(item["id"]) for item in database.list_products()]
        return {
            "brand": database.get_brand(),
            "products": [item for item in products if item is not None],
            "contents": [],
            "reviews": [],
            "assets": [],
            "audit": database.get_recent_audit(),
            "settings": {"demoMode": True, "permissions": database.get_demo_user()["permissions"]},
        }

    @app.get("/api/v1/{path:path}")
    def api_not_found(path: str) -> None:
        raise HTTPException(status_code=404, detail=f"接口 /api/v1/{path} 不存在")

    dist_dir = active_settings.frontend_dist
    static_dir = dist_dir / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa_fallback(path: str) -> Union[FileResponse, JSONResponse]:
        candidate = (dist_dir / path).resolve()
        if dist_dir.exists() and candidate.is_relative_to(dist_dir.resolve()) and candidate.is_file():
            return FileResponse(candidate)
        index = dist_dir / "index.html"
        if index.exists():
            return FileResponse(index)
        return JSONResponse(status_code=503, content={"error": {"code": "FRONTEND_NOT_BUILT", "message": "前端尚未构建，请先运行 npm run build"}})

    return app


app = create_app()
