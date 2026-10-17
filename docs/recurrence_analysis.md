# Recurrence Analysis (Phase D1)

RepeatFix's recurrence analysis is a conservative, explainable baseline. It never merges, deletes, or rewrites complaints. Text similarity alone is never treated as proof of recurrence.

## Problem definition

Distinguish:
- **Exact duplicate source records**: same source identity (same `source_key` or `source_record_id` from same dataset/source). These are not new incidents.
- **Potentially related complaints**: multiple reports that share some evidence but do not meet the conservative threshold to infer a persistent issue.
- **Potential recurrence**: multiple complaints that share compatible evidence (text, category, location, time) with sufficient combined justification. Such cases require **human review** and are never auto-merged.
- **Independent incidents**: insufficient shared evidence.

The analysis is decision-support only. It does not produce a calibrated probability.

## Available input fields

The service uses only fields that exist on `Complaint`:
- `complaint_id`, `title`, `description`, `category`
- `latitude`, `longitude`, `location_label`, `administrative_area`
- `created_at` (UTC naive in storage; comparison uses UTC)
- `source`, `synthetic`, `data_quality_flags`
- `raw_imported_values` (JSON) for source identity (`source_key`, `source_record_id`)

## Signals and configurable thresholds

| Signal | Threshold | Contribution | Notes |
|---|---|---|---|
| Text similarity (title/description, normalized) | `SIMILARITY_THRESHOLD = 0.70` | 0.40 | Uses SequenceMatcher ratio on normalized text. Alone insufficient to qualify as potential recurrence. |
| Same category | exact match (case-insensitive) | 0.20 | Compatibility only; different categories reduce confidence. |
| Geographic proximity | `LOCATION_METERS_MAX = 200.0` | 0.30 | Requires both complaints to have valid lat/lon. If only text location available, gives small contribution (0.05) for same admin area. |
| Time proximity | `TIME_WINDOW_DAYS = 30.0` | 0.10 | Requires valid created_at. |

Constraints:
- A `potential_recurrence` relation is accepted only when **all four** independent evidence types are present together: text similarity (`>= SIMILARITY_THRESHOLD`), same category, strong geographic proximity (`nearby` via valid lat/lon `<= LOCATION_METERS_MAX`), and time proximity (`<= TIME_WINDOW_DAYS` with valid `created_at`).
- If any of the four is missing, the candidate is rejected. In particular:
  - text similarity alone is rejected (insufficient);
  - a weak same-administrative-area match (0.05) does **not** substitute for validated coordinates;
  - a different category or a time gap outside the window disqualifies the candidate.
- Confidence = min(1.0, sum of contributions). Label: high >=0.8, medium >=0.5, low <0.5. This is **explainable aggregation**, not a calibrated probability.

## Missing data handling

- Missing coordinates: flagged in `missing_data` and `warnings`; location evidence treated conservatively (no strong contribution; weak text-location only).
- Missing timestamps: flagged; time proximity ignored or limited.
- Little/no text: similarity assessment limited; warnings added.
- Source identity missing/unreliable: exact-source detection falls back across available fields; never assumed.

All conservative failures result in assumptions/warnings without fabricating evidence.

## Duplicate detection vs recurrence analysis

| Aspect | Duplicate detection (Phase C) | Recurrence analysis (Phase D1) |
|---|---|---|
| Goal | Detect exact content duplicates and potential duplicates for the import batch | Assess whether different complaints suggest persistent/repeated issues across complaints |
| Merge behavior | Never merges; exact rejected at import, potential flagged | Never merges/deletes/rewrites; only reports relations |
| Scope | Per-import context + existing DB for idempotency and conflicts | Cross-complaint relations against recent candidates |
| Sufficient proof | Exact content signature; potential uses similarity+location+time (import-time heuristics) | Requires multiple evidence types; text similarity alone insufficient |
| Output | Import summary issues/codes | Explainable relations with reasons, confidence_explained, labels, assumptions/warnings |

## Output interpretation

Each `RecurrenceRelation` includes:
- `related_complaint_id`, `relation_type` (`exact_source`, `potential_recurrence`)
- `confidence_explained` (0-1, additive sum; explicitly not probability)
- `confidence_label` (`low/medium/high`) — human-readable bucket
- `reasons[]`: each has `code`, `description`, `weight_contribution`, `evidence` (values and thresholds)
- `evidence_summary`: short summary of evidence codes
- `requires_human_review`: always true
- `is_auto_merge_candidate`: always false

`RecurrenceAnalysisResult` also includes: `analyzed_at` (UTC), `warnings[]`, `assumptions[]`, `missing_data[]`, `has_reliable_coords`, `has_reliable_time`.

## Limitations

- Baseline heuristic, not validated against labeled recurrence data. Claims of accuracy require ground truth.
- Candidate set is limited (default 50, max 200) by recency to keep results explainable and bounded.
- Location evidence requires valid coordinates for strong contribution; text-based location matching is weak by design.
- Categories are treated as compatibility signal only; different schemas/categories may limit matching.
- Time window 30 days is a conservative starting point; appropriate window depends on domain.
- No automatic clustering beyond pairwise relations; future phases may introduce clustering with explicit uncertainty.

## Future evaluation needs

To validate (Phase F), need ground-truth labels distinguishing recurrence from independent incidents, with inter-annotator agreement. Without such labels, performance metrics must not be asserted as known.
