# -*- coding: utf-8 -*-
"""Vehicle-identity duplicate checks against certificate history."""
from __future__ import annotations

from typing import Any, Iterable

_REGISTRATION_KEYS = frozenset({
    "reg", "regnr", "registreringsnummer", "registration", "registrationnumber",
})
_VIN_KEYS = frozenset({
    "vin", "vinnummer", "chassi", "chassinummer", "chassisnumber",
    "vehicleidentificationnumber",
})


def normalize_registration(value: Any) -> str:
    """Normalize a registration/VIN value while ignoring spaces and punctuation."""
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def registration_from_values(values: dict[str, Any]) -> str:
    for key, value in values.items():
        if _normalized_key(key) in _REGISTRATION_KEYS:
            normalized = normalize_registration(value)
            if normalized:
                return normalized
    return ""


def vin_from_values(values: dict[str, Any]) -> str:
    for key, value in values.items():
        if _normalized_key(key) in _VIN_KEYS:
            normalized = normalize_registration(value)
            if normalized:
                return normalized
    return ""


def _record_value(record: Any, key: str, default: Any = "") -> Any:
    if isinstance(record, dict):
        return record.get(key, default)
    return getattr(record, key, default)


def _record_vehicle_id(record: Any, aliases: tuple[str, ...], value_keys: frozenset[str]) -> str:
    for alias in aliases:
        value = _record_value(record, alias, "")
        if value:
            normalized = normalize_registration(value)
            if normalized:
                return normalized
    values = _record_value(record, "values", {})
    if isinstance(values, dict):
        for key, value in values.items():
            if _normalized_key(key) in value_keys and value:
                normalized = normalize_registration(value)
                if normalized:
                    return normalized
    return ""


def find_duplicate_certificates(
    records: Iterable[Any],
    template: str,
    registration: str = "",
    vin: str = "",
) -> list[Any]:
    """Find same-template history entries matching registration and/or VIN.

    An empty identifier is ignored; at least one supplied identifier is required.
    VIN matching remains scoped to the same template to avoid warning when the
    same vehicle legitimately has different types of certificates.
    """
    wanted_template = str(template or "").strip().casefold()
    wanted_registration = normalize_registration(registration)
    wanted_vin = normalize_registration(vin)
    if not wanted_template or not (wanted_registration or wanted_vin):
        return []

    duplicates: list[Any] = []
    for record in records:
        record_template = str(
            _record_value(record, "template", "")
            or _record_value(record, "category", "")
            or ""
        ).strip().casefold()
        if record_template != wanted_template:
            continue
        record_registration = _record_vehicle_id(
            record,
            ("reg", "regnr", "registration", "registration_number"),
            _REGISTRATION_KEYS,
        )
        record_vin = _record_vehicle_id(
            record,
            ("vin", "chassi", "chassinummer", "vin_number", "chassis_number"),
            _VIN_KEYS,
        )
        if (
            (wanted_registration and record_registration == wanted_registration)
            or (wanted_vin and record_vin == wanted_vin)
        ):
            duplicates.append(record)
    return duplicates
