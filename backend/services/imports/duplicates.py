"""Conservative duplicate helpers.

Three distinct notions (never conflated):

1. Repeated source identifier — handled by the service via ``source_key``.
2. Exact duplicate record — identical normalized content signature.
3. Potentially similar complaint — explainable heuristic signals only.

Potential duplicates are only ever *flagged* for human review. This module never
merges or deletes anything.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Dict, List, Optional

from backend.core.time import to_naive_utc

# Explainable, documented thresholds. These are heuristics, not proof.
SIMILARITY_THRESHOLD = 0.85
LOCATION_METERS = 150.0
TIME_WINDOW_DAYS = 7.0

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


def normalize_text(*parts: Optional[str]) -> str:
    joined = " ".join(p for p in parts if p)
    lowered = joined.lower()
    without_punct = _PUNCT.sub(" ", lowered)
    return _WS.sub(" ", without_punct).strip()


def exact_signature(values: Dict) -> tuple:
    """Signature used to detect exact duplicate records.

    Two source rows are exact duplicates when all of these match after
    normalization: title, description, category, created_at, latitude, longitude.
    """
    created = values.get("created_at")
    if isinstance(created, datetime):
        created = to_naive_utc(created).isoformat()
    return (
        normalize_text(values.get("title")),
        normalize_text(values.get("description")),
        (values.get("category") or "").strip().lower(),
        created or "",
        _round_coord(values.get("latitude")),
        _round_coord(values.get("longitude")),
    )


def _round_coord(value) -> str:
    if value is None:
        return ""
    try:
        return f"{round(float(value), 6)}"
    except (TypeError, ValueError):
        return ""


def haversine_meters(
    lat1: float, lon1: float, lat2: float, lon2: float
) -> float:
    radius = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def text_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass
class Candidate:
    key: str
    category: Optional[str]
    text: str
    latitude: Optional[float]
    longitude: Optional[float]
    created_at: Optional[datetime]


class PotentialDuplicateIndex:
    """Bucketed index for explainable potential-duplicate detection."""

    def __init__(self) -> None:
        self._buckets: Dict[str, List[Candidate]] = {}

    def add(self, candidate: Candidate) -> None:
        bucket = (candidate.category or "").strip().lower()
        self._buckets.setdefault(bucket, []).append(candidate)

    def find(self, candidate: Candidate) -> List[dict]:
        """Return explainable matches against already-indexed candidates."""
        bucket = (candidate.category or "").strip().lower()
        matches: List[dict] = []
        for other in self._buckets.get(bucket, []):
            reasons = _match_reasons(candidate, other)
            if reasons:
                matches.append({"key": other.key, "reasons": reasons})
        return matches


def _match_reasons(a: Candidate, b: Candidate) -> List[str]:
    reasons: List[str] = []
    similarity = text_similarity(a.text, b.text)
    if a.text and b.text and similarity >= SIMILARITY_THRESHOLD:
        reasons.append(f"text_similarity={similarity:.2f}")
    if _nearby_and_recent(a, b):
        reasons.append("nearby_in_time")
    return reasons


def _nearby_and_recent(a: Candidate, b: Candidate) -> bool:
    if None in (a.latitude, a.longitude, b.latitude, b.longitude):
        return False
    if a.created_at is None or b.created_at is None:
        return False
    distance = haversine_meters(a.latitude, a.longitude, b.latitude, b.longitude)
    if distance > LOCATION_METERS:
        return False
    delta_days = abs(
        (to_naive_utc(a.created_at) - to_naive_utc(b.created_at)).total_seconds()
    ) / 86400.0
    return delta_days <= TIME_WINDOW_DAYS
