"""Phase D3.3 simulation report rendering: JSON + Markdown (design §8.1).

Pattern copied from ``backend/services/evaluation/reporting.py`` (D2.1):
JSON is the source of truth; Markdown is a rendering. D3-specific
requirements implemented here (§6.7, §7):

- the report opens with the ``SIMULATED DATA — NOT REAL-WORLD EVIDENCE``
  banner (§7.2);
- config echo includes the imported production constants, RNG derivation
  rule, grid enumeration, evidence mode, and window parameters (§6.7,
  §7.10);
- primary vs secondary outcomes are labelled (§7.8);
- a non-empty ``not_evaluated`` block mirrors the D2.1 convention and
  records that ``exact_source`` is absent-by-design (§4.4(iii));
- uncertainty wording states that intervals are Monte-Carlo error of the
  simulated-world estimator only (§6.6, §7.11);
- the limitations block states that M/M/1 validation is necessary, not
  sufficient (§6.9), and that deferred sensitivity blocks were not run
  (§7.9).

Audit-remediation additions:

- the **provenance** block (§ provenance) is rendered prominently and says
  plainly when the recorded commit alone does not reproduce the run;
- the **conditional censoring stamp** (§5) is rendered on the affected
  wait metrics and summarised in its own section;
- the **M/M/1 validation block** (§6.9) is rendered with the measured
  estimate, interval, analytic value and its limitations;
- the **backlog curve** B(t) (§5 M4) is rendered per cell and policy;
- a cell with an **empty evaluation cohort** is labelled as such and its
  outcomes are reported as ``not_evaluated`` rather than as measurements.

No rendering path may add an interpretation the experiment dict does not
already support.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple


def report_to_json(report: Dict[str, Any]) -> str:
    """JSON serialisation of the experiment report (source of truth)."""
    return json.dumps(report, indent=2, sort_keys=False, default=str)


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


def _summary_row(label: str, summary: Dict[str, Any]) -> str:
    return (
        f"| {label} | {_fmt(summary.get('mean'))} | {_fmt(summary.get('median'))} "
        f"| {_fmt(summary.get('p2_5'))} | {_fmt(summary.get('p97_5'))} "
        f"| {summary.get('n_seeds', 0)} |"
    )


_SUMMARY_HEADER = (
    "| metric | mean | median | p2.5 | p97.5 | n |\n"
    "| --- | --- | --- | --- | --- | --- |"
)

# Metrics rendered in the per-cell policy table.
_CELL_METRIC_ORDER: Tuple[str, ...] = (
    "wait_mean_days",
    "completion_rate",
    "fc_start",
    "fc_comp",
    "high_wait_mean_days",
    "backlog_growth_rate",
    "utilization",
)

# Maximum number of sampled points per backlog curve.
_BACKLOG_POINTS = 10


def _metric_label(metric_name: str, summary: Dict[str, Any]) -> str:
    """Metric label including the conditional censoring stamp (§5)."""
    warning = summary.get("censoring_warning")
    if warning:
        return f"{metric_name} ⚠ {warning}"
    return metric_name


def _backlog_curve_rows(
    cell_report: Dict[str, Any], policy_name: str
) -> Tuple[List[str], int]:
    """Sampled B(t) rows for one (cell, policy): header + body rows."""
    per_seed = cell_report["policies"][policy_name]["per_seed"]
    series_by_seed: Dict[str, List[Tuple[float, int]]] = {}
    for rep, payload in per_seed.items():
        raw = payload.get("backlog_series") or []
        if raw:
            series_by_seed[rep] = [(float(t), int(b)) for t, b in raw]
    if not series_by_seed:
        return [], 0
    length = min(len(series) for series in series_by_seed.values())
    if length == 0:
        return [], 0
    stride = max(1, length // _BACKLOG_POINTS)
    indices = list(range(0, length, stride))
    if indices[-1] != length - 1:
        indices.append(length - 1)
    reps = sorted(series_by_seed)
    rows = [
        "| t (days) | " + " | ".join(f"seed {rep}" for rep in reps) + " |",
        "| " + " | ".join("---" for _ in range(len(reps) + 1)) + " |",
    ]
    for index in indices:
        cells = [f"{series_by_seed[rep][index][0]:g}"]
        for rep in reps:
            series = series_by_seed[rep]
            cells.append(str(series[index][1]) if index < len(series) else "n/a")
        rows.append("| " + " | ".join(cells) + " |")
    return rows, stride


def _cell_section(cell_report: Dict[str, Any]) -> List[str]:
    cell = cell_report["cell"]
    cohort_status = cell_report.get("cohort_status", "evaluated")
    lines: List[str] = [
        f"### Cell {cell['index']}: `{cell['cell_id']}`",
        "",
        f"- ρ = {cell['rho']}, regime = `{cell['correlation_regime']}`, "
        f"visibility = `{cell['detector_visibility']}`",
        f"- Replications: {len(cell_report['seeds'])}; "
        f"complaints/rep: "
        f"{sorted({s['n_complaints'] for s in cell_report['seeds']})}",
        f"- Cohort status: `{cohort_status}` — "
        f"{cell_report.get('cohort_status_reason')}",
    ]
    if cohort_status != "evaluated":
        lines.append(
            f"- **{cell_report.get('cohort_status_label')}** — the "
            "warm-up state, the utilisation and the backlog growth recorded "
            "for this cell describe the drain of the warm-up backlog only. "
            "They are not evaluation-period outcomes and are not evidence "
            "for or against any policy."
        )
    lines += [
        "",
        "#### Per-policy metrics across seeds (primary + censoring highlighted)",
        "",
    ]
    for policy_name, payload in cell_report["policies"].items():
        lines.append(f"**{policy_name}**")
        lines.append("")
        outcome_status = payload.get("outcome_status", "evaluated")
        if outcome_status != "evaluated":
            lines.append(
                f"- Outcome status: `{outcome_status}` — "
                f"**{payload.get('outcome_status_label')}** "
                f"({payload.get('outcome_status_reason')})"
            )
        lines.append(_SUMMARY_HEADER)
        across = payload["across_seeds"]
        for metric_name in _CELL_METRIC_ORDER:
            summary = across.get(metric_name)
            if summary is None:
                lines.append(
                    f"| {_metric_label(metric_name, {})} | not evaluated | "
                    "not evaluated | not evaluated | not evaluated | 0 |"
                )
                continue
            lines.append(
                _summary_row(_metric_label(metric_name, summary), summary)
            )
        lines.append("")
        diagnostics = payload.get("no_cohort_diagnostics")
        if diagnostics:
            lines.append(
                f"- _No evaluation cohort — outcomes not evaluated._ "
                f"{diagnostics['note']}"
            )
            for metric_name, summary in diagnostics.get("metrics", {}).items():
                lines.append(
                    f"  - {metric_name} (not an outcome): "
                    f"mean={_fmt(summary.get('mean'))}, "
                    f"n={summary.get('n_seeds', 0)}"
                )
            lines.append("")

        curve_rows, stride = _backlog_curve_rows(cell_report, policy_name)
        if curve_rows:
            lines.append(f"**Backlog curve B(t) — {policy_name}**")
            lines.append("")
            lines.append(
                "B(t) = #{arrivals ≤ t} − #{service starts ≤ t} (queue "
                "waiting only; in-service excluded). Sampled every "
                f"{stride}th grid point; the complete daily series is in the "
                "JSON (`backlog_series`)."
            )
            lines.append("")
            lines += curve_rows
            lines.append("")

    lines.append("#### Paired contrasts (CRN per-seed differences)")
    lines.append("")
    contrasts = cell_report["paired_contrasts"]
    if not contrasts:
        lines.append("_No contrasts (fewer than two policies in this run)._")
        lines.append("")
    else:
        for pair_key, per_metric in contrasts.items():
            lines.append(f"**{pair_key}** (challenger − reference)")
            lines.append("")
            lines.append(_SUMMARY_HEADER)
            for metric_name in ("wait_mean_days", "high_wait_mean_days", "completion_rate"):
                summary = per_metric[metric_name]
                if summary.get("status") == "not_evaluated":
                    lines.append(
                        f"| {metric_name} (not evaluated) | not evaluated | "
                        "not evaluated | not evaluated | not evaluated | 0 |"
                    )
                    lines.append(
                        f"  - _{metric_name}: not evaluated — "
                        f"{summary.get('status_reason')}_"
                    )
                    continue
                resolved = "yes" if summary["resolved_excludes_zero"] else "no"
                lines.append(
                    _summary_row(f"{metric_name} (resolved={resolved})", summary)
                )
                caveat = summary.get("caveat")
                if caveat:
                    lines.append(f"  - _{metric_name}: {caveat}_")
            lines.append("")

    verification = cell_report["realized_generator_verification"]
    configured = verification["configured"]
    lines += [
        "#### Generator verification (§3.7)",
        "",
        f"- configured: p_repeat={configured['p_repeat']}, "
        f"p_high={configured['p_high']}, δ={configured['correlation_delta']}, "
        f"regime=`{configured['correlation_regime']}`",
        f"- realized P(high): "
        f"mean={_fmt(verification['realized_p_high']['mean'])}",
        f"- realized P(high | R=1): "
        f"mean={_fmt(verification['realized_p_high_given_r1']['mean'])}",
        f"- realized P(high | R=0): "
        f"mean={_fmt(verification['realized_p_high_given_r0']['mean'])}",
        "",
    ]
    return lines


def _provenance_section(report: Dict[str, Any]) -> List[str]:
    provenance = report.get("provenance")
    lines = ["## Provenance and reproducibility", ""]
    if not provenance:
        lines += [
            "**Provenance incomplete: not recorded.** The source revision that "
            "produced this report is unknown, so the report cannot be "
            "reproduced from the recorded commit.",
            "",
        ]
        return lines
    head = provenance.get("head", {})
    tree = provenance.get("working_tree", {})
    digest = provenance.get("source_digest", {})
    reproducible = provenance.get("reproducible_from_recorded_commit")
    if reproducible:
        lines.append(
            "- Status: `reproducible_from_commit` — the working tree was "
            "clean, so the recorded commit contains every source file the "
            "simulation used."
        )
    else:
        lines.append(
            "- Status: `incomplete` — the working tree was **not** clean at "
            "run time. The recorded commit does **not** reproduce this "
            "report by itself; the working-tree source digest below does."
        )
    lines += [
        f"- HEAD commit: `{head.get('commit') or 'n/a'}` "
        f"(full `{head.get('commit_full') or 'n/a'}`, "
        f"date `{head.get('commit_date') or 'n/a'}`, "
        f"branch `{head.get('branch') or 'n/a'}`)",
        f"- Working tree: clean={tree.get('clean')}; "
        f"modified tracked={len(tree.get('modified_tracked') or [])}, "
        f"staged={len(tree.get('staged') or [])}, "
        f"untracked={len(tree.get('untracked') or [])}",
        f"- `git diff HEAD` SHA-256: `{tree.get('diff_head_sha256') or 'n/a'}`",
        f"- Local source digest: sha256 over {digest.get('file_count')} "
        f"imported local source files = "
        f"`{digest.get('combined_sha256') or 'n/a'}`",
    ]
    uncommitted = provenance.get("uncommitted_paths") or []
    if uncommitted:
        lines.append(f"- Uncommitted paths ({len(uncommitted)}):")
        for path in uncommitted:
            lines.append(f"  - `{path}`")
    lines.append("")
    return lines


def _censoring_section(report: Dict[str, Any]) -> List[str]:
    censoring = report.get("censoring", {})
    lines = [
        "## Censoring (conditional warning, §5)",
        "",
        f"- Rule: {censoring.get('rule')}",
        f"- Threshold: `f_c^{{start}} > {censoring.get('threshold')}`; "
        f"stamp text: `{censoring.get('warning_text')}`",
        f"- Stamped (cell, policy, replication) combinations: "
        f"{censoring.get('triggered_count', 0)}",
        f"- Not evaluated (empty evaluation cohort → undefined "
        f"f_c^{{start}}): {censoring.get('not_evaluated_count', 0)}",
        "",
    ]
    triggered = censoring.get("triggered") or []
    if triggered:
        lines.append("| cell | policy | rep | cohort | f_c^{start} | stamp |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for entry in triggered:
            lines.append(
                f"| `{entry['cell_id']}` | {entry['policy']} | {entry['rep']} "
                f"| {entry['cohort_size']} | {_fmt(entry['fc_start'])} "
                f"| {entry['warning']} |"
            )
        lines.append("")
    return lines


def _mm1_section(report: Dict[str, Any]) -> List[str]:
    validation = report.get("mm1_validation")
    lines = ["## M/M/1 engine validation (§6.9)", ""]
    if not validation:
        lines += ["_No M/M/1 validation block recorded._", ""]
        return lines
    configuration = validation.get("configuration", {})
    interval = validation.get("confidence_interval_95_days", {})
    applicability = validation.get("applicability", {})
    criterion = validation.get("criterion", {})
    observed = validation.get("observed", {})
    lines += [
        f"- Purpose: {validation.get('purpose')}",
        f"- Validated quantity: {validation.get('target_metric')}",
        f"- Analytic target: {validation.get('analytic_target')}",
        f"- Configuration: ρ={configuration.get('rho')}, "
        f"c={configuration.get('servers')}, "
        f"service={configuration.get('service_dist')}, "
        f"E[S]={configuration.get('mean_service_days')} d, "
        f"μ={_fmt(configuration.get('mu_per_day'))}/day, "
        f"λ={_fmt(configuration.get('lambda_per_day'))}/day",
        f"- Windows (days): t_warm={configuration.get('t_warm_days')}, "
        f"t_obs={configuration.get('t_obs_days')}, "
        f"t_drain_max={configuration.get('t_drain_max_days')}",
        f"- Replications: {validation.get('replications')} "
        f"(requested {configuration.get('replications_requested')})",
        f"- Measured queue waiting time (mean of replication means): "
        f"**{_fmt(validation.get('estimate_queue_wait_mean_days'))} days**",
        f"- 95 % Student-t interval: "
        f"[{_fmt(interval.get('low'))}, {_fmt(interval.get('high'))}] days "
        f"(half-width {_fmt(interval.get('half_width'))}, "
        f"t_{interval.get('degrees_of_freedom')} = "
        f"{_fmt(interval.get('t_critical_975'))}, "
        f"SE {_fmt(interval.get('standard_error'))})",
        f"- Analytic E[W_q] = ρ/(μ−λ): "
        f"**{_fmt(validation.get('analytic_queue_wait_mean_days'))} days**",
        f"- Analytic E[W] = 1/(μ−λ) (time in system, reported separately): "
        f"{_fmt(validation.get('analytic_time_in_system_days'))} days",
        f"- Relaxation time 1/(μ−λ): "
        f"{_fmt(validation.get('relaxation_time_days'))} days; "
        f"t_warm / relaxation = "
        f"{_fmt(validation.get('warmup_over_relaxation_ratio'))}",
        f"- Criterion: {criterion.get('rule')} → "
        f"**{'PASS' if criterion.get('covers_analytic_value') else 'FAIL'}** "
        "(reported regardless of pass or fail)",
        f"- Applicability: {applicability.get('applicable')} — "
        f"{applicability.get('explanation')}",
        f"- Observed: min cohort={observed.get('cohort_size_min')}, "
        f"max cohort={observed.get('cohort_size_max')}, "
        f"max f_c^{{start}}={_fmt(observed.get('fc_start_max'))}, "
        f"max f_c^{{comp}}={_fmt(observed.get('fc_comp_max'))}",
        "",
        "**Preconditions**",
        "",
        "| precondition | passed | detail |",
        "| --- | --- | --- |",
    ]
    for name, entry in (validation.get("preconditions") or {}).items():
        lines.append(
            f"| {name.replace('_', ' ')} | "
            f"{'yes' if entry.get('passed') else 'no'} | {entry.get('detail')} |"
        )
    lines += ["", "**Limitations of this validation**", ""]
    for limitation in validation.get("limitations", []):
        lines.append(f"- {limitation}")
    lines.append("")
    return lines


def _contrast_evidence_section(report: Dict[str, Any]) -> List[str]:
    """Report-level statement of how far the paired contrasts can be read."""
    block = report.get("contrast_evidence")
    lines = ["## Paired-contrast evidence (how far the contrasts can be read)", ""]
    if not block:
        lines += ["_No paired-contrast evidence block recorded._", ""]
        return lines
    lines.append(f"- Rule: {block.get('rule')}")
    lines.append(
        f"- Replications used (R per cell): {block.get('replications_used')}; "
        f"minimum for an informative interval: "
        f"{block.get('min_replications_for_informative_interval')}"
    )
    lines.append(
        f"- Informative: **{'yes' if block.get('informative') else 'no'}**"
    )
    note = block.get("note")
    if note:
        lines.append(f"- **{note}**")
    lines.append("")
    return lines


def report_to_markdown(report: Dict[str, Any]) -> str:
    """Markdown rendering of the experiment report (design §8.1)."""
    config = report["config"]
    experiment = config["experiment"]
    sim = config["sim"]
    provenance = report.get("provenance") or {}
    reproducible = bool(provenance.get("reproducible_from_recorded_commit"))
    commit_line = (
        f"- Last committed revision (HEAD): `{report.get('code_commit') or 'n/a'}`"
    )
    if reproducible:
        commit_line += (
            " — the working tree was clean at run time, so this commit "
            "contains every source file the simulation used and reproduces "
            "the report."
        )
    else:
        commit_line += (
            " — the last commit only. It does **not** reproduce this report "
            "by itself: the working tree was not clean at run time. See "
            "**Provenance and reproducibility** below for the working-tree "
            "state and the source digest that do identify the run."
        )

    lines: List[str] = [
        f"# {report['banner']}",
        "",
        f"- Simulation version: `{report['sim_version']}`",
        commit_line,
        f"- Python: `{report.get('python_version')}`",
        f"- Run timestamp (UTC): `{report.get('run_timestamp') or 'n/a'}`",
        "",
    ]
    lines += _provenance_section(report)
    lines += [
        "## Configuration",
        "",
        f"- Grid: {experiment['grid_cell_count']} cells "
        f"(loads={experiment['loads']}, regimes={experiment['correlation_regimes']}, "
        f"visibility={experiment['detector_visibilities']}); "
        f"R = {experiment['replications']} seeds/cell; "
        f"master_seed = {experiment['master_seed']}",
        f"- Enumeration: {config['grid_enumeration_order']}",
        f"- Seed derivation: {config['rng_stream_derivation']}",
        f"- Evidence mode: {config['evidence_mode']}",
        f"- Windows (days): t_warm={sim['t_warm_days']}, "
        f"t_obs={sim['t_obs_days']}, t_drain_max={sim['t_drain_max_days']}, "
        f"t_cut={sim['t_cut_days']}",
        f"- E[S] = {sim['mean_service_days']} d "
        f"({sim['mean_service_days_status']})",
        f"- λ (per cell) = ρ·c/E[S]: base SimConfig ρ={sim['rho']} → "
        f"λ={sim['lambda_per_day']} arrivals/day shown here; "
        f"cells override ρ (and therefore λ) per the grid `loads` above",
        f"- Evidence saturation CAP (assumption, §4.4): {sim['evidence_cap']}",
        f"- Assumption knobs: {', '.join(sim['assumption_knobs'])}",
        "",
        "### Imported production constants (live values, never retyped)",
        "",
    ]
    for name, value in sim["production_constants"].items():
        lines.append(f"- `{name}` = `{value}`")
    lines += [
        "",
        "### Fairness checklist (§6.5)",
        "",
    ]
    checklist = report["fairness_checklist"]
    for key, value in checklist.items():
        if key == "note":
            continue
        if isinstance(value, dict):
            passed = value.get("passed")
            performed = value.get("checks_performed")
            lines.append(
                f"- {'✓' if passed else '✗'} {key.replace('_', ' ')} "
                f"({value.get('checks_passed', 0)}/{performed} checks passed)"
            )
            continue
        lines.append(f"- {'✓' if value else '✗'} {key.replace('_', ' ')}")
    lines += [
        f"- {checklist['note']}",
        "",
        "## Metric definitions",
        "",
        f"Cohort: {report['metric_definitions']['cohort']}",
        "",
        f"T_cut: {report['metric_definitions']['t_cut']}",
        "",
    ]
    definitions = report["metric_definitions"]
    for metric_id in ("M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9"):
        entry = definitions[metric_id]
        caveat = f" — _{entry['caveat']}_" if "caveat" in entry else ""
        lines.append(
            f"- **{metric_id} ({entry['tier']})** {entry['name']}: "
            f"`{entry['formula']}`{caveat}"
        )
    uncertainty = definitions["uncertainty"]
    lines += [
        "",
        f"Uncertainty: {uncertainty['per_cell']}; contrasts: "
        f"{uncertainty['contrasts']}.",
        f"**{uncertainty['wording_rule']}**",
        "",
        "## Outcomes",
        "",
        f"- Primary (predeclared, §7.8): {', '.join(report['primary_outcomes'])}",
        f"- Secondary/exploratory: {', '.join(report['secondary_outcomes'])}",
        f"- {report['outcome_labelling_note']}",
        "",
    ]
    lines += _censoring_section(report)
    lines += _mm1_section(report)
    lines += _contrast_evidence_section(report)
    lines += [
        "## Cells",
        "",
    ]
    for cell_report in report["cells"]:
        lines += _cell_section(cell_report)

    lines += [
        "## Metrics not evaluated",
        "",
        f"Status: `{report['not_evaluated']['status']}`",
        "",
    ]
    for item in report["not_evaluated"]["items"]:
        lines.append(f"- {item}")
    lines += [
        f"- {report['not_evaluated']['note']}",
        "",
        "## Deferred sensitivity blocks (not run)",
        "",
    ]
    for entry in report["deferred_sensitivities"]:
        lines.append(f"- `{entry['name']}` — {entry['detail']} ({entry['status']})")
    lines += [
        "",
        "## Limitations",
        "",
    ]
    for limitation in report["limitations"]:
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def write_report(
    report: Dict[str, Any], output_dir: str | Path, stem: str = "simulation"
) -> Tuple[Path, Path]:
    """Write ``<stem>.json`` and ``<stem>.md`` and return both paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{stem}.json"
    md_path = out / f"{stem}.md"
    json_path.write_text(report_to_json(report), encoding="utf-8")
    md_path.write_text(report_to_markdown(report), encoding="utf-8")
    return json_path, md_path
