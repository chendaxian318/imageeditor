"""Document model for non-destructive editing, fidelity metadata and source tracking."""

from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from image_ops import EditState, ExportOptions

ORIENTATION_TAG = 274
SOURCE_METADATA_KEY = "_light_image_editor_source_metadata"


@dataclass(frozen=True)
class SourceFingerprint:
    size: int
    mtime_ns: int
    device: int
    inode: int

    @classmethod
    def capture(cls, path: str | Path) -> SourceFingerprint:
        stat = Path(path).stat()
        return cls(
            size=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            device=stat.st_dev,
            inode=stat.st_ino,
        )

    def matches(self, path: str | Path) -> bool:
        try:
            return self == self.capture(path)
        except OSError:
            return False


@dataclass(frozen=True)
class SourceStatus:
    kind: Literal["unchanged", "modified", "renamed", "missing"]
    path: Path | None = None


@dataclass(frozen=True)
class SourceMetadata:
    original_format: str | None = None
    original_mode: str = "RGB"
    original_orientation: int = 1
    exif: bytes | None = None
    icc_profile: bytes | None = None
    dpi: tuple[float, float] | None = None
    xmp: bytes | None = None
    has_alpha: bool = False
    is_animated: bool = False
    frame_count: int = 1
    loop: int | None = None
    durations: tuple[int, ...] = ()

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        for key in ("exif", "icc_profile", "xmp"):
            value = data[key]
            data[key] = base64.b64encode(value).decode("ascii") if value else None
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SourceMetadata:
        decoded = dict(data)
        for key in ("exif", "icc_profile", "xmp"):
            value = decoded.get(key)
            decoded[key] = base64.b64decode(value) if isinstance(value, str) else None
        dpi = decoded.get("dpi")
        decoded["dpi"] = tuple(dpi) if isinstance(dpi, list) else dpi
        durations = decoded.get("durations", ())
        decoded["durations"] = tuple(int(value) for value in durations)
        return cls(**decoded)


@dataclass(frozen=True)
class DocumentState:
    edit_state: EditState = EditState()
    crop_box: tuple[int, int, int, int] | None = None
    crop_ratio: str = "自由"
    width_text: str = ""
    height_text: str = ""
    max_side_text: str = ""
    output_format: str = "JPEG"
    quality: int = 85
    target_size_text: str = ""
    filename_prefix: str = ""
    filename_suffix: str = ""
    watermark_text: str = ""
    watermark_position: str = "右下"
    watermark_opacity: int = 35
    watermark_size_percent: int = 5
    preserve_metadata: bool = True
    backup_on_overwrite: bool = True
    export_preset: str = "自定义"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        if self.crop_box is not None:
            data["crop_box"] = list(self.crop_box)
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> DocumentState:
        values = dict(data)
        edit = values.get("edit_state", {})
        values["edit_state"] = EditState(**edit) if isinstance(edit, dict) else EditState()
        crop = values.get("crop_box")
        values["crop_box"] = tuple(int(value) for value in crop) if crop is not None else None
        return cls(**values)

    def export_options(self) -> ExportOptions:
        width = _positive_optional_int(self.width_text, "宽度")
        height = _positive_optional_int(self.height_text, "高度")
        max_side = _positive_optional_int(self.max_side_text, "最长边")
        target_size_kb = _positive_optional_int(self.target_size_text, "目标 KB")
        if max_side is not None and (width is not None or height is not None):
            raise ValueError("最长边不能和宽度或高度同时填写。")
        if target_size_kb is not None and self.output_format not in {"JPEG", "WEBP"}:
            raise ValueError("目标大小自动压缩仅适用于 JPEG 和 WEBP。")
        if not 0 <= self.watermark_opacity <= 100:
            raise ValueError("水印透明度必须在 0 到 100 之间。")
        if not 1 <= self.watermark_size_percent <= 30:
            raise ValueError("水印字号必须在 1 到 30 之间。")
        return ExportOptions(
            output_format=self.output_format,
            quality=self.quality,
            target_size_kb=target_size_kb,
            max_width=max_side if max_side is not None else (width if height is None else None),
            max_height=max_side if max_side is not None else (height if width is None else None),
            exact_width=width if height is not None else None,
            exact_height=height if width is not None else None,
            crop_box=self.crop_box,
            edit_state=self.edit_state,
            watermark_text=self.watermark_text.strip(),
            watermark_position=self.watermark_position,
            watermark_opacity=self.watermark_opacity,
            watermark_size_percent=self.watermark_size_percent,
            preserve_metadata=self.preserve_metadata,
        )


@dataclass(frozen=True)
class DocumentOperation:
    sequence: int
    kind: str
    timestamp_utc: str
    state: DocumentState

    def to_json(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "timestamp_utc": self.timestamp_utc,
            "state": self.state.to_json(),
        }


@dataclass
class Document:
    image: Image.Image
    source_path: Path | None
    metadata: SourceMetadata = SourceMetadata()
    state: DocumentState = DocumentState()
    source_fingerprint: SourceFingerprint | None = None
    operations: list[DocumentOperation] = field(default_factory=list)
    base_revision: int = 0
    _clean_state: DocumentState = field(init=False, repr=False)
    _clean_base_revision: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.source_path = self.source_path.resolve() if self.source_path is not None else None
        if self.source_path is not None and self.source_fingerprint is None:
            try:
                self.source_fingerprint = SourceFingerprint.capture(self.source_path)
            except OSError:
                self.source_fingerprint = None
        self._clean_state = self.state
        self._clean_base_revision = self.base_revision

    @classmethod
    def from_decoded(
        cls,
        image: Image.Image,
        path: str | Path | None,
        state: DocumentState | None = None,
    ) -> Document:
        metadata = metadata_from_image(image)
        return cls(
            image=image,
            source_path=Path(path) if path is not None else None,
            metadata=metadata,
            state=state or DocumentState(),
        )

    @property
    def dirty(self) -> bool:
        return self.state != self._clean_state or self.base_revision != self._clean_base_revision

    def update_state(self, kind: str, **changes: Any) -> None:
        updated = replace(self.state, **changes)
        self.set_state(kind, updated)

    def set_state(self, kind: str, updated: DocumentState) -> None:
        if updated == self.state:
            return
        self.state = updated
        sequence = self.operations[-1].sequence + 1 if self.operations else 1
        self.operations.append(
            DocumentOperation(
                sequence=sequence,
                kind=kind,
                timestamp_utc=datetime.now(UTC).isoformat(),
                state=updated,
            )
        )
        self.operations = self.operations[-500:]

    def replace_image(self, image: Image.Image, kind: str) -> None:
        self.image = image
        self.base_revision += 1
        sequence = self.operations[-1].sequence + 1 if self.operations else 1
        self.operations.append(
            DocumentOperation(
                sequence=sequence,
                kind=kind,
                timestamp_utc=datetime.now(UTC).isoformat(),
                state=self.state,
            )
        )

    def mark_clean(self, new_path: str | Path | None = None) -> None:
        if new_path is not None:
            self.source_path = Path(new_path).resolve()
            try:
                self.source_fingerprint = SourceFingerprint.capture(self.source_path)
            except OSError:
                self.source_fingerprint = None
        self._clean_state = self.state
        self._clean_base_revision = self.base_revision

    def rebind_source(self, new_path: str | Path) -> None:
        """Track a renamed/moved source without changing whether edits are dirty."""

        self.source_path = Path(new_path).resolve()
        try:
            self.source_fingerprint = SourceFingerprint.capture(self.source_path)
        except OSError:
            self.source_fingerprint = None

    def mark_recovered_dirty(self) -> None:
        self._clean_state = DocumentState()
        self._clean_base_revision = -1

    def source_status(self) -> SourceStatus:
        if self.source_path is None or self.source_fingerprint is None:
            return SourceStatus("unchanged", self.source_path)
        if self.source_path.exists():
            if self.source_fingerprint.matches(self.source_path):
                return SourceStatus("unchanged", self.source_path)
            return SourceStatus("modified", self.source_path)
        try:
            siblings = self.source_path.parent.iterdir()
        except OSError:
            return SourceStatus("missing")
        for candidate in siblings:
            try:
                stat = candidate.stat()
            except OSError:
                continue
            if (
                stat.st_dev == self.source_fingerprint.device
                and stat.st_ino == self.source_fingerprint.inode
            ):
                return SourceStatus("renamed", candidate)
        return SourceStatus("missing")


def metadata_from_image(image: Image.Image) -> SourceMetadata:
    metadata = image.info.get(SOURCE_METADATA_KEY)
    if isinstance(metadata, SourceMetadata):
        return metadata
    dpi = image.info.get("dpi")
    dpi_tuple = None
    if isinstance(dpi, tuple) and len(dpi) == 2:
        dpi_tuple = (float(dpi[0]), float(dpi[1]))
    return SourceMetadata(
        original_format=image.format,
        original_mode=image.mode,
        exif=image.info.get("exif") if isinstance(image.info.get("exif"), bytes) else None,
        icc_profile=(
            image.info.get("icc_profile")
            if isinstance(image.info.get("icc_profile"), bytes)
            else None
        ),
        dpi=dpi_tuple,
        xmp=_first_bytes(image.info, "xmp", "XML:com.adobe.xmp"),
        has_alpha="A" in image.getbands() or "transparency" in image.info,
        is_animated=bool(getattr(image, "is_animated", False)),
        frame_count=int(getattr(image, "n_frames", 1)),
    )


def _first_bytes(info: Mapping[Any, Any], *keys: str) -> bytes | None:
    for key in keys:
        value = info.get(key)
        if isinstance(value, bytes):
            return value
        if isinstance(value, str):
            return value.encode("utf-8")
    return None


def _positive_optional_int(value: str, label: str) -> int | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        number = int(stripped)
    except ValueError as error:
        raise ValueError(f"{label}必须是数字。") from error
    if number <= 0:
        raise ValueError(f"{label}必须大于 0。")
    return number


def replace_orientation_in_exif(exif_data: bytes | None) -> bytes | None:
    if not exif_data:
        return None
    exif = Image.Exif()
    try:
        exif.load(exif_data)
    except (OSError, SyntaxError, ValueError):
        return exif_data
    if ORIENTATION_TAG in exif:
        del exif[ORIENTATION_TAG]
    return exif.tobytes() if len(exif) else None


def attach_source_metadata(image: Image.Image, metadata: SourceMetadata) -> Image.Image:
    image.info[SOURCE_METADATA_KEY] = metadata
    if metadata.exif:
        image.info["exif"] = metadata.exif
    if metadata.icc_profile:
        image.info["icc_profile"] = metadata.icc_profile
    if metadata.dpi:
        image.info["dpi"] = metadata.dpi
    if metadata.xmp:
        image.info["xmp"] = metadata.xmp
    return image
