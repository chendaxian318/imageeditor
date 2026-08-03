# M0 standard performance baseline

This record is the first controlled comparison point for the M0 source build.
It is not a universal product claim: the run used one Windows machine, the
development Python entry point, warm filesystem/antivirus caches after the
first sample, and a worktree that still contained the pending M0 changes.

Run context:

- captured: 2026-08-03 09:05:17 UTC;
- baseline commit before pending M0 commit: `96c1a2a71b45380fee80febcf163340ef8a070a6`;
- branch: `feature/m0-engineering-baseline`;
- Windows: 10.0.19045 x64;
- Python: 3.12.10;
- Pillow: 11.3.0;
- application version: 0.1.0;
- profile: 48 MP large image, 20-image switch/batch sets, three repetitions.

| Scenario | Median result |
|---|---:|
| New Python process to Tk ready | 541.330 ms |
| Create application inside worker | 373.230 ms |
| Open and render 8000 × 6000 JPEG | 1,595.369 ms |
| Switch 2560 × 1440 images | 170.080 ms/image |
| Batch 20 × 2400 × 1600 JPEG | 2,637.409 ms |
| Batch throughput | 7.583 images/s |

Command:

```powershell
.\.venv\Scripts\python.exe benchmarks\run_benchmarks.py `
    --profile standard `
    --output "$env:TEMP\light-image-editor-m0-standard.json"
```

The run completed all scenarios and removed temporary fixtures. Future
comparisons must use the same profile, machine, Windows power mode and build
type. Investigate a confirmed median regression greater than 15% before a
release; do not treat a single run as a hard failure by itself.
