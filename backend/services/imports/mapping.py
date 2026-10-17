"""Column mapping: match source CSV headers to supported complaint fields.

Rules:
- Automatic mapping only when a target has exactly one candidate header.
- Two or more candidate headers for one target is **ambiguous** and is never
  resolved silently; the caller must supply an explicit mapping.
- Explicit mappings override automatic detection.
- A source column may be assigned to at most one target.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Sequence

from backend.services.imports.fields import (
    FIELD_BY_NAME,
    REQUIRED_FIELDS,
    SUPPORTED_FIELDS,
    alias_set,
    normalize_header,
)


@dataclass
class MappingResult:
    mapping: Dict[str, str] = field(default_factory=dict)  # target -> source header
    detected_columns: List[str] = field(default_factory=list)
    unmatched_columns: List[str] = field(default_factory=list)
    ambiguous: Dict[str, List[str]] = field(default_factory=dict)
    missing_required: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors and not self.missing_required

    def source_to_target(self) -> Dict[str, str]:
        return {source: target for target, source in self.mapping.items()}


def _candidates_by_target(headers: Sequence[str]) -> Dict[str, List[str]]:
    normalized = {h: normalize_header(h) for h in headers}
    candidates: Dict[str, List[str]] = {}
    for spec in SUPPORTED_FIELDS:
        aliases = alias_set(spec)
        matches = [h for h in headers if normalized[h] in aliases]
        if matches:
            candidates[spec.name] = matches
    return candidates


def build_mapping(
    headers: Sequence[str],
    explicit: Mapping[str, str] | None = None,
) -> MappingResult:
    """Build and validate a mapping from source headers to target fields."""
    result = MappingResult(detected_columns=list(headers))

    # Reject duplicate header names: they make mapping ambiguous and are a
    # structural problem in the file itself.
    normalized = [normalize_header(h) for h in headers]
    seen: Dict[str, int] = {}
    duplicates = []
    for h in headers:
        key = normalize_header(h)
        seen[key] = seen.get(key, 0) + 1
    duplicates = [h for h in headers if seen[normalize_header(h)] > 1]
    if duplicates:
        result.errors.append(
            "Duplicate column name(s): " + ", ".join(sorted(set(duplicates)))
        )

    if not any(normalized):
        result.errors.append("CSV header row is empty or has no usable column names.")
        return result

    candidates = _candidates_by_target(headers)

    # Automatic detection (only unambiguous targets).
    for target, matches in candidates.items():
        if len(matches) == 1:
            result.mapping[target] = matches[0]
        else:
            result.ambiguous[target] = list(matches)

    # Explicit overrides.
    if explicit:
        for target, source in explicit.items():
            if not source:
                continue
            if target not in FIELD_BY_NAME:
                result.errors.append(
                    f"Unsupported target field '{target}'. Supported: "
                    + ", ".join(FIELD_BY_NAME)
                )
                continue
            if source not in headers:
                result.errors.append(
                    f"Explicit mapping for '{target}' references unknown column '{source}'."
                )
                continue
            result.mapping[target] = source
            # A resolved explicit choice is no longer ambiguous.
            result.ambiguous.pop(target, None)

    # A single source column must not feed two targets.
    used: Dict[str, str] = {}
    for target, source in result.mapping.items():
        if source in used:
            result.errors.append(
                f"Column '{source}' is mapped to both '{used[source]}' and '{target}'."
            )
        else:
            used[source] = target

    # Required fields must be mapped.
    result.missing_required = [f for f in REQUIRED_FIELDS if f not in result.mapping]

    # Any column not used by the mapping is reported but preserved in raw values.
    used_sources = set(result.mapping.values())
    result.unmatched_columns = [h for h in headers if h not in used_sources]

    return result
