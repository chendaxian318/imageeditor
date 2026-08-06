"""Secret storage protocols and Windows DPAPI-backed credentials."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

_TARGET_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_MAX_SECRET_BYTES: Final = 32 * 1024
_FILE_HEADER: Final = b"LIE-DPAPI-1\x00"
_CRYPTPROTECT_UI_FORBIDDEN: Final = 0x1


class CredentialStoreError(RuntimeError):
    """Base credential failure with deliberately non-sensitive messages."""

    code = "credential_store_error"


class CredentialAlreadyExistsError(CredentialStoreError):
    code = "credential_already_exists"

    def __init__(self) -> None:
        super().__init__("The credential already exists; overwrite was not authorized.")


class CredentialBackendUnavailableError(CredentialStoreError):
    code = "credential_backend_unavailable"

    def __init__(self) -> None:
        super().__init__("The protected credential backend is unavailable.")


class CredentialBackendError(CredentialStoreError):
    code = "credential_backend_error"

    def __init__(self) -> None:
        super().__init__("The protected credential operation failed.")


@runtime_checkable
class CredentialStore(Protocol):
    """Minimal provider credential storage boundary."""

    def save(self, target: str, name: str, secret: str, *, overwrite: bool = False) -> None: ...

    def read(self, target: str, name: str) -> str | None: ...

    def delete(self, target: str, name: str) -> bool: ...


class MemoryCredentialStore:
    """Thread-safe in-memory implementation intended for tests and ephemeral use."""

    def __init__(self) -> None:
        self._credentials: dict[tuple[str, str], str] = {}
        self._lock = threading.Lock()

    def save(self, target: str, name: str, secret: str, *, overwrite: bool = False) -> None:
        key = _validated_key(target, name)
        _validate_secret(secret)
        _validate_overwrite(overwrite)
        with self._lock:
            if key in self._credentials and not overwrite:
                raise CredentialAlreadyExistsError
            self._credentials[key] = secret

    def read(self, target: str, name: str) -> str | None:
        key = _validated_key(target, name)
        with self._lock:
            return self._credentials.get(key)

    def delete(self, target: str, name: str) -> bool:
        key = _validated_key(target, name)
        with self._lock:
            return self._credentials.pop(key, None) is not None


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_ulong),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


class WindowsDpapiCredentialStore:
    """Stores per-user DPAPI ciphertext outside application settings."""

    def __init__(self, directory: Path | None = None) -> None:
        if os.name != "nt":
            raise CredentialBackendUnavailableError
        self._directory = directory if directory is not None else _default_directory()
        self._lock = threading.Lock()

    def save(self, target: str, name: str, secret: str, *, overwrite: bool = False) -> None:
        key = _validated_key(target, name)
        secret_bytes = _validate_secret(secret)
        _validate_overwrite(overwrite)
        destination = self._credential_path(key)
        entropy = _credential_entropy(key)

        try:
            protected = _dpapi_protect(secret_bytes, entropy)
            self._directory.mkdir(parents=True, exist_ok=True)
            with self._lock:
                if destination.exists() and not overwrite:
                    raise CredentialAlreadyExistsError
                self._write_atomic(destination, _FILE_HEADER + protected, overwrite=overwrite)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialBackendError from None

    def read(self, target: str, name: str) -> str | None:
        key = _validated_key(target, name)
        source = self._credential_path(key)
        try:
            with self._lock:
                try:
                    payload = source.read_bytes()
                except FileNotFoundError:
                    return None
            if not payload.startswith(_FILE_HEADER):
                raise CredentialBackendError
            plaintext = bytearray(
                _dpapi_unprotect(payload[len(_FILE_HEADER) :], _credential_entropy(key))
            )
            try:
                return plaintext.decode("utf-8")
            finally:
                _zero_bytes(plaintext)
        except CredentialStoreError:
            raise
        except Exception:
            raise CredentialBackendError from None

    def delete(self, target: str, name: str) -> bool:
        key = _validated_key(target, name)
        destination = self._credential_path(key)
        try:
            with self._lock:
                try:
                    destination.unlink()
                except FileNotFoundError:
                    return False
            return True
        except Exception:
            raise CredentialBackendError from None

    def _credential_path(self, key: tuple[str, str]) -> Path:
        identifier = hashlib.sha256(f"{key[0]}\x00{key[1]}".encode("ascii")).hexdigest()
        return self._directory / f"{identifier}.credential"

    @staticmethod
    def _write_atomic(destination: Path, payload: bytes, *, overwrite: bool) -> None:
        descriptor = -1
        temporary_name = ""
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                dir=destination.parent,
                prefix=".credential-",
                suffix=".tmp",
            )
            with os.fdopen(descriptor, "wb") as temporary_file:
                descriptor = -1
                temporary_file.write(payload)
                temporary_file.flush()
                os.fsync(temporary_file.fileno())
            temporary_path = Path(temporary_name)
            if overwrite:
                os.replace(temporary_path, destination)
                temporary_name = ""
                return
            try:
                os.link(temporary_path, destination)
            except FileExistsError:
                raise CredentialAlreadyExistsError from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary_name:
                try:
                    Path(temporary_name).unlink()
                except FileNotFoundError:
                    pass


def _default_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise CredentialBackendUnavailableError
    return Path(local_app_data) / "LightImageEditor" / "Credentials"


def _validated_key(target: str, name: str) -> tuple[str, str]:
    if not isinstance(target, str) or not _TARGET_PATTERN.fullmatch(target):
        raise ValueError("credential target has an invalid format")
    if not isinstance(name, str) or not _NAME_PATTERN.fullmatch(name):
        raise ValueError("credential name has an invalid format")
    return target, name


def _validate_secret(secret: str) -> bytes:
    if not isinstance(secret, str):
        raise TypeError("credential secret must be text")
    try:
        encoded = secret.encode("utf-8")
    except UnicodeError:
        raise ValueError("credential secret has an invalid encoding") from None
    if not encoded or len(encoded) > _MAX_SECRET_BYTES:
        raise ValueError("credential secret has an invalid size")
    return encoded


def _validate_overwrite(overwrite: bool) -> None:
    if type(overwrite) is not bool:
        raise TypeError("overwrite must be a boolean")


def _credential_entropy(key: tuple[str, str]) -> bytes:
    return hashlib.sha256(f"LightImageEditor\x00{key[0]}\x00{key[1]}".encode("ascii")).digest()


def _dpapi_protect(plaintext: bytes, entropy: bytes) -> bytes:
    return _dpapi_transform(plaintext, entropy, protect=True)


def _dpapi_unprotect(ciphertext: bytes, entropy: bytes) -> bytes:
    return _dpapi_transform(ciphertext, entropy, protect=False)


def _dpapi_transform(data: bytes, entropy: bytes, *, protect: bool) -> bytes:
    if os.name != "nt":
        raise CredentialBackendUnavailableError
    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        raise CredentialBackendUnavailableError
    try:
        crypt32: Any = loader("crypt32", use_last_error=True)
        kernel32: Any = loader("kernel32", use_last_error=True)
        data_blob, data_buffer = _make_blob(data)
        entropy_blob, entropy_buffer = _make_blob(entropy)
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
                raise CredentialBackendError
            return ctypes.string_at(output_blob.pbData, output_blob.cbData)
        finally:
            try:
                if output_blob.pbData:
                    try:
                        ctypes.memset(output_blob.pbData, 0, output_blob.cbData)
                    finally:
                        kernel32.LocalFree(output_blob.pbData)
            finally:
                ctypes.memset(data_buffer, 0, len(data_buffer))
                ctypes.memset(entropy_buffer, 0, len(entropy_buffer))
    except CredentialStoreError:
        raise
    except Exception:
        raise CredentialBackendError from None


def _make_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


def _zero_bytes(value: bytearray) -> None:
    for index in range(len(value)):
        value[index] = 0
