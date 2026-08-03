# Performance benchmarks

`run_benchmarks.py` establishes four repeatable scenarios without changing the
application source or retaining generated images:

1. **Startup** — a new Python process imports the application, creates the real
   Tk window and reports when the window has completed idle layout work.
2. **Open a large image** — the real `ImageEditorApp.open_path` path loads and
   asynchronously renders a generated image until the preview is visible.
3. **Switch images** — a real editor window opens the first image and then
   switches through the remaining files in the generated folder.
4. **Batch process** — production `open_image`/`save_image` functions decode,
   resize and encode a deterministic set of JPEG files.

All fixtures and outputs live in a uniquely named Windows temporary directory
and are deleted after the run. The repository is unchanged unless `--output`
is explicitly requested.

## Commands

Fast smoke run used to validate the harness:

```powershell
.\.venv\Scripts\python.exe benchmarks\run_benchmarks.py --profile smoke
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

Release-comparison profile (48 MP large image, 20-image folders, three rounds):

```powershell
.\.venv\Scripts\python.exe benchmarks\run_benchmarks.py `
    --profile standard `
    --output "$env:TEMP\light-image-editor-benchmark.json"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

The JSON records the Git commit/dirty state, Python, Pillow, application
version, platform, fixture configuration, raw samples and summary statistics.

## Comparison rules

- Compare only the same profile and build type on the same hardware, Windows
  version and power mode.
- Close image-heavy applications and wait for Windows Update/indexing activity
  to settle.
- The first startup sample is the closest available approximation to a cold
  run; later samples benefit from filesystem and antivirus caches. Do not call
  either one a laboratory cold-start measurement.
- Use medians for release decisions and retain raw samples. One run is only a
  smoke result, not a statistically meaningful performance claim.
- Investigate regressions before defining hard gates. A useful initial review
  threshold is a median increase over 15%, followed by confirmation with at
  least five repetitions on controlled hardware.
- UI open and switching measurements wait for the application's explicit
  preview-ready generation, so background work is included instead of only
  measuring how quickly `open_path` returns.

## Limitations

- Startup creates the development Python entry point, not the packaged
  PyInstaller executable. Add a packaged-app ready signal before using the
  harness to compare `onefile` and `onedir` startup.
- Generated JPEG patterns are deterministic and suitable for comparisons, but
  they do not represent every camera image, ICC profile, metadata block,
  malformed input or compression distribution.
- UI scenarios require a Windows desktop session with Tk available. They are
  performance benchmarks, not functional UI automation tests.
