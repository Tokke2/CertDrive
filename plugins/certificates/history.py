# -*- coding: utf-8 -*-
"""Vehicle-identity duplicate checks against certificate history."""
from __future__ import annotations

from typing import Any, Iterable

_REGISTRATION_KEYS = frozenset({
    "reg", "regnr", "registreringsnummer", "registration", "registrationnumber",
})
_VIN_KEYS = frozenset({
    "vin", "vinnummer", "vinno", "chassi", "chassinummer",
    "chassisnumber", "identifikationsnummer", "identificationsnummer",
    "identificationnumber", "identificationno", "identifieringsnummer",
    "fordonsidentifikationsnummer",
    "fordonsidentifieringsnummer", "vehicleidentificationnumber",
    "vehicleidentificationno", "vehicleidnumber",
})


def normalize_registration(value: Any) -> str:
    """Normalize a registration/VIN value while ignoring spaces and punctuation."""
    return "".join(character for character in str(value or "").upper() if character.isalnum())


def _normalized_key(value: Any) -> str:
    return "".join(character for character in str(value).casefold() if character.isalnum())


def is_vin_field_name(value: Any) -> bool:
    """Return whether a placeholder denotes the vehicle identification number."""
    return _normalized_key(value) in _VIN_KEYS


def collapse_vin_fields(fields: Iterable[str]) -> list[str]:
    """Show one editable field when a template names both VIN and its identifier alias."""
    result: list[str] = []
    has_vin_field = False
    for field_name in fields:
        if is_vin_field_name(field_name):
            if has_vin_field:
                continue
            has_vin_field = True
        result.append(str(field_name))
    return result


def synchronize_vin_values(
    values: dict[str, Any], field_names: Iterable[str] = ()
) -> dict[str, str]:
    """Mirror the single VIN value to every equivalent template placeholder."""
    result = {str(key): str(value or "") for key, value in values.items()}
    candidates = sorted(
        ((key, value) for key, value in result.items() if is_vin_field_name(key) and value.strip()),
        key=lambda pair: (_normalized_key(pair[0]) != "vin",),
    )
    if not candidates:
        return result
    vin = candidates[0][1]
    aliases = {key for key in result if is_vin_field_name(key)}
    aliases.update(str(key) for key in field_names if is_vin_field_name(key))
    aliases.add("VIN")
    for alias in aliases:
        result[alias] = vin
    return result


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
