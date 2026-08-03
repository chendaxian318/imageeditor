# M0 benchmark harness smoke result

This result verifies that all four benchmark scenarios execute end to end. It
is **not** an accepted performance gate: the source worktree was dirty, the
smoke profile uses small fixtures, and every scenario ran only once.

Run context:

- captured: 2026-08-03 09:00:12 UTC;
- Git commit: `96c1a2a71b45380fee80febcf163340ef8a070a6`;
- branch: `feature/m0-engineering-baseline` (dirty worktree);
- Windows: 10.0.19045 x64;
- Python: 3.12.10;
- Pillow: 11.3.0;
- application version: 0.1.0;
- command: `.\.venv\Scripts\python.exe benchmarks\run_benchmarks.py --profile smoke`.

| Scenario | Smoke result |
| --- | ---: |
| Process launch to Tk ready | 564.224 ms |
| Internal application creation | 379.672 ms |
| Open and render 1600 × 1200 JPEG | 100.768 ms |
| Switch image, median of 3 switches | 59.216 ms/image |
| Batch 3 × 800 × 600 JPEG | 59.268 ms total |
| Batch throughput | 50.617 images/s |

The command exited with code 0 and temporary fixtures were removed. Establish
the first comparison baseline by running the `standard` profile from a clean,
locked release checkout on controlled hardware and retaining its JSON output
with the CI artifacts.
