# Phase D3 — Controlled Capacity Simulation: Design

Status: **design only — Phase D3 has not been implemented, no simulation has been run, and no
simulation results exist anywhere in this repository.** This document specifies a controlled,
reproducible discrete-event simulation for comparing complaint-prioritization strategies under
limited service capacity. It creates no code, no data, and no claims of effectiveness.

Evidence tags used throughout (consistent with `docs/research_evaluation_plan.md`, plus one
addition):

- **[VERIFIED]** — confirmed by reading the repository, tests, or the bundled development DB.
- **[GAP]** — required data, label, or capability that does **not** exist in the repository.
- **[ASSUMPTION]** — a deliberately chosen simulation input. It is neither measured nor
  validated; results are conditional on it.
- **[RECOMMENDATION]** — proposed design choice, not yet implemented.
- **[OPEN QUESTION]** — unresolved; needs a decision or further research before/during
  implementation.

> **Framing.** A simulation compares *policies under assumed worlds we construct*. It cannot
> establish that any policy works better in any real municipality. Every number a future
> Phase D3 run produces will describe the simulated data-generating process and the policies'
> behaviour inside it — nothing more. See §7 (Research integrity).

---

## 1. Verified repository facts this design builds on

- **[VERIFIED]** `backend/services/simulation/__init__.py` exists and is **empty**;
  `docs/architecture.md` lists `backend/services/simulation/` as the planned home for a
  "resource-constrained scheduling simulation". Phase D3 fills exactly this slot.
- **[VERIFIED]** `backend/services/prioritization/rule_based.py` implements the deterministic
  scorer
  `score = 0.40·severity + 0.25·age_norm + 0.20·recurrence_evidence + 0.10·completeness + 0.05·location − uncertainty_penalty`
  (weights at lines 129–135). It accepts a `recurrence_evidence` input (0–1, default 0.0) —
  the natural, formula-preserving slot for a recurrence-aware policy.
- **[VERIFIED — scorer limitation]** For unresolved complaints the scorer's age component is
  structurally 0: `end_ref` is `None` when `resolved_at` is absent, and the fallback computes
  `days_between(created_at, created_at)` (rule_based.py:99–102). The D2.1 harness documents
  the same behaviour for its rule-based baseline (`backend/services/evaluation/baselines.py`).
- **[VERIFIED]** `backend/services/evaluation/baselines.py` provides reusable, label-free
  orderings: `fifo_ranking` (created_at asc, tie-break `complaint_id` asc),
  `count_based_ranking` (same category + within `LOCATION_METERS_MAX` = 200 m + within
  `TIME_WINDOW_DAYS` = 30 d; missing fields ⇒ count 0; ties ⇒ FIFO) and
  `rule_based_ranking` (existing scorer with `resolved_at=None`). Missing timestamps sort via
  the `_EPOCH = datetime.min` sentinel.
- **[VERIFIED]** `backend/services/recurrence/service.py` (Phase D1) is the production
  pairwise recurrence detector: all four signals required (`text_similarity ≥
  SIMILARITY_THRESHOLD = 0.70`, `same_category`, `nearby ≤ 200 m`, `time_proximity ≤ 30 d`;
  exported as `REQUIRED_SIGNALS`), plus a separate `exact_source` path. It accepts
  `cutoff` (temporal as-of guard; candidates created after the cutoff or with no timestamp are
  skipped). Signal ablation is **not** part of the production API: it lives on the
  evaluation-side subclass in the harness. Thresholds live in
  `backend/services/recurrence/types.py` and must be **imported**, never copied.
- **[VERIFIED]** The D2.1 harness (`backend/services/evaluation/harness.py`) defines the
  eligibility conventions the simulation should mirror: *ranking-eligible* = has `created_at`;
  *recurrence-eligible* = ranking-eligible **and** has coordinates **and** `category`.
  `_select_candidates` picks the ≤50 most recent predecessors, naive-UTC sorted
  (audit-fixed; no `.timestamp()`), and `reporting.py` renders JSON+Markdown via
  `write_report`.
- **[VERIFIED]** Timestamp convention: naive UTC everywhere (`backend/core/time.py`:
  `utcnow`, `to_naive_utc`, `serialize_utc`).
- **[VERIFIED — gaps, restated from `docs/research_evaluation_plan.md` §B/§F]** No staffing,
  capacity, budget, or SLA fields exist anywhere. No arrival-rate or service-time statistics
  exist. `severity_label` is populated by **no** dataset (dev DB: 0/3). `resolved_at` is not
  importable. The development DB holds **3 complaint rows** (2 synthetic, 1 real) — far too
  small to be performance evidence of any kind.
- **[VERIFIED]** `numpy`, `pandas`, `pyyaml` and `pytest` are already pinned in
  `backend/requirements.txt`; no new dependency is required for the proposed design.
- **[VERIFIED]** Test infrastructure: isolated temp SQLite DB per session
  (`tests/conftest.py`); the D2.1 suite (`tests/backend/test_evaluation.py`, 146 tests total
  passing) is the pattern to follow.
- **[VERIFIED]** Report outputs go under the gitignored `data/` directory (`data/*` in
  `.gitignore`); the D2.1 CLI convention is `scripts/run_evaluation.py` with `--out`,
  `--seed`-style flags and a `--no-write` mode.

**What Phase D3 must therefore supply entirely by assumption:** the arrival process, the
service-capacity and handling-time model, the definition of "high priority", and the
correlation structure between recurrence and that high-priority designation. None of these
can be estimated from repository data. This is the central honesty constraint of the phase.

---

## 2. Scope: what Phase D3 is and is not

**Is [RECOMMENDATION]:**

- A *controlled discrete-event simulation* of a single complaint queue served by a fixed
  capacity of identical, non-preemptive servers.
- A *policy comparison rig*: FIFO, existing rule-based, count-based, and an explicitly
  defined recurrence-aware policy, all fed identical simulated complaint streams.
- A *scenario laboratory*: the data-generating process is parameterised (including the
  assumed relationship between recurrence and high-priority status), so results are reported
  per scenario — never as a single headline "X is better".
- Read-only with respect to the application: no DB writes, no API routes, no schema changes,
  no production behaviour changes, no frontend changes.

**Is not:**

- Evidence about real municipal performance. **[GAP]** No real arrival, service-time,
  staffing, or outcome data exists to ground any of it.
- A training pipeline for any model. No ML, no learned parameters.
- A replacement for the D2.1 label-free harness. The harness evaluates *rankings on stored
  records*; the simulation evaluates *queue disciplines under assumed load*. Both are
  label-free; neither validates correctness against ground truth.
- Justification for the pre-existing research-plan precondition. `docs/research_evaluation_plan.md`
  §H.2.4 said "only if a resource model can be justified from real data, implement the
  simulation". Phase D3 reframes this honestly: the resource model **cannot** be justified
  from data, so it becomes an explicit, recorded **[ASSUMPTION]** and an experimental control
  variable — which is exactly what a simulation is for, provided results are never dressed up
  as measurements.

---

## 3. Simulation model

### 3.1 Overview

A single logical queue of complaints; `c` identical servers (default `c = 1`); non-preemptive
service: once a server starts complaint *i*, it runs to completion; when a server becomes
free it selects the waiting complaint with the highest policy key (ties broken FIFO). Arrivals
occur over a finite horizon; service times are drawn per complaint at generation time (so
every policy experiences the identical service-time draw for the same complaint).

**[RECOMMENDATION]** Event loop: next event is `min(next arrival, next service completion)`.
At each service completion, each free server picks the current queue head per the policy. This
is the standard non-preemptive priority-queue discipline; it is fully deterministic given the
generated stream and seed.

#### 3.1.1 Decision-time information, immutability, and leakage prevention (critical review correction)

To guarantee that policies cannot access future information or simulation ground truth:

- **Immutable policy-visible snapshot.** At each decision time `t_dec`, the policy receives an
  immutable snapshot `S(t_dec)` constructed only from the observed history `H(t_dec) = {events with event.time <= t_dec}`.
  The snapshot is a copy/view that cannot be mutated by policy execution.
- **Observed-only fields.** Only fields that are observable at or before `t_dec` (e.g., arrival times,
  created_at, observable text/category/coords, evidence relations whose counterparts arrived <= t_dec)
  may enter `S(t_dec)`. No future timestamps, no future events, and no fields derived from future
  complaints are included.
- **Strict separation of state.**
  - **Latent/simulation truth:** `latent_issue_id`, `sim_is_high_priority`, true recurrence labels,
    generator parameters, internal RNG state, future outcomes — **never** included in policy-visible state.
  - **Observed evidence:** D1 relations computed from observable fields with observed_at <= t_dec.
  - **Policy-visible:** subset of observed evidence plus observable complaint fields present at t_dec.
- **Enforceable guard.** The harness/simulation must enforce: (a) time horizon guard (no event with
  time > t_dec), (b) field allowlist for policy inputs, (c) redaction of any attribute matching
  `sim_*`, `latent_*`, `true_*`, `generator_*`, or known ground-truth names. **[RECOMMENDATION]**
  Implement a read-only view type (or deep copy + allowlist) rather than passing raw `SimComplaint`
  objects, and assert the guard with runtime tests (§8.4.10) — the design claims no type-level
  enforcement, because none exists until implemented (§8.1).
- **Evidence timing (frozen vs dynamic).**
  - **Arrival-snapshot (default, [RECOMMENDATION]):** evidence for complaint *i* is computed at
    arrival time `t_i` from predecessors only (≤50) and stored immutably; policy uses this frozen
    snapshot filtered to `<= t_dec`. This matches D1 candidate semantics and bounds cost.
  - **Dynamic refresh (sensitivity, [RECOMMENDATION]):** evidence may be recomputed at decision
    times using all complaints with `arrival <= t_dec` (no successors). If retained as sensitivity
    (§6.3.4), both modes must be implemented and compared; dynamic mode must still never access
    future complaints. The default is arrival-snapshot.

### 3.2 Complaint arrival process

- **[ASSUMPTION]** Arrivals are a **homogeneous Poisson process** with rate λ (exponential
  inter-arrival times, drawn from `rng_arrivals`). This is the simplest stochastic arrival model
  with a closed form for validation (§6.9, tests) and is the default backbone.
- **[ASSUMPTION]** The *load level* is expressed as the utilisation ratio
  `ρ = λ · E[S] / c` where `E[S]` is the mean service time. Scenario loads: **low ρ = 0.5**,
  **moderate ρ = 0.8**, **high ρ = 1.2** (see §6.1 for the unstable-load caveat).
- **[ASSUMPTION — λ is derived, never free]:** for each (load, `c`, `E[S]`) cell the arrival
  rate is *computed*, not configured independently: **`λ = ρ · c / E[S]`** (rearranged from
  the ratio above), in arrivals per day when `E[S]` is in days. This fixes the time scale of
  the whole design: with `E[S] = 0.2 d` (§3.3 default) and `c = 1`, λ ∈ {2.5, 4.0, 6.0}
  arrivals/day for ρ ∈ {0.5, 0.8, 1.2}. Changing `E[S]` therefore changes λ, the expected
  stream length `n ≈ λ · (t_warm + t_obs)` (§6.8), and every window's meaning in service-time
  units — `E[S]` must be fixed before any run and echoed in every report (test §8.4.14).
- **[ASSUMPTION — recurrence generation]** Not every complaint is independent. The generator
  maintains a set of **latent issues** (simulation-only constructs, §3.6). Each arriving
  complaint is drawn either as a *singleton* (unique latent issue) or as a *repeat* of an
  existing latent issue, with scenario-configured probability `p_repeat`. A latent issue's
  repeats arrive with inter-arrival gaps drawn so that consecutive repeats fall inside the
  production `TIME_WINDOW_DAYS` = 30 d window with high probability, at coordinates jittered
  well inside `LOCATION_METERS_MAX` = 200 m of the issue's anchor point, and with title/
  description text drawn from the issue's template plus noise. This is what makes a repeat
  *detectable* by the real D1 detector — see §3.5 for the two detector-visibility regimes.
- **[RECOMMENDATION]** Category is drawn per latent issue (repeats of one issue share a
  category); some complaints are generated with missing `category`, missing coordinates, or
  missing `created_at` at a configured missingness rate (§3.6) to exercise every policy's
  missing-data path.
- **[ASSUMPTION]** A configured fraction `p_high` of complaints carries the latent flag
  `sim_is_high_priority`. Its relationship to latent issues is defined in §3.7 (correlation
  regimes). Policies never observe this flag (§5, §7).

### 3.3 Service capacity and handling times

- **[ASSUMPTION]** `c` identical servers, each handling one complaint at a time. Default
  `c = 1`; sensitivity over `c ∈ {1, 2, 4}` at fixed ρ — with λ derived as
  `λ = ρ · c / E[S]` (§3.2), λ scales linearly with `c` at fixed ρ (this replaces the
  earlier "λ scaled by 1/c" phrasing, which contradicted the definition of ρ).
- **[ASSUMPTION]** Service time `S_i` is drawn per complaint at generation from `rng_service`,
  independent of the policy. Default: **exponential** with mean `E[S]` (CV = 1). Sensitivity:
  constant service times (CV = 0) for exact-validation tests and other variants if needed later.
- **[GAP] / [ASSUMPTION]** No real service-time distribution exists in the repository
  (resolution times are effectively unobtainable — `resolved_at` is not importable). `E[S]`
  is a config knob in abstract "time units" (recommended unit: **days**, matching
  `TIME_WINDOW_DAYS`), not a calibrated municipal statistic. Reports must say so.
- **[ASSUMPTION — documented default, NOT calibrated]** `SimConfig` default
  **`E[S] = 0.2` days** (≈ 4.8 hours). This value is a *design placeholder chosen for
  internal consistency*, not an estimate of real municipal resolution time: it is not
  calibrated to any dataset, repository statistic, or expert judgement, and no such data
  exists (§1, §7.3). Its role is to fix the time scale so that (i) λ is well defined
  (§3.2), (ii) the default windows (`t_warm = 30 d`, `t_obs = 180 d`, §6.4) span many
  service times, and (iii) the M/M/1 validation warm-up is defensible relative to mixing
  (§6.9). `E[S]` remains a config knob; any alternative value (including the service-time
  sensitivity §6.3.1) is recorded in the report and never presented as calibrated.
- **[ASSUMPTION]** Service times are policy-independent and complaint-intrinsic: policy *A*
  does not make complaint *i* cheaper to fix than policy *B* does. This isolates the pure
  *ordering* effect, which is the research question. Any real coupling between priority and
  handling cost is out of scope and listed as an open question (§10).

### 3.4 Queue and scheduling behaviour

- Single FIFO queue, first-c-free-servers pick the policy head. Non-preemptive.
- **[RECOMMENDATION — key simplification]** All four policies (§4) reduce to **static
  per-complaint priority keys** fixed no later than complaint arrival (arrival-time evidence
  snapshots, §3.5, §4), plus FIFO tie-breaking. FIFO's key is arrival order — also static. The
  event loop is therefore uniform across policies; only the key function differs. This makes
  fair comparison structurally enforced rather than hoped for.
- Waiting time of complaint *i*: `W_i = start_service_i − arrival_i` (queue delay only).
  Flow time `F_i = completion_i − arrival_i = W_i + S_i` is reported as a secondary metric.
- No abandonment, no reordering after service start, no parallel handling of one complaint,
  no preemption. **[OPEN QUESTION]** whether a abandonment/retry layer is worth adding later
  (§10); it is excluded from the minimal design to keep the policy comparison clean.

### 3.5 Recurring complaints and the D1 detector

Two distinct roles must not be conflated:

1. **Latent issue structure** (generator-side): decides which complaints are *actually*
   repeats of the same simulated issue. Used **only** to (a) generate observable fields and
   (b) post-hoc *analyse* policies (e.g., "were latent repeats served earlier?"). Never given
   to any policy. Latent ground truth (including `latent_issue_id`) is simulation-only.
2. **Observable recurrence evidence** (policy-side): computed by running the **real**
   `RecurrenceAnalysisService` on the simulated complaints' observable fields, using the
   production thresholds and the production candidate cap.

**[RECOMMENDATION — evidence construction, leakage-free by design]** For each simulated
complaint *i* (in arrival order), run `analyze(i, candidates, cutoff=arrival_i)` — **[AUDIT
CORRECTION — cutoff is mandatory at every call]:** the current complaint's arrival time is
**always** passed as the `cutoff=` keyword argument (`recurrence/service.py:51`), so the
service itself discards any candidate created after `arrival_i` even if a pool error ever
feeds it one. `cutoff` is never left unset (leaving it `None` disables the service-side
as-of guard and is a design violation; the adapter's own unit tests assert the cutoff is
passed on every call, and the end-to-end future-leakage guard remains test §8.4.5). This is
defence in depth, not
the sole safeguard: the **candidate pool is additionally filtered to predecessors at query
time** (`candidates` are *i*'s
≤ `max_candidates` (= 50, production default) most recent **predecessors** (arrived before
*i*), mirroring the D2.1 harness's candidate-selection semantics over the arrival-ordered
stream), and **ground-truth isolation** (§3.1.1 field allowlist / `sim_`-prefix redaction,
test §8.4.10) is a third, independent safeguard — pool filtering, per-call cutoff, and field
allowlisting must all be present; removing any one of them weakens the boundary and is not
accepted as a simplification.

From that call, the resulting relation list is *i*'s **arrival-snapshot evidence**. At any later
decision time
`t`, the evidence usable for *i* is the subset of those relations whose counterpart has
arrived by `t` — computable by filtering the stored relation list on arrival time, with no
recomputation and no access to future complaints. This is exactly the D1 `cutoff` guarantee
re-expressed over the generated stream. Because candidates are restricted to *predecessors*,
every counterpart has `arrival ≤ arrival_i ≤ t` for any decision time `t ≥ arrival_i`, so the
arrival-snapshot evidence is **frozen at arrival** and the time-filter is a defensive assertion
(a no-op), not a recompute — this is the default evidence mode (§3.1.1). Refreshing evidence at
each scheduling decision (dynamic mode) is *not* the default and appears only as sensitivity
§6.3.4; if implemented, it still may only draw on complaints with `arrival ≤ t`. All evidence
passed to policies must satisfy the time guard and field allowlist (§3.1.1).

**[ASSUMPTION — detector-visibility regimes]** Because D1 requires all four signals, whether
it *fires* on latent repeats depends on how we generate observable text/coords:

- `detector_visible`: repeat complaints share near-identical text, same category, coordinates
  within 200 m, arrivals within 30 d ⇒ D1 relations exist. This is the regime where a
  recurrence-aware policy *can* differ from its base policy.
- `detector_blind`: repeats differ in text or coordinates (realistic for messy civic text) ⇒
  D1 finds nothing ⇒ recurrence-aware degenerates to its base policy. This regime exists to
  measure the *cost of degeneration* (compute overhead aside, results should match the base
  policy — a built-in sanity check).

**[ASSUMPTION — bounded-window approximation]** The predecessor window (≤50, frozen at
arrival) means evidence for *i* never includes complaints that arrived after *i* and later
became *i*'s "most recent 50". This mirrors production candidate selection semantics
(default `max_candidates=50`) and bounds computation at O(n · 50) detector calls per scenario
seed. Full decision-time recomputation over all arrived complaints is exact but O(n²);
listed as a sensitivity/optimisation item (§6.3, item 4), not the default.

### 3.6 Missing and unreliable data, and field separation

**Hard separation rule [RECOMMENDATION]:** simulation state lives in in-memory dataclasses
under `backend/services/simulation/`; it is **never persisted to the database**, never exposed
via the API, and never rendered in the frontend. Simulated complaints are *shaped like* the
D2.1 harness record dicts (same keys: `complaint_id`, `title`, `description`, `category`,
`latitude`, `longitude`, `created_at`, `status`, `severity_label`, `source`, `synthetic`,
…) so existing helpers (`count_based_ranking`, `RuleBasedPrioritizer`, D1 `analyze`) can be
reused unmodified. If any simulated complaint ever carried a `synthetic` flag it would be
`True` — but since nothing is written, the flag is a documentation-level guarantee, not a
runtime one.

| Kind | Fields | Seen by policies? | Persisted? |
|---|---|---|---|
| Real-schema observable fields (simulated values) | `complaint_id`, `title`, `description`, `category`, `latitude`, `longitude`, `location_label`, `administrative_area`, `created_at`, `status`, `source`, `synthetic` | yes (via policy-visible snapshot, observed subset) | **no** |
| Real-schema fields kept absent by default | `severity_label` (mirrors reality: no dataset supplies it), `resolved_at` (not importable) | only in declared sensitivity regimes (§6.3) | **no** |
| Simulation-only attributes (prefix `sim_`/`latent_`) | `latent_issue_id`, `sim_is_high_priority`, `sim_service_time`, `sim_arrival_index`, `true_*`, generator internals | **never** | **no** |
| Derived evidence (reused code outputs) | D1 relations (arrival-snapshot), count-based counts, rule-based scores | yes (per policy definition, observed-only) | **no** |

**Ground-truth isolation (enforceable) — critical review correction:**
- **Forbidden fields:** `latent_issue_id`, `sim_is_high_priority`, true recurrence labels/outcomes,
  `future_*`, generator parameters, RNG state. These must never appear in policy inputs.
- **Policy-visible data structure:** an immutable view (e.g. `PolicySnapshot`) containing only
  observable fields with observed_at <= t_dec and derived evidence filtered to observed events.
  The view exposes an allowlist; any access to forbidden names must be blocked.
- **Tests required:** (a) snapshot has no `sim_*`, `latent_*`, `true_*` fields; (b) future-insertion
  does not change earlier snapshots; (c) for detector-blind regime, recurrence-aware sees only
  observed evidence (none); (d) oracle-like access is impossible. Type-level enforcement alone
  is insufficient unless implemented; runtime validation is required.
- **[ASSUMPTION — missingness injection]** Configurable per-field drop rates (default 0;
  sensitivity 0.2 for coordinates and for `category`) applied at generation via `rng_missing`.
  Missing-data behaviour is then whatever the *existing, unmodified* code does: FIFO needs only
  `created_at`; `count_based_ranking` assigns count 0; the rule-based scorer flags uncertainty
  and never silently down-weights missing severity; D1 stays silent on recurrence-ineligible
  complaints (no coords/category) — matching the harness `RECURRENCE_ELIGIBILITY` convention.
- **[ASSUMPTION — unreliable coordinates]** Optional jitter scale on repeat coordinates (from
  `rng_evidence` or `rng_attrs`). Kept ≪ 200 m in `detector_visible`; set ≫ 200 m in
  `detector_blind`. There is no real coordinate-accuracy statistic to calibrate against **[GAP]**.
- **[RECOMMENDATION]** `created_at` is never dropped in the default design: a complaint without
  an arrival time cannot enter a queue. The `created_at`-missing path is exercised only in
  record-shaping unit tests, not in live scenarios.

### 3.7 Correlation regimes (reproducible generation rules)

The latent high-priority designation `sim_is_high_priority` is **evaluation-only** — used for
metric M6 and generator construction, never available to policies (§4.5, §7).

Let `p_high` be the marginal fraction of complaints designated high-priority (fixed per scenario),
and let `R_i ∈ {0,1}` indicate complaint `i`'s latent role: `R_i = 1` if `i` is a *first report* of a
latent issue that will have ≥1 repeat (a "repeat-linked" first report), `R_i = 0` otherwise
(singletons and repeat follow-ups). Define `π = P(R = 1)` (a generator quantity derived from
`p_repeat` and recorded in config).

**[ASSUMPTION — generation rule, keyed by regime]:**

- `independent`: draw `sim_is_high_priority` from Bernoulli(`p_high`) per complaint, independent
  of `R`. By construction, `P(high | R=1) = P(high | R=0) = p_high`.
- `correlated`: conditional rates `θ_c = P(high | R=1) = min(1, p_high + δ)` and
  `θ_n = P(high | R=0) = (p_high − θ_c·π)/(1 − π)`. The construction is **exactly
  marginal-preserving** when `0 ≤ θ_n ≤ 1`; when that fails for extreme `p_high`/`π`/`δ`
  combinations, the generator raises a configuration error and refuses to run — it never
  silently re-draws to hit the marginal, and it never clips `θ_n` (clipping would change
  `P(high)`). **[ASSUMPTION]** `δ > 0` is a config parameter (value recorded in config, not
  calibrated to anything).
- `anti_correlated`: same construction with `θ_c = max(0, p_high − δ)` and the same `θ_n`
  formula and feasibility check.
- **Degenerate feasibility:** if `π = 0` or `π = 1` (no variation in `R`), the non-independent
  regimes are undefined; the generator requires `0 < π < 1` for them and otherwise fails with
  a config error (the report states the realized `π`).

**What is correlated with what:** the designation is correlated with `R` (latent repeat-linkage
of first reports), **not** with detector visibility or observable fields directly. Marginal
`P(high) = p_high` is fixed across regimes (within feasibility); only `P(high | R)` changes.

**Interpretation caveat for the correlated regime [AUDIT CORRECTION — pre-registered]:**
in the `correlated` regime the high designation concentrates on `R = 1` — *first reports* of
latent issues that will later repeat. By construction a first report has **zero recurrence
evidence at its own arrival** (its relations, if any, are produced by later repeats that have
not arrived yet, §3.5/§4.4). A recurrence-aware policy therefore cannot identify those
complaints as recurrent at decision time in this regime; elevated M6 in `correlated` (or a
*M6 deficit* for `recurrence_aware` there) can reflect **evidence-availability lag** — the
detector needs the repeat to exist before evidence exists — as much as it reflects the
policy's ranking rule. This is an expected property of the regime, not a simulator defect.
Conversely, `anti_correlated` concentrates the designation on `R = 0`, where evidence-bearing
later arrivals are over-represented among the designated. **Results in either regime are
simulated-world behaviour only:** they are never proof that a policy is effective or
ineffective in the real world (§7), and an M6 difference must be reported together with the
regime's `P(high | R=1)`, `P(high | R=0)` echoes (§3.7 verification below) so the lag
mechanism is visible to the reader.

**Generated-relation verification [RECOMMENDATION]:** for each run, the report must include
empirical `P(high)`, `P(high | R=1)`, `P(high | R=0)`, and the configured `(p_high, π, δ)`, so a
reader can verify the generator produced the intended relationship. Unit tests check the
conditional/marginal targets within sampling tolerance.

---

## 4. Baseline policies

All policies consume the **same** policy-visible snapshots (§3.1.1) built from the same
simulated complaint stream and the same evidence construction; they differ only in the key
they assign. All break ties FIFO (then `complaint_id`, matching `baselines.py`). All are
recomputed identically across paired seeds (common random numbers, §6.2) so any difference
is attributable to ordering alone.

### 4.1 FIFO

- **Inputs:** `created_at` (arrival order) only.
- **Ranking rule:** oldest waiting complaint first — equivalent to key = arrival order.
- **Tie-breaking:** `complaint_id` asc (defensive; arrival order is already distinct).
- **Missing data:** none possible (arrival time always exists by construction).
- **Role:** the "do nothing smart" reference; directly answers whether any prioritisation
  beats a plain queue at the same capacity.

### 4.2 Existing rule-based prioritization

- **Inputs:** the production `RuleBasedPrioritizer` invoked exactly as the D2.1 harness's
  `rule_based_ranking` does: `resolved_at=None`, `recurrence_evidence` and
  `evidence_completeness` at documented defaults (0.0 / 0.5), `severity_label` passed
  through when present.
- **Ranking rule:** descending `priority_score`; key frozen at arrival (score is
  arrival-computable — with `resolved_at=None` the age component is structurally 0,
  **[VERIFIED]** §1, and no other component depends on time).
- **Tie-breaking:** FIFO.
- **Missing data:** production behaviour — missing severity ⇒ `missing_severity` flag +
  uncertainty penalty; missing coordinates ⇒ penalty; never silently "low priority".
- **Known limitation (inherited, documented in D2.1):** with no severity labels in the
  observable data the score is driven mostly by completeness/location/uncertainty. The
  simulation deliberately does **not** invent severity labels for this policy's default
  regime; a simulated-observable `severity_label` regime exists only in sensitivity (§6.3)
  and is labelled as an assumption about *future* data.

### 4.3 Count-based prioritization

- **Inputs:** the production `count_based_ranking` semantics (same category, ≤
  `LOCATION_METERS_MAX` m, ≤ `TIME_WINDOW_DAYS` d) evaluated over the **arrival-snapshot**
  neighbour set: complaint *i*'s count = number of *predecessors* (arrived before *i*)
  within the spatio-temporal-category window. Computed once per complaint at arrival.
- **Ranking rule:** descending snapshot count.
- **Tie-breaking:** FIFO (identical to `baselines.py`).
- **Missing data:** production behaviour — missing category or coordinates ⇒ count 0; such
  complaints sink to the FIFO tail but are never dropped.
- **Decision-time information boundary [AUDIT CORRECTION — the earlier "observational
  equivalence" claim in this section is withdrawn as false]:** predecessor-only counts are
  **required**, not merely preferred. A count computed over "all arrived" complaints (mutual
  counts, as `count_based_ranking` computes over whatever list it is given) includes
  *successors* of the waiter being scored — information that did not exist when the decision
  was made — and it changes relative order among waiters: two mutually adjacent waiters *i*
  (earlier) and *k* score 0 vs 1 under predecessor-only counts (*k* first) but tie at 1 under
  mutual counts, where FIFO serves *i* first. Predecessor-only counting is therefore mandated
  by the decision-time information boundary of §3.1.1 and the frozen arrival-snapshot
  contract of §3.5; it must not be weakened, and the snapshot contract itself is unchanged by
  this correction. **[DECISION — mechanism, recorded per §8.3]:** counts are computed by the
  simulation-side adapter (§8.3, option (c)); computing counts over the complete stream —
  including future complaints — is **FORBIDDEN** (§8.3 option (b)) because it introduces
  future-data leakage. The production `count_based_ranking` has no incremental/prefix API
  **[VERIFIED]**; the earlier "O(1) incremental" phrasing was aspirational, not a verified
  capability.
- **[ASSUMPTION — proxy, not truth]** This is an unsupervised proxy for "the problem keeps
  happening". It is *not* validated recurrence ground truth and must never be described as
  such (consistent with `research_evaluation_plan.md` §D.1.2).

### 4.4 Recurrence-aware prioritization (explicitly defined)

- **Inputs:** production rule-based scorer **plus** its existing `recurrence_evidence`
  slot, fed with the normalised D1 arrival-snapshot relation count:
  `recurrence_evidence_i = min(n_relations_i, CAP) / CAP` where `n_relations_i` counts D1
  `potential_recurrence` and `exact_source` relations to predecessors, and
  **[ASSUMPTION]** `CAP = 3` (saturation constant, simulation config — **not** a production
  constant; recorded in every report).
- **Ranking rule:** descending `priority_score` from the unmodified scorer. Because the
  scorer's recurrence weight is the existing 0.20, the policy's total swing attributable to
  recurrence evidence is bounded at 0.20 — the simulation *tests* that weight's effect; it
  does not endorse it.
- **Tie-breaking:** FIFO.
- **Missing data:** inherits rule-based missing-data behaviour; recurrence-ineligible
  complaints (no coords/category) simply get `recurrence_evidence = 0.0` — they are not
  penalised beyond that zero.
- **Explicit non-assumption [RECOMMENDATION]:** recurrence evidence is **not** severity and
  must not be equated with it. The design carries no claim that recurring complaints are more
  dangerous or urgent. Whether promoting recurring complaints helps or hurts the
  high-priority population is precisely what the correlated / independent /
  anti-correlated scenarios (§3.7) [audit correction — was mis-referenced as §3.2] are built
  to expose — including the case where
  recurrence-aware *loses*.
- **[DECISION — exact-source evidence is NOT generated and NOT evaluated in Phase D3,
  audit correction].** The production `exact_source` path (`recurrence/service.py`
  `_is_same_source_record:259`) fires only on database import provenance: a shared
  `source_key` inside `raw_imported_values`, or shared `source` **plus** a
  `source_record_id` from `raw_imported_values`. The Phase D3 generator **does not populate
  `raw_imported_values` provenance fields at all**, and the record adapter leaves them empty,
  because inventing source-record identities would fabricate import provenance the simulation
  has no basis for (and any future provenance generation would have to be labelled synthetic
  under the `sim_`/§7.2 rules and re-open this design — it is a scope change, not a default).
  Consequences, stated in advance: (i) no `exact_source` relation can fire on simulated data,
  (ii) the §4.4 evidence count `potential_recurrence + exact_source` therefore **reduces in
  practice to `potential_recurrence` alone**, and (iii) Phase D3 results evaluate exactly one
  evidence type — `potential_recurrence` — while `exact_source` is **explicitly not evaluated**
  and no finding may be attributed to it (the report's `not_evaluated` block must say so, and
  test §8.4.12 records `exact_source` as absent-by-design). The count expression is kept
  unchanged so the policy semantics still match production should provenance ever exist.

### 4.5 Policies that are deliberately excluded

- Any **oracle/ground-truth policy** given `latent_issue_id` or `sim_is_high_priority`:
  excluded from the headline comparison. It would trivially "win" the metrics it was
  optimising and teach nothing about deployable policies. If ever included for calibration
  context it must be renamed `oracle_*`, rendered in a separate table, and stamped
  "non-deployable bound — uses simulation ground truth".
- Any **learned/ranking model**: out of scope (no labels, no ML in this phase).

---

## 5. Evaluation metrics

All metrics are computed **per run** (one seed, one scenario, one load, one policy) and
then aggregated across seeds (§6.6). All time quantities are in the configured unit
(days). All metrics are computed over the **observation window** `[t_warm, t_end_obs]`
(§6.4) unless stated otherwise; complaints arriving before `t_warm` still occupy the queue
(standard warm-up deletion) but are excluded from metric populations. Censoring is handled
by explicit cohort/subset definitions below — never by silent exclusion.

| # | Metric | Formula | Interpretation | Limitations |
|---|---|---|---|---|
| M1 | Mean queue waiting time | `W̄_q = (1/n) Σ_{i ∈ S} (start_i − arrival_i)` where `S = {i in Cohort : start_i ≤ t_cut}` (started by cutoff) and `Cohort = {i : arrival_i ∈ [t_warm, t_end_obs]}` | Average queue delay (time in queue before service start) per **started** complaint in the cohort | **Right-censored:** complaints not started by `t_cut = t_end_obs + t_drain_max` are excluded, which biases the mean **downwards**. Always reported with M8 and `n = |S|`. Queue waiting time ≠ flow time (M9). |
| M2 | Median queue waiting time | `median({start_i − arrival_i : i ∈ S})`, same started subset `S` | Typical queue delay (robust) | Same censoring condition as M1; report with M8 and `n`. |
| M3 | 95th-percentile queue waiting time | `p95({start_i − arrival_i : i ∈ S})` via the **nearest-rank** definition (documented in `metrics.py` docstring) | Tail queue delay | Unstable at small `n`; under censoring describes only the started subset — never quoted without M8 and `n`. |
| M4 | Backlog over time + scalar growth rate | **Time series (kept as its own output):** `B(t) = #{arrivals ≤ t} − #{service starts ≤ t}` for `t ∈ [t_warm, t_end_obs]`, sampled on a fixed time grid. **Backlog counts jobs waiting in queue only — jobs in service are excluded** (system size = `B(t) + c_busy(t)` is reported separately if needed). **Scalar statistic [AUDIT CORRECTION — defined here]:** backlog growth rate `ĝ_B = (B(t_end_obs) − B(t_warm)) / t_obs` — **units:** complaints (queue entries) per day; **interval:** the observation window `[t_warm, t_end_obs]` (length `t_obs`); **method:** endpoint difference of the two grid samples at the window endpoints divided by the window length (no smoothing, no fitting — hand-computable for test §8.4.9). The curve `B(t)` and the scalar `ĝ_B` are both reported; `ĝ_B` is derived from the curve and is not independent evidence. | Queue length over time (drain/stability) plus a per-run growth-rate scalar comparable across policies/seeds | Measured over the observation window; grid resolution is a config value that is reported. Interpretation: `ĝ_B ≈ 0` with bounded `B(t)` ⇒ effectively stable in this horizon; `ĝ_B > 0` sustained ⇒ growth consistent with the ρ>1 stress condition (§6.1). A standard fluid approximation (textbook queueing arithmetic, **not** a repository claim) predicts drift ≈ `λ − c/E[S] = (ρ − 1)·c/E[S]`; it may be cited as an analytic reference for ρ>1 cells, with the same "necessary, not sufficient" wording discipline as §6.9. |
| M5 | Completion rate | `C = #{i ∈ Cohort : completion_i ≤ t_cut} / |Cohort|` — numerator and denominator use the **same arrival cohort**; a completion is a service-completion event ≤ `t_cut`. | Fraction of observed cohort demand processed by the drain cut-off | Equals 1.0 only when the cohort fully drains; under ρ>1 or a short drain `C < 1`. Always paired with ρ, M8 and the window parameters. **Not independent of M8 [AUDIT CORRECTION]:** by definition `C = 1 − f_c^{comp}` exactly (same numerator, same denominator, §5 M8) — the two are one quantity rendered two ways for reporting convenience, and must never be cited as two corroborating pieces of evidence. |
| M6 | High-priority waiting time (mean/median/p95) | M1–M3 restricted to `{i ∈ S : sim_is_high_priority = True}` (latent flag), same cohort and same `t_cut` | Whether a policy preferentially serves the designated complaints | **Simulated label only.** Behaviour against an assumed designation; meaningless outside the scenario's correlation regime (§3.7). Never real importance. In the `correlated` regime the designated set is dominated by evidence-free first reports, so M6 there can reflect **evidence-availability lag** rather than policy quality — see the pre-registered §3.7 interpretation caveat; it is never presented as proof of policy effectiveness or failure. |
| M7 | Capacity utilization | `U = (Σ_servers busy time within [t_warm, t_end_obs]) / (c · (t_end_obs − t_warm))`, busy time = length of service intervals intersected with the window | How hard the policy keeps capacity working | `U ∈ [0,1]` by construction (asserted in tests). Near-1.0 does **not** imply fairness or low waits. |
| M8 | Censoring fraction | `f_c^{start} = 1 − |S| / |Cohort|` (censoring for wait metrics M1–M3/M6) and `f_c^{comp} = 1 − #{i ∈ Cohort : completion_i ≤ t_cut} / |Cohort|` (censoring for flow time M9) | How much of the cohort is unobserved for each metric family | Itself a finite-horizon quantity; under ρ > 1 it is the primary honest signal that waits are unbounded. Both components are always reported with cohort size. |
| M9 | Flow time (secondary) | `F_i = completion_i − arrival_i = W_i + S_i` over `{i ∈ Cohort : completion_i ≤ t_cut}`; mean/median/p95 | End-to-end residence including service | With policy-independent `S` (§3.3) it differs from `W` only by a constant-in-expectation term; same censoring rules (`f_c^{comp}`) apply. |

**Metric definitions (precise):**
- **Queue waiting time W_q** = `service_start_i − arrival_i` (time spent waiting in queue before service begins). **Do not** use completion time. **Flow time F** = `completion_i − arrival_i` (total time in system) `= W_q + S_i`.
- **Cohort:** `Cohort = {i : arrival_i ∈ [t_warm, t_end_obs]}` — the single arrival cohort shared by every metric and every policy in a paired run.
- **Windows:** observation `[t_warm, t_end_obs]` with `t_end_obs = t_warm + t_obs`; drain cut-off `t_cut = t_end_obs + t_drain_max`. Arrivals after `t_end_obs` are not generated (default design); the run may continue to `t_cut` only to drain.
- **Started vs completed:** `S = {i ∈ Cohort : start_i ≤ t_cut}` (used by M1–M3, M6); completion-based sets are used by M5/M9. Both set sizes are always reported.
- **Backlog:** `B(t)` counts **queue-waiting jobs only** (in-service jobs excluded); this is stated in reports to avoid conflating queue length with system size.
- **Censoring:** unfinished complaints are never silently dropped: every wait statistic ships with `f_c^{start}`, every flow-time statistic with `f_c^{comp}`, plus cohort size and `n`. If `f_c^{start} > 0.1` the report stamps the wait metrics "censored subset — steady-state interpretation invalid". Under `ρ > 1`, mean waits are unbounded by construction, so finite-horizon values are conditional on `t_cut` (§6.1).
- **Comparable populations:** M1–M3/M6/M9 are only compared across policies within the same (scenario, load, seed) — where cohort and `t_cut` are identical by construction (§6.5). Cross-seed aggregation compares like-for-like censoring by reporting `f_c` alongside every aggregate.

**Explicitly not metrics of this phase [RECOMMENDATION]:** accuracy, precision, recall, NDCG,
resolution-time prediction error, calibration, and any "real response time improvement".
None is computable; none is simulated into existence. The D2.1 harness's `not_evaluated`
convention carries over verbatim (§8).

---

## 6. Experimental design

### 6.1 Scenario grid

Full factorial of:

- **Load** `ρ ∈ {0.5 (low), 0.8 (moderate), 1.2 (high)}`. **[ASSUMPTION]** at ρ > 1 the
  queue is unstable in steady state — backlog grows without bound. The high-load condition
  is therefore interpreted as a **finite-horizon stress test**: M4 (backlog growth rate
  `ĝ_B`, defined in §5) and
  M5/M8 (completion/censoring) are the headline metrics; M1–M3/M6 are reported but stamped
  "censored; steady-state interpretation invalid".
- **Correlation regime** ∈ {`correlated`, `independent`, `anti_correlated`} between
  `sim_is_high_priority` and latent repeat structure (§3.7). **[ASSUMPTION]** latent high-priority
  designation is evaluation-only and unavailable to policies.
- **Detector visibility** ∈ {`detector_visible`, `detector_blind`} (§3.5).
- **Policy** ∈ {FIFO, rule_based, count_based, recurrence_aware} (§4).

This grid is deliberately built so the simulation can report *when recurrence-aware helps,
when it is indistinguishable, and when it hurts* — the honest scientific shape of the
question — instead of a single predetermined winner.

### 6.2 Random seeds, repeated runs, common random numbers

- **[RECOMMENDATION]** `R = 30` independent seeds per grid cell (config knob; ≥30 keeps
  percentile summaries usable without pretending to normality).
- **[RECOMMENDATION — CRN]** Within one (scenario, load, seed), **one** generated arrival
  stream and **one** set of per-complaint service times are drawn and replayed identically
  for every policy. Paired per-seed *differences* between policies are the primary
  comparison quantity (far lower variance than comparing unpaired aggregates). Replicate seeds
  are derived deterministically (e.g. `master_seed + cell_index * 1000 + rep`) where
  `cell_index` comes from an explicit ordered enumeration of the grid written into the config,
  so the mapping is reproducible rather than implicit; every seed actually used is recorded
  in the report.
- **Critical review correction — stream separation:** Separate reproducible RNG streams for exogenous variables:
  `rng_arrivals` (inter-arrival gaps), `rng_service` (service times), `rng_attrs` (complaint
  attributes/categories/text templates), `rng_recurrence` (latent-issue/repeat structure),
  `rng_evidence` (evidence noise/coordinate jitter), `rng_missing` (missingness injection).
  Each stream is derived **deterministically** from `(master_seed, replication, cell_id,
  stream_name)` with a stable derivation (e.g. first 64 bits of
  `sha256(f"{master_seed}:{cell}:{rep}:{name}")` fed to `random.Random`/`numpy` PCG).
  **Python's built-in `hash()` must not be used for seed derivation** — it is salted per
  process (`PYTHONHASHSEED`) and would break cross-process reproducibility.
  **Policy execution must not consume or shift any generator exogenous stream.** The default
  policies are deterministic and consume no variates; if a stochastic policy is ever added, it
  gets its own separate stream (named in config) and the CRN claim is restated accordingly.
- **Verification of equivalent streams.** For the same (scenario, load, seed), the complete
  generated input — arrival times, service times, observable attributes, latent structure
  (generator side), evidence inputs — is produced **once** and shared by all four policies;
  policies are pure functions of that shared stream + their key. Tests additionally compare
  checksums of the exogenous event list across policy runs to prove no stream was consumed
  or shifted by policy execution (§8.4). Changing a regime or load changes the stream and
  is **not** paired across regimes/loads — pairing is only within a cell.
- Determinism requirement: `simulate(config, seed)` is a pure function; same input ⇒
  bit-identical output **[RECOMMENDATION]** (unit-tested, §8.4).

### 6.3 Sensitivity analysis

Configured, reported separately from the main grid, never mixed into headline tables:

1. **Service-time shape:** constant (CV=0) vs exponential (CV=1); M1/M3 are distribution-
   sensitive.
2. **Missingness:** coordinate/category drop rates {0, 0.2} — does the recurrence-aware
   policy lose its edge (or gain falsely) when evidence coverage collapses?
3. **Simulated-observable severity:** give rule-based and recurrence-aware a generated
   `severity_label` (observable, flagged **[ASSUMPTION]**, mirrors a future data source that
   does not exist today **[GAP]**). Tests whether conclusions flip when the scorer's dominant
   term becomes active.
4. **Evidence snapshot vs dynamic recomputation** for count/recurrence policies (§3.5
   bounded-window approximation vs O(n²) exact) — quantifies the approximation's effect.
5. **Servers** `c ∈ {1, 2, 4}` at fixed ρ — multi-server interference effects.
6. **Ageing variant** of rule-based: production scorer + an explicitly simulation-only
   additive wait-age bonus (documented formula, separate policy name
   `rule_based_plus_ageing`, stamped "simulation-only variant; not a production policy").
   Exists because the production age term is structurally 0 (§1) and queueing systems
   normally reward patience; without it, "rule-based" is a weaker baseline than its name
   suggests.
7. **`p_repeat`, `p_high`, evidence saturation `CAP`** ∈ {1, 3, 5}: how sensitive are
   conclusions to the assumed generative magnitudes?
8. **Detector-blind regression:** recurrence_aware must reproduce rule_based metrics exactly
   (same keys) — a structural invariant, asserted in tests, not a tuning knob.

### 6.4 Warm-up and observation windows

- **[RECOMMENDATION]** Define windows precisely:
  - `t_warm` = warm-up period (transient); arrivals before `t_warm` may be generated but the
    **observation cohort** excludes them from metric populations (standard warm-up deletion).
    The system state at `t_warm` is the initial state for observation.
  - `t_end_obs = t_warm + t_obs` = end of observation window for arrivals/cohort.
  - Cohort = `{i | arrival_i ∈ [t_warm, t_end_obs]}`.
  - Metrics M1–M3/M6 computed on this cohort over the started subset (`start ≤ t_cut`),
    M5/M9 over the completed subset (`completion ≤ t_cut`), with
    `t_cut = t_end_obs + t_drain_max` (§5).
  - Backlog `B(t)` and utilization `U` measured over `[t_warm, t_end_obs]` (observation window)
    unless stated otherwise.
  - Simulation may continue to `t_cut` to allow draining; events after `t_cut` do not create
    new cohort members and waiting-time statistics do not include them.
- Defaults (days): `t_warm = 30`, `t_obs = 180`, `t_drain_max = 90` **[ASSUMPTION]** — sized
  so the 30-day recurrence window is spanned many times over; not calibrated to any real
  planning cycle. These are config knobs.
- A **cold-start variant** (no warm-up, full-horizon cohort) is reported as sensitivity so
  warm-up choice cannot secretly determine conclusions.
- **Censoring reporting:** For every run, report `f_c^{start}`, `f_c^{comp}` (M8), cohort size,
  started/completed counts, and window parameters (`t_warm,t_obs,t_drain_max,t_cut`). If any
  metric is reported on a subset, the subset definition is stated explicitly.

### 6.5 Fair comparison conditions (checklist, enforced by construction)

1. Identical arrival streams and service times per (scenario, load, seed) across policies —
   CRN (§6.2).
2. Identical policy-visible snapshots; no policy sees `sim_*`/`latent_*`/`true_*` attributes
   or future events (§3.1.1, §3.6).
3. Identical eligibility conventions (ranking/recurrence eligibility as in §1).
4. Identical evidence-snapshot semantics (same frozen/dynamic mode setting) for the two
   evidence-based policies.
5. Identical warm-up/observation/drain windows and censoring cut-off (`t_cut`).
6. Identical metric code paths (one `metrics.py`, shared by all policies).
7. Any policy difference in *compute* (e.g., D1 overhead) is out of scope for the metrics —
   wall-clock cost is reported separately if at all, never mixed into waiting-time results.

### 6.6 Uncertainty summaries

- Per cell: mean, median, and empirical 2.5/97.5 percentiles of each metric across the `R`
  seeds; per-seed raw values retained in the machine-readable report. Always include censoring
  summaries `f_c^{start}` and `f_c^{comp}` (mean/median/p95 across seeds) when reporting wait
  or flow-time statistics.
- Policy contrasts: per-seed paired differences (CRN), summarised by mean difference and
  empirical 2.5/97.5 percentiles of the differences. A contrast is "resolved in this
  simulated world" only if that interval excludes 0 **[RECOMMENDATION]**.
- **[RECOMMENDATION — wording rule]** these intervals quantify **Monte-Carlo error of the
  simulation estimator only**. They are *not* confidence intervals about the real world and
  must never be rendered or captioned as such. Distinguish Monte Carlo uncertainty from
  uncertainty about real municipal performance.
- No p-value fishing across the grid; the grid is reported whole, resolved and unresolved
  cells alike. Predefine primary outcomes (e.g. M1/M6 queue waiting times for the started subset,
  M5 completion rate, M8 censoring fraction) and secondary/exploratory outcomes (M4,M7,M9)
  in the report config. All planned scenarios/cells are reported (including cases where
  recurrence-aware performs worse).

### 6.7 Reproducible configuration and output reports

- Single YAML config (pyyaml already pinned) or CLI flags mirroring
  `scripts/run_evaluation.py` conventions: `--out data/sim_reports`, `--stem`, `--seeds`,
  `--scenario`, `--load`, `--policy`, `--no-write`. Outputs land in the gitignored `data/`
  tree (§1).
- Every report embeds: sim version (`d3.3` — the D3.3 milestone version; the design text was
  written against `d3.0`, the version of the design's own draft), code commit hash **plus the
  full provenance block** (git HEAD, working-tree state and the digest of every imported local
  source file — a bare SHA does not reproduce a run from a dirty tree, and the report says so
  explicitly), Python version, full config
  (including **imported** production constants — `SIMILARITY_THRESHOLD`,
  `LOCATION_METERS_MAX`, `TIME_WINDOW_DAYS`, `REQUIRED_SIGNALS` — referenced live, never
  retyped), seed list, scenario definitions, metric definitions (M1–M9 formulas with cohort
  and censoring rules), window parameters (`t_warm,t_obs,t_drain_max,t_cut`), RNG stream
  derivation, evidence mode (arrival-snapshot vs dynamic), and the banners of §7.
- **Artifact bundle (required):** seeds, configs, logs, and code revision (git SHA). JSON
  is the source of truth; Markdown is a rendering (reuse the D2.1 `reporting.py` pattern).
- **Fairness checklist recorded:** same snapshot semantics, same exogenous streams (CRN),
  same action constraints, same measurement window/cutoff for all policies in each comparison.

### 6.8 Computational feasibility (provisional; benchmark before believing it)

- **Configurable stream length.** The generated stream length is an explicit config knob:
  arrivals are generated on `[0, t_end_obs)` (§6.4) which implies an expected count
  `n ≈ λ · (t_warm + t_obs)` **[ASSUMPTION]**, with an optional hard cap `max_arrivals`
  (default: none; recorded when set) to bound runtime. The effective `n` per cell is written
  into the report.
- **Main-factorial size [RECOMMENDATION]:**
  - Generated cells (stream + evidence): `3 loads × 3 regimes × 2 visibility × R=30 = 540`
    streams per configuration.
  - Policy runs: `540 × 4 policies = 2160` queue simulations.
  - Evidence (D1, ≤50 candidates per complaint): computed **once per stream** and shared by
    policies ⇒ `540` evidence passes, each `O(n · 50)` detector pair checks.
  - Sensitivity items (§6.3) are **additional** runs and are not part of this count.
- **Runtime expectation:** **[ASSUMPTION — no benchmark exists]** per-run cost is dominated
  by `n`, evidence (`O(540 · 50n)` detector checks) and event count `O(n)`. All wall-clock
  figures are **provisional until benchmarked** with a small `R` pilot; the pilot result
  (runs/hour, `n`, machine) must be recorded in `docs/development_log.md` before the full
  grid is launched. No performance claim appears in this document.
- **Prioritisation [RECOMMENDATION]:** run the main factorial first and report it whole.
  Sensitivity analyses (§6.3) are optional, run afterwards, and may be reduced or dropped
  under budget limits — always stating which were skipped. Never substitute sensitivity
  runs for missing main-grid cells.
- **Essential vs deferrable interactions:** the essential comparisons are the **policy ×
  load**, **policy × correlation-regime**, and **policy × detector-visibility** interactions
  (these are the research questions; the grid is built to expose them). Higher-order
  interactions (load × regime, regime × visibility, three-way) and the sensitivity
  interactions are **deferred**; the report does not interpret them unless explicitly
  scheduled later.

### 6.9 M/M/1 validation of the engine (scope-limited)

**Purpose:** sanity-check the event loop against a known analytic result — nothing more.

- **Assumptions required for the analytic result (standard M/M/1 theory — not a repository claim):**
  single server (`c = 1`), Poisson arrivals at rate `λ`, i.i.d. exponential service times with
  rate `μ = 1/E[S]`, FIFO discipline, infinite buffer, no abandonment, and **stability
  `ρ = λ/μ < 1`**. The simulation is validated in its FIFO policy configuration at
  `ρ ∈ {0.5, 0.8}` with `detector_visibility` irrelevant (FIFO uses no evidence).
- **Formula matched to the metric:** for M/M/1,
  `E[W_q] = λ / (μ(μ − λ)) = ρ / (μ − λ)` is the expected **queue waiting time** — the
  quantity measured by **M1** (`start − arrival`). The expected **time in the system** is
  `E[W] = 1 / (μ − λ) = E[W_q] + 1/μ`, corresponding to **M9** (flow time, `completion − arrival`).
  The validation must state which of the two it targets and must not compare an analytic
  `E[W]` against empirical `W_q` (or vice versa) — standard M/M/1 algebra (textbook result, not repository-derived).
- **Design [RECOMMENDATION]:**
  1. Independent replications (`R_v ≥ 30` seeds) of a *single* fixed configuration
     (`ρ = 0.8`, exponential service, FIFO, long horizon), each producing one steady-state
     estimate of mean `W_q` from the observation cohort (§5) after warm-up deletion.
  2. Warm-up: use the configured `t_warm` (§6.4) and report it; a warm-up sensitivity
     (longer `t_warm`) is included because truncation bias is a known risk.
     **[AUDIT CORRECTION — warm-up must be defensible relative to mixing time]:** for the
     validation configuration (default `E[S] = 0.2 d` §3.3, `ρ = 0.8`, `c = 1`, exponential
     service) the M/M/1 relaxation time is `1/(μ − λ) = E[S]/(1 − ρ) = 1 day`, so the default
     `t_warm = 30 d` spans ≈30 mixing times before the observation window opens — a
     defensible warm-up *for this configuration*, stated explicitly rather than assumed. The
     report records `E[S]`, `μ = 1/E[S]`, `λ = ρ·c/E[S]`, the computed relaxation time, and
     `t_warm / (1/(μ−λ))` alongside the validation result. If `E[S]` is changed, the ratio
     must be re-checked and `t_warm` (or the validation-specific warm-up) raised accordingly;
     a ratio < ~10 is flagged in the report as an insufficient-warm-up warning, never as a
     pass. The planned warm-up sensitivities are retained unchanged: a longer-`t_warm`
     replication here, and the cold-start (no warm-up) variant of §6.4.
  3. Criterion: compute a two-sided 95% Student-t confidence interval over the `R_v`
     replication means and require `E[W_q] = ρ/(μ−λ)` to fall inside it **[RECOMMENDATION]**;
     report the estimate, the CI, and the analytic value in the report regardless of pass/fail.
     Alternatively (or additionally) a pre-registered equivalence band
     `|estimate − analytic| ≤ ε · analytic` with a fixed `ε` recorded in config may be used —
     the band must be fixed before running, never tuned after.
  4. Preconditions asserted before the test is interpreted: `ρ < 1`, censoring fraction
     `f_c^{start} ≈ 0` (a non-negligible `f_c` invalidates the comparison because M1 would be a
     censored subset), and `n` large enough that the CI is informative.
- **Limitations (must accompany any passing result):** this check validates **only** the
  FIFO/exponential/`c=1`/`ρ<1` configuration and, indirectly, the event loop and clock
  arithmetic feeding M1. It does **not** validate: priority key handling, multi-server
  behaviour, non-exponential service, ρ>1 stress runs, the generator's recurrence/evidence
  paths, the metric code for M4–M8, or any policy comparison. **A passing M/M/1 check is
  necessary, not sufficient — it is never evidence that every part of the simulator is
  correct, and reports must not phrase it that way [RECOMMENDATION — wording rule].**
  Test 4 in §8.4 implements this section.

---

## 7. Research integrity (non-negotiable rules)

1. **The development database is not evidence.** **[VERIFIED]** it contains 3 rows (2
   synthetic). It may be used for smoke-testing plumbing only — never for a performance
   number, never in a report as a "result dataset".
2. **Every simulated quantity is labelled.** Reports open with the banner
   `SIMULATED DATA — NOT REAL-WORLD EVIDENCE`. Simulation-only attributes carry the `sim_` /
   `latent_` prefix in code, config, and reports (§3.6 table).
3. **No invented real-world labels or operating statistics.** Arrival rates, service times,
   capacity, `p_high`, and the correlation regimes are recorded as **[ASSUMPTION]** values
   with no external calibration claim. No staffing, SLA, or municipal statistic is asserted
   anywhere.
4. **No fabricated or predetermined results.** This document contains no results. The
   scenario grid (§6.1) is designed to be capable of showing recurrence-aware *losing*; a
   report that only ever shows it winning is a bug in the generator or the analysis, and
   must be treated as such. All cells — including degenerate and unresolved ones — are
   published.
5. **Simulation cannot establish real-world effectiveness.** Results generalise only to the
   assumed data-generating process. Every report repeats this in its limitations block;
   every derived write-up must too.
6. **Production behaviour is untouched.** Policies *call* existing code read-only
   (`RuleBasedPrioritizer`, `RecurrenceAnalysisService` with `cutoff`, FIFO/count semantics
   per the §8.3 decision); no production threshold, weight, or default changes, no
   schema/API/frontend changes (consistent with the D2.1 constraint set).
   *Correction (D3.3 audit remediation):* the **behaviour** claim stands — no threshold,
   weight, or default changed — but the **API** claim needed one qualification: the
   evaluation-only `disabled_signals` keyword was removed from
   `RecurrenceAnalysisService.analyze()` so the production API carries no ablation surface
   (§8). The simulation package itself still adds no production code; it depends on the
   *uncommitted* D2.1 changes to `recurrence/service.py` and `evaluation/*` (§8).
7. **Relationship to prior claims.** Nothing here upgrades the D2.1 statement that agreement
   ≠ accuracy: within a simulation, "policy P beats policy Q on M6" still means only "P
   ordered this assumed world's latent designation earlier at this capacity" — a statement
   about the world we typed into the YAML file.
8. **Predeclared outcomes.** Primary outcomes are fixed before any run: mean queue waiting
   time (M1) and its high-priority subset (M6) on the started subset, completion rate (M5),
   and censoring fraction (M8). Secondary/exploratory: backlog (M4), utilization (M7), flow
   time (M9), medians/p95 (M2/M3). The report labels each table accordingly; no outcome may
   be promoted from secondary to primary after results are seen.
9. **All planned scenarios are reported.** Every main-grid cell — including detector-blind
   cells, unresolved contrasts, and cells where recurrence-aware performs *worse* — appears
   in the report. Omitting unfavourable or degenerate cells is a reporting failure (§6.6,
   §7.4). Sensitivity blocks that were run are reported whole; sensitivity blocks that were
   skipped are listed as skipped.
10. **Full reproducibility record.** Every report embeds: RNG seeds and stream-derivation
    rule (§6.2), full configuration, code revision (git SHA), metric definitions with cohort
    and censoring rules (§5, §6.4), stated assumptions (**[ASSUMPTION]** values echoed), and
    censoring information (`f_c^{start}`, `f_c^{comp}`, cohort sizes). A report missing any
    of these is incomplete and must not be quoted.
11. **Uncertainty wording.** Intervals over seeds are **Monte Carlo uncertainty of the
    simulation estimator** (§6.6). No interval, table, or sentence from this phase may be
    rendered as uncertainty about real municipal performance. Simulation results alone never
    support claims of **real-world effectiveness, novelty, or accuracy** — such claims require
    real data and real labels, which do not exist **[GAP]**.

---

## 8. Implementation proposal (minimal, testable module)

All under the existing empty package `backend/services/simulation/` **[VERIFIED]** §1.
The D3 simulation code itself modifies no production file; the only "touch points" are
imports of existing code.

**Correction (D3.3 audit remediation, 2026-10-21) — production dependency.** The earlier
wording of this line ("No production file is modified") was incomplete in two ways, and is
restated here so the dependency is never implicit:

1. **One production file was changed during the D3.3 audit remediation:**
   `backend/services/recurrence/service.py` — the evaluation-only `disabled_signals` keyword
   was *removed* from `RecurrenceAnalysisService.analyze()` so the production API carries no
   ablation surface at all. Ablation is now an evaluation-only concern
   (`_AblationRecurrenceService` overriding the protected `_required_signals()` hook in
   `evaluation/harness.py`). The D3 package adds no production code; the remediation
   *removed* a production keyword, and `baselines.py`/`harness.py` were adapted to the
   hook instead.
2. **The current implementation depends on uncommitted D2.1 production changes.** The
   simulation calls `RecurrenceAnalysisService.analyze(..., cutoff=...)` and imports
   `REQUIRED_SIGNALS`, `SIMILARITY_THRESHOLD`, `LOCATION_METERS_MAX` and `TIME_WINDOW_DAYS`
   live from `recurrence/types.py`; it also uses `count_based_ranking`, `fifo_ranking` and
   `rule_based_ranking` from `evaluation/baselines.py` and the harness conventions in
   `evaluation/harness.py`. The `cutoff` as-of guard and the baseline functions are **D2.1
   additions that are not yet committed**. Running the simulation therefore depends on a
   working tree that carries the D2.1 *and* D3.3 changes, not on the last commit — which
   is why no report may claim reproducibility from a bare SHA. Every report records the
   state explicitly: the `provenance` block is labelled `incomplete` whenever the working
   tree was dirty, and carries the `git diff HEAD` digest plus a SHA-256 digest over every
   imported local source file, so the exact source state behind a report is identifiable
   even while no single commit reproduces it. Until a commit exists that contains D2.1 and
   D3.3 together, that is the strongest reproducibility claim the reports make.

### 8.1 Module layout

| File | Responsibility | Key symbols |
|---|---|---|
| `model.py` | Typed config + immutable simulated entities (dataclasses; Pydantic only if validation ergonomics demand it) | `SimConfig` (all knobs: λ/ρ, `E[S]`, `c`, `p_repeat`, `p_high`, `correlation_regime`, `δ`, `detector_visibility`, missingness rates, `t_warm`, `t_obs`, `t_drain_max`, `max_arrivals`, `R`, `master_seed`, `evidence_cap`, window constants referenced from production), `SimComplaint` (observable record dict + `sim_*`/`latent_*` fields), `PolicySnapshot` (immutable policy-visible view, §3.1.1), `ArrivalStream`, `RunResult` |
| `generate.py` | Seeded data-generating process | `generate_stream(config, seed) -> ArrivalStream`: separate derived RNG streams per family (§6.2), latent-issue Poisson arrivals, observable-field synthesis (text templates per issue for `detector_visible`), missingness injection, per-complaint service-time draw, latent high-priority assignment per correlation regime (§3.7). Pure function of (config, seed). |
| `evidence.py` | Leakage-free evidence snapshots via **unmodified production code** | `arrival_snapshot_evidence(stream, config)`: for each complaint, run `RecurrenceAnalysisService.analyze` over ≤ `max_candidates` predecessors (naive-UTC ordering, parity with harness semantics, §8.3) and store the relation list; compute count-based snapshot counts per the §8.3 decision (prefix call vs adapter). Evidence for *i* at time `t` = observed subset filtered by `arrival ≤ t` (§3.1.1, §3.5). |
| `policies.py` | The four policies as key functions with a uniform signature | `PolicyKey = Callable[[PolicySnapshot], Tuple[float, datetime, str]]`; `FIFO`, `RULE_BASED`, `COUNT_BASED`, `RECURRENCE_AWARE` (scorer + `min(n, cap)/cap` into `recurrence_evidence`); registry `POLICIES: dict[str, Policy]`. Keys are static (§3.4). Policies accept **only** `PolicySnapshot` (observable + observed-evidence fields); simulation-only fields exist solely on `SimComplaint`. Separation is enforced by a runtime allowlist + tests (§3.1.1, §8.4.10) — **[RECOMMENDATION]**, not yet implemented; no type-level enforcement is claimed. |
| `queue_sim.py` | The discrete-event engine | `run_policy(stream, config, policy) -> RunResult`: deterministic event loop (arrivals, c servers, non-preemptive head-of-queue selection); produces per-complaint `arrival/start/completion/served` records, backlog grid (queue-only), busy-time trace. |
| `metrics.py` | M1–M9, one implementation shared by all policies | `compute_metrics(run_result, config) -> Metrics`; cohort + started/completed subset definitions (§5); censoring accounting `f_c^{start}`/`f_c^{comp}` (M8) mandatory; nearest-rank p95 documented in the docstring. |
| `experiment.py` | Grid × seeds orchestration with CRN | `run_experiment(config) -> ExperimentReport`: per-cell paired runs, per-seed aggregation, paired policy contrasts, sensitivity blocks kept separate. |
| `report.py` | JSON + Markdown rendering, D2.1 style | `report_to_json`, `report_to_markdown`, `write_report` (pattern copied from `backend/services/evaluation/reporting.py`, including the SIMULATED banner, a `not_evaluated` block mirroring the harness convention, and imported-constant echo). |
| `scripts/run_simulation.py` | CLI, mirroring `run_evaluation.py` | argparse flags (§6.7), `_git_commit()` reuse pattern, `--no-write` stdout mode. |

### 8.2 Reuse map (no new wheels)

- `backend/services/prioritization/rule_based.py` — rule-based and recurrence-aware scores.
- `backend/services/evaluation/baselines.py` — count and FIFO semantics / tie-break rules.
- `backend/services/evaluation/harness.py` — candidate-selection and eligibility conventions
  (matched for parity; whether to import the harness's private helpers or re-implement them
  is the §8.3/§10.9 open question — the harness itself is not invoked, as it is DB-bound
  while the simulation is stream-bound).
- `backend/services/recurrence/service.py` + `types.py` — the real detector and its live
  thresholds.
- `backend/core/time.py` — naive-UTC discipline everywhere.
- `backend/services/evaluation/reporting.py` — report-writing pattern.

### 8.3 Production-interface compatibility (inspected signatures — no production changes)

**[VERIFIED — read from source during design review]** The exact interfaces the recurrence-aware
policy and the evidence adapter need, as they exist today:

| Interface | Exact signature / structure | Location | Reusable without production change? |
|---|---|---|---|
| Detector entry point | `RecurrenceAnalysisService.analyze(self, complaint: ComplaintInput, candidates: List[ComplaintInput], *, cutoff: Optional[datetime] = None) -> RecurrenceAnalysisResult` | `recurrence/service.py:51` (class at :38) | **Yes** — `cutoff` gives the as-of guard and is **mandatory in D3: every simulation call passes `cutoff = arrival_i` of the complaint being analysed** (§3.5). The earlier `disabled_signals` keyword was removed from the production signature (D3.3 audit remediation); ablation is now an evaluation-only concern (`_AblationRecurrenceService` overriding `_required_signals()` in `harness.py`), so no ablation surface exists on the production API. |
| Detector input | `ComplaintInput` dataclass fields: `complaint_id, title, description, category, latitude, longitude, location_label, administrative_area, created_at, source, synthetic, data_quality_flags, raw_imported_values` | `recurrence/types.py:23` | **Yes** — simulated observable records are converted to this shape by a simulation-side adapter (outside production code). |
| Detector output | `RecurrenceAnalysisResult.relations: List[RecurrenceRelation]`; each `RecurrenceRelation` has `related_complaint_id`, `relation_type` ∈ {`exact_source`, `potential_recurrence`, `related_candidate`}, `confidence_explained`, `reasons`, … | `recurrence/types.py:52,:67` | **Yes** — read-only; policy counts `potential_recurrence` + `exact_source` relations (§4.4). |
| Scorer entry point | `RuleBasedPrioritizer.score(self, inputs: PrioritizationInputs) -> PrioritizationResult` with `PrioritizationInputs(complaint_id, category, created_at, resolved_at, status, latitude, longitude, severity_label, evidence_completeness=0.5, recurrence_evidence=0.0, uncertainty_flags=None)` and result field `priority_score` | `prioritization/rule_based.py:71,:31,:50` | **Yes** — the recurrence-aware policy calls `score(...)` directly with `recurrence_evidence` set to the normalised D1 count. |
| Rule-based ordering helper | `rule_based_ranking(records: List[dict]) -> Tuple[List[str], Dict[str, float]]` — invokes the scorer with `resolved_at=None` and **hard-coded default** `recurrence_evidence` (0.0) | `evaluation/baselines.py:132` | **Partly** — fine for the `rule_based` baseline, but it **cannot inject recurrence evidence**; the recurrence-aware policy must call `score()` directly (the design already does this, §4.4). |
| FIFO ordering | `fifo_ranking(records: List[dict]) -> List[str]` (sorts `created_at` asc, tie `complaint_id` asc, missing ⇒ `_EPOCH`) | `evaluation/baselines.py:67` | **Yes** — semantics reused for keys/tie-breaks. |
| Count ordering | `count_based_ranking(records: List[dict], *, meters: float = LOCATION_METERS_MAX, days: float = TIME_WINDOW_DAYS) -> Tuple[List[str], Dict[str, int]]` — counts **over the full list passed** (mutual counts, `O(k²)`), no incremental/prefix API exists | `evaluation/baselines.py:76` | **Partly — [DECISION — resolved, audit correction]:** predecessor-only snapshot counts (§4.3) are *not* a direct call, and the production function **must never be called on the complete stream or on any pool containing complaints that arrive after the complaint being scored** — that is option (b), **FORBIDDEN: it derives scores from future data** (§4.3). Prefix calls (option (a)) are exact but `O(n³)` overall and are not recommended. **Recommended: option (c) — a simulation-specific adapter** under `backend/services/simulation/` that (i) imports the production constants `LOCATION_METERS_MAX`/`TIME_WINDOW_DAYS` live from `recurrence/types.py`, (ii) uses the production distance function `haversine_meters` (imported exactly as `baselines.py:23` does) together with the production category case-folding/`created_at`-normalisation conventions (`to_naive_utc`, epoch fallback), (iii) scores complaint *i* against **predecessors only**: *j* is eligible iff it sorts strictly before *i* under the same deterministic key used for FIFO (`created_at` ascending, ties broken by `complaint_id` ascending, missing `created_at` ⇒ epoch) — so predecessor eligibility and its ties are deterministic, and (iv) orders by descending predecessor count with the production tie-break (count desc, `created_at` asc, `complaint_id` asc — `baselines.py:121-128`), the rule §4.3 specifies. Cost `O(n·k)`. **Required:** a parity test (§8.4.16) asserting the adapter's counts and ordering equal `count_based_ranking`'s on small, controlled inputs where the predecessor pool and the full list coincide, plus hand-built tie and missing-data cases. |
| Candidate selection | `_select_candidates(subject, pool, max_candidates) -> List[Dict]` and `_to_complaint_input(record) -> ComplaintInput` — both module-**private** (leading underscore) in the harness | `evaluation/harness.py:222`, `:205` | **Partly — [DECISION — resolved, audit correction]:** simulation runtime code **must not import private underscore-prefixed helpers** from the evaluation harness (or any production module); importing private names couples D3 to internals that may change without notice. The simulation instead provides **simulation-local adapters** (`evidence.py`/adapter layer, §8.1) that re-implement the ~10 lines of record→`ComplaintInput` conversion and predecessor-pool selection against the *public* signatures (`ComplaintInput`, `RecurrenceAnalysisService.analyze`), **no production file is modified**, and **parity tests** (§8.4.16) compare the adapters against the harness helpers' behaviour on small controlled inputs — the test suite may reference the private helpers as a frozen oracle (a rename surfaces as a test failure); production and simulation runtime code never do. |
| Report writing | `write_report(report: Dict[str, Any], output_dir: str \| Path, stem: str = "evaluation") -> Tuple[Path, Path]` | `evaluation/reporting.py:168` | **Yes** — pattern/shape reusable; D3 renders its own reports with the SIMULATED banner (§8.1) rather than reusing the D2.1 Markdown text. |

**Adapter placement [RECOMMENDATION]:** all record→`ComplaintInput` conversion, snapshot
filtering, and policy key functions live **only** under `backend/services/simulation/`
(§8.1). Production code is imported and called read-only; no production signature, default,
or behaviour is changed (§7.6, §8.5). If any of the "Uncertain/Partly" rows above forces a
production change, that is a **design stop** — record it as an **[OPEN QUESTION]** and re-open
the design instead of editing production code.

### 8.4 Test plan (`tests/backend/test_simulation.py`)

Deterministic, fast (the statistical test marked slow if needed); no DB fixtures required
for most tests (stream-level, not DB-level):

1. **Determinism:** same `(config, seed)` ⇒ identical `RunResult` bytes.
2. **CRN fairness:** two policies on one seed consume identical arrival/service streams
   (compare exogenous event checksums before/after policy execution — proves policies neither
   consumed nor shifted generator streams, §6.2).
3. **Exact FIFO validation:** **single server `c = 1`** (audit correction — the Lindley
   recurrence assumes one server), constant service times ⇒ waits match the closed-form
   recurrence `W_{i+1} = max(0, W_i + S − gap_i)` exactly.
4. **M/M/1 validation:** per §6.9 — FIFO, ρ=0.8, independent replications, analytic
   `E[W_q] = ρ/(μ−λ)` (queue wait, **not** `1/(μ−λ)`) inside the 95% CI of replication means;
   preconditions (`ρ<1`, `f_c^{start}` ≈ 0) asserted; marked as a statistical/slow test.
5. **No future leakage:** inserting a *future* compatible complaint never changes any earlier
   complaint's evidence snapshot or any policy key (the D1 `cutoff` guarantee, end-to-end).
6. **Policy discipline:** higher key served before older lower key; exact tie ⇒ older first.
7. **Missing-data parity:** dropping coordinates/category reproduces the documented
   production behaviours (count 0, uncertainty flags, recurrence-ineligible) — asserted
   against the same expectations as the D2.1 tests.
8. **Censoring accounting:** constructed backlog ⇒ `f_c^{start}`/`f_c^{comp}` (M8) and
   started/completed-subset metrics agree with hand computation.
9. **Backlog/utilisation exactness:** toy trace ⇒ M4 (`B(t)` **and** the scalar `ĝ_B`,
   §5) and M7 match hand computation (backlog
   excludes in-service jobs); `U ∈ [0,1]`.
10. **Ground-truth isolation:** policy snapshots contain no `sim_*`/`latent_*`/`true_*`
    fields (allowlist assertion, not a type-level claim); M6 uses the latent flag only in
    the metrics layer; a spy asserts policies never receive it.
11. **Detector-blind invariant:** `recurrence_aware` keys ≡ `rule_based` keys (modulo the
    0.0 recurrence input) ⇒ identical metrics.
12. **Detector-visible smoke:** `detector_visible` streams produce ≥1 D1 relation for latent
    repeats (integration with the real service); with provenance absent by design (§4.4),
    **zero** `exact_source` relations are produced — asserted, so accidental provenance
    generation fails loudly.
13. **Correlation-regime check:** generated `P(high)`, `P(high | R=1)`, `P(high | R=0)`
    match the configured `(p_high, π, δ)` within sampling tolerance (§3.7).
14. **Config echo:** report config round-trips and references production constants by live
    import (same values as `recurrence/types.py`).
15. **Report rendering:** JSON round-trip; Markdown contains the SIMULATED banner, metric
    formulas, and a non-empty `not_evaluated` block (real-world effectiveness et al.).
16. **Adapter parity [AUDIT CORRECTION — required by §8.3]:** (a) the simulation count
    adapter's counts and ordering equal `count_based_ranking`'s on small, controlled inputs
    where the predecessor pool coincides with the full list, plus hand-built cases for ties,
    missing category/coordinates/`created_at`, and the `LOCATION_METERS_MAX` /
    `TIME_WINDOW_DAYS` boundaries (constants imported live, not copied); (b) the
    simulation-local record conversion and predecessor-pool selection match the harness
    helpers' behaviour on the same inputs — the test suite may reference
    `_to_complaint_input`/`_select_candidates` as a frozen oracle, while simulation runtime
    code never imports them (§8.3). Also asserts the adapter never sees a complaint whose
    predecessor relation includes a successor (predecessor-only eligibility, §4.3).

### 8.5 Explicit non-goals of the implementation

No DB writes/migrations; no API routes; no frontend; no new dependencies; no ML; no external
datasets; no results committed (reports stay in gitignored `data/`).

**Correction (D3.3 audit remediation):** the original wording of this section also listed
"no changes to `baselines.py`, `harness.py`, `rule_based.py`, or the recurrence service".
That is no longer accurate and is not restored here, because the two lists mean different
things:

- The D3 *simulation package* still changes none of them — it only imports them.
- The **D2.1** milestone changed `baselines.py` and `harness.py` (baselines, cutoff
  support, the evaluation harness); the **D3.3 audit remediation** changed
  `recurrence/service.py` (removal of the `disabled_signals` keyword) and adapted
  `evaluation/harness.py` to the `_required_signals()` hook. `rule_based.py` is untouched.
- All of those changes are still **uncommitted**, so the simulation runs against a working
  tree, not against the last commit (§8, provenance).

No production *behaviour* of the prioritization or recurrence logic changed: the D3
remediation removed an evaluation-only keyword and replaced it with an evaluation-only
subclass hook; the underlying decision rule is unchanged.

---

## 9. Distinguishing summary

- **Verified facts** (§1, §8.3): the empty `simulation/` package, the scorer and its structural
  age limitation, the reusable baselines and detector (with their exact current signatures),
  the fact that `rule_based_ranking` cannot inject recurrence evidence, the absence of an
  incremental count API and of public harness helpers, naive-UTC conventions, the 3-row dev DB,
  the total absence of resource/outcome data.
- **Assumptions** (everywhere in §3, §4.4, §6): Poisson arrivals, exponential service,
  non-preemptive single-queue c-servers, latent-issue repeat generation, `p_high`, `π`, `δ`
  and the correlation regimes, evidence saturation `CAP=3`, warm-up/duration defaults,
  missingness rates, detector-visibility regimes, and every runtime estimate in §6.8.
  Each is a config knob with a recorded value; none is calibrated to reality.
- **Proposed design choices (not yet implemented)** (§3.1.1, §4–§8): the immutable
  policy-visible snapshot with runtime allowlist, static-key policy unification,
  arrival-snapshot evidence with the ≤50 predecessor window (dynamic mode only as
  sensitivity), CRN with separated derivation-stable streams, the sensitivity list, the
  precise cohort/censoring metric set M1–M9, the scope-limited M/M/1 validation (§6.9), the
  provisional feasibility budget (§6.8), the module layout and test plan.
- **Open implementation decisions recorded, not guessed (§8.3, §10).**

## 10. Open questions

1. **Service-time model fidelity.** Is exponential enough, or does a heavy-tailed / category-
   dependent service time change policy rankings? No data exists to decide; keep CV a
   sensitivity knob (§6.3.1) until real closure data exists.
2. **Abandonment and re-filing.** Real citizens re-file or give up; neither is modelled.
   Does abandonment invert any ranking conclusion? Deferred to keep the minimal rig clean.
3. **Dynamic (non-static) evidence.** How much does exact decision-time recomputation of
   count/recurrence evidence (O(n²)) move results vs the bounded predecessor snapshot?
   Sensitivity item (§6.3.4); implement only if the snapshot variant shows material policy
   separation.
4. **Ageing bonus formula** for the simulation-only `rule_based_plus_ageing` variant: form
   and scale of the wait-age term; must stay explicitly non-production.
5. **Multi-class capacity.** Real crews are specialised (roads vs noise). A single-class
   server pool is the minimal model; category-constrained servers are a plausible Phase D3.5
   extension but change the comparison question.
6. **Detector cost realism.** If compute overhead of D1 ever matters operationally, a
   separate cost model is needed; deliberately excluded here (§6.5.7).
7. **Relationship to a future labelled dataset.** If a real dataset with closure times and
   recurrence labels is ever imported (research plan §H.2), the simulation's assumptions
   should be *re-visited* against it — not silently kept. The report should say this.
8. ~~Count-snapshot mechanism~~ — **RESOLVED in this revision (audit correction).** The
   simulation-side adapter (option (c): imported production constants + `haversine_meters`,
   predecessor-only eligibility, production tie-break, parity test §8.4.16a) is chosen;
   prefix calls are not recommended and full-stream/mutual counting is **forbidden** as
   future-data leakage (§4.3, §8.3). No longer an open question.
9. ~~Private harness helper reuse~~ — **RESOLVED in this revision (audit correction).**
   Simulation runtime code must not import `_select_candidates` / `_to_complaint_input`;
   simulation-local adapters with parity tests (§8.3, §8.4.16b) are required. No longer an
   open question.

---

*This document is a design artefact only. It implements nothing, measures nothing, and
claims no expected outcome. Any Phase D3 implementation must start from this file, keep the
integrity rules of §7, and record every deviation from the design in
`docs/development_log.md`.*
