# Third-party licence inventory

This document records the dependency audit updated on **2026-08-04**.
It is an engineering compliance inventory, not legal advice and not a
substitute for the authoritative licence text distributed by each component.

## Audited environment

- Platform: Windows x64
- Python: 3.12.10
- Pillow: 11.3.0
- NumPy: 2.5.1
- PyInstaller: 6.17.0
- Source of version data: installed metadata in `.venv` plus `pip freeze --all`
- Application artifacts checked: `release/LightImageEditor/ImageEditor.exe`,
  `release/LightImageEditor-Portable-x64-0.9.0.exe` and
  `release/LightImageEditor-Setup-x64-0.9.0.exe`

The virtual environment is not a release artifact. Paths below identify the
authoritative files in the audited local installation so that the release
process can copy them into a distributable notice bundle.

## Application runtime and packaging components

| Component | Version | Role | Declared licence | Authoritative local notice |
| --- | ---: | --- | --- | --- |
| Python | 3.12.10 | Embedded interpreter/runtime | PSF License Version 2 and bundled notices | Python installation `LICENSE.txt` |
| Tcl/Tk | 8.6 | Tkinter GUI runtime | Tcl/Tk licence | Python installation `tcl`/`tk` licence files; verify during packaging |
| Pillow | 11.3.0 | Image decode, processing and encode | MIT-CMU, plus bundled native-library terms | `.venv/Lib/site-packages/pillow-11.3.0.dist-info/licenses/LICENSE` |
| NumPy | 2.5.1 | Float32 colour processing, curves, histograms and analysis | BSD-3-Clause, plus bundled third-party terms | `.venv/Lib/site-packages/numpy-2.5.1.dist-info/licenses/` |
| PyInstaller | 6.17.0 | Build tool and embedded bootloader/runtime hooks | GPL-2.0-or-later WITH Bootloader-exception; runtime hooks Apache-2.0 | `.venv/Lib/site-packages/pyinstaller-6.17.0.dist-info/licenses/COPYING.txt` |
| pyinstaller-hooks-contrib | 2026.6 | PyInstaller hook set | Standard hooks GPL-2.0-or-later; runtime hooks Apache-2.0 | `.venv/Lib/site-packages/pyinstaller_hooks_contrib-2026.6.dist-info/licenses/LICENSE` |
| Inno Setup | 7.0.2 | Windows installer compiler | Free for non-commercial use; commercial use requires a licence | Verified compiler under `.tools/inno`; authoritative terms at `jrsoftware.org` |
| Compact ICC Profiles | DisplayP3Compat-v4 | Display P3 automated test fixture | CC0-1.0 / public domain dedication | Embedded in `test_v03_safety.py`; source: `saucecontrol/Compact-ICC-Profiles` |

## Optional codec not included in the base installer

`pillow-heif==1.5.0` is pinned separately in `requirements-heif.lock` for users who
choose HEIF/HEIC support. It is not installed by `build.ps1`, is not part of the
base dependency closure, and is not bundled in the standard v0.9.0 artifacts.
Any future release that bundles it must add its licence and the complete native
`libheif`/codec notice set to the distributed notice bundle.

The PyInstaller exception permits linking or embedding its compiled bootloader
and related files into another program and distributing that combination
without GPL restrictions arising solely from those files. This statement is a
summary; the package-supplied `COPYING.txt` controls.

Inno Setup 7 is a build-time tool with separate commercial licensing. The
current build is suitable only for non-commercial development/testing unless
the publisher obtains the required Inno Setup commercial licence. Its terms
are independent of the unresolved first-party source-code rights below.

## Installed build dependency closure

These packages were installed in the audited `.venv`. Most are build-time
tools and are not expected to be imported by the application at runtime, but
they remain part of the reproducible build input inventory.

| Package | Locked/installed version | Declared licence | Local licence file |
| --- | ---: | --- | --- |
| altgraph | 0.17.5 | MIT | `.venv/Lib/site-packages/altgraph-0.17.5.dist-info/LICENSE` |
| ast-serialize | 0.6.0 | MIT | `.venv/Lib/site-packages/ast_serialize-0.6.0.dist-info/licenses/LICENSE` |
| librt | 0.13.0 | MIT | `.venv/Lib/site-packages/librt-0.13.0.dist-info/licenses/LICENSE` |
| mypy | 2.3.0 | MIT; bundled typeshed data Apache-2.0 | `.venv/Lib/site-packages/mypy-2.3.0.dist-info/licenses/` |
| mypy-extensions | 1.1.0 | MIT | `.venv/Lib/site-packages/mypy_extensions-1.1.0.dist-info/licenses/LICENSE` |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause | `.venv/Lib/site-packages/packaging-26.2.dist-info/licenses/` |
| pathspec | 1.1.1 | MPL-2.0 | `.venv/Lib/site-packages/pathspec-1.1.1.dist-info/licenses/LICENSE` |
| pefile | 2024.8.26 | MIT | `.venv/Lib/site-packages/pefile-2024.8.26.dist-info/LICENSE` |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | `.venv/Lib/site-packages/pywin32_ctypes-0.2.3.dist-info/LICENSE.txt` |
| ruff | 0.16.1 | MIT, with separately licensed derived/vendored components | `.venv/Lib/site-packages/ruff-0.16.1.dist-info/licenses/LICENSE` |
| typing-extensions | 4.16.0 | PSF-2.0 | `.venv/Lib/site-packages/typing_extensions-4.16.0.dist-info/licenses/LICENSE` |
| pip | 26.2 | MIT, with separately licensed vendored components | `.venv/Lib/site-packages/pip-26.2.dist-info/licenses/` |
| setuptools | 83.0.0 | MIT, with separately licensed vendored components | `.venv/Lib/site-packages/setuptools-83.0.0.dist-info/licenses/` and `setuptools/_vendor/` |

`mypy`, `ruff`, `pathspec`, `ast-serialize`, `librt`, `mypy-extensions`,
`typing-extensions`, `pip` and `setuptools` are development, checking or
environment/bootstrap tools. They should not be copied into a release merely
because they are installed in the build virtual environment. If a future
packaging configuration includes any of them, its complete bundled or
vendored-licence set must also ship. In particular, `pathspec` uses MPL-2.0;
its presence in the isolated build environment does not impose MPL terms on
the application source.

## Native libraries in the Pillow 11.3.0 Windows wheel

The installed Pillow licence bundle identifies the native components below.
The current PyInstaller executable contains Pillow extension modules including
`_imaging`, `_imagingcms`, `_imagingft`, `_webp`, and `_avif`, so these notices
must be treated as distributable notices rather than build-only metadata.

| Component | Version in Pillow notice | Licence family (summary only) |
| --- | ---: | --- |
| Brotli | 1.1.0 | MIT |
| FreeType | 2.13.3 | FreeType Project Licence or GPL option; this project uses the permissive FreeType terms and acknowledgement |
| HarfBuzz | 11.2.1 | MIT-style |
| Little CMS | 2.17 | MIT-style |
| libavif | 1.3.0 | BSD-2-Clause-style terms, with third-party notices |
| libjpeg-turbo | 3.1.1 | BSD/IJG/zlib-family terms applying to different portions |
| libpng | 1.6.49 | libpng licence |
| libwebp | 1.5.0 | BSD-3-Clause |
| OpenJPEG | 2.5.3 | BSD-2-Clause |
| LibTIFF | 4.7.0 | LibTIFF permissive licence |
| XZ Utils / liblzma | 5.8.1 | 0BSD for the linked library; package contains other separately licensed files |
| zlib-ng | 2.2.4 | zlib licence |

Do not use the summaries in this table as replacement licence texts. The
complete terms and copyright notices for every row are concatenated in:

```text
.venv/Lib/site-packages/pillow-11.3.0.dist-info/licenses/LICENSE
```

That file must be preserved verbatim in the notice bundle of any installer
that contains the audited Pillow wheel. A different Pillow wheel may contain a
different native dependency set and requires a fresh audit.

## Reproducible audit command

Run from the repository root after creating the locked virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip freeze --all
.\.venv\Scripts\python.exe -c "from PIL import features; features.pilinfo()"
```

For each installed distribution, inspect `License-Expression`, `License`, and
the files named `LICENSE`, `COPYING`, or `NOTICE` in its `.dist-info` metadata.
Do not infer a licence solely from a project name or a package-index summary.

## Release obligations and open items

1. Ship `LICENSE`, `NOTICE.md`, this inventory, and the complete upstream
   third-party licence texts with each installer/archive.
2. Confirm the first-party code ownership and written redistribution right;
   the current `LICENSE` deliberately grants no rights.
3. Generate the inventory from the clean locked environment used for the
   actual release, not from a developer machine with extra packages.
4. Inspect the built PyInstaller archive and installer payload, then remove
   notices for components demonstrably absent and add any newly present ones.
5. Record source availability obligations if future dependencies use copyleft
   terms without a linking/distribution exception.
6. Review fonts, icons, sample images, translations, and other assets
   separately; Python package metadata does not cover those assets.

Useful upstream sources:

- Python licence: <https://docs.python.org/3/license.html>
- Pillow source and licence: <https://github.com/python-pillow/Pillow>
- PyInstaller licence: <https://pyinstaller.org/en/stable/license.html>
- Inno Setup licence/download information: <https://jrsoftware.org/isdl.php>
- Tcl/Tk licence: <https://www.tcl-lang.org/software/tcltk/license.html>
