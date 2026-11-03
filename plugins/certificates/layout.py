"""Pure responsive-layout rules for the certificate form."""
from __future__ import annotations

TWO_COLUMN_MIN_WIDTH = 520
THREE_COLUMN_MIN_WIDTH = 1080


def field_column_count(width: int) -> int:
    """Return field-grid columns from Qt logical width, independent of DPI."""
    logical_width = max(0, int(width))
    if logical_width >= THREE_COLUMN_MIN_WIDTH:
        return 3
    if logical_width >= TWO_COLUMN_MIN_WIDTH:
        return 2
    return 1
