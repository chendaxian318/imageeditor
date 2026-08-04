# Changelog

## 0.4.0 — 2026-08-04

### Non-destructive editing core

- Added a serializable command timeline with deterministic stage ordering: geometry, crop, colour, annotation, size and export.
- Added stable undo/redo for 100+ operations, branch truncation, slider-command merging and direct jumps from the history panel.
- Added one renderer shared by preview, export, size estimation and clipboard copy so full-resolution preview/export pixels stay aligned.
- Added fine rotation with optional automatic edge crop, projection-based automatic straighten, histogram analysis and before/after split comparison.
- Added levels, per-channel RGB curves, colour temperature, tint, shadows, highlights, bounded sharpening and edge-preserving denoise.

### Persistent projects

- Added versioned `.lieproj` JSON projects with external source references, relative-path fallback, source SHA-256 validation and atomic writes.
- Added schema migration for legacy document state, tolerant unknown top-level fields and safe read-only opening for unsupported future commands.
- Added timeline-aware crash recovery and project-file registration in the Windows installer.

### Validation

- 79 automated tests pass, including 100-operation undo/redo, project round trips and migration, unknown-field compatibility, auto-straighten confidence, histogram counts and preview/export pixel identity.

## 0.3.0 — 2026-08-04

### Fidelity and document model

- Added a single `Document` model for edit state, crop/resize/export state, operation history, dirty tracking, source metadata and source identity.
- Normalized all eight EXIF Orientation values on decode and removed Orientation on export to prevent repeated rotation.
- Preserved ICC, EXIF, DPI and XMP where the target Pillow format supports them; added ICC-to-sRGB conversion for display previews without mutating source pixels or profiles.
- Added byte-exact original-quality passthrough for unchanged same-format files, including animated APNG and WebP.
- Removed implicit default resizing so an unchanged export is not needlessly resampled.

### Safe editing and recovery

- Added same-directory atomic replacement, explicit conflict policies and optional timestamp-preserving `.bak` backups before overwrite.
- Added transparent-to-opaque and animated-to-single-frame confirmation gates.
- Added GIF/APNG/WebP animation detection and visible frame-count labels.
- Added transactional two-file autosave generations and startup recovery, including recovery after source deletion.
- Added source fingerprints and live detection of external modification, rename and deletion; stale-source overwrite is blocked.
- Added decode limits for decompression bombs, pixel/dimension limits, animation frame count, truncation and unsupported formats.
- Added social-media, avatar, email, original-quality and custom export presets.
- Added GIF to the Windows candidate default-app registrations.

### Validation

- 68 automated tests pass, including eight orientation golden cases, sRGB and Display P3 preview conversion, ICC/EXIF/DPI/XMP round trips, GIF/APNG/WebP animation guards, interrupted atomic writes, source conflicts, crash recovery and malformed input boundaries.
- A real Tk UI smoke test passed asynchronous open, `Document` binding, dirty tracking and autosave.

## 0.2.0 — 2026-08-03

### Default viewer

- Added per-user single-instance coordination using a named mutex and authenticated local pipe.
- Added multi-path activation, deterministic natural folder order, previous/next navigation and adjacent-image prefetch.
- Added a default compact viewer mode with an optional editor panel.
- Added fullscreen, immersive viewing, configurable slideshow and loop playback.
- Added native Windows drag-and-drop, recent directories, fit/actual-size viewing and window geometry persistence.
- Added background decoding and preview rendering; damaged or unsupported files no longer block navigation.
- Added file rename, copy, move, Explorer reveal and Recycle Bin-only deletion.
- Added format, dimensions, colour mode, file size, dates and basic EXIF properties.

### Installation and release

- Changed the installed application to PyInstaller `onedir`; retained a portable `onefile` build.
- Replaced the legacy custom installer with a standard per-user Inno Setup installer and Apps & Features uninstall entry.
- Added optional shortcuts, an image context menu and Windows candidate-default-app registration.
- Added IPC-coordinated upgrade/uninstall exit with save, discard and cancel handling; removed forced process termination.
- Added deterministic product/version metadata, recursive SHA-256 manifests and pinned installer compiler verification.

### Validation

- 49 automated tests pass, including 500-file ordering, 20 activation redirects and real unsaved-upgrade cancellation.
- Packaged EXE validation: 20 launches produced one main process and displayed the final activation.
- Installer validation: three fresh installs, three overwrite upgrades and three uninstalls completed without application file or registry residue; the final documentation rebuild also passed one complete cycle.
- Standard benchmark on the development machine: startup median 597 ms, 48 MP first-visible preview median 1,443 ms, switch median 117 ms (three rounds).

### Release blockers

- Imported upstream source still lacks a verified redistribution licence; public or commercial distribution remains blocked.
- The current Inno Setup 7 compiler is licensed for non-commercial use; commercial builds require a separate Inno Setup licence.
- Executables are not code-signed. Obtain a trusted signing certificate and timestamp release artifacts before public distribution.
