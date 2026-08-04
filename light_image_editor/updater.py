"""Explicit, fail-closed update download and rollback services."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from .update_security import RsaPublicKey, verify_rs256

UPDATE_CHANNELS = ("stable", "beta")
MANIFEST_URLS = {
    "stable": "https://github.com/chendaxian318/imageeditor/releases/latest/download/update-stable.json",
    "beta": "https://github.com/chendaxian318/imageeditor/releases/download/beta/update-beta.json",
}
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_INSTALLER_BYTES = 512 * 1024 * 1024


class UpdateError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


@dataclass(frozen=True)
class UpdateAsset:
    url: str
    sha256: str
    size: int
    authenticode_thumbprints: tuple[str, ...]


@dataclass(frozen=True)
class UpdateManifest:
    channel: str
    version: str
    published_utc: str
    minimum_version: str
    release_notes_url: str
    asset: UpdateAsset
    key_id: str


def version_tuple(version: str) -> tuple[int, int, int]:
    parts = version.split(".")
    if len(parts) != 3 or any(not item.isdigit() for item in parts):
        raise UpdateError(f"invalid release version: {version}")
    return tuple(int(item) for item in parts)  # type: ignore[return-value]


def parse_signed_manifest(
    content: bytes,
    trusted_keys: dict[str, RsaPublicKey],
    expected_channel: str,
) -> UpdateManifest:
    if len(content) > MAX_MANIFEST_BYTES:
        raise UpdateError("update manifest is too large")
    try:
        envelope = json.loads(content.decode("utf-8"))
        payload = envelope["payload"]
        key_id = str(envelope["key_id"])
        signature = str(envelope["signature"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise UpdateError("invalid update manifest") from error
    if envelope.get("schema_version") != 1:
        raise UpdateError("unsupported update manifest schema")
    key = trusted_keys.get(key_id)
    if key is None or not verify_rs256(payload, signature, key):
        raise UpdateError("update manifest signature verification failed")
    try:
        channel = str(payload["channel"])
        asset_data = payload["assets"]["windows_x64"]
        asset = UpdateAsset(
            url=str(asset_data["url"]),
            sha256=str(asset_data["sha256"]).casefold(),
            size=int(asset_data["size"]),
            authenticode_thumbprints=tuple(
                str(value).replace(" ", "").upper()
                for value in asset_data["authenticode_thumbprints"]
            ),
        )
        manifest = UpdateManifest(
            channel=channel,
            version=str(payload["version"]),
            published_utc=str(payload["published_utc"]),
            minimum_version=str(payload.get("minimum_version", "0.1.0")),
            release_notes_url=str(payload.get("release_notes_url", "")),
            asset=asset,
            key_id=key_id,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise UpdateError("update manifest is incomplete") from error
    if channel != expected_channel or channel not in UPDATE_CHANNELS:
        raise UpdateError("update channel mismatch")
    version_tuple(manifest.version)
    version_tuple(manifest.minimum_version)
    if not asset.url.startswith("https://"):
        raise UpdateError("update asset must use HTTPS")
    if len(asset.sha256) != 64 or any(
        character not in "0123456789abcdef" for character in asset.sha256
    ):
        raise UpdateError("invalid update asset hash")
    if asset.size <= 0 or asset.size > MAX_INSTALLER_BYTES:
        raise UpdateError("invalid update asset size")
    if not asset.authenticode_thumbprints:
        raise UpdateError("update asset has no trusted Authenticode publisher")
    return manifest


def default_fetcher(url: str) -> BinaryIO:
    request = urllib.request.Request(url, headers={"User-Agent": "LightImageEditor-Updater/1"})
    return urllib.request.urlopen(request, timeout=20)


def fetch_manifest(url: str, fetcher: Callable[[str], BinaryIO] = default_fetcher) -> bytes:
    try:
        with fetcher(url) as response:
            content = response.read(MAX_MANIFEST_BYTES + 1)
    except (OSError, TimeoutError) as error:
        raise UpdateError(f"could not download update manifest: {error}") from error
    if len(content) > MAX_MANIFEST_BYTES:
        raise UpdateError("update manifest is too large")
    return content


def verify_authenticode(path: Path, allowed_thumbprints: tuple[str, ...]) -> bool:
    if os.name != "nt" or not allowed_thumbprints:
        return False
    command = (
        "$s=Get-AuthenticodeSignature -LiteralPath $args[0];"
        "[pscustomobject]@{Status=$s.Status.ToString();"
        "Thumbprint=if($s.SignerCertificate){$s.SignerCertificate.Thumbprint}else{''}}"
        "|ConvertTo-Json -Compress"
    )
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command, str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return False
    thumbprint = str(result.get("Thumbprint", "")).replace(" ", "").upper()
    return result.get("Status") == "Valid" and thumbprint in allowed_thumbprints


class UpdateCache:
    def __init__(self, root: str | Path | None = None) -> None:
        base = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LightImageEditor" / "updates"
        self.root = Path(root) if root else base
        self.state_path = self.root / "state.json"

    def state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}

    def stage(self, installer: Path, manifest: UpdateManifest) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.root / f"LightImageEditor-Setup-x64-{manifest.version}.exe"
        if installer.resolve() != target.resolve():
            staging = target.with_suffix(".staging")
            shutil.copy2(installer, staging)
            os.replace(staging, target)
        old_state = self.state()
        old_current = old_state.get("current")
        current = {
            "path": str(target),
            "version": manifest.version,
            "sha256": manifest.asset.sha256,
            "authenticode_thumbprints": list(manifest.asset.authenticode_thumbprints),
        }
        payload: dict[str, object] = {"schema_version": 1, "current": current}
        if isinstance(old_current, dict) and old_current.get("path") != str(target):
            payload["previous"] = old_current
        elif isinstance(old_state.get("previous"), dict):
            payload["previous"] = old_state["previous"]
        temporary_state = self.state_path.with_suffix(".tmp")
        temporary_state.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary_state, self.state_path)
        return target

    def rollback_installer(
        self,
        authenticode_verifier: Callable[[Path, tuple[str, ...]], bool] = verify_authenticode,
    ) -> Path | None:
        previous = self.state().get("previous")
        if not isinstance(previous, dict):
            return None
        try:
            path = Path(str(previous["path"]))
            expected_hash = str(previous["sha256"])
            thumbprints = tuple(str(item) for item in previous["authenticode_thumbprints"])
        except (KeyError, TypeError):
            return None
        if not path.is_file() or sha256_file(path) != expected_hash:
            return None
        return path if authenticode_verifier(path, thumbprints) else None


class UpdateService:
    def __init__(
        self,
        trusted_keys: dict[str, RsaPublicKey],
        cache: UpdateCache | None = None,
        fetcher: Callable[[str], BinaryIO] = default_fetcher,
        authenticode_verifier: Callable[[Path, tuple[str, ...]], bool] = verify_authenticode,
    ) -> None:
        self.trusted_keys = trusted_keys
        self.cache = cache or UpdateCache()
        self.fetcher = fetcher
        self.authenticode_verifier = authenticode_verifier

    def check(self, channel: str, current_version: str) -> UpdateManifest | None:
        if channel not in UPDATE_CHANNELS:
            raise UpdateError("unsupported update channel")
        content = fetch_manifest(MANIFEST_URLS[channel], self.fetcher)
        manifest = parse_signed_manifest(content, self.trusted_keys, channel)
        if version_tuple(current_version) < version_tuple(manifest.minimum_version):
            raise UpdateError("current version is too old for an automatic update")
        return (
            manifest if version_tuple(manifest.version) > version_tuple(current_version) else None
        )

    def download(self, manifest: UpdateManifest) -> Path:
        self.cache.root.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix="update-", suffix=".part", dir=self.cache.root
        )
        os.close(file_descriptor)
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        total = 0
        try:
            with self.fetcher(manifest.asset.url) as response, temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > manifest.asset.size or total > MAX_INSTALLER_BYTES:
                        raise UpdateError("update download exceeded declared size")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if total != manifest.asset.size:
                raise UpdateError("update download was interrupted")
            if digest.hexdigest() != manifest.asset.sha256:
                raise UpdateError("update installer hash verification failed")
            if not self.authenticode_verifier(temporary, manifest.asset.authenticode_thumbprints):
                raise UpdateError("update installer Authenticode verification failed")
            return self.cache.stage(temporary, manifest)
        except (OSError, TimeoutError) as error:
            raise UpdateError(f"could not download update: {error}") from error
        finally:
            temporary.unlink(missing_ok=True)


def launch_installer(installer: Path) -> None:
    if not installer.is_file():
        raise UpdateError("verified installer is no longer available")
    subprocess.Popen(
        [str(installer)],
        cwd=installer.parent,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
        close_fds=True,
    )
