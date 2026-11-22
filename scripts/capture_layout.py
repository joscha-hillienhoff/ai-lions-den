#!/usr/bin/env python3
"""Capture every view and phase at the agreed viewports and report layout issues.

Usage:
    npm run build
    uv run playwright install chromium
    uv run python scripts/capture_layout.py --out /tmp/layout-shots
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

try:
    from scripts.layout_harness import collect_session_layout, write_issues_report
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts.layout_harness import collect_session_layout, write_issues_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Directory for PNG captures (default: layout-shots in the system temp directory)",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Capture only the CI matrix instead of every listed viewport",
    )
    parser.add_argument("--report", type=Path, default=None, help="Write layout issues here")
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="layout-session-") as temp:
        shots = arguments.out
        if shots is None:
            shots = Path(tempfile.gettempdir()) / "layout-shots"
        shots.mkdir(parents=True, exist_ok=True)
        captures, summary = collect_session_layout(
            Path(temp), full_matrix=not arguments.ci, screenshot_dir=shots
        )
        failures = 0
        for capture in captures:
            for issue in capture.issues():
                failures += 1
                print(f"{capture.summary()}: {issue}")
        print(f"{summary['captures']} captures in {shots}")
        if arguments.report:
            write_issues_report(captures, arguments.report)
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
