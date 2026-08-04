"""Local diagnostics, privacy redaction and explicit report export."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
import traceback
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from .version import __version__

LOGGER_NAME = "light_image_editor"
EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")
IP_PATTERN = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
TOKEN_PATTERN = re.compile(
    r"(?i)(\b(?:authorization|token|api[_-]?key|secret|password)\b[\"']?\s*[:=]\s*)"
    r"([\"']?)[^\"'\s,;}]+[\"']?"
)
WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:\\[^\r\n\t\"']+")


def diagnostic_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "LightImageEditor"


def redact_text(value: str) -> str:
    redacted = value
    home = str(Path.home())
    variants = {home, home.replace("\\", "/")}
    for variant in sorted(variants, key=len, reverse=True):
        if variant:
            redacted = redacted.replace(variant, "<USER_HOME>")
    redacted = EMAIL_PATTERN.sub("<EMAIL>", redacted)
    redacted = IP_PATTERN.sub("<IP_ADDRESS>", redacted)
    redacted = TOKEN_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<REDACTED>{match.group(2)}",
        redacted,
    )
    redacted = WINDOWS_PATH_PATTERN.sub("<LOCAL_PATH>", redacted)
    return redacted


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact_text(super().format(record))


def configure_diagnostics(root: str | Path | None = None) -> logging.Logger:
    base = Path(root) if root else diagnostic_root()
    log_dir = base / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    if not any(
        isinstance(handler, logging.handlers.RotatingFileHandler) for handler in logger.handlers
    ):
        handler = logging.handlers.RotatingFileHandler(
            log_dir / "application.log",
            maxBytes=1024 * 1024,
            backupCount=4,
            encoding="utf-8",
        )
        handler.setFormatter(
            RedactingFormatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
        )
        logger.addHandler(handler)
    logger.info("application diagnostics started version=%s", __version__)
    return logger


def _safe_settings(settings: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "schema_version",
        "locale",
        "theme_mode",
        "decode_workers",
        "preview_cache",
        "preserve_metadata",
        "backup_on_overwrite",
        "batch_concurrency",
        "batch_memory_mb",
        "update_channel",
        "crash_reporting_consent",
    }
    result = {key: settings[key] for key in sorted(allowed & settings.keys())}
    for key in ("recent_files", "recent_directories", "recent_projects"):
        value = settings.get(key, [])
        result[f"{key}_count"] = len(value) if isinstance(value, list) else 0
    return result


class CrashStore:
    def __init__(self, root: str | Path | None = None) -> None:
        base = Path(root) if root else diagnostic_root()
        self.root = base / "crashes"

    def record(
        self,
        exception_type: type[BaseException],
        exception: BaseException,
        traceback_value: TracebackType | None,
        thread_name: str = "main",
    ) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        identifier = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
        payload = {
            "schema_version": 1,
            "id": identifier,
            "created_utc": datetime.now(UTC).isoformat(),
            "app_version": __version__,
            "thread": thread_name,
            "exception_type": exception_type.__name__,
            "message": redact_text(str(exception)),
            "traceback": redact_text(
                "".join(traceback.format_exception(exception_type, exception, traceback_value))
            ),
            "acknowledged": False,
            "uploaded": False,
        }
        target = self.root / f"{identifier}.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, target)
        return target

    def pending(self) -> list[Path]:
        if not self.root.exists():
            return []
        pending: list[Path] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                if not json.loads(path.read_text(encoding="utf-8")).get("acknowledged", False):
                    pending.append(path)
            except (OSError, ValueError, TypeError):
                pending.append(path)
        return pending

    def reports(self) -> list[Path]:
        """Return all retained crash records, including acknowledged ones."""

        if not self.root.exists():
            return []
        return sorted(self.root.glob("*.json"))

    def mark_uploaded(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["uploaded"] = True
        payload["acknowledged"] = True
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def acknowledge(self, path: Path) -> None:
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["acknowledged"] = True
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class CrashSender(Protocol):
    def __call__(self, payload: bytes) -> bool: ...


def upload_crash_report(
    path: Path,
    *,
    consent_enabled: bool,
    user_confirmed: bool,
    sender: CrashSender,
) -> bool:
    if not consent_enabled or not user_confirmed:
        raise PermissionError("crash reporting requires saved consent and per-report confirmation")
    payload = redact_text(path.read_text(encoding="utf-8")).encode("utf-8")
    return bool(sender(payload))


class DiagnosticReporter:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root else diagnostic_root()
        self.crashes = CrashStore(self.root)

    def export(self, target: str | Path, settings: dict[str, Any], notes: str = "") -> Path:
        destination = Path(target)
        destination.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": 1,
            "generated_utc": datetime.now(UTC).isoformat(),
            "app_version": __version__,
            "report_id": uuid.uuid4().hex,
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "machine": platform.machine(),
                "python": platform.python_version(),
            },
            "settings": _safe_settings(settings),
            "notes": redact_text(notes),
            "privacy": "Paths, email addresses, IP addresses and secret-like values are redacted.",
        }
        with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "report.json",
                json.dumps(report, ensure_ascii=False, indent=2),
            )
            for path in sorted((self.root / "logs").glob("application.log*")):
                try:
                    archive.writestr(f"logs/{path.name}", redact_text(path.read_text("utf-8")))
                except OSError:
                    continue
            for path in self.crashes.reports():
                try:
                    archive.writestr(f"crashes/{path.name}", redact_text(path.read_text("utf-8")))
                except OSError:
                    continue
        return destination


def install_crash_handlers(store: CrashStore | None = None) -> CrashStore:
    crash_store = store or CrashStore()
    original_hook = sys.excepthook
    original_thread_hook = threading.excepthook

    def main_hook(
        exception_type: type[BaseException],
        exception: BaseException,
        traceback_value: TracebackType | None,
    ) -> None:
        crash_store.record(exception_type, exception, traceback_value)
        original_hook(exception_type, exception, traceback_value)

    def thread_hook(arguments: threading.ExceptHookArgs) -> None:
        exception = arguments.exc_value or RuntimeError("worker failed without exception value")
        crash_store.record(
            arguments.exc_type,
            exception,
            arguments.exc_traceback,
            arguments.thread.name if arguments.thread else "worker",
        )
        original_thread_hook(arguments)

    sys.excepthook = main_hook
    threading.excepthook = thread_hook
    return crash_store
