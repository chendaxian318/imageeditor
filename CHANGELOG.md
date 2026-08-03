# Changelog

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
