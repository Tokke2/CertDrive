"""Shared, theme-independent design tokens for IntygsDrive's Qt UI."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SpacingTokens:
    """Logical-pixel rhythm used by layouts, controls and stylesheet rules."""

    xsmall: int = 4
    small: int = 8
    medium: int = 12
    large: int = 16
    xlarge: int = 24
    xxlarge: int = 32


@dataclass(frozen=True, slots=True)
class ShapeTokens:
    """Shared corner radii and control sizing."""

    radius_small: int = 5
    radius_medium: int = 8
    control_height: int = 34
    field_min_width: int = 280


SPACING = SpacingTokens()
SHAPE = ShapeTokens()
BASE_FONT_HEIGHT = 16


def scaled(value: int, font_height: int, *, minimum: int = 1) -> int:
    """Scale a logical size by the active font metric, with a safe lower bound."""
    height = max(1, int(font_height))
    return max(minimum, int(round(int(value) * height / BASE_FONT_HEIGHT)))


def font_scale(font_height: int) -> float:
    """Return a conservative relative text scale for width/spacing decisions."""
    return max(0.85, min(1.8, max(1, int(font_height)) / BASE_FONT_HEIGHT))
