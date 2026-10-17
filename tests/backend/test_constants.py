from typing import get_args

from backend.core.constants import ALLOWED_STATUSES, RESOLVED_STATUSES, ComplaintStatus


def test_complaint_status_literal_matches_allowed_statuses():
    assert tuple(get_args(ComplaintStatus)) == ALLOWED_STATUSES


def test_resolved_statuses_are_allowed_statuses():
    assert set(RESOLVED_STATUSES).issubset(set(ALLOWED_STATUSES))


def test_allowed_statuses_has_no_duplicates():
    assert len(ALLOWED_STATUSES) == len(set(ALLOWED_STATUSES))
