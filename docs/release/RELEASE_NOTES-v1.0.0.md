# Light Image Editor 1.0.0

Light Image Editor 1.0.0 is the first stable Windows release intended for daily use as a
default image viewer and non-destructive editor. This document describes the frozen release
content; public publication is prohibited until `V1_RELEASE_GATES.json` and the generated
`RELEASE_GATE_STATUS.json` both pass.

## Highlights

- Single-instance viewing with multi-path forwarding, directory navigation, prefetch, full
  screen, immersive mode, slideshows, drag-and-drop and safe Recycle Bin deletion.
- Non-destructive history, versioned `.lieproj` projects, crash recovery, metadata and colour
  preservation, atomic saves and explicit animated-image safeguards.
- Editable annotations, privacy tools, AVIF and animated-image support, optional HEIF/HEIC,
  cancellable batch queues and importable presets.
- Chinese and English interfaces, system/light/dark themes, high-DPI support, keyboard
  navigation, offline-first operation and user-initiated diagnostics.
- Per-user Windows installer, portable build, registered default-app capabilities, stable and
  Beta update channels, signed manifests and a verified previous-version rollback package.

## Compatibility

- Windows 10 or Windows 11, x64.
- Base installer supports JPEG, PNG, APNG, WebP, AVIF, BMP, TIFF and GIF.
- HEIF/HEIC requires the separately documented optional codec.

## Security and privacy

Images remain on the local computer. Update checks occur only when requested. A production
release contains timestamped Authenticode signatures, SHA-256 checksums, a CycloneDX SBOM,
third-party notices and source-to-artifact provenance. Report problems at
<https://github.com/chendaxian318/imageeditor/issues>.
