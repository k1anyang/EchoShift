"""The EchoShift mark, described once.

The logo is an accent-coloured rounded tile carrying a white geometric "E"
whose middle arm becomes a right-pointing shift arrow.  It is drawn in two
places that cannot share drawing code -- the Tk interface paints it on a
``Canvas``, and the Windows ``.ico`` is rendered with Pillow -- so what they
share instead is *this geometry*: every shape is expressed in tile-relative
units (0..1) and each renderer only supplies its own primitives.

Keeping one description is what stops the two from drifting: changing the
arrow's reach here changes both the header mark and the icon files, and
``tests/test_brandmark.py`` fails if a renderer stops consuming it.

Units: ``x``/``y`` run 0..1 across the tile, ``stroke``/``radius`` are fractions
of the tile's width.  The shapes are laid out so the mark's bounding box is
centred on (0.5, 0.5).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "MARK_STROKE",
    "MARK_RADIUS",
    "MARK_LINES",
    "MARK_ARROW",
    "mark_strokes",
    "mark_arrow",
    "mark_bounds",
]


def _seg(x1: float, y1: float, x2: float, y2: float) -> tuple[float, float, float, float]:
    return (x1, y1, x2, y2)


#: Corner radius of the tile, and the thickness of the E's strokes.
MARK_RADIUS = 56 / 256
MARK_STROKE = 22 / 256

#: The E: spine, top arm, bottom arm, middle arm (which the arrow continues).
MARK_LINES: tuple[tuple[float, float, float, float], ...] = (
    _seg(0.209, 0.258, 0.209, 0.742),   # spine
    _seg(0.209, 0.258, 0.564, 0.258),   # top arm
    _seg(0.209, 0.742, 0.564, 0.742),   # bottom arm
    _seg(0.209, 0.500, 0.564, 0.500),   # middle arm, same length as the others
)

#: The shift arrow: base-top, tip, base-bottom.  It starts just past the middle
#: arm so the two read as one stroke with a gap no wider than the stroke itself.
MARK_ARROW: tuple[tuple[float, float], ...] = (
    (0.572, 0.355),
    (0.791, 0.500),
    (0.572, 0.645),
)


@dataclass(frozen=True)
class Stroke:
    """One stroked segment, in tile-relative coordinates."""

    x1: float
    y1: float
    x2: float
    y2: float


def mark_strokes(end: float = 1.0) -> tuple[Stroke, ...]:
    """The E's segments, optionally shortened towards their start.

    ``end`` scales how far each stroke reaches, which is all a caller needs to
    reproduce the mark at any size; it exists so the geometry can be exercised
    without a display.
    """
    return tuple(
        Stroke(x1, y1, x1 + (x2 - x1) * end, y1 + (y2 - y1) * end)
        for x1, y1, x2, y2 in MARK_LINES
    )


def mark_arrow() -> tuple[tuple[float, float], ...]:
    """The arrow polygon's three points."""
    return MARK_ARROW


def mark_bounds() -> tuple[float, float, float, float]:
    """Bounding box of the strokes and the arrow: ``(x1, y1, x2, y2)``.

    The tests use this to assert the mark stays centred in its tile.
    """
    xs: list[float] = []
    ys: list[float] = []
    for stroke in mark_strokes():
        xs += [stroke.x1, stroke.x2]
        ys += [stroke.y1, stroke.y2]
    for x, y in mark_arrow():
        xs.append(x)
        ys.append(y)
    half = MARK_STROKE / 2
    return (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
