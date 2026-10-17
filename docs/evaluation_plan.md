# Evaluation plan

Status: **planned — no evaluation results exist yet.** This document defines *how* the system
will be evaluated once a suitable labelled dataset is loaded (Phase F). Metrics are not
reported here because none have been computed.

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
