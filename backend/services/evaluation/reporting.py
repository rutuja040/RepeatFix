"""Rendering helpers for Phase D2.1 evaluation reports (JSON + Markdown)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Tuple


def report_to_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=False, ensure_ascii=False)


def _rate_table(rates: Dict[str, float]) -> str:
    lines = ["| field | missing rate |", "| --- | --- |"]
    for field_name, rate in rates.items():
        lines.append(f"| {field_name} | {rate:.4f} |")
    return "\n".join(lines)


def _baseline_section(baseline: Dict[str, Any]) -> str:
    lines = [
        f"### {baseline['name']} (`{baseline['key']}`)",
        "",
        f"- Eligible: {baseline['eligible_count']}",
        f"- Ranked: {baseline['ranking_size']}",
        f"- Top {len(baseline['top_k'])}: {', '.join(baseline['top_k']) or '(none)'}",
        "",
        "Notes:",
    ]
    lines += [f"- {note}" for note in baseline["notes"]]
    lines.append("")
    lines.append("Metrics:")
    lines.append("")
    lines.append("| metric | status | reason |")
    lines.append("| --- | --- | --- |")
    for metric, info in baseline["performance_metrics"].items():
        lines.append(f"| {metric} | {info['status']} | {info['reason']} |")
    return "\n".join(lines)


def _ablation_section(ablation: Dict[str, Any]) -> str:
    lines = [
        f"Eligible for recurrence: {ablation['eligible_count']}",
        "",
        "| config | disabled signals | relations | complaints w/ relations | coverage |",
        "| --- | --- | --- | --- | --- |",
    ]
    for config in ablation["configs"]:
        disabled = ", ".join(config["disabled_signals"]) or "(none)"
        lines.append(
            f"| {config['name']} | {disabled} | {config['relations_total']} | "
            f"{config['complaints_with_relations']} | {config['coverage']:.4f} |"
        )
    lines.append("")
    lines.append("Agreement vs full policy (Jaccard):")
    lines.append("")
    lines.append("| config | shared | only in config | only in full | jaccard |")
    lines.append("| --- | --- | --- | --- | --- |")
    for name, info in ablation["agreement"]["vs_full"].items():
        jaccard = (
            f"{info['jaccard']:.4f}"
            if info.get("jaccard") is not None
            else "n/a (undefined: both sets empty)"
        )
        lines.append(
            f"| {name} | {info['shared']} | {info['only_in_a']} | "
            f"{info['only_in_b']} | {jaccard} |"
        )
    lines.append("")
    lines.append(f"> {ablation['agreement']['note']}")
    return "\n".join(lines)


def report_to_markdown(report: Dict[str, Any]) -> str:
    meta = report["metadata"]
    snap = report["snapshot"]
    counts = report["counts"]
    config = report["config"]

    lines = [
        "# RepeatFix Evaluation Report (Phase D2.1)",
        "",
        "Label-free evaluation harness output. No accuracy, precision, recall or "
        "calibration metrics are reported because no ground-truth labels exist.",
        "",
        "## Run metadata",
        "",
        f"- Harness version: {meta['harness_version']}",
        f"- Run timestamp (UTC): {meta['run_timestamp']}",
        f"- Code commit: {meta['code_commit'] or '(not recorded)'}",
        "",
        "## Snapshot",
        "",
        f"- Source: {snap['source']}",
        f"- Total complaints: {snap['total_complaints']}",
        f"- Import batches: {snap['total_import_batches']}",
        f"- Complaint history rows: {snap['total_complaint_history_rows']}",
        f"- Synthetic complaints: {snap['synthetic_complaints']}",
        f"- created_at range: {snap['created_at_min']} .. {snap['created_at_max']}",
        "",
        "## Configuration",
        "",
        f"- Evaluation cutoff (t0): {config['cutoff']}",
        f"- Max candidates per complaint: {config['max_candidates']}",
        f"- Include synthetic: {config['include_synthetic']}",
        f"- Ranking eligibility: {config['ranking_eligibility']}",
        f"- Recurrence eligibility: {config['recurrence_eligibility']}",
        f"- Required recurrence signals: {', '.join(config['required_recurrence_signals'])}",
        (
            "- Recurrence thresholds: similarity >= "
            f"{config['recurrence_thresholds']['similarity_threshold']}, "
            "nearby <= "
            f"{config['recurrence_thresholds']['location_meters_max']:g} m, "
            "time window <= "
            f"{config['recurrence_thresholds']['time_window_days']:g} days"
        ),
        (
            "- Count-based thresholds: same category within "
            f"{config['count_based_thresholds']['meters']:g} m and "
            f"{config['count_based_thresholds']['days']:g} days"
        ),
        "",
        "## Counts",
        "",
        f"- Ranking-eligible: {counts['ranking_eligible']}",
        f"- Recurrence-eligible: {counts['recurrence_eligible']}",
        f"- Excluded (synthetic): {counts['excluded']['synthetic']}",
        f"- Excluded (no created_at): {counts['excluded']['no_created_at']}",
        f"- Excluded (after cutoff): {counts['excluded']['after_cutoff']}",
        "- Excluded (recurrence, missing coordinates/category): "
        f"{counts['excluded']['recurrence_missing_coordinates_or_category']}",
        "",
        "## Missing data",
        "",
        "### All complaints",
        "",
        _rate_table(report["missing_data"]["all_complaints"]),
        "",
        "### Ranking-eligible",
        "",
        _rate_table(report["missing_data"]["ranking_eligible"]),
        "",
        "## Baselines",
        "",
    ]

    for baseline in report["baselines"]:
        lines.append(_baseline_section(baseline))
        lines.append("")

    lines += [
        "## Recurrence signal ablation",
        "",
        _ablation_section(report["recurrence_ablation"]),
        "",
        "## Metrics not evaluated",
        "",
        "| metric | reason |",
        "| --- | --- |",
    ]
    for entry in report["not_evaluated"]:
        lines.append(f"| {entry['metric']} | {entry['reason']} |")
    lines.append("")
    return "\n".join(lines)


def write_report(
    report: Dict[str, Any], output_dir: str | Path, stem: str = "evaluation"
) -> Tuple[Path, Path]:
    """Write ``<stem>.json`` and ``<stem>.md`` and return both paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"{stem}.json"
    md_path = out / f"{stem}.md"
    json_path.write_text(report_to_json(report), encoding="utf-8")
    md_path.write_text(report_to_markdown(report), encoding="utf-8")
    return json_path, md_path
