## 2026-10-22 — Phase D3.3 audit remediation, second pass: registry integrity, evidence-cap binding, small-R contrast qualification, honest `code_commit` labelling

A follow-up review of the D3.3 pilot (2026-10-21 entry above) found the remediation itself
present and correct, but four residual defects around it, plus two documentation errors. This
entry records the second pass. **No production file was modified** (the D2.1 production changes
remain uncommitted and untouched); nothing staged, committed, reset or reverted.

### Changes — simulation package
- `policies.py`: the registry is now checked against the **live** registry instead of a snapshot
  map taken at import time. `make_recurrence_aware_key` stamps the closure's own
  `_evidence_cap` and `_policy_name` into the key; `validate_policy` rejects a key whose
  identity does not match `POLICIES[name].key` (`_validate_key_matches_name`), so a policy
  cannot be registered under a name that is not its own, and a `recurrence_aware` key must
  declare an `evidence_cap` equal to the closure's cap.
- `queue_sim.py`: the `recurrence_aware` evidence cap is checked for **presence** as well as
  equality with `config.evidence_cap`, and the cap check now runs *before* the
  evidence-presence check, so a mis-bound cap is reported as a cap problem rather than as a
  missing-evidence problem.
- `experiment.py`: paired contrasts now carry the evidence needed to read them —
  `replications_used`, `evidence_strength` (`insufficient_replications` /
  `sufficient_replications` / `no_observations`), `evidence_for_either_policy` and a `caveat`
  sentence. `MIN_REPLICATIONS_FOR_CONTRAST_INTERVAL = 5` is the threshold below which the
  design §6.6 empirical 2.5/97.5 percentile interval degenerates to the sample
  minimum/maximum; the report gains a `contrast_evidence` block stating the rule, the
  replications used and whether the intervals are informative at all. The §6.6
  `resolved_excludes_zero` semantics are **unchanged** — it still records that the observed
  interval excludes 0 — but it is no longer readable as "the policies differ" at small R.
  The fairness checklist's `policy_order_independence.passed` is now computed from the checks
  actually performed instead of being hard-coded `True`, and `_limitations()` takes the
  replication count so the small-R limitation is emitted from the same data.
- `report.py`: the header now labels the hash as **"Last committed revision (HEAD)"** and says
  explicitly that the commit alone does **not** reproduce the report when the working tree was
  dirty at run time (the previous bare "code commit" wording invited exactly the
  misreading the provenance block exists to prevent). New section **"Paired-contrast evidence
  (how far the contrasts can be read)"**, and the per-metric contrast caveats are rendered in
  the cell tables.

### Changes — documentation
- `docs/phase_d3_simulation_design.md`: §7 item 6 and §8 previously asserted that *no
  schema/API change* had been made and that "no production file is modified". Both were false
  as written after the first pass removed the `disabled_signals` keyword from
  `RecurrenceAnalysisService.analyze()`. They now state what is true: **production
  *behaviour* is untouched** (no threshold, weight or default changed) but the production
  *API* lost its evaluation-only ablation surface, and the simulation package depends on the
  uncommitted D2.1 changes to `recurrence/service.py` and `evaluation/*`. §6.7's "sim version
  (`d3.0`)" and "code commit hash" were corrected to `d3.3` and to the full provenance block
  (HEAD + working-tree state + source digest).
- `docs/development_log.md`: the 2026-10-21 entry's "5 979 complaints total" was wrong —
  the pilot's 36 streams contain **31 422** complaints (the figure from the earlier
  `--max-arrivals 150` run, which is a different run). Corrected in place.

### Tests (`tests/backend/test_simulation.py`, 121 → 132; full suite 269 → 280)
New `TestContrastEvidenceQualification` (small-R qualification, `resolved_excludes_zero`
semantics preserved, report-level `contrast_evidence` block, limitations text),
`TestRegistryAndCapConsistency` (key/name identity, declared vs configured cap, cap-check
ordering) and `TestProvenanceLabelling` (header wording, dirty-tree wording, HEAD labelling).
No existing assertion was weakened.

### Tests run (final)
- `python -m pytest tests/backend/test_simulation.py --tb=short -v -p no:cacheprovider` → 132 passed (455.30 s)
- `python -m pytest --tb=short -v -p no:cacheprovider` → 280 passed (541.70 s)
- `python -m ruff check .` → All checks passed
- `git diff --check` → clean; `git diff --cached --stat` → empty; nothing staged/committed/reset/reverted.

### Corrected pilot re-run (determinism check, no design change)
- Command: `.\.venv\Scripts\python.exe scripts\run_simulation.py --replications 2 --master-seed 20261021 --out data\sim_reports --stem pilot_d33_corrected`
- Config unchanged from the 2026-10-21 pilot (18-cell §6.1 factorial, R=2, default windows,
  E[S]=0.2, CAP=3, no `--max-arrivals`). Work done: 36 streams, 31 422 complaints, 144 policy
  runs, 36 evidence passes, 30 M/M/1 replications. Elapsed **1 404.7 s** (machine-load
  variance; the two runs are not comparable on wall clock).
- Result: the re-run is **bit-identical** to the previous run in every simulated quantity —
  all 36 stream checksums, every cohort size, every per-seed and across-seed metric to full
  precision, and the complete M/M/1 block (estimate 0.7879499871603614 d, CI
  [0.6654350213810113, 0.9104649529397114] covering the analytic 0.800, all preconditions
  passed, warm-up/relaxation ratio 30). The **only** difference in the JSON is the newly
  added top-level `contrast_evidence` block — the code changes in this pass are validation
  and reporting only and touch no simulated value. This is a third independent CLI process
  agreeing to the last bit.
- Outcomes unchanged: all 18 cells evaluated with non-empty cohorts (ρ=0.5: 417–458,
  ρ=0.8: 714–729, ρ=1.2: 1 089–1 140 per replication); ρ=1.2 utilization 1.000 and backlog
  growth +1.20…+1.22 /day (unstable as designed); fairness 36/36 on each of the seven
  runtime checks plus 18/18 order-independence; provenance `incomplete` (dirty tree,
  21 uncommitted paths, 239 hashed sources).
- **Censoring stamp still silent**: `f_c^{start} = 0.0` for every (cell, policy,
  replication) — the 90-day drain starts every complaint even at ρ=1.2 — so the conditional
  stamp correctly does not fire. The report must not claim a warning that was not applied;
  the `not_evaluated` and stamp paths remain covered by unit tests that force the condition.
- The report now shows the small-R qualification explicitly: with R=2 the paired-contrast
  intervals are marked `insufficient_replications` and `evidence_for_either_policy: false`,
  so the pilot's per-cell differences are observed differences between two replications, not
  evidence that any policy differs.
- The earlier flawed `data/sim_reports/pilot_d33.*` pair is preserved untouched and is
  superseded by this one; its `code_commit` is a tree revision, not a reproducibility claim.

### Remaining tasks / deferred
- Full §6.1 grid launch (R=30) — **not started**; separate approval after this pass is reviewed.
- All §6.3 sensitivity blocks, including `rule_based_plus_ageing` and the cold-start variant.
- §6.3.4 dynamic-evidence mode (only if the snapshot variant shows material policy separation).
- Multi-server `c > 1`.
- D3.4 remains out of scope.

## 2026-10-21 — Phase D3.3 audit remediation: provenance, censoring, M/M/1 block, empty cohorts, fairness, API cleanup

An independent audit of D3.3 found the milestone only partially delivered against
`docs/phase_d3_simulation_design.md` (missing provenance, missing the §6.9 M/M/1 validation
block, an unconditional censoring stamp, silent empty-cohort handling, construction-only
fairness, a production ablation parameter, an unenforced evidence cap, and a stale
`d3.1` version tag). This entry records the remediation. **One production file was modified**
(`backend/services/recurrence/service.py`) — see the API change below. Nothing staged or
committed.

### Changes — simulation package
- `provenance.py` (new, D2.1-style): `git_head()`, `git_working_tree()`, `local_source_digest()`
  (sha256 over every imported local `.py` file), `build_provenance()`. `git_working_tree()`
  classifies porcelain-v1 lines into modified/staged/untracked and deliberately strips only
  trailing newlines — whitespace-stripping the whole buffer eats the leading space of the
  first `" M path"` line and shifts its status character into the path (this produced a
  mangled `ackend/...` entry in the first corrected pilot; fixed before the final one).
- `validation.py` (new, design §6.9): `analytic_queue_wait_mean()` returns `None` outside its
  domain (ρ≥1, c≠1, non-exponential) instead of emitting an unstable steady-state formula;
  `run_mm1_validation()` runs ρ=0.8 FIFO-only replications (default 30, `--mm1-replications`)
  and reports the estimate, Student-t interval, the analytic E[W_q] and the separately-labelled
  time-in-system E[W], the criterion result (pass *or* fail, always reported), applicability,
  preconditions, and limitations.
- `metrics.py`: `CENSORING_WARN_THRESHOLD = 0.1`, `CENSORING_WARNING_TEXT`, and
  `censoring_warning(fc_start)` — the stamp is **conditional**: applied per
  (cell, policy, replication) only when `f_c^{start} > 0.1`; an undefined fraction (empty
  evaluation cohort) is reported as `not_evaluated` and never triggers it.
- `experiment.py`: `SimConfig`/`ExperimentConfig` gain `mm1_replications` and `provenance`;
  `SIM_VERSION` bumped `d3.1` → `d3.3`; seed records renamed `stream_seed` → `rep_index` and
  extended with `derived_stream_seeds` for all six streams (arrivals, service, attrs,
  recurrence, evidence, missing); `_verify_fairness()` now executes seven real per-(cell, rep)
  runtime checks plus one policy-order-independence check per cell (a violation raises
  `AssertionError`) instead of asserting fairness by construction; empty evaluation cohorts
  are no longer silent — cells carry `cohort_status`, policies carry `outcome_status` plus a
  `no_cohort_diagnostics` block (utilization/backlog growth moved out of `across_seeds`), and
  **every** contrast in an empty cell is marked `status="not_evaluated"` with
  `evidence_for_either_policy=False` and `resolved_excludes_zero=False`; the report gains a
  per-cell `censoring` block, a report-level `censoring` summary, the `mm1_validation` block,
  and the injected `provenance` block.
- `report.py`: rewritten to render the new sections — provenance (with an explicit
  `incomplete` status sentence when the tree is dirty: *the recorded commit alone does not
  reproduce this run*), the conditional censoring section, the M/M/1 section, the backlog
  curve table (`B(t)` at 10 points), `No evaluation cohort — outcomes not evaluated` rows,
  `not evaluated` rows for metrics with no seeds, and the fairness checklist with its actual
  check counts. The λ config echo now states that it is the *base* SimConfig value and that
  cells override ρ (and therefore λ) per the grid `loads`.
- `queue_sim.py`: `run_policy` raises when a `recurrence_aware` policy's `evidence_cap` does
  not match the config's — the cap is config-owned and cannot be silently diverged from.
- `scripts/run_simulation.py`: new `--mm1-replications` flag; computes and injects
  `report["provenance"]` after the run (the grid stays pure/deterministic); warns when
  `--max-arrivals` is used, since it truncates the design horizon.

### Changes — production API (D2.1 surface)
- `backend/services/recurrence/service.py`: the `disabled_signals` keyword was **removed**
  from `RecurrenceAnalysisService.analyze()`. Ablation is an evaluation-only concern; the
  harness now subclasses the service as `_AblationRecurrenceService`
  (`backend/services/evaluation/harness.py`), overriding the new protected hook
  `_required_signals()`. The production API carries no ablation surface. Design §8.3 and
  `docs/evaluation_plan.md` updated accordingly.

### Tests (`tests/backend/test_simulation.py`, 105 → 121; full suite 251 → 269)
New `TestAuditRemediation` coverage: censoring threshold/stamp/absence and its JSON *and*
Markdown rendering; empty-cohort `not_evaluated` contrasts and diagnostics; M/M/1 block
structure, out-of-domain analytic guard, t-critical values, and report rendering; backlog
curve presence; provenance structure, dirty-tree `incomplete` status, and Markdown; the
fairness checklist verifying the *actual* checks ran (not just that a checklist exists);
seed records exposing `rep_index` and all six derived stream seeds. Production-side:
`test_production_api_exposes_no_signal_ablation_parameter` and
`test_ablation_is_evaluation_side_only`. The seed-record test was rewritten for the new
shape; `_tiny_config` sets `mm1_replications=3`.

### Tests run (final)
- `python -m pytest tests/backend/test_simulation.py -v` → 121 passed
- `python -m pytest -v` → 269 passed
- `python -m ruff check .` → All checks passed
- `git diff --check` → clean; `git diff --cached --stat` → empty; nothing staged/committed/reset/reverted.

### Corrected pilot (design §6.8, replacing the degenerate `--max-arrivals 150` one)
- Command: `.\.venv\Scripts\python.exe scripts\run_simulation.py --replications 2 --master-seed 20261021 --out data\sim_reports --stem pilot_d33_corrected`
- Config: full 18-cell §6.1 factorial, R=2, default windows, E[S]=0.2, CAP=3, **no
  `--max-arrivals`** so every stream spans the design horizon (t_warm=30 → t_cut=300).
- Artifacts: `data/sim_reports/pilot_d33_corrected.{json,md}` (3.8 MB / 178 KB); the earlier
  flawed `pilot_d33.*` pair is preserved untouched.
- Work done: 36 streams (31 422 complaints total), 144 policy runs, 36 evidence passes,
  30 M/M/1 validation replications. Elapsed 881 s on the first pass and 513 s on the
  post-fix re-run — machine-load variance only: the two independent CLI processes produced
  **bit-identical** metrics (every cohort size, every mean to 16 significant digits, all
  M/M/1 statistics and stream checksums), which doubles as a cross-process determinism
  check. Evidence generation dominates (≈5–14 s per stream at n≈525/840/1 260).
- Outcomes: every cell has a non-empty cohort (ρ=0.5: 411–460, ρ=0.8: 662–778,
  ρ=1.2: 1 025–1 140 per replication); ρ=1.2 utilization 1.000 and backlog growth
  +0.91…+1.24 /day (unstable as designed); M/M/1 estimate 0.788 d, 95 % CI
  [0.665, 0.910] covering the analytic 0.800 (PASS, all preconditions, warmup/relaxation=30);
  fairness 36/36 on each of the seven checks plus 18/18 order-independence; provenance
  recorded as `incomplete` (dirty tree, 21 uncommitted paths, 239 hashed sources,
  diff+source digests).
- **Censoring did not trigger anywhere**: `f_c^{start} = 0.0` for every (cell, policy,
  replication) — the 90-day drain is enough to start every complaint even at ρ=1.2, so the
  new conditional stamp is correctly *silent* in this pilot. Its behaviour is covered by
  unit tests that force the condition.
- Stream comparability with the old pilot verified: the first 150 complaints of the
  ρ=1.2 correlated stream are identical (id, arrival, service time) in both runs — the
  corrected run is a strict extension, not a different RNG path.

### Remaining tasks / deferred
- Full §6.1 grid launch (R=30) — separate approval after this remediation is reviewed.
- All §6.3 sensitivity blocks, including `rule_based_plus_ageing` and the cold-start variant.
- §6.3.4 dynamic-evidence mode (only if the snapshot variant shows material policy separation).
- Multi-server `c > 1`.
- `evidence.py`'s stale "no production file is modified" docstring claim was corrected in
  the same pass (the `disabled_signals` removal made it false as written).

## 2026-10-21 — Phase D3.3: policies, experiment grid, reporting, CLI

Completed the D3.3 milestone of `docs/phase_d3_simulation_design.md` (§4 policies, §6 experimental design, §8.1 `experiment.py`/`report.py`/CLI slots). No production file was modified; nothing staged or committed.

### Changes — simulation package
- `model.py`: `SimConfig` gains `replications` (R, §6.2) and `evidence_cap` (CAP, §4.4 — config-owned saturation constant, echoed in `to_dict` with the assumption knobs); new frozen `PolicyEvidence` bundle (predecessor snapshot counts + D1 arrival-snapshot counts, built once per replication); `POLICY_VISIBLE_FIELDS` extended with the two observed-only derived-evidence fields of §3.6 row 2 (`snapshot_count`, `d1_evidence_count`).
- `policies.py`: all four §4 policies — `fifo` (unchanged), `rule_based` (production `RuleBasedPrioritizer` with `resolved_at=None`, default recurrence evidence — D2.1 baseline semantics), `count_based` (predecessor-only snapshot counts; never full-stream/mutual counting, §4.3/§8.3 option (c)), `recurrence_aware` (production scorer + `min(n_D1, CAP)/CAP` in the existing `recurrence_evidence` slot; CAP bound from config). Registry safety: every accepted policy is created by the module's approved factory (`make_policy`/`policies_for_config`), which stamps a module-private token; `validate_policy` rejects unknown names, hand-built `Policy` objects that merely reuse a registered name, and non-`Policy` values. Design-reconciliation decision (recorded per plan): the D3.1 engine's `POLICIES[name] is policy` identity check is replaced by this token validation so config-bound CAP instances work while forgeries stay rejected.
- `queue_sim.py`: `run_policy` gains a keyword-only `evidence` parameter; `_snapshot_of` attaches the two allowlisted evidence fields from the frozen bundle (absent ids ⇒ 0); `count_based`/`recurrence_aware` raise when `evidence is None`. Keys remain static (§3.4); no latent field is reachable.
- `experiment.py` (new): ordered §6.1 grid enumeration (`load × regime × visibility`, ids recorded), sha256 seed derivation re-used from `generate.derive_stream_seed` (design §6.2's arithmetic form is an example; the sha256 rule is the design's preferred derivation and was already implemented — recorded in the report), one stream + one frozen evidence bundle per (cell, rep) shared by all four policies (CRN, §6.5), config-bound policies via `policies_for_config`, per-seed raw metrics, across-seed summaries (mean/median/nearest-rank 2.5/97.5 percentiles, §6.6), paired per-seed contrasts with `resolved_excludes_zero` flags and the Monte-Carlo-only wording, §3.7 generator-verification aggregates, a policy-order-independence assertion (reversed execution order on rep 0 of every cell), fairness checklist, primary/secondary outcome labels (§7.8), `not_evaluated` block (including `exact_source` absent-by-design per §4.4(iii)), deferred §6.3 sensitivities listed as not run, and the limitations block (M/M/1 "necessary, not sufficient" wording per §6.9).
- `report.py` (new): JSON source of truth + Markdown rendering (D2.1 `reporting.py` pattern) with the `SIMULATED DATA — NOT REAL-WORLD EVIDENCE` banner first, config echo with live production constants, metric formulas with cohort/censoring rules, contrast tables, and `write_report`.
- `scripts/run_simulation.py` (new): CLI mirroring `run_evaluation.py` (`--out`, `--stem`, `--replications`, `--master-seed`, `--load`, `--scenario`, `--visibility`, `--policy`, `--max-arrivals`, generator/window overrides, `--no-write`); gitignored `data/sim_reports` default.

### Tests (`tests/backend/test_simulation.py`, 77 → 105)
Policy discipline and FIFO tie-breaking (§8.4.6); evidence-cap saturation/arithmetic and CAP-config binding (§4.4); registry safety (forgery/unknown/factory-accepted/evidence-required); detector-blind invariance `recurrence_aware ≡ rule_based` outcomes and metrics (§8.4.11/§6.3.8); missing-data key behaviour (§8.4.7 policy layer); grid enumeration order; CRN checksum recording; policy-order independence; experiment determinism (deep-equal reports); paired-contrast math recomputed by hand; resolved-flag rule; no latent keys in report data (M6 definition text excepted); report banner/JSON round-trip/formulas/labels/`not_evaluated`/deferred list; config echo vs live constants; `write_report` round-trip; CLI `--no-write` JSON. Existing tests updated for D3.3 scope (registry now has four policies; spy policy built via the approved factory; `run_policy` signature includes keyword-only `evidence`) — no assertions were weakened.

### Pilot (design §6.8 requirement, before any full grid)
- Command: `.\.venv\Scripts\python.exe scripts\run_simulation.py --replications 2 --max-arrivals 150 --master-seed 20261021 --out data\sim_reports --stem pilot_d33`
- Config: full 18-cell §6.1 factorial (3 loads × 3 regimes × 2 visibility), R=2, default windows (t_warm=30, t_obs=180, t_drain=90 d), default E[S]=0.2 d, CAP=3, `max_arrivals=150` (caps n; recorded in the report).
- Work done: 36 streams (n=150 each; 5 400 complaints total), 144 policy runs, 36 evidence passes.
- Elapsed: **208.6 s** (~620 streams/h, ~2 490 policy runs/h at n=150) on Windows 10 (AMD64), Python 3.11.9. Evidence generation (O(n·50) D1 pair checks) dominates; policy runs are cheap.
- Errors/limitations: none; reports written under gitignored `data/sim_reports/`. Full-grid runtime remains **provisional**: cells at ρ=1.2 with R=30 generate n≈1 260 per stream (~8× the pilot's pair-check volume per stream), so the 540-stream grid will take many hours — a decision to launch it is intentionally deferred beyond this milestone.

### Tests run
- `python -m pytest tests/backend/test_simulation.py -q` → 105 passed
- `python -m pytest -q` → 251 passed (the figure of 253 previously logged here was wrong;
  the D3.3 audit re-ran the suite and found 251 — corrected 2026-10-21)
- `python -m ruff check .` → All checks passed
- `git diff --check` → clean; `git diff --cached --stat` → empty; nothing staged/committed/reset/reverted.

### Remaining tasks / deferred
- Full §6.1 grid launch (R=30) — separate approval after pilot review (§6.8).
- All §6.3 sensitivity blocks (listed as not run in every report), including `rule_based_plus_ageing` and the cold-start variant.
- §6.3.4 dynamic-evidence mode (only if the snapshot variant shows material policy separation).
- Multi-server `c > 1` (config enforces c=1; §6.3 item 5).

## 2026-10-21 — Phase D3.2: simulation evidence, information boundary, and validation tests

Completed the D3.2 test/validation phase over the existing D3 simulation core (design `docs/phase_d3_simulation_design.md` §8.4). No production behaviour change; nothing staged or committed.

- `backend/services/simulation/model.py`: `ArrivalStream` now carries realized diagnostics (`realized_repeat_fraction`, `realized_p_high`, `realized_p_high_given_r1`, `realized_p_high_given_r0`; `None` for empty strata) and includes them in `checksum()`.
- `backend/services/simulation/generate.py`: computes and populates those diagnostics (repeat fraction = `(n - n_latent_issues)/n`; conditional means from the per-complaint `sim_is_high_priority` flags); docstring updated.
- `tests/backend/test_simulation.py`: 37 → 77 tests. New coverage per the design's test plan:
  - **Evidence adapter** (`evidence.py`): detector-visible streams produce D1 relations through the unmodified production recurrence service (parity test vs `RecurrenceAnalysisService.analyze` with the same cutoff); detector-blind streams produce zero relations (count-based counts stay non-zero — the adapter is text-independent by construction, which the test now documents); relations satisfy the four required signals and always point at predecessors; future complaints never change earlier evidence; `filter_relations_as_of` drops not-yet-arrived counterparts; runtime code imports no harness private (AST import-graph check; the docstring's references to `_select_candidates`/`_to_complaint_input` are documentation, not imports).
  - **Production parity**: prefix snapshot counts equal `count_based_ranking` over the same prefix for generated streams; boundary inclusivity (≤200 m, ≤30 d), missing category/coordinates scoring 0, deterministic tie-breaking, and candidate-selection/conversion parity against the D2.1 harness helpers used strictly as a frozen oracle.
  - **Information boundary**: the policy-visible snapshot schema matches the `POLICY_VISIBLE_FIELDS` allowlist exactly; queue execution receives only those observable fields; inverting the latent `sim_is_high_priority` flags leaves starts, waits, M1–M9 metrics, and policy ordering identical while only the M6 high-priority subset changes; inserting a future record changes nothing before its arrival. (Metric-bearing variants set `t_warm_days=0.0` — with the default warm-up the small streams landed entirely in the warm-up window and the cohort was empty, which would have made the assertions vacuous.)
  - **M6 / M/M/1**: hand-computed high-priority subset metrics, `None`-not-zero for an empty subset, unfinished high-priority complaints excluded from the cohort, and the design §6.9 validation — 30 seeded replications at ρ=0.8 (analytic `E[W_q] = ρ/(μ−λ) = 0.8` days): realized mean wait **0.7546 d**, two-sided Student-t 95% CI **±0.0789** (df=29, t=2.04523), **covers 0.8**; minimum steady-state cohort 789; flow-conditioning fractions < 0.01.
  - Also: echoed production constants vs live imports, arrival-offset/missingness/correlation-regime invariants (infeasible `θ_n` raises rather than clipping; degenerate π rejected for non-independent regimes), and the tiny-rate horizon guard.
- Three test defects were found and fixed during the run, all in the tests themselves: the detector-blind count assertion (wrong expectation, see above), the harness-import check (upgraded from source-substring to AST so docstring mentions don't false-positive), and the empty-warm-up cohort issue.

### Tests run
- `python -m pytest tests/backend/test_simulation.py -q` → 77 passed
- `python -m pytest -q` → 227 passed
- `python -m ruff check .` → All checks passed
- `git diff --check` → clean (CRLF warnings only); `git diff --cached --stat` → empty; `git status --short` → all D2.1/D3.1/D3.2 work remains untracked/modified as before, nothing staged.

### Remaining tasks
- Phase D3.3 (out of scope here): experiment grid CLI (`experiment.py`/`report.py`), multi-policy runs, and report generation.

## 2026-10-18 — Phase D2.1 audit fixes

Small, justified fixes from the D2.1 harness audit; no Phase D3 work and no production behaviour change.

- `backend/services/evaluation/harness.py`:
  - `_select_candidates` no longer converts naive timestamps with `.timestamp()` (which interprets them in the host's local timezone). It now sorts naive-UTC datetimes directly — most recent first, `created_at`-missing last, ties by `complaint_id` — matching the project's `to_naive_utc` convention and preserving the previous ordering for all UTC-stored data.
  - Jaccard agreement between two empty relation sets is now reported as `null` with `jaccard_status: "undefined_both_sets_empty"` instead of `1.0` (0/0 is undefined; `1.0` reads as perfect agreement). Applies to `vs_full` and `matrix_jaccard`.
  - The report's `config` block now records the numeric thresholds needed to reproduce a run (`recurrence_thresholds`: similarity/metres/days; `count_based_thresholds`: metres/days), imported live from `backend/services/recurrence/types.py` constants.
- `backend/services/evaluation/reporting.py`: Markdown renders the new thresholds and prints `n/a (undefined: both sets empty)` for null Jaccard values.
- `docs/evaluation_plan.md`: documents the undefined-Jaccard representation and the recorded thresholds.
- Tests (`tests/backend/test_evaluation.py`): added regressions — candidate exactly at the as-of cutoff (service and harness), empty eligible dataset, deterministic tie-breaking across all three baselines and candidate selection, count-based ranking with missing coordinates/category, timezone-independent candidate ordering, exact-source relation at the cutoff, and threshold recording.

## 2026-10-17 — Phase D2.1: label-free evaluation harness

### Changes — backend
- Recurrence service (`backend/services/recurrence/service.py`): added two optional, backward-compatible parameters — `cutoff` (temporal as-of guard that ignores candidates created after the cutoff or with no timestamp) and `disabled_signals` (evaluation-only signal ablation that removes a signal from the four-signal acceptance rule). With both unset the Phase D1 behaviour is unchanged; exported `REQUIRED_SIGNALS`.
- `backend/services/evaluation/baselines.py`: implemented deterministic, label-free baselines — `fifo_ranking`, `count_based_ranking` (same-category, ≤200 m, ≤30 d; missing fields score 0) and `rule_based_ranking` (invokes the existing `RuleBasedPrioritizer` with `resolved_at=None`; `recurrence_evidence`/`evidence_completeness` keep their documented defaults).
- `backend/services/evaluation/harness.py`: `EvaluationConfig` + `run_evaluation` — loads a snapshot, applies the cutoff, computes ranking/recurrence eligible sets, runs baselines and ablations, and emits JSON-serialisable counts, per-field missing-data rates and agreement statistics. `resolved_at` is never used as a feature.
- `backend/services/evaluation/reporting.py`: `report_to_json`, `report_to_markdown`, `write_report`.
- `scripts/run_evaluation.py`: CLI entry point (`--db`, `--cutoff`, `--out`, `--max-candidates`, `--top-k`, `--include-synthetic`, `--no-write`).
- Tests: `tests/backend/test_evaluation.py` — temporal leakage, backward compatibility, determinism, ablation/agreement, missing-data handling, report structure and synthetic exclusion (11 tests).

### Design decisions
- Baselines share one ranking-eligible set; recurrence uses a stricter subset (coordinates + category + timestamp).
- `resolved_at` is never supplied to the scorer and is excluded from feature construction, preventing resolution-time leakage.
- Rule-based baseline invokes the existing scorer faithfully; the age component's 0-value for unresolved records is documented, not patched.
- Ablation = relaxing one signal from the conjunctive rule (the only meaningful ablation of an all-of rule); each run records its exact configuration.
- All label-dependent metrics are emitted with `status: "not_evaluated"` and a reason — agreement/overlap is reported but explicitly never framed as accuracy.

### Tests run
- `python -m pytest -q` → 135 passed
- `python -m ruff check .` → All checks passed
- `scripts/run_evaluation.py` smoke-tested against the development database (read-only; reports written under the gitignored `data/eval_reports`).

### Remaining tasks
- Phase D3+: simulation, resource-aware scheduling, dashboard integration, and evaluation against a labelled dataset once one exists.

## 2026-10-17 — Phase C: CSV data ingestion and data quality

### Changes — backend
- Config: added `max_import_rows` (default 50000).
- Constants: added `IMPORT_STATUS_*` and `IMPORT_STATUSES`.
- Models: created `ImportBatch` and `ComplaintImportLink`; registered in `backend/models/__init__.py`; added `Complaint.import_links` relationship.
- Services: `backend/services/imports/` with `fields.py` (aliases/normalization), `mapping.py`, `validation.py` (row-level issues), `duplicates.py` (exact/signature + potential), `service.py` (bounded streaming read, two-phase transaction, identity/computation, persistence, error mapping).
- Schemas: `backend/schemas/imports.py` (`ImportIssue`, `ImportLimits`, `ImportFieldsResponse`, `ImportSummary`, `ImportListItem`); registered in `backend/schemas/__init__.py`.
- Routes: `backend/api/routes/imports.py` (`GET /imports/fields`, `POST /imports/csv`, `GET /imports`, `GET /imports/{import_id}`); registered in `backend/main.py`; version bumped to 0.3.0.
- Tests: added `tests/backend/test_imports.py` with comprehensive coverage (file handling, mapping, DQ, duplicates/idempotency, provenance/transactions/rollback, regressions) using synthetic inline fixtures; added `tests/backend/test_constants.py` (Phase B consistency).

### Changes — frontend
- Types: added import types (`ImportIssue`, `ImportLimits`, `ImportFieldInfo`, `ImportFieldsResponse`, `ImportSummary`, `ImportListItem`).
- API: added `getImportFields`, `importCsv` (multipart FormData), `listImports` (X-Total-Count), `getImport`; improved `errorMessage`/`importErrorDetails` for `{message,details}` envelope.
- Components: `ImportMappingEditor`, `ImportSummaryPanel`.
- Pages: `ImportsPage` with upload, mapping, dry-run, results, history (paginated); added route `/imports` and nav link "Import".
- Styles: added import-specific styles.
- Tests: added `tests/api_imports.test.ts`, `tests/imports_components.test.tsx`, `tests/imports_page.test.tsx`.

### Design decisions
- Two-phase transaction: commit batch (provisional failed), then persist accepted rows and update status in one transaction; on failure mark batch failed with accepted_rows=0.
- Dataset identity: `label:<source_label>` or `cols:<sha256(sorted normalized headers)[:16]>`.
- Idempotency via `source_key` (dataset_identity::source_record_id).
- Duplicates: exact rejected, potential flagged but never merged; within-file duplicates rejected.
- No uploads persisted to disk; bounded streaming with max size/rows. Issues capped at 500.
- UTF-8 and UTF-8-BOM only. CORS exposes `X-Total-Count`.

### Tests run
- `python -m pytest -q` → 112 passed
- `python -m ruff check .` → All checks passed
- `npm run typecheck` (frontend) → no errors
- `npm run test -- --run` (frontend) → 39 passed
- `npm run build` (frontend) → succeeded
- Live smoke: `GET /imports/fields` and `POST /imports/csv` (multipart) verified against uvicorn server; import accepted as expected.

### Remaining tasks
- (Phase C complete; next phases per roadmap)
- Phase D: FIFO + complaint-count baselines wired end-to-end, score explanations.
- Phase E: recurrence/duplicate analysis with uncertainty.
- Phase F: evaluation module (chronological splits, leakage checks, metrics).
- Phase G: resource-constrained simulation.
- Phase H: dashboard integration.
- Phase I: stabilization, full test/build pass, docs review.
