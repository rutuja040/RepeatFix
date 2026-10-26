# RepeatFix — Research Evaluation Design (Phase D2)

Status: **design document — the label-free Phase D2.1 harness it specifies now exists (see
`docs/evaluation_plan.md`); no labelled-data experiment and no performance results have been
computed.**
This document is a read-only inspection report and evaluation design. It contains **no
measured performance numbers**. Where a conclusion cannot be supported by the repository,
it is explicitly marked as a gap or hypothesis.

Evidence tags used throughout:

- **[VERIFIED]** — confirmed by reading the repository, tests, or the bundled development DB.
- **[GAP]** — required data, label, or capability that does **not** exist in the repository.
- **[HYPOTHESIS]** — plausible but unproven; would need literature review or experiments.
- **[RECOMMENDATION]** — proposed design choice, not yet implemented.

Research title: *"RepeatFix: Risk-Aware Prioritization of Recurring Civic Complaints Under
Resource Constraints."*

> **Important framing.** The repository currently supports **decision support**, not automated
> decisions, and it has **no labelled dataset and no operational outcomes**. Therefore almost
> every *performance* metric discussed below is currently **not computable**. This document
> designs the evaluation so that, once data exists, results are produced honestly; until then
> the only defensible measurements are system-behaviour and agreement/coverage statistics.

---

## A. Repository findings

### A.1 Architecture and stack

- **[VERIFIED]** Backend: Python 3.11, FastAPI, Pydantic v2 (`extra="forbid"` schemas),
  SQLAlchemy 2.x over SQLite (`repeatfix.db`). Frontend: React 19 + TypeScript + Vite.
  Analysis libraries are already pinned in `backend/requirements.txt`: `pandas`, `numpy`,
  `scikit-learn`, `matplotlib`, `seaborn`, `pyarrow`, `tqdm`, `pyyaml`.
- **[VERIFIED]** Layering: routes (`backend/api/routes/`) → services
  (`backend/services/`) → models (`backend/models/`). Prioritization logic is intended to live
  only in `backend/services/prioritization/` (per `docs/architecture.md`).
- **[VERIFIED]** Timestamps are stored as **naive UTC** in SQLite and serialized by the API with
  an explicit `+00:00` suffix (`backend/core/time.py`).

### A.2 Data model

- **[VERIFIED]** `complaints` table (22 columns): `id`, `complaint_id` (unique),
  `title`, `description`, `category`, `latitude`, `longitude`, `location_label`,
  `administrative_area`, `created_at`, `last_updated_at`, `resolved_at`, `status`,
  `assigned_department`, `source`, `synthetic`, `evidence_metadata`, `severity_label`,
  `data_quality_flags`, `priority_score`, `priority_rationale`, `raw_imported_values`.
- **[VERIFIED]** `complaint_history` (append-only field-change log):
  `id`, `complaint_id` (FK), `field_name`, `old_value`, `new_value`, `changed_at`, `changed_by`.
- **[VERIFIED]** `import_batches` and `complaint_import_links` provide full CSV provenance
  (dataset identity, content SHA-256, mapping, per-row issues, row counts).
- **[VERIFIED]** `status` vocabulary: `open`, `in_progress`, `resolved`, `closed`
  (`backend/core/constants.py`).

### A.3 Prioritization service

- **[VERIFIED]** `backend/services/prioritization/rule_based.py` implements a deterministic,
  documented additive scorer:
  `score = 0.40·severity + 0.25·age_norm + 0.20·recurrence_evidence + 0.10·completeness + 0.05·location − uncertainty_penalty`.
  It returns `priority_score` (0–1), `components`, `rationale`, `uncertainty_flags`,
  `data_quality_warnings`, `missing_fields`.
- **[VERIFIED]** Missing severity is flagged (`missing_severity`), never silently treated as low.
- **[VERIFIED — critical]** The prioritizer is **not wired to the API**. `RuleBasedPrioritizer`
  and `PrioritizationInputs` are referenced **only in tests**; `priority_score` is written to
  the DB **nowhere**. Every row's `priority_score` is `NULL` in normal use, and the UI renders
  it as an empty/placeholder value. No list endpoint sorts by priority.

### A.4 Recurrence service (Phase D1)

- **[VERIFIED]** `backend/services/recurrence/` implements a conservative, explainable pairwise
  analyzer. The endpoint `POST /api/v1/recurrence/analyze` returns relations for one complaint
  against up to `max_candidates` (default 50, capped 200) other complaints.
- **[VERIFIED]** Current acceptance rule (after commit `d0cdd29`) requires **all four** signals:
  `text_similarity` (≥0.70), `same_category`, `nearby` (valid lat/lon within 200 m), and
  `time_proximity` (≤30 days). Any missing signal ⇒ no relation. Weak
  `same_admin_area` (0.05) never substitutes for validated coordinates.
- **[VERIFIED]** Exact-source detection uses `source_key`/`source_record_id` via
  `raw_imported_values`, producing a separate `exact_source` relation.
- **[VERIFIED]** All relations set `requires_human_review=True` and
  `is_auto_merge_candidate=False`. `confidence_explained` is an additive sum that is
  **explicitly not a calibrated probability** (and equals 1.0 whenever the four-signal rule
  passes).
- **[VERIFIED]** Relations are **not persisted**; each request recomputes them. Nothing is
  stored in a table.
- **[VERIFIED — evaluation-relevant]** Candidate selection is
  `ORDER BY created_at DESC, id DESC LIMIT n` with **no "as-of" decision time**. It can include
  complaints created *after* the complaint under analysis, which is a leakage hazard for any
  historical evaluation.

### A.5 CSV import pipeline (Phase C)

- **[VERIFIED]** `POST /api/v1/imports/csv` (multipart) with bounded streaming, UTF-8/UTF-8-BOM
  only, max 100 MB / 50,000 rows, column-alias auto-detection plus explicit mapping, per-row
  validation, exact-duplicate rejection, potential-duplicate flagging (never merged), and
  idempotency via `source_key`. Two-phase transaction preserves provenance even on failure.
- **[VERIFIED]** Supported import targets (`backend/services/imports/fields.py`) include:
  `complaint_id`, `title`, `description`, `category`, `latitude`, `longitude`,
  `location_label`, `administrative_area`, `created_at`, `status`, `assigned_department`,
  `source`, `severity_label`, `evidence_metadata`, `synthetic`.
- **[VERIFIED — critical]** **`resolved_at` is not an importable field.** Imported rows with
  `status=resolved` do **not** get a `resolved_at` value. Only complaints transitioned through
  the API (`PATCH .../complaints/{id}` with a resolved/closed status) receive `resolved_at`
  (`backend/services/complaints/service.py`).
- **[VERIFIED]** Provenance is preserved (original values in `raw_imported_values`; cleaned
  values in columns), which is favorable for defensible evaluation and audit.

### A.6 Existing evaluation / simulation scaffolding

- **[VERIFIED]** `backend/services/evaluation/baselines.py` exists but contains only constant
  identifiers: `Baselines.FIFO`, `Baselines.COMPLAINT_COUNT`, `Baselines.RULE_BASED`, plus a
  `ComparisonResult` dataclass. **No baseline is implemented and nothing calls it.**
- **[VERIFIED]** `backend/services/simulation/__init__.py` is empty.
- **[VERIFIED]** `docs/evaluation_plan.md` exists and is marked *planned*; it already sketches
  research question, baselines, metrics, splitting, and leakage prevention. This Phase D2
  document refines it and adds the data-availability audit.

### A.7 Tests and current verified status

- **[VERIFIED]** Backend: `python -m pytest` → **124 passed** (includes
  `tests/backend/test_recurrence.py`, 12 tests). `python -m ruff check .` → clean.
- **[VERIFIED]** Frontend: `npm run typecheck` clean; `npm test` → 39 passed; `npm run build`
  succeeds.
- **[VERIFIED]** Tests use an isolated temp SQLite DB (`tests/conftest.py`) and never touch the
  development database.

### A.8 Datasets actually present

- **[VERIFIED]** `samples/demo_synthetic.csv` — 5 rows, `synthetic=true`, no `resolved_at`, no
  severity. Explicitly labelled as fabricated; must never be used for real-world claims.
- **[VERIFIED]** `repeatfix.db` (development DB) — 3 complaint rows, 1 import batch, 1 import
  link, 4 history rows. No severity labels, no priority scores, one `resolved_at`.
- **[GAP]** No real civic dataset (e.g., a 311 export) is bundled, downloaded, or referenced
  anywhere in the repository.

---

## B. Actual available data fields

This section reports what the system **can actually hold and validate today**, not what would be
convenient. "Importable" means the CSV pipeline can populate it.

| Capability | Field(s) | Present? | Importable? | Notes |
|---|---|---|---|---|
| Complaint creation time | `created_at` | **[VERIFIED]** yes | yes | Server default if absent; imported values normalized to naive UTC. |
| Resolution time | `resolved_at` | **[VERIFIED]** yes (column) | **no** | Only set by API status transitions. Imported `resolved` rows have `resolved_at=NULL`. |
| Last update time | `last_updated_at` | **[VERIFIED]** yes | no | System-managed. |
| Categories | `category` | **[VERIFIED]** yes | yes | Free text, kept verbatim; no controlled vocabulary. |
| Descriptions | `description` | **[VERIFIED]** yes | yes | Optional; counts toward completeness. |
| Titles | `title` | **[VERIFIED]** yes | yes | Required. |
| Severity / urgency labels | `severity_label` | **[VERIFIED]** column exists | yes | **Human-assessed**; optional; **no dataset supplies it** (dev DB: 0/3 non-null). |
| Location label / area | `location_label`, `administrative_area` | **[VERIFIED]** yes | yes | Free text. |
| Coordinates | `latitude`, `longitude` | **[VERIFIED]** yes | yes | Range-validated; partial coords flagged. |
| Coordinate reliability | — | **[PARTIAL]** | — | No accuracy field; reliability is inferred only from presence/validity and the `incomplete_coordinates` flag. |
| Status / outcome proxy | `status` | **[VERIFIED]** yes | yes | `open/in_progress/resolved/closed`; unknown values defaulted + flagged. |
| Assigned department | `assigned_department` | **[VERIFIED]** yes | yes | Free text; no controlled list. |
| Provenance | `source`, `raw_imported_values`, import links | **[VERIFIED]** yes | yes | Strong provenance. |
| Synthetic marker | `synthetic` | **[VERIFIED]** yes | yes | Enables exclusion of synthetic rows from real claims. |
| Data-quality flags | `data_quality_flags` | **[VERIFIED]** yes | derived | Encoded warnings from import. |
| Model outputs | `priority_score`, `priority_rationale` | **[VERIFIED]** columns exist | no | **Never populated by the running app.** |
| Recurrence relations | — | **[VERIFIED]** computed on request | no | Not persisted; no table. |
| **Recurrence / duplicate ground-truth labels** | — | **[GAP]** | — | Do **not** exist anywhere in the repo. |
| **Resolution outcome** (fixed/not-fixed, quality) | — | **[GAP]** | — | Only `status`/`resolved_at` exist. |
| **Staffing / budget / workload / resources** | — | **[GAP]** | — | No scheduler, capacity, crew, budget, or SLA fields anywhere. |
| **Priority/relevance labels** (expert "should handle first") | — | **[GAP]** | — | None. |

**Bottom line.** The system can hold *descriptive* complaint data (time, text, category,
location, status, provenance) and a *human severity label* that no actual dataset provides.
It has **no labels for relevance, recurrence, or outcomes**, and **no resource model**.

---

## C. Research question and evaluation target

### C.1 What RepeatFix can currently produce

1. **[VERIFIED]** A per-complaint **rule-based priority score** (0–1) with components,
   rationale, and uncertainty flags — implemented but not wired to the API.
2. **[VERIFIED]** Pairwise **potential-recurrence relations** with an explainable evidence
   summary and mandatory human review — implemented via one endpoint, not persisted.

### C.2 Candidate evaluation targets and their data requirements

| Target | What RepeatFix would predict/rank | Required labels | Available today? |
|---|---|---|---|
| Recurrence detection | whether two complaints are the same recurring issue | ground-truth recurrent/duplicate pairs | **[GAP]** no |
| Recurrence candidate agreement | how D1's outputs change across signals/configs | none (self-comparison) | **[VERIFIED]** yes (behavioural only) |
| Risk/high-priority ranking | which complaints should be handled first | expert or severity-based relevance labels | **[GAP]** no (severity column empty) |
| Resolution-time prediction | expected time-to-resolution | `created_at` + `resolved_at` pairs | **[GAP]** ~none (import can't set `resolved_at`) |
| Resource-constrained scheduling | order to maximise throughput under capacity | resource model + outcomes | **[GAP]** no |

### C.3 What constitutes success (and what does not)

- **[RECOMMENDATION]** Success must be defined **per target**, against a **defensible reference**:
  - *Recurrence detection* requires labelled recurrent pairs; success = precision/recall/F1 at a
    documented threshold. **[GAP]** cannot be computed now.
  - *Ranking* requires relevance labels; success = Precision@K / Recall@K / NDCG@K vs baselines.
    **[GAP]** cannot be computed now.
  - *Behavioural validity* (computable now) = determinism, coverage, missing-data handling, and
    **agreement/ablation** between signal configurations. This measures the *system*, not
    *quality against ground truth*.
- **[RECOMMENDATION]** A result must **not** be called "success" merely because the pipeline
  runs, or because a method out-performs FIFO on an unlabelled proxy.

### C.4 Can recurrence detection be evaluated separately from prioritization?

- **[HYPOTHESIS / design]** Conceptually yes: recurrence detection is a **pairwise binary**
  task; prioritization is a **per-complaint ranking** task consuming a recurrence signal.
  They can and should be evaluated independently.
- **[GAP]** However, **neither can be validly evaluated on real data today**: recurrence has no
  ground-truth pairs, and prioritization has no relevance labels and is not even wired on real
  complaint features. Their separation is a design decision for when labels exist.

---

## D. Baselines and metrics

### D.1 Baselines (only where the data supports them)

All baselines must be compared on the **identical eligible set** of complaints. Any difference
in eligibility must be reported.

1. **FIFO** — **[VERIFIED data: `created_at`]**. Order eligible complaints by `created_at`
   ascending (oldest first), tie-break `complaint_id` ascending. Needs no labels. It is the
   natural "do nothing smart" reference and directly answers "does prioritization beat a queue?".
2. **Complaint-count (spatio-temporal/category count)** — **[VERIFIED data: coords, `created_at`,
   `category`]**. For each complaint, count other complaints in the same category within a fixed
   radius (e.g., reuse `LOCATION_METERS_MAX = 200 m`) and time window (e.g., 30 days), excluding
   the complaint itself. Rank by descending count, tie-break FIFO. This is an **unsupervised
   proxy** for "the problem keeps happening"; it is defensible because it uses only observed
   data. It is **not** validated recurrence ground truth and must be described as a proxy.
3. **Existing rule-based prioritization** — **[VERIFIED code]**. Rank by
   `RuleBasedPrioritizer.score(...)`. Caveat: because `severity_label` is absent in all current
   data, the score collapses largely to age/completeness/location; this must be stated. This
   baseline requires wiring the scorer to the API (currently not wired) or calling it in an
   evaluation script.
4. **Recurrence-aware prioritization** — **[RECOMMENDATION; depends on D1]**. Augment baseline 2
   or 3 with the D1 recurrence signal, e.g.:
   - (a) cluster size from D1 relations (count of `potential_recurrence` partners), or
   - (b) a binary `has_recurrence` feature combined with the rule-based score.
   Must define the exact combination and keep it reproducible. Because D1 requires all four
   signals, coverage will be low on sparse data; report coverage explicitly.
5. **Risk-aware prioritization** — **NOT defensible yet [GAP]**. "Risk" requires validated risk
   indicators (severity, injury/damage, expert risk grades). The only candidate,
   `severity_label`, is human-assessed and **absent from all available data**; there is no
   external risk source. Do **not** invent risk labels. Defer this baseline until a labelled
   risk signal exists.
6. **Simple count-based baseline** — synonymous with (2); if kept separate, use category-only
   counts (no geography/time) as an even weaker reference.

### D.2 Metrics — what each measures, required data, limitations

| Metric | Measures | Required data | Status today |
|---|---|---|---|
| Precision@K / Recall@K / NDCG@K | whether relevant/high-priority complaints appear near the top of the ranking | **relevance labels** (expert priority or defensible severity) | **[GAP]** not computable |
| Precision / Recall / F1 (recurrence) | agreement of predicted recurrence pairs with ground-truth recurrent/duplicate pairs | labelled recurrent pairs | **[GAP]** not computable |
| PR-AUC (recurrence) | threshold-independent pair-quality | labelled pairs | **[GAP]** not computable |
| MAE / RMSE (resolution time) | predicted vs actual time-to-resolution | `created_at` + `resolved_at` pairs | **[GAP]** ~not computable (import gap) |
| Brier score / calibration | quality of probabilistic outputs | a **calibrated** probability output | **[GAP]** D1 output is explicitly not a probability |
| Coverage / missing-data rate | fraction of complaints analysable; which signals/fields absent | field presence | **[VERIFIED]** computable now |
| Determinism | identical inputs ⇒ identical outputs | none | **[VERIFIED]** computable now |
| Signal-ablation agreement | how relations change when a signal/threshold is removed | none | **[VERIFIED]** computable now (descriptive) |
| Simulation: waiting time, breach rate, workload balance | effect of scheduling under capacity | a **resource model** (staff/throughput/SLA) | **[GAP]** model absent |

**Rules for honest reporting.**
- **[RECOMMENDATION]** Any metric whose labels/inputs are missing is reported as **"not
  evaluated"** — never estimated, never faked.
- **[RECOMMENDATION]** Behavioural metrics (coverage, determinism, ablation agreement) must be
  labelled as *system behaviour*, not *accuracy*.
- **[RECOMMENDATION]** Any simulation result must be explicitly labelled **simulated**, with all
  assumptions (arrival rates, capacity, service times) recorded, and must never be presented as
  a real-world outcome.

---

## E. Experimental protocol

### E.1 Data cleaning and inclusion/exclusion

- **[RECOMMENDATION]** Exclude `synthetic=true` rows from any real-world claim; keep them only
  for pipeline/behaviour tests.
- **[RECOMMENDATION]** Deduplicate by provenance `source_key` (idempotent imports already
  guarantee this) and drop exact duplicates flagged at import.
- **[RECOMMENDATION]** For recurrence analysis, the eligible set requires: valid `latitude` **and**
  `longitude`, non-null `created_at`, and non-null `category` — because D1 cannot emit a
  relation without all four signals. Report the fraction dropped per criterion.
- **[RECOMMENDATION]** Record every exclusion count and rule; keep a frozen dataset manifest.

### E.2 Splits and temporal validity

- **[RECOMMENDATION]** There is currently **no model training**, so no train/val/test split is
  needed yet. If ranking is evaluated, use a **chronological** protocol, not random splits.
- **[RECOMMENDATION]** Define an explicit **decision time** `t0`. A complaint is "in the queue"
  only if `created_at ≤ t0`. The candidate set for recurrence must also satisfy
  `created_at ≤ t0`. The current endpoint does **not** enforce this and must not be used as-is
  for historical evaluation.
- **[RECOMMENDATION]** Relevance/outcome labels for evaluation must be observable **after** `t0`
  (e.g., resolved later), which is legitimate for *evaluation* but forbidden as an *input*.

### E.3 Leakage prevention

- **[RECOMMENDATION]** Never use `resolved_at` (or anything derived from it) as an input feature
  when scoring "at submission time".
- **[RECOMMENDATION]** Exclude future complaints from the candidate set (enforce `created_at ≤ t0`).
- **[RECOMMENDATION]** Do not tune thresholds, weights, or signal choices on the test period.
- **[RECOMMENDATION]** `complaint_history` may contain post-hoc changes; when reconstructing
  historical state, use field values **as of `t0`**, not final values.

### E.4 Baseline comparison and ablations

- **[RECOMMENDATION]** Compare all methods on the same eligible set and the same `t0`.
- **[RECOMMENDATION]** Ablations for D1: remove each of {text, category, location, time}; vary
  thresholds (`SIMILARITY_THRESHOLD`, `LOCATION_METERS_MAX`, `TIME_WINDOW_DAYS`); report the
  change in relation count, coverage, and pairwise agreement. This is descriptive and
  label-free.
- **[RECOMMENDATION]** Ablations for prioritization: remove each weighted component; compare
  rankings on the eligible set (label-free comparisons can report rank correlation/overlap, but
  **not** "quality").

### E.5 Reproducibility

- **[RECOMMENDATION]** Fix random seeds for any stochastic step (none currently, but required
  for future work).
- **[RECOMMENDATION]** Record: code commit hash, the dataset identity
  (`import_batches.dataset_identity`), `content_sha256`, all thresholds/config, inclusion rules,
  `t0`, and the exact command run.
- **[RECOMMENDATION]** Emit results with a machine-readable config sidecar so any number can be
  re-derived.

### E.6 Missing, unreliable, or conflicting information

- **[RECOMMENDATION]** Do not impute silently. Surface the D1 `missing_data` and `warnings`
  lists and the prioritizer's `uncertainty_flags`/`missing_fields` alongside any result.
- **[RECOMMENDATION]** Treat partial coordinates and unparseable timestamps as missing (import
  already flags these).
- **[RECOMMENDATION]** Report conflicting fields (e.g., `resolved` status with null
  `resolved_at`) rather than guessing a resolution time.

---

## F. Data and research gaps

1. **[GAP] No ground-truth recurrence/duplicate labels.** Recurrence precision/recall/F1 cannot
   be computed. Needed: labelled pairs or a documented annotation protocol (with inter-annotator
   agreement).
2. **[GAP] No relevance / high-priority labels.** Precision@K, Recall@K, NDCG@K cannot be
   computed. Needed: expert priority labels or a defensible severity source.
3. **[GAP] Severity labels absent in practice.** `severity_label` exists but no dataset
   populates it (dev DB: 0/3). The rule-based score is therefore dominated by age/completeness.
4. **[GAP] Resolution time not importable.** `resolved_at` is set only by API status changes;
   imported `resolved` rows have null `resolved_at`. Resolution-time metrics are therefore
   generally not computable on imported datasets.
5. **[GAP] No resource-constraint data.** No staff, crew, capacity, budget, SLA, or workload
   fields exist, so "under resource constraints" cannot currently be modelled or evaluated.
6. **[GAP] No real dataset.** Only a 5-row synthetic sample and a 3-row dev DB exist.
7. **[GAP] Recurrence relations not persisted; no `as-of` cutoff.** Limits historical,
   leakage-free evaluation as-is.
8. **[GAP] Prioritization not wired end-to-end.** `priority_score` is never computed/stored;
   there is no ranked list endpoint.
9. **[GAP] No evaluation harness.** `services/evaluation/baselines.py` is a stub; nothing
   computes metrics.
10. **[GAP] No controlled category/department vocabulary**, which complicates cross-dataset
    comparability.

### Additional evidence required before evaluation claims

- A real, provenance-documented complaint dataset imported through the existing pipeline.
- A documented labelling effort (or an explicit statement that only behavioural metrics are
  reportable).
- Confirmation of whether resolution timestamps can be obtained (dataset with closure times
  **and** an import field to carry them, which does not exist yet).

---

## G. Novelty assessment

### G.1 What is implemented (facts)

- **[VERIFIED]** Conservative, explainable pairwise recurrence detection with a strict
  four-signal conjunction, explicit reasons, and mandatory human review.
- **[VERIFIED]** Provenance-preserving CSV ingestion with validation, idempotency, and
  duplicate flagging (never merging).
- **[VERIFIED]** Uncertainty-aware rule-based prioritization that refuses to treat missing data
  as low priority.
- **[VERIFIED]** Decision-support framing with explicit "no auto-merge/no auto-decision" rules.

### G.2 What is proposed but NOT implemented

- **[GAP / PROPOSED]** Wire prioritization end-to-end and expose ranked lists with explanations.
- **[GAP / PROPOSED]** Evaluation harness with temporal splits and leakage checks.
- **[GAP / PROPOSED]** Resource-constrained scheduling simulation.
- **[GAP / PROPOSED]** Dashboard integration; any ML model.

### G.3 Novelty — stated honestly

- **[HYPOTHESIS]** The potentially research-worthy contribution would be an *evaluation* of
  whether (and when) conservative recurrence evidence improves civic complaint prioritization
  under resource constraints, with uncertainty and provenance made explicit. This is a
  **hypothesis about a research direction**, not a demonstrated contribution.
- **[RECOMMENDATION]** Do **not** claim novelty for *combining* known components
  (duplicate detection, spatio-temporal matching, rule-based scoring, scheduling). Combination
  alone is not novelty. Any novelty claim must survive a literature review and a controlled
  comparison — neither of which exists yet.
- **No citations are provided here.** References must be gathered and verified in a real
  literature review; none have been fabricated.

### G.4 Literature-review questions (to answer before any novelty claim)

- How is duplicate/recurrence detection handled in civic 311 analytics, and what ground truth do
  those works use?
- What civic-complaint prioritization / learning-to-rank methods exist, and against which
  baselines and labels are they evaluated?
- How is "risk" defined and validated in public-service triage literature?
- What resource-constrained (queue/inspection-routing) scheduling formalisms are standard, and
  are they applicable to civic complaints?
- What explainability and human-in-the-loop standards apply to civic decision support?
- Are there public 311 datasets with usable closure timestamps and duplicate/recurrence labels?

---

## H. Recommended implementation plan

### H.1 Smallest scientifically defensible next step

**[RECOMMENDATION]** Implement a **read-only evaluation harness** that can only report what the
data supports, and that enforces temporal validity. Concretely, the minimal step is:

1. **Temporal "as-of" evaluation mode.** Given a decision time `t0`, build the eligible set and
   the recurrence candidate set using **only** records with `created_at ≤ t0` (fixing the current
   leakage hazard). Keep this in the evaluation layer; do not change D1's live behaviour.
2. **Label-free baseline runner.** Compute FIFO and the spatio-temporal/category count baseline,
   plus the existing rule-based score (called directly), on the frozen eligible set.
3. **Signal ablations for D1** (drop each signal; vary thresholds) and report **coverage,
   missing-data rates, determinism, and pairwise agreement** only.
4. **Explicit "not evaluated" reporting** for every performance metric lacking labels
   (recurrence precision/recall/F1, P@K/R@K/NDCG@K, resolution-time MAE/RMSE, calibration).
5. **Machine-readable run manifest** (commit, dataset identity, `content_sha256`, config, `t0`,
   inclusion rules, seed).

This step is defensible because it cannot produce a false performance claim: it measures only
system behaviour and agreement, and it refuses to compute metrics whose inputs are absent.
It also deliberately does **not** implement a model, a simulation, or a dashboard.

### H.2 Sequencing after the above

1. **[RECOMMENDATION]** Obtain a real, provenance-documented dataset and import it via the
   existing pipeline (respecting size limits; no large blind downloads here).
2. **[RECOMMENDATION]** Define and execute a labelling protocol for recurrence/relevance; record
   inter-annotator agreement. Only then compute precision/recall/F1 and ranking metrics.
3. **[RECOMMENDATION]** Wire prioritization end-to-end (expose scores + explanations) **without**
   changing its formula, so rankings can be evaluated.
4. **[RECOMMENDATION]** Only if a resource model can be justified from real data, implement the
   simulation — clearly labelled as simulated.
5. **[RECOMMENDATION]** Conduct the literature review (Section G.4) before any novelty claim.

### H.3 Explicit non-goals for the next step

- No ML model training.
- No synthetic or fabricated metrics.
- No dashboard redesign.
- No Phase D3 / simulation implementation.
- No application code, schema, test, dependency, or configuration changes until the design is
  approved.

---

## Appendix — Verified facts vs. gaps (quick reference)

- **[VERIFIED]** Prioritization scorer implemented but **not wired**; `priority_score` always null in use.
- **[VERIFIED]** D1 recurrence implemented; strict four-signal rule; relations not persisted; no `as-of` cutoff.
- **[VERIFIED]** CSV import cannot populate `resolved_at`.
- **[VERIFIED]** Provenance is strong (`raw_imported_values`, import links, `content_sha256`).
- **[VERIFIED]** Only synthetic sample data and a 3-row dev DB exist.
- **[GAP]** No recurrence labels, no relevance labels, no severity in practice, no resolution-time data for imports, no resource model, no evaluation harness.
- **[RECOMMENDATION]** Next step = read-only, temporally-valid, label-aware evaluation harness that reports "not evaluated" where labels are missing.
