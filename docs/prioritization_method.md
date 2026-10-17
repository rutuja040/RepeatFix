# Prioritization method

Status: **transparent baseline implemented and unit-tested; not validated on real data.**

The engine is a single backend module: `backend/services/prioritization/rule_based.py`.
No weights below are claimed to be scientifically optimal — they are explicit, adjustable,
documented starting points.

## Inputs

| Feature | Meaning | Expected range | Missing behavior |
|---|---|---|---|
| `severity_label` | Independently assessed severity (human/label) | `critical/high/medium/low` (or synonyms) | Missing → `severity=0.0` **plus** `missing_severity` uncertainty flag. Never treated as "confirmed low". |
| `created_at` | Complaint creation time | ISO datetime | Age contribution `0`; `created_at` not auto-flagged as missing (age unknown). |
| `resolved_at` | Resolution time (if any) | ISO datetime | If `status == "resolved"` and absent, age computed to now. |
| `recurrence_evidence` | Evidence that the problem repeats | 0.0–1.0 | Absent → 0.0 plus `missing_recurrence_evidence` flag. |
| `evidence_completeness` | Fraction of expected fields present/credible | 0.0–1.0 (default 0.5) | <0.4 triggers `low_evidence_completeness`. |
| `latitude`/`longitude` | Location | valid coords | Absent → `location_completeness=0` + `missing_location` flag. |

## Formula (documented, transparent)

```
base = 0.40*severity
     + 0.25*age_norm            # age_norm = min(unresolved_days / 30, 1)
     + 0.20*recurrence_evidence
     + 0.10*evidence_completeness
     + 0.05*location_completeness
```

Then:

```
if uncertainty_flags:
    base -= min(0.15, 0.03 * n_unique_flags)   # bounded penalty
score = clamp(base, 0.0, 1.0)
```

- `age_norm` uses `resolved_at - created_at` when resolved, otherwise "time unresolved".
- Weights sum to 1.0 before the uncertainty penalty.

## Missing values

- Missing severity / recurrence / location add **uncertainty flags**, not low scores.
- Missing severity **and** very low recurrence **and** low completeness together append
  `manual_review_recommended` to `uncertainty_flags`.
- Missing data never silently produces a confident low-priority classification.

## Normalization

- `severity` mapped to fixed ordinal values (0.9 / 0.6 / 0.3 / 0.5 default).
- `age_norm` capped at 1.0 for ≥30 days unresolved.
- Final score clamped to `[0, 1]`.
- There is **no cross-dataset min–max rescaling** in this baseline (deterministic and
  independent of other records in the batch).

## Tie-breaking (current)

- The module returns a score per complaint only; it does not sort a list.
- When a list is ordered for ranking (Phase D/F), ties will be broken deterministically by
  `(priority_score DESC, created_at ASC, complaint_id ASC)`. _(not yet wired end-to-end)_

## Why a complaint gets its rank (plain English)

`PrioritizationResult.rationale` carries human-readable reasons, e.g.
`"unresolved > 30d"`, `"uncertainty_penalty_applied"`,
`"insufficient_evidence_manual_review"`.

The API (Phase D) will expose `components` + `rationale` + `uncertainty_flags` so an
officer sees the score, its parts, and its caveats together.

## Explicit non-claims

- Number of duplicate reports is **not** allowed to set severity.
- A model score alone never labels a complaint an "emergency".
- The score supports, but does not replace, human judgment.

## Determinism

Same inputs + same config ⇒ same output. Covered by
`tests/backend/test_prioritization.py::test_deterministic_for_identical_inputs`.
