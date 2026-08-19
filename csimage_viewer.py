"""Minimal production entry point for the CSImage viewer."""

from __future__ import annotations

import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--local-ai-worker":
        from light_image_editor.local_ai_tools import run_worker_cli

        raise SystemExit(run_worker_cli(sys.argv[2:]))

    from light_image_editor.app import main

    raise SystemExit(main())
