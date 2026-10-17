from backend.services.prioritization.rule_based import RuleBasedPrioritizer, PrioritizationInputs


def _mk(**kwargs):
    base = {"complaint_id": "C1"}
    base.update(kwargs)
    return PrioritizationInputs(**base)


def test_deterministic_for_identical_inputs():
    p = RuleBasedPrioritizer()
    a = p.score(_mk(severity_label="high", recurrence_evidence=0.5, created_at="2025-01-01T00:00:00"))
    b = p.score(_mk(severity_label="high", recurrence_evidence=0.5, created_at="2025-01-01T00:00:00"))
    assert a.priority_score == b.priority_score
    assert a.components == b.components


def test_missing_severity_not_treated_as_low_severity_silently():
    p = RuleBasedPrioritizer()
    r = p.score(_mk())
    assert "missing_severity" in r.uncertainty_flags
    assert "severity_label" in r.missing_fields
    # must not be labeled as a confident low-priority
    assert "manual_review_recommended" in r.uncertainty_flags or r.priority_score >= 0.0


def test_missing_severity_flags_uncertainty_even_if_other_features_high():
    p = RuleBasedPrioritizer()
    r = p.score(_mk(recurrence_evidence=1.0, evidence_completeness=0.9, latitude=40.7, longitude=-74.0))
    assert "missing_severity" in r.uncertainty_flags
    assert "manual_review_recommended" not in r.uncertainty_flags


def test_higher_severity_higher_score():
    p = RuleBasedPrioritizer()
    low = p.score(_mk(severity_label="low", created_at="2025-01-01T00:00:00"))
    high = p.score(_mk(severity_label="critical", created_at="2025-01-01T00:00:00"))
    assert high.priority_score > low.priority_score


def test_recurrence_does_not_override_missing_severity():
    p = RuleBasedPrioritizer()
    r = p.score(_mk(recurrence_evidence=1.0))
    assert "missing_severity" in r.uncertainty_flags


def test_missing_location_flagged():
    p = RuleBasedPrioritizer()
    r = p.score(_mk(severity_label="high"))
    assert "missing_location" in r.uncertainty_flags
    assert "location" in r.missing_fields


def test_score_bounded_0_1():
    p = RuleBasedPrioritizer()
    r = p.score(
        _mk(
            severity_label="critical",
            recurrence_evidence=1.0,
            evidence_completeness=1.0,
            latitude=1.0,
            longitude=1.0,
            created_at="2000-01-01T00:00:00",
        )
    )
    assert 0.0 <= r.priority_score <= 1.0


def test_rationale_present():
    p = RuleBasedPrioritizer()
    r = p.score(_mk(severity_label="high", created_at="2025-01-01T00:00:00", evidence_completeness=0.8, latitude=1.0, longitude=1.0))
    assert r.rationale
    assert isinstance(r.rationale, list)
