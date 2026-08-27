from __future__ import annotations

from typing import Any


BRAND: dict[str, Any] = {
    "id": "brand-demo",
    "companyName": "明远保险服务",
    "productName": "保创AI",
    "primaryColor": "#6D5EF7",
    "secondaryColor": "#19BFA1",
    "tone": "专业、亲切、克制，不制造焦虑",
    "slogan": "一份产品资料，生成全套营销内容",
    "disclaimer": "本文仅供保险知识交流，具体保障责任、责任免除及投保规则以保险合同条款为准。",
}

DEMO_USER: dict[str, Any] = {
    "id": "super-demo",
    "name": "超级演示用户",
    "mode": "local_demo",
    "roles": ["企业管理员", "内容创作者", "产品审核员", "合规审核员"],
}

VIEWER_USER: dict[str, Any] = {
    "id": "viewer-demo",
    "name": "只读演示用户",
    "mode": "local_demo",
    "roles": ["只读访客"],
}

PERMISSIONS = [
    "brand.manage",
    "product.create",
    "product.update",
    "product.confirm",
    "content.create",
    "content.edit",
    "content.submit",
    "review.product",
    "review.compliance",
    "asset.view",
    "asset.download",
    "asset.duplicate",
    "asset.invalidate",
    "user.manage",
    "audit.view",
]

ROLE_PERMISSIONS: dict[str, list[str]] = {
    "企业管理员": ["brand.manage", "asset.view", "asset.download", "asset.duplicate", "asset.invalidate", "user.manage", "audit.view"],
    "内容创作者": ["product.create", "product.update", "content.create", "content.edit", "content.submit", "asset.view", "asset.download", "asset.duplicate"],
    "产品审核员": ["product.confirm", "review.product", "asset.view"],
    "合规审核员": ["review.compliance", "asset.view", "asset.invalidate"],
    "只读访客": [],
}

PERMISSION_DESCRIPTIONS: dict[str, str] = {
    "brand.manage": "管理品牌字段与参考素材",
    "product.create": "创建产品草稿",
    "product.update": "更新产品草稿与版本",
    "product.confirm": "确认产品关键事实",
    "content.create": "创建内容项目",
    "content.edit": "编辑内容草稿",
    "content.submit": "提交不可变内容版本",
    "review.product": "执行产品审核",
    "review.compliance": "执行合规审核",
    "asset.view": "查看素材库",
    "asset.download": "下载批准素材",
    "asset.duplicate": "复制批准素材为草稿",
    "asset.invalidate": "处置过期与失效素材",
    "user.manage": "查看演示权限矩阵（正式版用于用户管理）",
    "audit.view": "查看不可变操作记录",
}

PRODUCT: dict[str, Any] = {
    "id": "darwin-12",
    "name": "复星联合达尔文12号重大疾病保险（互联网）",
    "insurer": "复星联合健康保险",
    "category": "重大疾病保险",
    "version": "V1",
    "status": "已确认",
    "sourceType": "预置演示资料",
    "summary": "重疾赔付后轻中症责任仍可继续赔付，无分组、无间隔期；意外导致重疾可额外赔付；支持多项可选责任组合。",
    "updatedAt": "2026-08-26T09:00:00+08:00",
    "features": [
        "重疾赔付后轻中症仍可继续赔付",
        "中症60%基本保额，最多3次",
        "轻症30%基本保额，最多4次",
        "可选重疾多次给付与恶性肿瘤治疗津贴",
        "保障期间可选终身或保至70岁",
    ],
    "facts": [
        {"label": "投保年龄", "value": "28天（含）至55周岁（含）", "confirmed": True},
        {"label": "保障期间", "value": "终身、保至70岁", "confirmed": True},
        {"label": "承保职业", "value": "1-4类", "confirmed": True},
        {"label": "等待期", "value": "180天", "confirmed": True},
        {"label": "缴费期间", "value": "趸交、5年、10年、20年、30年、35年", "confirmed": True},
        {"label": "犹豫期", "value": "15日", "confirmed": True},
        {"label": "销售区域", "value": "中国大陆", "confirmed": True},
    ],
    "documents": [
        {"name": "产品详情页资料", "type": "网页", "status": "已解析"},
        {"name": "保险条款", "type": "PDF", "status": "演示占位"},
        {"name": "产品说明书", "type": "PDF", "status": "演示占位"},
        {"name": "费率表", "type": "XLSX", "status": "演示占位"},
        {"name": "投保规则", "type": "PDF", "status": "演示占位"},
    ],
}
