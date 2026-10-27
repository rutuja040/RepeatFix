"""Phase D3.2 seeded stream generation (design §3.2, §3.3, §3.5–§3.7, §6.2).

Pure functions only: ``generate_stream(config, seed)`` is a deterministic
function of its arguments. It draws all six random-stream families of design
§6.2 from independently derived ``random.Random`` instances:

- ``arrivals`` — exponential inter-arrival gaps (homogeneous Poisson, §3.2);
- ``service`` — per-complaint service times (§3.3), drawn at generation so
  they are policy-independent and replayed identically for every policy;
- ``recurrence`` — latent repeat-coin/issue-pick draws and the
  ``sim_is_high_priority`` designation draws (§3.2, §3.7);
- ``attrs`` — latent-issue attribute draws (text templates, administrative
  area, anchor coordinates) and repeat noise suffixes (§3.2);
- ``evidence`` — per-complaint coordinate jitter (§3.2, §3.6);
- ``missing`` — missingness injection for coordinates/category (§3.6).

Seed derivation follows design §6.2 exactly: the first 64 bits (big-endian)
of ``sha256(f"{master_seed}:{cell}:{rep}:{name}")``. Python's built-in
``hash()`` is deliberately never used because it is salted per process and
would break cross-process reproducibility.

**Documented per-arrival draw order** (determinism contract; design §6.2
stream separation). For each accepted arrival, in stream order:

1. ``arrivals``: exponential gap (drawn before the horizon check).
2. ``service``: service time (only for arrivals inside the horizon).
3. ``recurrence``: repeat coin; for repeats, a uniform pick among latent
   issues whose previous report lies within the production
   ``TIME_WINDOW_DAYS`` (live import) — consecutive same-issue reports
   therefore fall inside the 30-day window **by construction**, which
   satisfies §3.2's "with high probability" condition a fortiori.
4. ``attrs``: for singletons — template indices, administrative-area index
   and anchor coordinates of a brand-new latent issue; for repeats — a
   noise-suffix index (suffixes include the empty string).
5. ``evidence``: coordinate jitter (visible regime only uses it to stay
   inside the detector's window; the jitter magnitude is identical in both
   detector-visibility regimes so visibility is the only factor that
   differs between them).
6. ``missing``: coordinate/category drop coins (rates from config, §3.6).

After the full stream is built, latent first-report roles ``R`` are marked
(§3.7), the realized ``π`` is computed and recorded on the stream, the
correlation-regime feasibility check runs (a configuration error — never a
silent clip or re-draw, §3.7), and ``sim_is_high_priority`` is drawn per
complaint from ``rng_recurrence``. The stream also records the realized
repeat proportion and the empirical ``P(high)``, ``P(high | R=1)`` and
``P(high | R=0)`` diagnostics the §3.7 generated-relation verification
requires (``None`` when a stratum is empty).

Detector-visibility regimes (§3.5):

- ``detector_visible``: repeats share the issue's title/description template
  plus a short noise suffix, share a category, sit within
  ``LOCATION_METERS_MAX`` of each other by jitter construction, and arrive
  within ``TIME_WINDOW_DAYS`` — all four D1 signals can fire.
- ``detector_blind``: text-blind only — every description additionally
  carries a per-complaint unique token ``" ref " + sha256(id)*2`` (128 hex
  chars). For normalized base length ``B`` the SequenceMatcher ratio of any
  two complaints is then bounded by ``B/(B+66) < 0.7 = SIMILARITY_THRESHOLD``
  for ``B < 154``; templates here are kept well below that bound (test
  §8.4 verifies the bound empirically against the production
  ``text_similarity``). Coordinates/jitter are identical across regimes, so
  the one deliberately differing factor is text.

The generator never populates ``raw_imported_values`` provenance (design
§4.4 decision), so no ``exact_source`` relation can fire on simulated data.

No module in this package's execution path is imported by production
complaint-management code, and ``run_policy`` never receives an RNG: policy
execution cannot consume or shift any generator stream (design §6.2).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import replace
from typing import Dict, List, Optional, Tuple

from backend.services.recurrence.types import LOCATION_METERS_MAX, TIME_WINDOW_DAYS
from backend.services.simulation.model import (
    ArrivalStream,
    SimComplaint,
    SimConfig,
)

STREAM_NAMES: Tuple[str, ...] = (
    "arrivals",
    "service",
    "attrs",
    "recurrence",
    "evidence",
    "missing",
)

# [ASSUMPTION] Synthetic text templates (design §3.2: "text drawn from the
# issue's template plus noise"). Normalized lengths are deliberately kept
# small (titles ~40-55, descriptions ~55-65) so that the detector-blind
# token of §3.5 bounds pairwise text similarity below SIMILARITY_THRESHOLD
# even for identical template pairs.
TITLE_TEMPLATES: Tuple[str, ...] = (
    "Pothole on residential street near junction",
    "Streetlight out along the pavement stretch",
    "Overflowing public bin at the corner",
    "Graffiti on the underpass wall panel",
    "Broken paving slab on the footpath",
    "Illegal dumping by the roadside verge",
    "Damaged bench in the small green park",
    "Clogged drain cover after heavy rain",
    "Loose manhole cover rattling at night",
    "Overgrown hedge blocking the walkway",
)

DESCRIPTION_TEMPLATES: Tuple[str, ...] = (
    "Reported again near the same stretch of road surface",
    "Residents notice the issue recurring at this location",
    "Condition worsens steadily around this block segment",
    "Visible damage reported at this spot by pedestrians",
    "Public path obstructed here for several consecutive days",
    "Maintenance needed at this recurring problem site",
    "Surface defect persists along this section of the street",
    "Local access affected at this point of the walkway",
    "Infrastructure fault noted repeatedly at this position",
    "Neighbourhood concern raised about this persistent spot",
)

# [ASSUMPTION] Generic administrative areas — abstract labels, not real
# municipal geography (design §7.3: no invented real-world statistics).
AREAS: Tuple[str, ...] = (
    "Area A", "Area B", "Area C", "Area D",
    "Area E", "Area F", "Area G", "Area H",
)

# [ASSUMPTION] Generic complaint categories, drawn per latent issue
# (design §3.2, §4.3: repeats of one issue share a category).
CATEGORIES: Tuple[str, ...] = (
    "roads", "lighting", "waste", "vandalism",
    "pavement", "dumping", "parks", "drainage",
)

# Noise suffixes for visible-regime repeats (design §3.2). The empty string
# keeps some repeats near-identical; the longest (" repeated", 9 chars)
# still leaves normalized similarity far above SIMILARITY_THRESHOLD for the
# template bases above.
NOISE_SUFFIXES: Tuple[str, ...] = ("", " again", " still ongoing", " repeated")

# [ASSUMPTION] Anchor region for latent-issue coordinates (design §3.6):
# uniform in a +/-0.05 degree box around an arbitrary base point. This is a
# simulation construct, not a claim about any real place.
_BASE_LAT = 52.52
_BASE_LON = 13.405
_ANCHOR_HALF_DEG = 0.05

# Coordinate jitter is bounded at LOCATION_METERS_MAX/4 (= 50 m) along each
# axis, scaled by the metre-per-degree figure at the latitude scale
# (1 deg latitude ~ 111320 m). Two reports of the same latent issue are
# therefore never more than roughly 117 m apart worst-case (diagonal of a
# 100 m x 61 m box at this latitude) — strictly inside the production
# LOCATION_METERS_MAX = 200 m detection window (design §3.2). The same
# jitter magnitude applies in both detector-visibility regimes (§3.5).
_JITTER_METERS = LOCATION_METERS_MAX / 4.0
_JITTER_DEG = _JITTER_METERS / 111320.0


def derive_stream_seed(
    master_seed: int,
    cell_id: str,
    rep: int,
    stream_name: str,
) -> int:
    """Deterministic per-stream seed (design §6.2).

    Returns the first 64 bits (big-endian) of
    ``sha256(f"{master_seed}:{cell_id}:{rep}:{stream_name}")`` as an int
    suitable for ``random.Random``. Stable across processes and platforms;
    never uses ``hash()``.
    """
    payload = f"{master_seed}:{cell_id}:{rep}:{stream_name}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def make_rng_streams(
    master_seed: int,
    rep: int,
    cell_id: str = "",
) -> Dict[str, random.Random]:
    """One independent ``random.Random`` per named stream (design §6.2)."""
    return {
        name: random.Random(derive_stream_seed(master_seed, cell_id, rep, name))
        for name in STREAM_NAMES
    }


def _blind_token(complaint_id: str) -> str:
    """Per-complaint unique text token for the ``detector_blind`` regime.

    The 128 hex characters (sha256 of the id, doubled) dilute the shared
    template base far enough that the production SequenceMatcher ratio of
    any two complaints stays below SIMILARITY_THRESHOLD = 0.7 for normalized
    bases shorter than 154 characters (§3.5 module docstring bound).
    """
    digest = hashlib.sha256(complaint_id.encode("utf-8")).hexdigest()
    return " ref " + digest + digest


def generate_stream(
    config: SimConfig,
    seed: int,
    *,
    cell_id: str = "",
) -> ArrivalStream:
    """Generate the complete arrival/service/evidence stream for one run.

    ``seed`` is the replication index (``rep`` in design §6.2's stream
    derivation); the grid cell identifier defaults to ``""`` until
    ``experiment.py`` supplies enumerated cell ids from the config's grid
    ordering. The returned :class:`ArrivalStream` is immutable and is the
    shared common-random-numbers input for every policy in a paired run.

    Raises ``ValueError`` when a non-independent correlation regime is
    requested but the realized ``π`` or the derived ``θ_n`` fall outside
    the feasible ranges of design §3.7 — the generator never clips or
    re-draws to force the marginal.
    """
    rngs = make_rng_streams(config.master_seed, seed, cell_id)
    arrivals_rng = rngs["arrivals"]
    service_rng = rngs["service"]
    attrs_rng = rngs["attrs"]
    recurrence_rng = rngs["recurrence"]
    evidence_rng = rngs["evidence"]
    missing_rng = rngs["missing"]

    rate = config.lambda_per_day
    horizon_end = config.day_to_datetime(config.t_end_obs_days)
    blind = config.detector_visibility == "detector_blind"

    complaints: List[SimComplaint] = []
    # Latent issue states (generator-side only, never exported): each issue
    # remembers its attribute draws, report indices and last report day.
    issues: Dict[int, Dict[str, object]] = {}
    day_offset = 0.0
    index = 0
    while True:
        if config.max_arrivals is not None and index >= config.max_arrivals:
            break
        day_offset += arrivals_rng.expovariate(rate)
        # Float-level horizon guard first: with a very small lambda the day
        # offset can be large enough that timedelta(...) overflows; the
        # datetime comparison below then preserves the exact half-open
        # [epoch, epoch + t_end_obs) semantics for all normal values
        # (design §6.4; audit fix for tiny-lambda OverflowError).
        if day_offset >= config.t_end_obs_days:
            break
        arrival = config.day_to_datetime(day_offset)
        if arrival >= horizon_end:
            break
        if config.service_dist == "constant":
            service_days = float(config.mean_service_days)
        else:
            service_days = service_rng.expovariate(1.0 / float(config.mean_service_days))

        complaint_id = f"SIM-{seed}-{index:06d}"

        # 3. Latent recurrence structure: repeat coin + eligible-issue pick.
        issue_index: Optional[int] = None
        is_repeat = False
        if issues and recurrence_rng.random() < config.p_repeat:
            eligible = [
                key
                for key, state in issues.items()
                if day_offset - float(state["last_report_day"]) <= TIME_WINDOW_DAYS
            ]
            if eligible:
                is_repeat = True
                issue_index = eligible[recurrence_rng.randrange(len(eligible))]

        # 4. Attributes (rng_attrs).
        if not is_repeat:
            issue_index = len(issues)
            title_index = attrs_rng.randrange(len(TITLE_TEMPLATES))
            description_index = attrs_rng.randrange(len(DESCRIPTION_TEMPLATES))
            category_index = attrs_rng.randrange(len(CATEGORIES))
            area_index = attrs_rng.randrange(len(AREAS))
            anchor_lat = _BASE_LAT + (attrs_rng.random() * 2.0 - 1.0) * _ANCHOR_HALF_DEG
            anchor_lon = _BASE_LON + (attrs_rng.random() * 2.0 - 1.0) * _ANCHOR_HALF_DEG
            issues[issue_index] = {
                "title_index": title_index,
                "description_index": description_index,
                "category_index": category_index,
                "area_index": area_index,
                "anchor_lat": anchor_lat,
                "anchor_lon": anchor_lon,
                "reports": [],
                "last_report_day": day_offset,
            }
        state = issues[issue_index]
        noise_suffix = ""
        if is_repeat:
            noise_suffix = NOISE_SUFFIXES[attrs_rng.randrange(len(NOISE_SUFFIXES))]

        # 5. Coordinate jitter (rng_evidence) around the issue anchor.
        latitude = float(state["anchor_lat"]) + (
            (evidence_rng.random() * 2.0 - 1.0) * _JITTER_DEG
        )
        longitude = float(state["anchor_lon"]) + (
            (evidence_rng.random() * 2.0 - 1.0) * _JITTER_DEG
        )
        latitude = min(90.0, max(-90.0, latitude))
        longitude = min(180.0, max(-180.0, longitude))

        # 6. Missingness injection (rng_missing); created_at is never
        # dropped (design §3.6: a complaint without an arrival time cannot
        # enter a queue). Category is drawn per latent issue (§3.2, §4.3):
        # every report of one issue shares it.
        if missing_rng.random() < config.missing_coord_rate:
            latitude = None
            longitude = None
        category: Optional[str] = CATEGORIES[int(state["category_index"])]
        if missing_rng.random() < config.missing_category_rate:
            category = None

        title = TITLE_TEMPLATES[int(state["title_index"])]
        description = DESCRIPTION_TEMPLATES[int(state["description_index"])] + noise_suffix
        if blind:
            description += _blind_token(complaint_id)

        complaints.append(
            SimComplaint(
                complaint_id=complaint_id,
                arrival=arrival,
                service_days=service_days,
                title=title,
                description=description,
                category=category,
                latitude=latitude,
                longitude=longitude,
                location_label=None,
                administrative_area=AREAS[int(state["area_index"])],
                status="open",
                source="simulated",
                synthetic=True,
                latent_issue_id=f"LI-{seed}-{issue_index:04d}",
            )
        )
        state["reports"].append(index)  # type: ignore[union-attr]
        state["last_report_day"] = day_offset
        index += 1

    # R-marking (design §3.7): R_i = 1 iff complaint i is the first report
    # of a latent issue that has at least one repeat.
    n = len(complaints)
    r_flags = [False] * n
    for state in issues.values():
        reports = state["reports"]
        if len(reports) >= 2:
            r_flags[reports[0]] = True  # type: ignore[index]
    n_r = sum(1 for flag in r_flags if flag)
    realized_pi = (n_r / n) if n else 0.0
    n_latent_issues = len(issues)

    # High-priority designation per correlation regime (design §3.7).
    theta_c = theta_n = float(config.p_high)
    if n and config.correlation_regime != "independent":
        if not 0.0 < realized_pi < 1.0:
            raise ValueError(
                f"correlation_regime={config.correlation_regime!r} requires "
                f"0 < pi < 1 (design §3.7 degenerate-feasibility rule); "
                f"realized pi={realized_pi:.6f} for this stream"
            )
        if config.correlation_regime == "correlated":
            theta_c = min(1.0, config.p_high + config.correlation_delta)
        else:  # anti_correlated
            theta_c = max(0.0, config.p_high - config.correlation_delta)
        theta_n = (
            config.p_high - theta_c * realized_pi
        ) / (1.0 - realized_pi)
        if not 0.0 <= theta_n <= 1.0:
            raise ValueError(
                f"correlation_regime={config.correlation_regime!r} is "
                f"infeasible for p_high={config.p_high}, "
                f"delta={config.correlation_delta}, realized "
                f"pi={realized_pi:.6f}: theta_n={theta_n:.6f} outside "
                "[0, 1] (design §3.7 forbids clipping or re-drawing)"
            )
    if n:
        complaints = [
            replace(
                complaint,
                sim_is_high_priority=(
                    recurrence_rng.random()
                    < (theta_c if r_flags[i] else theta_n)
                ),
            )
            for i, complaint in enumerate(complaints)
        ]

    # Realized generator diagnostics (design §3.7 "generated-relation
    # verification"): repeat proportion plus empirical P(high), P(high | R=1)
    # and P(high | R=0). Conditional strata are None when empty (never a
    # fabricated zero). These are evaluation-side metadata, never
    # policy-visible.
    high_flags = [complaint.sim_is_high_priority for complaint in complaints]
    repeat_fraction = ((n - n_latent_issues) / n) if n else 0.0
    p_high = (sum(high_flags) / n) if n else 0.0
    r1_positions = [i for i in range(n) if r_flags[i]]
    r0_positions = [i for i in range(n) if not r_flags[i]]
    p_high_given_r1 = (
        sum(high_flags[i] for i in r1_positions) / len(r1_positions)
        if r1_positions
        else None
    )
    p_high_given_r0 = (
        sum(high_flags[i] for i in r0_positions) / len(r0_positions)
        if r0_positions
        else None
    )

    return ArrivalStream(
        complaints=tuple(complaints),
        master_seed=config.master_seed,
        cell_id=cell_id,
        seed=seed,
        realized_pi=realized_pi,
        n_latent_issues=n_latent_issues,
        realized_repeat_fraction=repeat_fraction,
        realized_p_high=p_high,
        realized_p_high_given_r1=p_high_given_r1,
        realized_p_high_given_r0=p_high_given_r0,
    )
