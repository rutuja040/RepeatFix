"""Supported import fields, their aliases, and header normalization.

This module is deliberately dependency-free (no FastAPI/DB) so it can be unit
tested in isolation and reused by mapping code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from backend.schemas.complaint import (
    MAX_CATEGORY_LEN,
    MAX_COMPLAINT_ID_LEN,
    MAX_DEPARTMENT_LEN,
    MAX_DESCRIPTION_LEN,
    MAX_EVIDENCE_LEN,
    MAX_LOCATION_LEN,
    MAX_SEVERITY_LEN,
    MAX_SOURCE_LEN,
    MAX_TITLE_LEN,
)


@dataclass(frozen=True)
class FieldSpec:
    """A complaint field that an import can populate."""

    name: str
    required: bool
    max_length: Optional[int]
    description: str
    aliases: Tuple[str, ...]


# Target fields map 1:1 to the RepeatFix complaint model. No new mandatory
# complaint fields are introduced for any particular dataset.
SUPPORTED_FIELDS: Tuple[FieldSpec, ...] = (
    FieldSpec(
        name="complaint_id",
        required=True,
        max_length=MAX_COMPLAINT_ID_LEN,
        description="External/import identifier for the record (globally unique).",
        aliases=(
            "complaint_id",
            "request_id",
            "case_id",
            "ticket_id",
            "service_request_id",
            "sr_number",
            "unique_key",
            "reference_id",
            "reference",
            "id",
        ),
    ),
    FieldSpec(
        name="title",
        required=True,
        max_length=MAX_TITLE_LEN,
        description="Short complaint title/subject.",
        aliases=(
            "title",
            "complaint_title",
            "subject",
            "summary",
            "headline",
            "name",
        ),
    ),
    FieldSpec(
        name="description",
        required=False,
        max_length=MAX_DESCRIPTION_LEN,
        description="Free-text complaint description.",
        aliases=(
            "description",
            "complaint_description",
            "details",
            "body",
            "narrative",
            "notes",
        ),
    ),
    FieldSpec(
        name="category",
        required=False,
        max_length=MAX_CATEGORY_LEN,
        description="Complaint category/type (kept verbatim; never coerced).",
        aliases=(
            "category",
            "complaint_type",
            "service_type",
            "issue_type",
            "type",
        ),
    ),
    FieldSpec(
        name="latitude",
        required=False,
        max_length=None,
        description="Latitude in decimal degrees (-90..90).",
        aliases=("latitude", "lat", "y"),
    ),
    FieldSpec(
        name="longitude",
        required=False,
        max_length=None,
        description="Longitude in decimal degrees (-180..180).",
        aliases=("longitude", "lon", "lng", "long", "x"),
    ),
    FieldSpec(
        name="location_label",
        required=False,
        max_length=MAX_LOCATION_LEN,
        description="Human-readable location/address label.",
        aliases=(
            "location_label",
            "location",
            "address",
            "street_address",
            "incident_address",
            "location_description",
        ),
    ),
    FieldSpec(
        name="administrative_area",
        required=False,
        max_length=MAX_LOCATION_LEN,
        description="Administrative area (borough, district, ward, ...).",
        aliases=(
            "administrative_area",
            "borough",
            "city",
            "district",
            "region",
            "ward",
            "area",
        ),
    ),
    FieldSpec(
        name="created_at",
        required=False,
        max_length=None,
        description="Original submission timestamp (normalized to UTC).",
        aliases=(
            "created_at",
            "created_date",
            "submitted_date",
            "submitted_at",
            "date_created",
            "opened_at",
            "reported_date",
            "created",
        ),
    ),
    FieldSpec(
        name="status",
        required=False,
        max_length=None,
        description="Workflow status. Unknown values are flagged and defaulted.",
        aliases=("status", "complaint_status", "current_status"),
    ),
    FieldSpec(
        name="assigned_department",
        required=False,
        max_length=MAX_DEPARTMENT_LEN,
        description="Responsible department/agency.",
        aliases=(
            "assigned_department",
            "department",
            "agency",
            "assigned_to",
            "responsible_department",
        ),
    ),
    FieldSpec(
        name="source",
        required=False,
        max_length=MAX_SOURCE_LEN,
        description="Provenance label for the record.",
        aliases=("source", "data_source", "origin"),
    ),
    FieldSpec(
        name="severity_label",
        required=False,
        max_length=MAX_SEVERITY_LEN,
        description="Human-assessed severity (never treated as model output).",
        aliases=("severity_label", "severity", "priority", "urgency"),
    ),
    FieldSpec(
        name="evidence_metadata",
        required=False,
        max_length=MAX_EVIDENCE_LEN,
        description="Free-form evidence/source metadata.",
        aliases=("evidence_metadata", "evidence"),
    ),
    FieldSpec(
        name="synthetic",
        required=False,
        max_length=None,
        description="Whether the record is synthetic (true/false).",
        aliases=("synthetic", "is_synthetic", "synthetic_flag"),
    ),
)

FIELD_BY_NAME: Dict[str, FieldSpec] = {f.name: f for f in SUPPORTED_FIELDS}
REQUIRED_FIELDS: Tuple[str, ...] = tuple(f.name for f in SUPPORTED_FIELDS if f.required)


def supported_field_names() -> list[str]:
    return [f.name for f in SUPPORTED_FIELDS]


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_header(header: str) -> str:
    """Normalize a source column header to compare against aliases.

    Lowercases, trims, strips a UTF-8 BOM, and collapses any run of non
    alphanumeric characters to a single underscore.
    """
    if header is None:
        return ""
    value = header.replace("\ufeff", "").strip().lower()
    value = _NON_ALNUM.sub("_", value)
    return value.strip("_")


def alias_set(spec: FieldSpec) -> frozenset[str]:
    """All normalized tokens that can identify a target field."""
    return frozenset({normalize_header(spec.name), *(normalize_header(a) for a in spec.aliases)})
