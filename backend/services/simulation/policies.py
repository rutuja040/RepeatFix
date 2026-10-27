"""Phase D3 scheduling policies: uniform static key functions (design §3.4, §4).

A policy is a pure function from an immutable :class:`PolicySnapshot` to a
static priority key ``(score, tie_datetime, complaint_id)`` (design §8.1
``policies.py``). Ranking convention across every policy (design §4):

- **descending** ``score`` first;
- ties broken **FIFO** (older ``tie_datetime`` first, which is arrival order
  in this phase);
- remaining ties broken by ``complaint_id`` ascending for determinism,
  mirroring ``baselines.py`` ordering helpers.

The engine therefore selects ``min`` over the rank ``(-score, tie_datetime,
complaint_id)``.

D3.3 implements all four design §4 policies. The evidence-based policies
consume only the two derived-evidence fields of :class:`PolicySnapshot`
(``snapshot_count`` of §4.3 and ``d1_evidence_count`` of §4.4), which the
engine populates from frozen arrival-snapshot evidence (§3.1.1, §3.5) —
never from latent truth and never from future complaints. Keys are static
(§3.4): with arrival-snapshot evidence the key is fixed no later than
complaint arrival.

Registry safety (approved-plan §3): policies are accepted by the engine only
when created through the module's approved factory (:func:`make_policy`,
used by the default constants and by :func:`policies_for_config`), which
stamps an unforgeable-by-convention module-private token. A hand-built
``Policy(name="fifo", ...)`` that merely reuses a registered name is
rejected; unknown names are rejected; evidence-based policies are rejected
when the required evidence bundle is missing. This is runtime validation,
consistent with the design's §3.1.1 stance that allowlists are asserted at
runtime rather than claimed as type-level enforcement.

The factory stamp alone is not enough, though: the factory is public, so a
caller could mint ``make_policy(name="fifo", key=rule_based_key)`` — a
factory-stamped object that would be accepted by a token-only check while
the report labels it ``fifo`` and the engine measures something else. A
mis-labelled policy is a silent measurement error, so :func:`validate_policy`
also verifies that the policy's **key function is the one registered under
that name** (identity for the three static policies; for
``recurrence_aware`` the factory-made closure must carry the saturation
constant CAP that the policy instance itself declares). The CAP travels with
the policy (design §4.4), so a ``recurrence_aware`` instance whose closure
was built for a different CAP is rejected as well.

``PolicySnapshot`` carries only observable, decision-time fields — exactly
the ``POLICY_VISIBLE_FIELDS`` allowlist of ``model.py`` (§3.1.1, §3.6),
including the observed-only derived-evidence fields of §3.6 table row 2.
Simulation-only fields (``sim_``/``latent_``/``true_``/``future_``/
``generator_``) must never appear on it (§7.2); ground-truth isolation and
the future-data boundary are asserted by tests (§8.4.10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple

from backend.services.prioritization.rule_based import (
    PrioritizationInputs,
    RuleBasedPrioritizer,
)

PolicyKey = Callable[["PolicySnapshot"], Tuple[float, datetime, str]]

# Module-private factory token: instances carrying it were built by the
# approved constructors below. Leading underscore = not part of the public
# API (same convention as the harness private helpers of §8.3).
_POLICY_TOKEN = object()

# Policies that cannot produce a meaningful key without the frozen
# arrival-snapshot evidence bundle (design §4.3 predecessor counts, §4.4
# D1 relation counts). The engine rejects them when evidence is absent.
EVIDENCE_BASED_POLICY_NAMES = frozenset({"count_based", "recurrence_aware"})


@dataclass(frozen=True)
class PolicySnapshot:
    """Immutable policy-visible view of one waiting complaint (§3.1.1).

    Fields are exactly the observable allowlist of design §3.6 — identity,
    timestamps, simulated observable attributes (row 1), and the two
    observed-only derived-evidence fields (row 2): ``snapshot_count`` is
    the predecessor-only spatio-temporal-category count of §4.3 and
    ``d1_evidence_count`` is the frozen arrival-snapshot D1 relation count
    of §4.4 (``potential_recurrence`` + ``exact_source``; ``exact_source``
    is absent-by-design on simulated data, §4.4 decision). Latent
    generator truth (``latent_issue_id``, ``sim_is_high_priority``) and any
    future-derived value are structurally absent; the allowlist is
    asserted at runtime by test §8.4.10, not claimed as type-level
    enforcement.
    """

    complaint_id: str
    created_at: datetime
    title: str = ""
    description: str = ""
    category: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    location_label: Optional[str] = None
    administrative_area: Optional[str] = None
    status: str = "open"
    source: str = "simulated"
    synthetic: bool = True
    snapshot_count: int = 0
    d1_evidence_count: int = 0


@dataclass(frozen=True)
class Policy:
    """One registered scheduling policy (name + static key function).

    ``evidence_cap`` is carried only by the recurrence-aware policy: the
    §4.4 saturation constant CAP is a simulation config value owned by the
    policy instance ("must travel with its policy", model.py D3.2 note),
    bound at creation by :func:`policies_for_config` from
    ``SimConfig.evidence_cap``.
    """

    name: str
    key: PolicyKey
    description: str = ""
    evidence_cap: Optional[float] = None
    # Unforgeable-by-convention factory stamp; compare/exclude from repr so
    # policy equality stays about (name, key, cap).
    binding_token: Any = field(default=None, compare=False, repr=False)


def make_policy(
    name: str,
    key: PolicyKey,
    description: str = "",
    evidence_cap: Optional[float] = None,
) -> Policy:
    """The approved policy constructor (registry safety, approved-plan §3).

    Every policy the engine will accept is created here or by
    :func:`policies_for_config`; the stamped token is what
    :func:`validate_policy` checks. Hand-built ``Policy(...)`` instances
    are rejected even when their name is registered.
    """
    if evidence_cap is not None and evidence_cap <= 0:
        raise ValueError("evidence_cap (CAP) must be > 0 (design §4.4)")
    return Policy(
        name=name,
        key=key,
        description=description,
        evidence_cap=evidence_cap,
        binding_token=_POLICY_TOKEN,
    )


def fifo_key(snapshot: PolicySnapshot) -> Tuple[float, datetime, str]:
    """FIFO: score 0 for everyone; FIFO order falls out of the tie-break."""
    return (0.0, snapshot.created_at, snapshot.complaint_id)


# One shared production scorer instance (deterministic, stateless scoring);
# invoked read-only exactly as the D2.1 harness's rule_based_ranking does
# (resolved_at=None, documented defaults), design §4.2/§8.3.
_SCORER = RuleBasedPrioritizer()


def _score(
    snapshot: PolicySnapshot,
    recurrence_evidence: float = 0.0,
) -> float:
    """Production rule-based score for one snapshot (design §4.2).

    ``resolved_at`` is always ``None`` (no resolution information exists or
    may be assumed, §3.6 table row 2); ``severity_label`` is not carried by
    the policy-visible snapshot and is passed through as ``None`` (mirrors
    reality: no dataset supplies it, §1/§4.2); ``evidence_completeness``
    keeps the scorer's documented default 0.5.
    """
    result = _SCORER.score(
        PrioritizationInputs(
            complaint_id=snapshot.complaint_id,
            category=snapshot.category,
            created_at=snapshot.created_at,
            resolved_at=None,
            status=snapshot.status,
            latitude=snapshot.latitude,
            longitude=snapshot.longitude,
            severity_label=None,
            evidence_completeness=0.5,
            recurrence_evidence=recurrence_evidence,
        )
    )
    return float(result.priority_score)


def rule_based_key(snapshot: PolicySnapshot) -> Tuple[float, datetime, str]:
    """Existing rule-based prioritization (design §4.2).

    Production scorer with ``recurrence_evidence`` at its documented
    default 0.0 — the D2.1 harness baseline semantics. Key frozen at
    arrival: with ``resolved_at=None`` the age component is structurally 0
    (§1) and no other component depends on time (§4.2).
    """
    return (_score(snapshot), snapshot.created_at, snapshot.complaint_id)


def count_based_key(snapshot: PolicySnapshot) -> Tuple[float, datetime, str]:
    """Count-based prioritization (design §4.3).

    Descending predecessor-only snapshot count (§4.3/§8.3 decision:
    counts are computed by the simulation-side adapter over predecessors
    only — never over the complete stream, which would be future-data
    leakage). Missing category/coordinates score 0 via the adapter
    (production behaviour); ties fall back to FIFO then id.
    """
    return (
        float(snapshot.snapshot_count),
        snapshot.created_at,
        snapshot.complaint_id,
    )


def make_recurrence_aware_key(cap: float) -> PolicyKey:
    """Build the §4.4 recurrence-aware key bound to saturation constant CAP.

    ``recurrence_evidence = min(n_relations, CAP) / CAP`` with
    ``n_relations`` the frozen arrival-snapshot D1 count
    (``potential_recurrence`` + ``exact_source``; the latter never fires on
    simulated data, §4.4). Fed into the unmodified production scorer's
    existing recurrence slot (weight 0.20 — the design *tests* that
    weight's effect, §4.4). Complaints with no coords/category are
    recurrence-ineligible and simply carry count 0; they are not further
    penalised (§4.4).

    The returned closure is stamped with the CAP it was built for
    (``_evidence_cap``) so the registry can verify that the key and the
    policy instance agree on the saturation constant (design §4.4).
    """
    if cap <= 0:
        raise ValueError("evidence_cap (CAP) must be > 0 (design §4.4)")

    def key(snapshot: PolicySnapshot) -> Tuple[float, datetime, str]:
        evidence = min(float(snapshot.d1_evidence_count), float(cap)) / float(cap)
        return (
            _score(snapshot, recurrence_evidence=evidence),
            snapshot.created_at,
            snapshot.complaint_id,
        )

    key._evidence_cap = float(cap)  # type: ignore[attr-defined]
    key._policy_name = "recurrence_aware"  # type: ignore[attr-defined]
    return key


FIFO = make_policy(
    name="fifo",
    key=fifo_key,
    description="Oldest-first queue (design §4.1); the reference policy.",
)

RULE_BASED = make_policy(
    name="rule_based",
    key=rule_based_key,
    description=(
        "Existing production rule-based scorer with resolved_at=None and "
        "default recurrence evidence (design §4.2)."
    ),
)

COUNT_BASED = make_policy(
    name="count_based",
    key=count_based_key,
    description=(
        "Predecessor-only spatio-temporal-category counts via the "
        "simulation-side adapter (design §4.3, §8.3 option (c))."
    ),
)

# Default CAP = 3 [ASSUMPTION, design §4.4]; experiment runs always use
# policies_for_config(config) so the configured CAP travels with the policy.
RECURRENCE_AWARE = make_policy(
    name="recurrence_aware",
    key=make_recurrence_aware_key(3.0),
    description=(
        "Production rule-based scorer plus min(n_D1_relations, CAP)/CAP in "
        "the recurrence_evidence slot (design §4.4)."
    ),
    evidence_cap=3.0,
)

POLICIES: Dict[str, Policy] = {
    policy.name: policy
    for policy in (FIFO, RULE_BASED, COUNT_BASED, RECURRENCE_AWARE)
}


def policies_for_config(config: Any) -> Dict[str, Policy]:
    """Fresh registry of the four policies bound to a ``SimConfig``.

    Binds the recurrence-aware saturation constant to
    ``config.evidence_cap`` (design §4.4: CAP is a config knob that travels
    with its policy) and stamps every instance with the approved factory
    token so the engine accepts them. FIFO/rule-based/count-based keys are
    cap-independent and are returned as freshly stamped equivalents of the
    module defaults.
    """
    cap = float(config.evidence_cap)
    return {
        "fifo": make_policy(
            name="fifo", key=fifo_key, description=FIFO.description
        ),
        "rule_based": make_policy(
            name="rule_based", key=rule_based_key, description=RULE_BASED.description
        ),
        "count_based": make_policy(
            name="count_based", key=count_based_key, description=COUNT_BASED.description
        ),
        "recurrence_aware": make_policy(
            name="recurrence_aware",
            key=make_recurrence_aware_key(cap),
            description=RECURRENCE_AWARE.description,
            evidence_cap=cap,
        ),
    }


def validate_policy(policy: Policy) -> None:
    """Registry-safety check (approved-plan §3); raises ``ValueError``.

    Accepts only factory-stamped instances whose name is registered **and**
    whose key function is the one registered under that name.

    Rejects: unknown names; arbitrary ``Policy(...)`` objects that merely
    reuse a registered name; non-``Policy`` values; factory-stamped objects
    whose key function belongs to a different policy (a mis-labelled policy
    would be reported under the wrong name); and ``recurrence_aware``
    instances whose declared ``evidence_cap`` does not match the CAP the
    key closure was built for (design §4.4).
    """
    name = getattr(policy, "name", None)
    if not isinstance(policy, Policy) or name not in POLICIES:
        raise ValueError(
            f"unknown policy {name!r}; expected one of {sorted(POLICIES)}"
        )
    if policy.binding_token is not _POLICY_TOKEN:
        raise ValueError(
            f"policy {name!r} was not created by the approved factory "
            "(make_policy / policies_for_config); hand-built Policy objects "
            "are rejected even with a registered name (registry safety)"
        )
    _validate_key_matches_name(policy)


def _validate_key_matches_name(policy: Policy) -> None:
    """The key function must be the one registered under ``policy.name``.

    A factory-stamped object is not by itself a trustworthy label: the
    factory is public, so ``make_policy(name="fifo", key=rule_based_key)``
    would pass a token-only check while the report names the measurement
    ``fifo``. Rejecting the mismatch keeps the report's policy names and
    the engine's behaviour in sync (design §4, §6.5).

    The reference key is read from the live ``POLICIES`` registry at
    validation time, so a registry extended by a caller stays consistent
    with itself.
    """
    name = policy.name
    key = policy.key
    if name == "recurrence_aware":
        built_cap = getattr(key, "_evidence_cap", None)
        if built_cap is None:
            raise ValueError(
                "policy 'recurrence_aware' must be created with the key "
                "returned by make_recurrence_aware_key(cap) so the saturation "
                "constant CAP travels with the policy (design §4.4)"
            )
        if policy.evidence_cap is None:
            raise ValueError(
                "policy 'recurrence_aware' must declare the saturation "
                "constant CAP it was built with (design §4.4)"
            )
        if float(built_cap) != float(policy.evidence_cap):
            raise ValueError(
                f"policy 'recurrence_aware' declares evidence_cap="
                f"{policy.evidence_cap} but its key was built for CAP="
                f"{built_cap}; bind the policy with policies_for_config(config) "
                "so the CAP travels with the policy (design §4.4)"
            )
        return
    expected = POLICIES[name].key
    if key is not expected:
        raise ValueError(
            f"policy {name!r} carries a key function that is not the one "
            f"registered under that name; a mis-labelled policy would be "
            "reported under the wrong name (registry safety)"
        )


def selection_rank(policy: Policy, snapshot: PolicySnapshot) -> Tuple[float, datetime, str]:
    """Engine-side minimisation rank implementing the design's ordering rule.

    ``min`` over this rank equals "highest score first, then oldest
    ``created_at`` first, then smallest ``complaint_id``" — the uniform
    tie-break of design §4.
    """
    score, tie_datetime, complaint_id = policy.key(snapshot)
    return (-score, tie_datetime, complaint_id)
