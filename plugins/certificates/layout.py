"""Pure grouping and responsive-layout rules for the certificate form."""
from __future__ import annotations

from core.design_tokens import font_scale

TWO_COLUMN_MIN_WIDTH = 520
THREE_COLUMN_MIN_WIDTH = 1080

_VEHICLE_TERMS = (
    "fordon", "reg", "registr", "vin", "chassi", "fabrikat", "marke",
    "märke", "modell", "arsmodell", "årsmodell", "fordonsar", "fordonsår",
    "vehicle", "make", "model",
)
_CERTIFICATE_TERMS = (
    "intyg", "cert", "dokument", "datum", "date", "utfaerd", "utfärd",
    "giltig", "kontroll", "signatur", "underskrift",
)


def field_group(field_name: str) -> str:
    """Map common vehicle-certificate fields to readable form sections."""
    normalized = "".join(character for character in str(field_name).casefold() if character.isalnum())
    if any(term in normalized for term in _VEHICLE_TERMS):
        return "Fordonsuppgifter"
    if any(term in normalized for term in _CERTIFICATE_TERMS):
        return "Intyg och kontroll"
    return "Övriga uppgifter"


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
