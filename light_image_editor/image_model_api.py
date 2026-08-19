"""Lightweight, provider-configurable image-edit API client for the Qt viewer."""

from __future__ import annotations

import base64
import json
import secrets
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.parse import urlparse, urlunparse

from PIL import Image, ImageOps

from .image_loader import decode_image

DEFAULT_IMAGE_EDIT_ENDPOINT = "https://api.openai.com/v1/images/edits"
DEFAULT_IMAGE_EDIT_MODEL = "gpt-image-2"
ADAPTIVE_REVERSE_IMAGE_MODELS = frozenset({"gpt-image-2-all"})
MAX_SOURCE_BYTES = 50 * 1024 * 1024
MAX_RESPONSE_BYTES = 80 * 1024 * 1024
MAX_RESULT_PIXELS = 64_000_000
MAX_CONNECTION_RESPONSE_BYTES = 2 * 1024 * 1024


class ImageModelApiError(RuntimeError):
    """Safe, user-facing failure raised at the remote image-edit boundary."""


@dataclass(frozen=True, slots=True)
class ImageTextEditApiRequest:
    source_path: Path
    endpoint: str
    model: str
    api_key: str = field(repr=False)
    original_text: str = ""
    replacement_text: str = ""
    instruction: str = ""
    quality: str = "medium"

    def __post_init__(self) -> None:
        source = self.source_path.resolve()
        endpoint = self.endpoint.strip()
        model = self.model.strip()
        original = self.original_text.strip()
        replacement = self.replacement_text.strip()
        instruction = self.instruction.strip()
        key = self.api_key.strip()
        _validate_endpoint(endpoint)
        if not source.is_file():
            raise ValueError("当前图片文件不存在。")
        if not model or len(model) > 160:
            raise ValueError("模型名称不能为空或过长。")
        if not replacement and not instruction:
            raise ValueError("请输入新文字，或在补充要求中说明要修改的内容。")
        if any(len(value) > 8_000 for value in (original, replacement, instruction)):
            raise ValueError("文字修改要求过长。")
        if self.quality not in {"low", "medium", "high"}:
            raise ValueError("图片质量设置无效。")
        if not key and not _is_local_endpoint(endpoint):
            raise ValueError("远程 API 需要填写 API Key。")
        object.__setattr__(self, "source_path", source)
        object.__setattr__(self, "endpoint", endpoint)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "api_key", key)
        object.__setattr__(self, "original_text", original)
        object.__setattr__(self, "replacement_text", replacement)
        object.__setattr__(self, "instruction", instruction)

    @property
    def prompt(self) -> str:
        requirements = [
            "只修改图片中的目标文字及其必要的边缘像素，其他人物、物体、背景和构图保持不变。",
            "新文字必须延续原文字的字体风格、字重、字号、颜色、渐变、描边、阴影、发光、纹理、排版、透视与弯曲效果。",
            "清除旧文字时自然补全被遮挡背景，不得留下重影、乱码或额外文字。",
            "保持输出图片的宽高比和主体位置不变。",
        ]
        if self.original_text and self.replacement_text:
            requirements.insert(
                0,
                f"将图片中的文字“{self.original_text}”准确替换为“{self.replacement_text}”。",
            )
        elif self.replacement_text:
            requirements.insert(
                0, f"识别图片中最主要的目标文字，并替换为“{self.replacement_text}”。"
            )
        if self.instruction:
            requirements.append(f"用户补充要求：{self.instruction}")
        return "\n".join(requirements)


@dataclass(frozen=True, slots=True)
class ImageTextEditApiResult:
    png_bytes: bytes
    prompt: str


class _Response(Protocol):
    def read(self, amount: int = -1) -> bytes: ...

    def __enter__(self) -> _Response: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> object: ...


UrlOpener = Callable[..., _Response]


def check_image_api_connection(
    endpoint: str,
    api_key: str,
    *,
    timeout: float = 15.0,
    opener: UrlOpener | None = None,
) -> str:
    """Verify an OpenAI-compatible credential without uploading an image."""

    normalized_endpoint = endpoint.strip()
    key = api_key.strip()
    _validate_endpoint(normalized_endpoint)
    if not key and not _is_local_endpoint(normalized_endpoint):
        raise ValueError("请先填写 API 密钥。")
    models_endpoint = _models_endpoint(normalized_endpoint)
    headers = {
        "Accept": "application/json",
        "User-Agent": "CSImage/2.0",
    }
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(models_endpoint, headers=headers, method="GET")
    open_url = opener or cast(UrlOpener, urllib.request.urlopen)
    try:
        with open_url(request, timeout=timeout) as response:
            payload = _read_limited(response, MAX_CONNECTION_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        detail = _safe_http_error(error)
        raise ImageModelApiError(f"验证失败（HTTP {error.code}）：{detail}") from None
    except urllib.error.URLError as error:
        reason = str(error.reason).strip() or "无法连接服务器"
        raise ImageModelApiError(f"无法连接 AI 服务：{reason}") from None
    except TimeoutError:
        raise ImageModelApiError("连接验证超时，请检查网络或服务地址。") from None
    except OSError as error:
        raise ImageModelApiError(f"连接验证失败：{error}") from None

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ImageModelApiError("服务已连接，但返回内容不是兼容的 JSON。") from None
    if not isinstance(decoded, dict):
        raise ImageModelApiError("服务已连接，但返回内容不兼容。")
    if isinstance(decoded.get("error"), dict):
        message = str(decoded["error"].get("message", "密钥或服务配置无效"))[:500]
        raise ImageModelApiError(f"验证失败：{message}")
    return "连接成功，API 密钥可用。"


def edit_image_text(
    request: ImageTextEditApiRequest,
    *,
    timeout: float = 300.0,
    opener: UrlOpener | None = None,
) -> ImageTextEditApiResult:
    """Upload one normalized PNG and return a validated PNG result."""

    image_bytes, source_size = _encode_source_png(request.source_path)
    boundary = f"----CSImage{secrets.token_hex(18)}"
    fields = {
        "model": request.model,
        "prompt": request.prompt,
    }
    if request.model in ADAPTIVE_REVERSE_IMAGE_MODELS:
        fields["response_format"] = "b64_json"
    else:
        fields.update(
            {
                "size": "auto",
                "quality": request.quality,
                "output_format": "png",
            }
        )
    body = _multipart_body(
        boundary,
        fields=fields,
        filename=f"{request.source_path.stem}.png",
        image_bytes=image_bytes,
    )
    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Accept": "application/json",
        "User-Agent": "CSImage/2.0",
    }
    if request.api_key:
        headers["Authorization"] = f"Bearer {request.api_key}"
    http_request = urllib.request.Request(
        request.endpoint,
        data=body,
        headers=headers,
        method="POST",
    )
    open_url = opener or cast(UrlOpener, urllib.request.urlopen)
    try:
        with open_url(http_request, timeout=timeout) as response:
            payload = _read_limited(response, MAX_RESPONSE_BYTES)
    except urllib.error.HTTPError as error:
        detail = _safe_http_error(error)
        raise ImageModelApiError(f"图片编辑 API 返回 HTTP {error.code}：{detail}") from None
    except urllib.error.URLError as error:
        reason = str(error.reason).strip() or "无法连接服务器"
        raise ImageModelApiError(f"无法连接图片编辑 API：{reason}") from None
    except TimeoutError:
        raise ImageModelApiError("图片编辑请求超时；服务端状态未知，请先检查服务记录。") from None
    except OSError as error:
        raise ImageModelApiError(f"图片编辑请求失败：{error}") from None

    try:
        decoded = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ImageModelApiError("图片编辑 API 返回了无法识别的响应。") from None
    image_payload = _image_payload_from_response(decoded)
    if image_payload.startswith(("https://", "http://")):
        result_bytes = _download_result(image_payload, timeout, open_url)
    else:
        if image_payload.startswith("data:") and "," in image_payload:
            image_payload = image_payload.split(",", 1)[1]
        try:
            result_bytes = base64.b64decode(image_payload, validate=True)
        except (ValueError, TypeError):
            raise ImageModelApiError("图片编辑 API 返回的图像数据无效。") from None
    return ImageTextEditApiResult(
        png_bytes=_normalize_result_png(result_bytes, target_size=source_size),
        prompt=request.prompt,
    )


def _models_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    path = parsed.path.rstrip("/")
    for suffix in ("/images/edits", "/images/generations"):
        if path.endswith(suffix):
            path = f"{path[: -len(suffix)]}/models"
            break
    else:
        path = f"{path}/models"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def _validate_endpoint(endpoint: str) -> None:
    if not endpoint or len(endpoint) > 2_048:
        raise ValueError("API 地址不能为空或过长。")
    parsed = urlparse(endpoint)
    if not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("API 地址格式无效。")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and _is_local_endpoint(endpoint):
        return
    raise ValueError("远程 API 必须使用 HTTPS；本机服务可以使用 HTTP。")


def _is_local_endpoint(endpoint: str) -> bool:
    host = (urlparse(endpoint).hostname or "").casefold()
    return host in {"localhost", "127.0.0.1", "::1"}


def _encode_source_png(path: Path) -> tuple[bytes, tuple[int, int]]:
    image = decode_image(path)
    try:
        if image.width * image.height > MAX_RESULT_PIXELS:
            raise ImageModelApiError("图片像素过大，请先在“改图”中缩小尺寸后再调用模型。")
        source_size = image.size
        buffer = BytesIO()
        image.save(buffer, "PNG", optimize=True)
        encoded = buffer.getvalue()
    finally:
        image.close()
    if len(encoded) > MAX_SOURCE_BYTES:
        raise ImageModelApiError("图片编码后超过 50 MB，请先缩小尺寸后再调用模型。")
    return encoded, source_size


def _multipart_body(
    boundary: str,
    *,
    fields: dict[str, str],
    filename: str,
    image_bytes: bytes,
) -> bytes:
    safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
    chunks: list[bytes] = []
    marker = boundary.encode("ascii")
    for name, value in fields.items():
        chunks.extend(
            (
                b"--" + marker + b"\r\n",
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("ascii"),
                value.encode("utf-8"),
                b"\r\n",
            )
        )
    chunks.extend(
        (
            b"--" + marker + b"\r\n",
            (
                f'Content-Disposition: form-data; name="image"; filename="{safe_filename}"\r\n'
            ).encode(),
            b"Content-Type: image/png\r\n\r\n",
            image_bytes,
            b"\r\n--" + marker + b"--\r\n",
        )
    )
    return b"".join(chunks)


def _read_limited(response: _Response, limit: int) -> bytes:
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ImageModelApiError("图片编辑 API 返回的数据过大。")
    return payload


def _safe_http_error(error: urllib.error.HTTPError) -> str:
    try:
        payload = error.read(64 * 1024)
        decoded = json.loads(payload.decode("utf-8"))
        if isinstance(decoded, dict):
            nested = decoded.get("error")
            if isinstance(nested, dict) and isinstance(nested.get("message"), str):
                return nested["message"][:500]
            if isinstance(decoded.get("message"), str):
                return str(decoded["message"])[:500]
    except Exception:
        pass
    return str(error.reason).strip()[:500] or "请求失败"


def _image_payload_from_response(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ImageModelApiError("图片编辑 API 响应缺少结果对象。")
    candidates: list[Any] = []
    data = payload.get("data")
    if isinstance(data, list):
        candidates.extend(data)
    output = payload.get("output")
    if isinstance(output, list):
        candidates.extend(output)
    elif isinstance(output, dict):
        candidates.append(output)
    candidates.append(payload)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ("b64_json", "image_base64", "url"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    raise ImageModelApiError("图片编辑 API 没有返回图像结果。")


def _download_result(
    url: str,
    timeout: float,
    opener: UrlOpener,
) -> bytes:
    _validate_endpoint(url)
    headers = {"Accept": "image/*", "User-Agent": "CSImage/2.0"}
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener(request, timeout=timeout) as response:
            return _read_limited(response, MAX_RESPONSE_BYTES)
    except (urllib.error.URLError, OSError) as error:
        raise ImageModelApiError(f"无法下载图片编辑结果：{error}") from None


def _normalize_result_png(
    payload: bytes,
    *,
    target_size: tuple[int, int] | None = None,
) -> bytes:
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise ImageModelApiError("图片编辑结果为空或过大。")
    try:
        with Image.open(BytesIO(payload)) as source:
            source.load()
            if source.width * source.height > MAX_RESULT_PIXELS:
                raise ImageModelApiError("图片编辑结果像素过大。")
            converted = source.copy()
    except ImageModelApiError:
        raise
    except Exception:
        raise ImageModelApiError("图片编辑结果不是有效图片。") from None
    try:
        if target_size is not None and converted.size != target_size:
            fitted = ImageOps.fit(
                converted,
                target_size,
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            converted.close()
            converted = fitted
        buffer = BytesIO()
        converted.save(buffer, "PNG", optimize=True)
        return buffer.getvalue()
    finally:
        converted.close()
