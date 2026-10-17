# Limitations and unsupported claims

## Data limitations

- **No real dataset is bundled.** `samples/demo_synthetic.csv` is fabricated and labeled
  `synthetic=true`. It is for UI/dev/unit tests only and must never be used to claim
  real-world model performance.
- NYC 311 (or any real dataset) has **not** been downloaded or inspected yet. Its schema,
  closure-time availability, duplicate labels, and recurrence labels are unknown.
- If the eventual dataset lacks reliable severity or duplicate/recurrence labels, then
  severity- and duplicate-detection performance **cannot** be validated. Such limitations
  must be reported rather than filled with invented labels.

## Unsupported claims (currently)

- No claim that the prioritized ordering improves real municipal response time.
- No claim that any score or weight is scientifically optimal.
- No claim of validated duplicate-detection accuracy (no labelled data yet).
- No claim that a complaint is dangerous or urgent based on report count or model score.
- No real-world operational outcomes have been measured or are implied.

## Technical limitations (current build)

- Prioritization baseline implemented but **only unit-tested on synthetic inputs**; not
  evaluated on real records. Scores are **not** computed/stored for complaints through the
  API yet, so the `priority_score` column is null in normal use.
- Recurrence analysis, evaluation, and simulation are **not implemented yet** (planned).
- No authentication or authorization, and no audit actor: complaint history `changed_by` is a
  fixed `api` placeholder. Not required for local, single-user development, and none is
  claimed to exist.
- No delete endpoint; complaints are edited, never removed.
- SQLite is single-file/local only; not intended for concurrent multi-user production.
- `created_at` handling in the baseline is simplified (age-to-now) — full temporal handling
  arrives with the evaluation phase.

## Risks of using predicted priority for real decisions

- A priority score reflects the data present; missing data can shift scores. Always read
  `uncertainty_flags` and `missing_fields` alongside the number.
- Predicted priority is decision **support** only. It must not be used to automatically
  dismiss, close, or deprioritize complaints.
- Recurrence/duplicate suggestions are **candidates for human review**, not automatic
  merges. Automatic merging could hide distinct incidents.
- Scores must be re-checked after data-quality fixes, since missing fields change the input.
