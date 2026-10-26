"""CLI entry point for the Phase D2.1 label-free evaluation harness.

Usage (from the repository root):

    .\\.venv\\Scripts\\python.exe scripts\\run_evaluation.py
    .\\.venv\\Scripts\\python.exe scripts\\run_evaluation.py --cutoff 2024-06-01T00:00:00
    .\\.venv\\Scripts\\python.exe scripts\\run_evaluation.py --db repeatfix.db --out data\\eval_reports

The harness is read-only with respect to complaints; it only writes report files
under the chosen output directory (default ``data/eval_reports``, which is
gitignored).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the RepeatFix label-free evaluation harness.")
    parser.add_argument("--db", default=None, help="SQLite DB path or SQLAlchemy URL (default: settings.db_url).")
    parser.add_argument("--cutoff", default=None, help="ISO-8601 evaluation cutoff (t0). Default: max created_at.")
    parser.add_argument("--out", default=str(_ROOT / "data" / "eval_reports"), help="Output directory for reports.")
    parser.add_argument("--stem", default="evaluation", help="Report filename stem.")
    parser.add_argument("--max-candidates", type=int, default=50, help="Max candidate complaints per subject.")
    parser.add_argument("--top-k", type=int, default=20, help="Top-k to include in reports.")
    parser.add_argument("--include-synthetic", action="store_true", help="Include synthetic complaints.")
    parser.add_argument("--no-write", action="store_true", help="Print JSON to stdout instead of writing files.")
    return parser.parse_args(argv)


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_ROOT),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip() or None
    except Exception:
        return None


def main(argv=None) -> int:
    args = _parse_args(argv)

    if args.db:
        url = args.db
        if "://" not in url:
            url = "sqlite:///" + str(Path(url).resolve().as_posix())
        os.environ["DB_URL"] = url

    from backend.core.time import utcnow
    from backend.db.session import SessionLocal
    from backend.services.evaluation.harness import EvaluationConfig, run_evaluation
    from backend.services.evaluation.reporting import report_to_json, write_report

    cutoff = None
    if args.cutoff:
        cutoff = datetime.fromisoformat(args.cutoff)

    config = EvaluationConfig(
        cutoff=cutoff,
        max_candidates=args.max_candidates,
        include_synthetic=args.include_synthetic,
        top_k=args.top_k,
        run_timestamp=utcnow(),
        code_commit=_git_commit(),
        db_description=os.environ.get("DB_URL", "settings.db_url"),
    )

    db = SessionLocal()
    try:
        report = run_evaluation(db, config)
    finally:
        db.close()

    if args.no_write:
        print(report_to_json(report))
        return 0

    json_path, md_path = write_report(report, args.out, stem=args.stem)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
