from __future__ import annotations

import os
import struct
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Protocol
from urllib.parse import urlparse

import httpx


class VisualProviderError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message


class VisualCancelled(Exception):
    pass


@dataclass(frozen=True)
class VisualResult:
    data: bytes
    mime_type: str
    width: int
    height: int


class ImageProvider(Protocol):
    name: str
    model: str

    def generate(
        self,
        snapshot: dict[str, Any],
        *,
        on_task_created: Callable[[str], None],
        is_cancelled: Callable[[], bool],
    ) -> VisualResult: ...


def validate_raster(data: bytes, *, qwen_output: bool = False) -> tuple[str, str, int, int]:
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
        width, height = struct.unpack(">II", data[16:24])
        mime_type, extension = "image/png", ".png"
    elif not qwen_output and data.startswith(b"\xff\xd8"):
        width, height = _jpeg_dimensions(data)
        mime_type, extension = "image/jpeg", ".jpg"
    else:
        raise ValueError("unsupported image signature")
    if width < 64 or height < 64 or width > 8192 or height > 8192 or width * height > 24_000_000:
        raise ValueError("unsafe image dimensions")
    return mime_type, extension, width, height


def _jpeg_dimensions(data: bytes) -> tuple[int, int]:
    position = 2
    while position + 9 < len(data):
        if data[position] != 0xFF:
            position += 1
            continue
        marker = data[position + 1]
        position += 2
        if marker in {0xD8, 0xD9}:
            continue
        if position + 2 > len(data):
            break
        segment_length = int.from_bytes(data[position:position + 2], "big")
        if segment_length < 2 or position + segment_length > len(data):
            break
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            height = int.from_bytes(data[position + 3:position + 5], "big")
            width = int.from_bytes(data[position + 5:position + 7], "big")
            return width, height
        position += segment_length
    raise ValueError("invalid jpeg structure")


def image_size_for(snapshot: dict[str, Any]) -> str:
    image_type = str(snapshot.get("settings", {}).get("imageType", "长图"))
    return {
        "长图": "1024*1536",
        "宣传长图": "1024*1536",
        "营销海报": "1024*1024",
        "公众号头图": "1536*768",
    }.get(image_type, "1024*1536")


def visual_prompt(snapshot: dict[str, Any]) -> str:
    copy = snapshot.get("confirmedCopy") if isinstance(snapshot.get("confirmedCopy"), dict) else {}
    settings = snapshot.get("settings") if isinstance(snapshot.get("settings"), dict) else {}
    brand = snapshot.get("brand") if isinstance(snapshot.get("brand"), dict) else {}
    selling_points = copy.get("sellingPoints") if isinstance(copy.get("sellingPoints"), list) else []
    blocks = copy.get("blocks") if isinstance(copy.get("blocks"), list) else []
    visual_topics = [str(item.get("text", "")) for item in blocks if isinstance(item, dict) and item.get("text")]
    return "\n".join([
        "生成一张专业、可信、现代的保险营销视觉背景素材。",
        f"成品类型：{settings.get('imageType', '宣传长图')}；受众：{settings.get('audience', '家庭用户')}；目的：{settings.get('purpose', '产品科普')}。",
        f"内容主题：{copy.get('title', '')}。视觉语义参考：{'；'.join(map(str, selling_points + visual_topics))[:1800]}。",
        f"品牌基调：{brand.get('tone', '专业、亲切、克制')}；品牌主色 {brand.get('primaryColor', '#6D5EF7')}，辅助色 {brand.get('secondaryColor', '#19BFA1')}。",
        "只生成背景、人物或抽象插图，不要在图中绘制任何汉字、英文字母、数字、Logo、商标、二维码、表格、印章、水印或界面文字。",
        "为后续程序排版预留清晰留白：顶部标题区、中部信息卡片区、底部免责声明区；主体不要遮挡这些区域。",
        "人物如出现应为自然成年亚洲人，场景温暖稳重，避免医疗器械恐吓画面、夸张收益暗示和不当承诺。",
    ])


class QwenImageProvider:
    name = "qwen"

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        poll_timeout: Optional[int] = None,
        max_download_bytes: int = 15 * 1024 * 1024,
        client_factory: Optional[Callable[..., Any]] = None,
    ):
        self.api_key = api_key if api_key is not None else os.getenv("DASHSCOPE_API_KEY", "").strip()
        self.model = model or os.getenv("QWEN_IMAGE_MODEL", "qwen-image-3.0")
        self.base_url = (base_url or os.getenv("DASHSCOPE_API_BASE_URL", "").strip() or self._default_base_url()).rstrip("/")
        self.poll_timeout = poll_timeout if poll_timeout is not None else int(os.getenv("QWEN_IMAGE_TIMEOUT_SECONDS", "180"))
        self.max_download_bytes = max_download_bytes
        self.client_factory = client_factory or httpx.Client

    @staticmethod
    def _default_base_url() -> str:
        workspace_id = os.getenv("DASHSCOPE_WORKSPACE_ID", "").strip()
        region = os.getenv("DASHSCOPE_REGION", "beijing").strip().lower()
        domains = {
            "beijing": "cn-beijing.maas.aliyuncs.com",
            "singapore": "ap-southeast-1.maas.aliyuncs.com",
            "frankfurt": "eu-central-1.maas.aliyuncs.com",
            "tokyo": "ap-northeast-1.maas.aliyuncs.com",
        }
        if workspace_id and region in domains:
            return f"https://{workspace_id}.{domains[region]}/api/v1"
        if region == "singapore":
            return "https://dashscope-intl.aliyuncs.com/api/v1"
        return "https://dashscope.aliyuncs.com/api/v1"

    def generate(
        self,
        snapshot: dict[str, Any],
        *,
        on_task_created: Callable[[str], None],
        is_cancelled: Callable[[], bool],
    ) -> VisualResult:
        if not self.api_key:
            raise VisualProviderError("CONFIG_QWEN_KEY_MISSING", "千问 API Key 未配置，已保留全部输入")
        if self.model not in {"qwen-image-3.0", "qwen-image-3.0-pro"}:
            raise VisualProviderError("QWEN_MODEL_INVALID", "千问图像模型配置无效")
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-DashScope-Async": "enable",
        }
        body = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": [{"text": visual_prompt(snapshot)}]}]},
            "parameters": {
                "prompt_extend": True,
                "prompt_extend_method": "direct",
                "enable_thinking": False,
                "n": 1,
                "size": image_size_for(snapshot),
                "negative_prompt": "文字，汉字，字母，数字，Logo，商标，二维码，表格，水印，签名，印章，低清晰度，畸形手指",
                "watermark": False,
            },
        }
        timeout = httpx.Timeout(30.0, connect=10.0)
        try:
            with self.client_factory(timeout=timeout, follow_redirects=False) as client:
                response = client.post(f"{self.base_url}/services/aigc/image-generation/generation", headers=headers, json=body)
                payload = self._checked_json(response)
                task_id = str(payload.get("output", {}).get("task_id", "")).strip()
                if not task_id:
                    raise VisualProviderError("QWEN_OUTPUT_INVALID", "千问未返回有效任务编号，可稍后重试")
                on_task_created(task_id)
                deadline = time.monotonic() + self.poll_timeout
                while time.monotonic() < deadline:
                    if is_cancelled():
                        self._cancel_provider_task(client, headers, task_id)
                        raise VisualCancelled()
                    time.sleep(2)
                    poll = client.get(f"{self.base_url}/tasks/{task_id}", headers={"Authorization": headers["Authorization"]})
                    result = self._checked_json(poll)
                    output = result.get("output", {})
                    status = str(output.get("task_status", "")).upper()
                    if status in {"PENDING", "RUNNING"}:
                        continue
                    if status == "CANCELED":
                        raise VisualCancelled()
                    if status == "FAILED":
                        code = str(output.get("code", result.get("code", "")))
                        self._raise_provider_code(code)
                    if status != "SUCCEEDED":
                        raise VisualProviderError("QWEN_OUTPUT_INVALID", "千问返回了无法识别的任务状态，可重试")
                    image_url = self._image_url(output)
                    data = self._download(client, image_url)
                    mime_type, _, width, height = validate_raster(data, qwen_output=True)
                    return VisualResult(data=data, mime_type=mime_type, width=width, height=height)
        except VisualCancelled:
            raise
        except VisualProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise VisualProviderError("QWEN_TIMEOUT", "千问图片服务响应超时，输入已保留，可重试") from exc
        except httpx.HTTPError as exc:
            raise VisualProviderError("QWEN_NETWORK_ERROR", "暂时无法连接千问图片服务，可稍后重试") from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise VisualProviderError("QWEN_OUTPUT_INVALID", "千问图片结果无效，输入已保留，可重试") from exc
        raise VisualProviderError("QWEN_TIMEOUT", "千问图片生成超时，输入已保留，可重试")

    def _checked_json(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 429:
            raise VisualProviderError("QWEN_RATE_LIMITED", "千问服务限流或额度不足，请稍后重试")
        if response.status_code in {401, 403}:
            raise VisualProviderError("QWEN_AUTH_FAILED", "千问配置无效，请检查服务端 Key、地域和业务空间")
        if response.status_code >= 400:
            raise VisualProviderError("QWEN_PROVIDER_ERROR", f"千问图片服务暂时不可用（{response.status_code}）")
        value = response.json()
        if not isinstance(value, dict):
            raise VisualProviderError("QWEN_OUTPUT_INVALID", "千问返回内容无效，可重试")
        if value.get("code"):
            self._raise_provider_code(str(value["code"]))
        return value

    @staticmethod
    def _raise_provider_code(code: str) -> None:
        normalized = code.lower()
        if "inspection" in normalized or "inappropriate" in normalized:
            raise VisualProviderError("QWEN_CONTENT_BLOCKED", "图片请求或结果被内容安全策略拦截，请调整视觉描述后重试")
        if "limit" in normalized or "quota" in normalized or "throttl" in normalized:
            raise VisualProviderError("QWEN_RATE_LIMITED", "千问服务限流或额度不足，请稍后重试")
        if "key" in normalized or "auth" in normalized:
            raise VisualProviderError("QWEN_AUTH_FAILED", "千问配置无效，请检查服务端 Key、地域和业务空间")
        raise VisualProviderError("QWEN_PROVIDER_ERROR", "千问图片任务失败，输入已保留，可重试")

    @staticmethod
    def _image_url(output: dict[str, Any]) -> str:
        url = str(output["choices"][0]["message"]["content"][0]["image"])
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise ValueError("unsafe result url")
        if not (host == "aliyuncs.com" or host.endswith(".aliyuncs.com")):
            raise ValueError("untrusted result host")
        return url

    def _download(self, client: httpx.Client, image_url: str) -> bytes:
        with client.stream("GET", image_url) as response:
            if response.status_code != 200:
                raise VisualProviderError("QWEN_RESULT_EXPIRED", "千问图片临时链接不可用，请重新生成")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type not in {"image/png", "application/octet-stream"}:
                raise VisualProviderError("QWEN_OUTPUT_INVALID", "千问返回的文件类型无效")
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes(1024 * 1024):
                size += len(chunk)
                if size > self.max_download_bytes:
                    raise VisualProviderError("QWEN_IMAGE_TOO_LARGE", "千问返回图片超过本地安全上限")
                chunks.append(chunk)
        return b"".join(chunks)

    def _cancel_provider_task(self, client: httpx.Client, headers: dict[str, str], task_id: str) -> None:
        try:
            client.post(f"{self.base_url}/tasks/{task_id}/cancel", headers={"Authorization": headers["Authorization"]})
        except httpx.HTTPError:
            pass
