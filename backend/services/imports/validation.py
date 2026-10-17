"""Row-level validation and normalization for imported records.

Produces an explicit per-row result: accepted, flagged (accepted with warnings),
or rejected. Values are normalized following documented rules; original values
are preserved separately by the caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from backend.core.constants import ALLOWED_STATUSES
from backend.core.time import to_naive_utc
from backend.services.imports.fields import FIELD_BY_NAME

# --- documented policy constants ---
DEFAULT_STATUS = "open"

# --- issue codes (stable identifiers for tests and clients) ---
CODE_MISSING_REQUIRED = "missing_required"
CODE_VALUE_TOO_LONG = "value_too_long"
CODE_INVALID_NUMBER = "invalid_number"
CODE_OUT_OF_RANGE = "out_of_range"
CODE_INVALID_DATE = "invalid_date"
CODE_INCOMPLETE_COORDINATES = "incomplete_coordinates"
CODE_UNKNOWN_STATUS = "unknown_status_defaulted"
CODE_INVALID_BOOLEAN = "invalid_boolean_defaulted"
CODE_MISSING_CREATED_AT = "missing_created_at"


@dataclass
class Issue:
    row_number: int
    field: Optional[str]
    code: str
    message: str
    status: str  # "rejected" | "flagged" | "accepted"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "row_number": self.row_number,
            "field": self.field,
            "code": self.code,
            "message": self.message,
            "status": self.status,
        }


@dataclass
class RowResult:
    row_number: int
    raw: Dict[str, str]
    values: Dict[str, Any] = field(default_factory=dict)
    errors: List[Issue] = field(default_factory=list)
    warnings: List[Issue] = field(default_factory=list)
    source_record_id: Optional[str] = None

    @property
    def accepted(self) -> bool:
        return not self.errors

    @property
    def flagged(self) -> bool:
        return self.accepted and bool(self.warnings)


_TRUTHY = {"true", "1", "yes", "y", "t"}
_FALSY = {"false", "0", "no", "n", "f"}

_DATETIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
)


def parse_bool(value: str) -> Optional[bool]:
    v = value.strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


def parse_datetime(value: str) -> Optional[datetime]:
    """Parse a supported date/time string into a naive-UTC datetime."""
    text = value.strip()
    if not text:
        return None
    iso = text.replace("Z", "+00:00") if text.endswith("Z") else text
    try:
        return to_naive_utc(datetime.fromisoformat(iso))
    except ValueError:
        pass
    for fmt in _DATETIME_FORMATS:
        try:
            return to_naive_utc(datetime.strptime(text, fmt))
        except ValueError:
            continue
    return None


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def validate_row(
    raw: Mapping[str, str],
    mapping: Mapping[str, str],
    *,
    row_number: int,
    default_synthetic: bool,
) -> RowResult:
    """Validate a single source row against the mapped complaint fields."""
    result = RowResult(row_number=row_number, raw=dict(raw))

    def source_value(target: str) -> Optional[str]:
        source_header = mapping.get(target)
        if source_header is None:
            return None
        return raw.get(source_header)

    def reject(field_name: str, code: str, message: str) -> None:
        result.errors.append(Issue(row_number, field_name, code, message, "rejected"))

    def warn(field_name: str, code: str, message: str) -> None:
        result.warnings.append(Issue(row_number, field_name, code, message, "flagged"))

    # --- required text fields ---
    for target in ("complaint_id", "title"):
        spec = FIELD_BY_NAME[target]
        value = _clean(source_value(target))
        if value is None:
            reject(target, CODE_MISSING_REQUIRED, f"'{target}' is required but was empty.")
            continue
        if spec.max_length and len(value) > spec.max_length:
            reject(
                target,
                CODE_VALUE_TOO_LONG,
                f"'{target}' exceeds {spec.max_length} characters.",
            )
            continue
        result.values[target] = value
        if target == "complaint_id":
            result.source_record_id = value

    # --- optional text fields (length-checked) ---
    optional_text = (
        "description",
        "category",
        "location_label",
        "administrative_area",
        "assigned_department",
        "source",
        "severity_label",
        "evidence_metadata",
    )
    for target in optional_text:
        spec = FIELD_BY_NAME[target]
        value = _clean(source_value(target))
        if value is None:
            continue
        if spec.max_length and len(value) > spec.max_length:
            reject(
                target,
                CODE_VALUE_TOO_LONG,
                f"'{target}' exceeds {spec.max_length} characters.",
            )
            continue
        result.values[target] = value

    # --- coordinates ---
    lat_raw = _clean(source_value("latitude"))
    lon_raw = _clean(source_value("longitude"))
    lat = _parse_coord(lat_raw, "latitude", -90.0, 90.0, result, reject)
    lon = _parse_coord(lon_raw, "longitude", -180.0, 180.0, result, reject)
    if (lat_raw is not None) != (lon_raw is not None):
        warn(
            "latitude",
            CODE_INCOMPLETE_COORDINATES,
            "Only one of latitude/longitude was provided; location is incomplete.",
        )
    if lat is not None:
        result.values["latitude"] = lat
    if lon is not None:
        result.values["longitude"] = lon

    # --- created_at ---
    created_raw = _clean(source_value("created_at"))
    if created_raw is not None:
        parsed = parse_datetime(created_raw)
        if parsed is None:
            reject(
                "created_at",
                CODE_INVALID_DATE,
                f"Could not parse date '{created_raw}'.",
            )
        else:
            result.values["created_at"] = parsed
    else:
        warn(
            "created_at",
            CODE_MISSING_CREATED_AT,
            "No submission timestamp; import time will be recorded instead.",
        )

    # --- status ---
    status_raw = _clean(source_value("status"))
    if status_raw is None:
        result.values["status"] = DEFAULT_STATUS
    else:
        normalized = status_raw.strip().lower()
        if normalized in ALLOWED_STATUSES:
            result.values["status"] = normalized
        else:
            warn(
                "status",
                CODE_UNKNOWN_STATUS,
                f"Unknown status '{status_raw}'; defaulted to '{DEFAULT_STATUS}'.",
            )
            result.values["status"] = DEFAULT_STATUS

    # --- synthetic ---
    synthetic_raw = _clean(source_value("synthetic"))
    if synthetic_raw is None:
        result.values["synthetic"] = default_synthetic
    else:
        parsed_bool = parse_bool(synthetic_raw)
        if parsed_bool is None:
            warn(
                "synthetic",
                CODE_INVALID_BOOLEAN,
                f"Could not interpret '{synthetic_raw}' as a boolean; using default.",
            )
            result.values["synthetic"] = default_synthetic
        else:
            result.values["synthetic"] = parsed_bool

    return result


def _parse_coord(
    value: Optional[str],
    name: str,
    low: float,
    high: float,
    result: RowResult,
    reject,
) -> Optional[float]:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        reject(name, CODE_INVALID_NUMBER, f"'{name}' is not a valid number: '{value}'.")
        return None
    if not (low <= number <= high):
        reject(
            name,
            CODE_OUT_OF_RANGE,
            f"'{name}' must be between {low} and {high} (got {number}).",
        )
        return None
    return number
