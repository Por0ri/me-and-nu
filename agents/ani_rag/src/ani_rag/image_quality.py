"""Resolution-aware image candidate ranking shared by storage and generation."""

from __future__ import annotations


def image_resolution_tier(width: int | None, height: int | None) -> int:
    """Return a preference tier for likely card-size sharpness; 0 is best, 2 is weakest."""
    width, height = width or 0, height or 0
    if width <= 0 or height <= 0:
        return 2
    ratio = width / height
    if ratio >= 2.4:
        target_width, target_height = 1080, 432
    elif ratio < 0.82:
        target_width, target_height = 850, 1200
    elif ratio < 1.25:
        target_width = target_height = 900
    else:
        target_width, target_height = 1080, 700
    coverage = min(width / target_width, height / target_height)
    return 0 if coverage >= 1.0 else 1 if coverage >= 0.65 else 2
