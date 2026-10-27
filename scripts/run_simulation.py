"""CLI entry point for the Phase D3.3 controlled capacity simulation.

Usage (from the repository root):

    .\\.venv\\Scripts\\python.exe scripts\\run_simulation.py --no-write
    .\\.venv\\Scripts\\python.exe scripts\\run_simulation.py --replications 2 --max-arrivals 80
    .\\.venv\\Scripts\\python.exe scripts\\run_simulation.py --load 0.8 --scenario independent --out data\\sim_reports

The default run is the full §6.1 factorial (3 loads × 3 regimes × 2
visibility × R seeds × 4 policies). Use ``--replications`` with a small R
for pilots (design §6.8 requires a small-R pilot benchmark before the full
grid is launched). Reports are written under ``data/sim_reports`` by
default (gitignored, §1); ``--no-write`` prints JSON to stdout instead.

This CLI never touches the database, the API, or the frontend (design §2,
§8.5); it is read-only with respect to the application.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the RepeatFix controlled capacity simulation (Phase D3)."
    )
    parser.add_argument(
        "--out", default=str(_ROOT / "data" / "sim_reports"),
        help="Output directory for reports.",
    )
    parser.add_argument("--stem", default="simulation", help="Report filename stem.")
    parser.add_argument(
        "--replications", type=int, default=None,
        help="Replications R per cell (default: SimConfig.replications = 30).",
    )
    parser.add_argument(
        "--master-seed", type=int, default=None,
        help="Master seed for deterministic stream derivation (default: 0).",
    )
    parser.add_argument(
        "--load", type=float, action="append", default=None,
        help="Restrict to one or more ρ values (default: 0.5, 0.8, 1.2).",
    )
    parser.add_argument(
        "--scenario", action="append", default=None,
        choices=("correlated", "independent", "anti_correlated"),
        help="Restrict to correlation regimes (default: all three).",
    )
    parser.add_argument(
        "--visibility", action="append", default=None,
        choices=("detector_visible", "detector_blind"),
        help="Restrict to detector-visibility regimes (default: both).",
    )
    parser.add_argument(
        "--policy", action="append", default=None,
        choices=("fifo", "rule_based", "count_based", "recurrence_aware"),
        help="Restrict to policies (default: all four).",
    )
    parser.add_argument(
        "--max-arrivals", type=int, default=None,
        help=(
            "Hard cap on generated arrivals per stream (bounds runtime). "
            "The cap truncates the observation window and can produce empty "
            "evaluation cohorts — prefer the design horizon (no cap)."
        ),
    )
    parser.add_argument(
        "--mm1-replications", type=int, default=None,
        help=(
            "Replications of the §6.9 M/M/1 validation configuration "
            "(default: 30). The validation block is included in every report "
            "regardless of its outcome."
        ),
    )
    parser.add_argument(
        "--p-repeat", type=float, default=None, help="Configured p_repeat knob.",
    )
    parser.add_argument(
        "--p-high", type=float, default=None, help="Configured p_high knob.",
    )
    parser.add_argument(
        "--delta", type=float, default=None,
        help="Correlation delta δ (design §3.7).",
    )
    parser.add_argument(
        "--evidence-cap", type=float, default=None,
        help="Evidence saturation CAP for recurrence_aware (design §4.4).",
    )
    parser.add_argument(
        "--t-warm", type=float, default=None, help="Warm-up window in days.",
    )
    parser.add_argument(
        "--t-obs", type=float, default=None, help="Observation window in days.",
    )
    parser.add_argument(
        "--t-drain", type=float, default=None, help="Drain window in days.",
    )
    parser.add_argument(
        "--mean-service", type=float, default=None,
        help="E[S] in days (documented placeholder, never calibrated).",
    )
    parser.add_argument(
        "--no-write", action="store_true",
        help="Print JSON to stdout instead of writing files.",
    )
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

    from backend.core.time import utcnow
    from backend.services.simulation.experiment import (
        DETECTOR_VISIBILITIES,
        LOADS,
        CORRELATION_REGIMES,
        ExperimentConfig,
        run_experiment,
    )
    from backend.services.simulation.model import SimConfig
    from backend.services.simulation.report import (
        report_to_json,
        write_report,
    )

    sim_overrides = {}
    if args.replications is not None:
        sim_overrides["replications"] = args.replications
    if args.master_seed is not None:
        sim_overrides["master_seed"] = args.master_seed
    if args.max_arrivals is not None:
        sim_overrides["max_arrivals"] = args.max_arrivals
    if args.p_repeat is not None:
        sim_overrides["p_repeat"] = args.p_repeat
    if args.p_high is not None:
        sim_overrides["p_high"] = args.p_high
    if args.delta is not None:
        sim_overrides["correlation_delta"] = args.delta
    if args.evidence_cap is not None:
        sim_overrides["evidence_cap"] = args.evidence_cap
    if args.t_warm is not None:
        sim_overrides["t_warm_days"] = args.t_warm
    if args.t_obs is not None:
        sim_overrides["t_obs_days"] = args.t_obs
    if args.t_drain is not None:
        sim_overrides["t_drain_max_days"] = args.t_drain
    if args.mean_service is not None:
        sim_overrides["mean_service_days"] = args.mean_service
    sim = SimConfig(**sim_overrides)

    config = ExperimentConfig(
        sim=sim,
        loads=tuple(args.load) if args.load else LOADS,
        correlation_regimes=(
            tuple(args.scenario) if args.scenario else CORRELATION_REGIMES
        ),
        detector_visibilities=(
            tuple(args.visibility) if args.visibility else DETECTOR_VISIBILITIES
        ),
        policy_filter=tuple(args.policy) if args.policy else None,
        run_timestamp=utcnow(),
        code_commit=_git_commit(),
        mm1_replications=(
            args.mm1_replications if args.mm1_replications is not None else 30
        ),
    )

    report = run_experiment(config)

    # Provenance is computed *after* the run so that the source digest
    # covers every module the run actually imported. The recorded commit
    # alone reproduces the run only when the working tree was clean; the
    # block says so explicitly otherwise (audit remediation, § provenance).
    from backend.services.simulation.provenance import build_provenance

    report["provenance"] = build_provenance()

    if args.max_arrivals is not None:
        print(
            f"WARNING: --max-arrivals {args.max_arrivals} truncates the "
            "observation window; cells whose cohort is empty are reported as "
            "'No evaluation cohort — outcomes not evaluated'.",
            file=sys.stderr,
        )

    if args.no_write:
        print(report_to_json(report))
        return 0

    json_path, md_path = write_report(report, args.out, stem=args.stem)
    print(f"Wrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
