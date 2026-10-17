from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, List


@dataclass
class Baseline:
    name: str
    key: str


class Baselines:
    FIFO = Baseline(name="First-in-first-out", key="fifo")
    COMPLAINT_COUNT = Baseline(name="Complaint-count ranking", key="complaint_count")
    RULE_BASED = Baseline(name="Rule-based priority", key="rule_based")


@dataclass
class ComparisonResult:
    method: str
    metrics: Dict[str, Any]
    eligible_count: int
    notes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
