"""Recognize another site's resize, recompression, or crop of a cover's source art."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageOps, ImageStat


@dataclass(frozen=True)
class _View:
    edges: int
    colors: bytes


CoverViews = tuple[_View | None, ...]


def _view(image: Image.Image, box: tuple[float, float, float, float]) -> _View | None:
    thumbnail = image.resize((16, 16), Image.Resampling.LANCZOS, box=box)
    # A solid color, border or blank background is not evidence of shared artwork.
    if max(ImageStat.Stat(thumbnail).stddev) < 18:
        return None
    light = thumbnail.convert("L").tobytes()
    edges = 0
    for step, rows, columns in ((2, 8, 7), (32, 7, 8)):
        for y in range(rows):
            for x in range(columns):
                i = y * 32 + x * 2
                edges = (edges << 1) | (light[i] > light[i + step])
    return _View(edges, thumbnail.tobytes())


@lru_cache(maxsize=8192)
def _read_views(path: str, version: str | tuple[int, int]) -> CoverViews:
    # Asset paths are content-addressed on ingest. The version also invalidates
    # cached results for legacy assets whose file has been replaced in place.
    with Image.open(path) as source:
        source.draft("RGB", (512, 512))
        rgba = ImageOps.exif_transpose(source).convert("RGBA")
    image = Image.new("RGB", rgba.size, "white")
    image.paste(rgba, mask=rgba.getchannel("A"))
    w, h = image.size
    views = [_view(image, (0, 0, w, h))]
    # A distributor may trim the sides while retaining the full height (or
    # vice versa). Compare the same portion of that dimension on both sources.
    for dimension in (h, w):
        side = min(w, h, 0.78 * dimension)
        if side * side < w * h / 3:
            views.append(None)
        else:
            views.append(_view(image, ((w - side) / 2, (h - side) / 2, (w + side) / 2, (h + side) / 2)))
    return tuple(views)


def cover_views(path: str, sha256: str | None) -> CoverViews:
    if not path:
        return ()
    try:
        if sha256:
            version = sha256
        else:
            stat = os.stat(path)
            version = (stat.st_mtime_ns, stat.st_size)
        return _read_views(os.path.abspath(path), version)
    except (OSError, ValueError, Image.DecompressionBombError):
        # Old imports may lack a local file. Their URL, digest, and provider
        # identity still participate in the ordinary duplicate check.
        return ()


def same_cover_art(left: CoverViews, right: CoverViews) -> bool:
    for a, b in zip(left, right):
        if a is None or b is None or (a.edges ^ b.edges).bit_count() > 16:
            continue
        # Edges alone would also match silhouettes or recolored posters.
        difference = sum(abs(x - y) for x, y in zip(a.colors, b.colors))
        if difference <= 12 * len(a.colors):
            return True
    return False
