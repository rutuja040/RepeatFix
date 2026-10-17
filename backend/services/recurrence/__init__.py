# Explainable Recurrence Analysis (Phase D1)

"""Recurrence analysis service for RepeatFix.

This module implements an explainable, conservative baseline for detecting
potential recurrence relationships between complaints. It never merges,
deletes, or rewrites existing complaints. Text similarity alone is not
sufficient to establish recurrence. Potential relationships require
explainable justification.

Available signals (only those supported by existing data):
- Text similarity (title/description)
- Compatible categories
- Geographic proximity (when both have valid lat/lon)
- Location label/administrative area similarity
- Time proximity
- Reliable source identifiers
- Data-quality flags and missing-field awareness

Output is deterministic and explains the reasons for any match.
"""