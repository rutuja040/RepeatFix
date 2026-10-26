# Evaluation plan

Status: **partially implemented — a minimal, reproducible, label-free harness exists (Phase
D2.1); no evaluation *results* claiming accuracy are reported.** This document defines *how*
the system will be evaluated once a suitable labelled dataset is loaded (Phase F). Metrics
that require ground-truth labels are not computed, and are reported as **not evaluated**.

## Implemented label-free harness (Phase D2.1)

`backend/services/evaluation/` implements a deterministic, read-only evaluation that is
runnable today without any labelled data:

- `harness.py` — loads a snapshot, applies a temporal as-of cutoff, computes eligible sets,
  runs the baselines, runs recurrence signal ablations and assembles the report.
- `baselines.py` — FIFO, complaint-count and the existing rule-based priority ordering.
- `reporting.py` — renders the report to JSON and Markdown.

Entry point:

```
.\\.venv\\Scripts\\python.exe scripts\\run_evaluation.py
.\\.venv\\Scripts\\python.exe scripts\\run_evaluation.py --cutoff 2026-06-01T00:00:00 --out data\\eval_reports
```

What the harness reports: snapshot description, evaluation cutoff, configuration, record
counts, per-field missing-data rates, the three baseline orderings, and the recurrence
ablation (relation counts / coverage per configuration, plus pairwise agreement).

What the harness deliberately does **not** report: precision, recall, F1, Precision@K,
NDCG, MAE/RMSE, or calibration. Those require ground-truth labels that do not exist, so
every label-dependent metric is emitted with `status: "not_evaluated"` and an explicit
reason.

### Temporal as-of evaluation

- `RecurrenceAnalysisService.analyze(...)` accepts an optional, backward-compatible `cutoff`.
  When set, candidates created after the cutoff (or with no timestamp) are ignored, so a
  historical decision can never depend on future information. With no cutoff the Phase D1
  behaviour is unchanged.
- The harness also filters its own candidate pool at query time, and passes the cutoff to
  the service as defence in depth.
- `resolved_at` is never used as a feature anywhere in the harness.

### Recurrence signal ablation

`analyze(...)` exposes no ablation parameter. The harness passes its `disabled_signals`
set through a private evaluation-side subclass (`_AblationRecurrenceService` in
`backend/services/evaluation/harness.py` that overrides the protected `_required_signals()`
hook), so the production `RecurrenceAnalysisService.analyze()` signature carries no
ablation surface at all. Disabling a signal removes it from the four-signal acceptance
requirement (the signal is still computed and reported, it just stops being *required*),
which is the only meaningful ablation of a conjunctive rule. Configurations: `full`,
`without_text_similarity`, `without_same_category`, `without_nearby`,
`without_time_proximity`.

Reported per configuration: relation counts, coverage, evidence-code frequencies and
Jaccard agreement between configurations. **Agreement is not accuracy** — without pair
labels a configuration that agrees with the full policy may be equally wrong. When both
compared relation sets are empty the Jaccard index is undefined (0/0); the report emits
`null` with `jaccard_status: "undefined_both_sets_empty"` rather than a misleading `1.0`.

The report's configuration block records the numeric thresholds used by the run
(recurrence: similarity, metres, days; count-based: metres, days), taken live from the
production constants so the experiment can be reproduced.

### Scientific limitations of the current harness

- No ground-truth recurrence, relevance or resolution labels exist, so the harness can only
  compare methods by their deterministic orderings and set overlap, never by correctness.
- The recurring "age" component of the rule-based score is 0 for unresolved records, so that
  baseline is driven almost entirely by location completeness and uncertainty penalties.
- Recurrence confidence is an explainable sum of weights, not a calibrated probability.
- The dataset is tiny (synthetic samples plus a small development database), so no
  statistically meaningful conclusion can be drawn; the harness exists to prove the
  protocol and keep it reproducible.


## Research question

Does recurrence-aware, risk-based complaint prioritization select higher-priority /
higher-risk complaints earlier than simple complaint-count or first-in-first-out ordering?

Decision-support framing: the tool supports evidence-based scheduling; it does not claim to
measure or improve real municipal response times.

## Prediction targets (only where data supports them)

| Target | Required labels | Available? |
|---|---|---|
| High-priority ranking | severity/high-priority label, or expert review labels | Unknown (dataset not loaded) |
| Resolution-time prediction | `created_at` + `resolved_at` pairs | Unknown |
| Recurrence / duplicate classes | labelled duplicate or recurrence cases | Unknown |

Any target whose required labels are absent will be reported as **not evaluated**. Metrics
will not be computed without their required inputs.

## Baselines

1. **FIFO** — order by eligible creation / waiting time.
2. **Complaint-count** — order by number of associated reports, only where reliable incident
   associations exist.
3. **Rule-based** — documented transparent scoring (current `rule_based.py`).
4. **Predictive model** — only if a valid target and enough observations exist.
5. **RepeatFix** — the recurrence-aware, risk-based method (Phases D–E).

All baselines are compared on **identical eligible records**; any difference in the eligible
set between methods must be explained explicitly.

## Metrics

- Ranking: Precision@K, Recall@K, NDCG@K.
- Binary high-priority: precision, recall, F1, PR-AUC (when the target exists).
- Resolution-time: MAE, RMSE (only when closure timestamps exist).
- Probabilistic: Brier score + calibration (only for probabilistic outputs).
- Duplicate/recurrence: per-class precision/recall/F1 (only with labelled cases).
- Simulation: high-risk waiting time, target-breach rate, workload balance (Phase G).

Metrics whose labels/inputs are missing are skipped, not faked.

## Splitting strategy

- Use **chronological** train/validation/test splits when historical timestamps permit.
- Document for each prediction task: prediction time, features available at that time, and
  the target being predicted.

## Leakage prevention

- A model predicting resolution at submission time must **not** use the actual closing
  timestamp as an input feature.
- Test-set information must not be used to tune scoring weights or choose a model.
- `resolved_at`-derived features are excluded from any "at submission time" prediction.

## Reproducibility

- Fixed random seeds for any stochastic step.
- Fixed data sampling / filtering described in scripts.
- Inputs, config, and splits recorded alongside outputs so results are re-derivable.
