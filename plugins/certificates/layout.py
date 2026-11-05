"""Pure grouping and responsive-layout rules for the certificate form."""
from __future__ import annotations

import unicodedata

from core.design_tokens import font_scale

TWO_COLUMN_MIN_WIDTH = 520
THREE_COLUMN_MIN_WIDTH = 1080

FIELD_GROUP_ORDER = (
    "Fordonsuppgifter",
    "Intyg och kontroll",
    "Övriga uppgifter",
)

_VEHICLE_TERMS = (
    "fordon", "reg", "registr", "vin", "chassi", "fabrikat", "marke",
    "modell", "arsmodell", "fordonsar", "vehicle", "make", "model", "year",
    "handelsbet", "commercialname", "tradename",
)
_CERTIFICATE_TERMS = (
    "intyg", "cert", "dokument", "datum", "date", "utfaerd", "giltig",
    "kontroll", "signatur", "underskrift", "typeapproval", "typgodk",
    "approval",
)


def field_group(field_name: str, display_name: str = "") -> str:
    """Group by both a placeholder identifier and its user-visible label."""
    combined = f"{field_name} {display_name}".casefold()
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", combined)
        if character.isalnum()
    )
    if any(term in normalized for term in _VEHICLE_TERMS):
        return FIELD_GROUP_ORDER[0]
    if any(term in normalized for term in _CERTIFICATE_TERMS):
        return FIELD_GROUP_ORDER[1]
    return FIELD_GROUP_ORDER[2]


def field_column_count(width: int, font_height: int = 16) -> int:
    """Choose 1–3 columns, scaling breakpoints with accessible text size."""
    logical_width = max(0, int(width))
    scale = font_scale(font_height)
    two_column_min = round(TWO_COLUMN_MIN_WIDTH * scale)
    three_column_min = round(THREE_COLUMN_MIN_WIDTH * scale)
    if logical_width >= three_column_min:
        return 3
    if logical_width >= two_column_min:
        return 2
    return 1
