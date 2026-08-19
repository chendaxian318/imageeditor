"""Small DPAPI-backed store for the viewer's default image API key."""

from __future__ import annotations

import ctypes
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Final

_HEADER: Final = b"CSIMAGE-DPAPI-1\x00"
_ENTROPY: Final = hashlib.sha256(b"CSImage\x00default-image-api-key").digest()
_MAX_SECRET_BYTES: Final = 32 * 1024
_CRYPTPROTECT_UI_FORBIDDEN: Final = 0x1


class SecureImageApiKeyError(RuntimeError):
    """Safe error raised when protected key storage is unavailable."""


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class SecureImageApiKeyStore:
    """Protect one API key with Windows DPAPI for the current user account."""

    def __init__(self, directory: Path | None = None) -> None:
        if os.name != "nt":
            raise SecureImageApiKeyError("当前系统不支持 Windows 加密凭据存储。")
        local_app_data = os.environ.get("LOCALAPPDATA")
        if directory is None and not local_app_data:
            raise SecureImageApiKeyError("无法确定本机凭据存储目录。")
        self._directory = (
            directory
            if directory is not None
            else Path(str(local_app_data)) / "CSImage" / "Credentials"
        )
        self._path = self._directory / "default-image-api.credential"

    def save(self, api_key: str) -> None:
        encoded = _validate_key(api_key)
        try:
            protected = _dpapi_transform(encoded, protect=True)
            self._directory.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self._directory,
                prefix=".default-image-api-",
                suffix=".tmp",
            )
            try:
                with os.fdopen(descriptor, "wb") as temporary:
                    descriptor = -1
                    temporary.write(_HEADER + protected)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, self._path)
                temporary_name = ""
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                if temporary_name:
                    try:
                        Path(temporary_name).unlink()
                    except FileNotFoundError:
                        pass
        except SecureImageApiKeyError:
            raise
        except Exception:
            raise SecureImageApiKeyError("无法安全保存 API 密钥。") from None

    def read(self) -> str | None:
        try:
            try:
                payload = self._path.read_bytes()
            except FileNotFoundError:
                return None
            if not payload.startswith(_HEADER):
                raise SecureImageApiKeyError("已保存的 API 密钥无法识别。")
            plaintext = bytearray(_dpapi_transform(payload[len(_HEADER) :], protect=False))
            try:
                return plaintext.decode("utf-8")
            finally:
                _zero_bytes(plaintext)
        except SecureImageApiKeyError:
            raise
        except Exception:
            raise SecureImageApiKeyError("无法读取已保存的 API 密钥。") from None

    def delete(self) -> bool:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            raise SecureImageApiKeyError("无法清除已保存的 API 密钥。") from None
        return True


def _validate_key(api_key: str) -> bytes:
    if not isinstance(api_key, str):
        raise TypeError("API 密钥必须是文本。")
    encoded = api_key.strip().encode("utf-8")
    if not encoded or len(encoded) > _MAX_SECRET_BYTES:
        raise ValueError("API 密钥为空或长度无效。")
    return encoded


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    loader = getattr(ctypes, "WinDLL", None)
    if os.name != "nt" or loader is None:
        raise SecureImageApiKeyError("当前系统不支持 Windows 加密凭据存储。")
    try:
        crypt32: Any = loader("crypt32", use_last_error=True)
        kernel32: Any = loader("kernel32", use_last_error=True)
        data_blob, data_buffer = _make_blob(data)
        entropy_blob, entropy_buffer = _make_blob(_ENTROPY)
        output_blob = _DataBlob()
        try:
            function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
            succeeded = function(
                ctypes.byref(data_blob),
                None,
                ctypes.byref(entropy_blob),
                None,
                None,
                _CRYPTPROTECT_UI_FORBIDDEN,
                ctypes.byref(output_blob),
            )
            if not succeeded:
                raise SecureImageApiKeyError("Windows 无法处理加密凭据。")
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            try:
                if output_blob.pbData:
                    ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
                    kernel32.LocalFree(output_blob.pbData)
            finally:
                ctypes.memset(data_buffer, 0, len(data_buffer))
                ctypes.memset(entropy_buffer, 0, len(entropy_buffer))
    except SecureImageApiKeyError:
        raise
    except Exception:
        raise SecureImageApiKeyError("Windows 加密凭据操作失败。") from None


def _make_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


def _zero_bytes(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0
