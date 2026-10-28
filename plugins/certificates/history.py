# -*- coding: utf-8 -*-
"""Registration-number duplicate checks against certificate history."""
from __future__ import annotations

from typing import Any, Iterable

_REGISTRATION_KEYS = frozenset({
    "reg", "regnr", "registreringsnummer", "registration", "registrationnumber",
})


def normalize_registration(value: Any) -> str:
    """Normalize a Swedish plate value while ignoring spaces and punctuation."""
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def registration_from_values(values: dict[str, Any]) -> str:
    for key, value in values.items():
        normalized_key = "".join(character for character in str(key).casefold() if character.isalnum())
        if normalized_key in _REGISTRATION_KEYS:
            normalized = normalize_registration(value)
            if normalized:
                return normalized
    return ""


def _record_value(record: Any, key: str, default: Any = "") -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def find_duplicate_certificates(
    records: Iterable[Any], template: str, registration: str
) -> list[Any]:
    """Return history entries with the same template and normalized reg number."""
    wanted_template = str(template or "").strip().casefold()
    wanted_registration = normalize_registration(registration)
    if not wanted_template or not wanted_registration:
        return []

    duplicates: list[Any] = []
    for record in records:
        record_template = str(
            _record_value(record, "template", "")
            or _record_value(record, "category", "")
            or ""
        ).strip().casefold()
        record_registration = normalize_registration(
            _record_value(record, "reg", "")
            or _record_value(record, "regnr", "")
            or _record_value(record, "registration", "")
            or _record_value(record, "registration_number", "")
        )
        if not record_registration:
            values = _record_value(record, "values", {})
            if isinstance(values, dict):
                record_registration = registration_from_values(values)
        if record_template == wanted_template and record_registration == wanted_registration:
            duplicates.append(record)
    return duplicates
