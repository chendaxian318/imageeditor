# Changelog

## 0.9.0-rc.1 — 2026-08-04

### Release-candidate quality

- Froze features and added machine-readable RC gates, a seven-day stability ledger, Windows 10/11 compatibility matrix and disposable-profile lifecycle requirements.
- Added repeatable startup, first-image, switching, zoom, export, batch and RSS measurements plus a same-machine 15% regression gate against the accepted v0.8.0 baseline.
- Added configurable 20,000-switch and 1,000-item batch soak testing with bounded-memory and injected damaged/missing-file checks.
- Released stale preview frames and owned intermediate images deterministically to reduce long-session memory retention.
- Added local Defender scanning, final Authenticode inspection, source-to-artifact provenance, PyInstaller diagnostic evidence and fail-closed RC verification.
- Completed the user manual and updated privacy, release, licence and third-party inventory references for 0.9.0.

### Remaining external gates

- Public release remains blocked by publisher code signing, first-party rights confirmation, the 20-user Beta minimum, seven consecutive P0/P1-free days, Windows 10/11 clean-VM evidence and approved multi-engine false-positive review.

## 0.8.0 — 2026-08-04

### Beta release system

- Added stable and Beta channels with user-initiated checks only; signed manifests, declared size, SHA-256 and trusted Windows Authenticode publisher verification all fail closed.
- Added atomic update downloads, a cache of the previous verified installer and an explicit rollback action.
- Added versioned settings migration that preserves recent files and unknown fields, with a pre-migration backup.
- Added rotating local diagnostics, crash recovery records, privacy redaction and user-initiated ZIP report export; anonymous upload remains off by default and requires both saved consent and per-report confirmation.
- Added uninstall-time choice to preserve or clear settings, sessions, logs, update cache and batch state.
- Added deterministic CycloneDX 1.6 SBOM generation, release SHA-256 coverage, Beta release notes, known issues, feedback template and P0/P1 ownership/closure register.
- Added release signing gates and dual-channel manifest generation when the Authenticode tool, certificate thumbprint and protected manifest private key are configured.

### Validation

- 113 automated tests pass, including signature tampering, channel mismatch, interrupted download, hash mismatch, publisher rejection, rollback, redaction, dual-consent and legacy migration cases.
- Unsigned local builds carry `SIGNING_REQUIRED.txt` and cannot produce update-channel manifests; public Beta remains blocked until the final binaries have a trusted publisher signature.

## 0.7.0 — 2026-08-04

### Experience and accessibility

- Added light, dark and system-following themes backed by shared colour, spacing, focus and state tokens.
- Enabled Windows Per-Monitor V2 DPI awareness, live Tk scaling refresh and monitor-work-area geometry clamping for 100%, 125%, 150% and 200% DPI configurations.
- Added F6/Shift+F6 region navigation, visible focus styling, accessible names for the canvas/toolbars/options/status areas and keyboard-safe handling that no longer steals navigation keys from text inputs.
- Added a searchable keyboard-reference window, first-use guide, keyboard-operable empty state and actionable retry/choose/reveal recovery for failed image loads.
- Fixed a native `WM_DROPFILES` crash by queuing dropped paths in the Win32 callback and dispatching them later from the Tk event loop instead of calling Tk while its main loop has released the Python GIL.

### Localisation and settings

- Moved Simplified Chinese and English UI resources to external UTF-8 JSON catalogs and added live language switching without reinstalling or restarting.
- Added an appearance/language, performance/cache, saving/metadata, privacy, update and file-association settings center.
- Kept the entire core application offline-capable, prohibited background uploads and automatic network requests, and shipped a bilingual privacy statement.
- Added both locale catalogs to the installed `onedir` and portable `onefile` PyInstaller recipes.

### Validation

- Added catalog parity and reversible-switch tests, four DPI-scale and negative-coordinate multi-monitor geometry tests, explicit offline-policy checks, packaged-resource checks and a real Tk settings/accessibility smoke test.
- Added a real synthetic `WM_DROPFILES` regression test with a Unicode path; the process remains alive and the path reaches the Tk callback.
- Verified the English main window, menus, status values and keyboard-help surface contain no remaining Chinese fragments after a live switch.

## 0.6.0 — 2026-08-04

### Formats and animation

- Enabled AVIF open, convert and export through the AVIF codec already present in the locked Pillow 11.3.0 Windows wheel.
- Added runtime discovery for the optional `pillow-heif` HEIF/HEIC decoder with a separate lock file; the base installer remains free of that dependency.
- Added background multi-frame GIF/APNG/WebP playback and an explicit animated export workflow for GIF, APNG and WebP.
- Registered AVIF, APNG and optional HEIF/HEIC extensions as Windows candidate default-app formats.

### Persistent batch processing

- Added a versioned batch manifest with pending, running, succeeded, failed and cancelled states; interrupted running tasks recover as pending.
- Added configurable concurrency and a weighted memory budget, cancellation, failed-item retry, whole-job rerun and atomic progress checkpoints.
- Added versioned import/export presets, rename templates with name/stem/extension/index/date variables, directory-tree preservation and explicit error/overwrite/unique conflict policies.
- Added metadata cleaning, resize, watermark and format-conversion recipes, same-file overwrite protection, CSV/JSON error reports, completion notifications and output-folder actions.
- Isolated read-only, damaged, oversized and same-name inputs so one task never blocks the remaining queue.

### Validation

- 98 automated tests pass, including real AVIF round trips, three animation formats, preset schema gates, cancellation/recovery/retry and a 1,003-file mixed-format stress test.
- The stress test completed 1,001 valid inputs while isolating one damaged and one oversized input; active image reservations remained within a configured 32 MB budget.
- A real Tk smoke test played a three-frame animation and completed six UI-launched AVIF batch tasks without cross-thread Tk calls.

## 0.5.0 — 2026-08-04

### Editable annotations and privacy tools

- Added non-destructive text, arrow, line, rectangle, ellipse, freehand and numbered-marker objects.
- Added local mosaic, Gaussian blur and solid-redaction objects; effects remain bounded to their selected regions and undo restores the original pixels.
- Added topmost object selection, move, four-corner resize, colour, fill, stroke, opacity, text, font-size and effect-strength controls.
- Persisted every annotation object and update command in `.lieproj` projects and abnormal-exit sessions so objects remain editable across launches.

### High-frequency workflows

- Added free-form `width:height` crop ratios, interactive four-corner perspective correction and four-sided canvas expansion with a configurable background.
- Added configurable favourite actions and keyboard shortcuts with duplicate and syntax validation.
- Added recent project and export-location menus, remembered export destinations and optional startup restoration of the latest project workspace.
- Added DPI-aware selection handles and hit regions; verified 16-pixel targets at 200% DPI.

### Technical decision gate

- Tkinter remains the UI framework through v1.0: 100 vector objects render at a 41.3 ms median and 10 local effects at a 118.3 ms median on the development machine, while 200% DPI create/move interactions pass.
- A framework migration remains a separate future project if later accessibility or canvas-performance evidence fails the documented gates.

### Validation

- 90 automated tests pass, including project editability, local-effect boundaries, undo restoration, custom crop validation, 200% DPI hit sizing, perspective/canvas rendering and a 100-object performance gate.
- Real Tk smoke tests pass object creation, selection, move, style update, undo/redo and 200% DPI canvas interaction.

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
