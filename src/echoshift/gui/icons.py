"""Vector icons drawn straight onto a Canvas.

No image files and no imaging library: every icon is a handful of
``create_line`` / ``create_polygon`` / ``create_oval`` calls, which keeps the
bundle dependency-free and lets the icons recolour for hover and disabled
states.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable

__all__ = ["ICONS", "draw_icon", "icon_names"]


def _line(canvas: tk.Canvas, points: list[float], colour: str, width: float) -> int:
    return canvas.create_line(*points, fill=colour, width=width, capstyle="round", joinstyle="round")


def _file_plus(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h * 0.7, cy - h, cx + h * 0.15, cy - h, cx + h * 0.75, cy - h * 0.4,
              cx + h * 0.75, cy + h, cx - h * 0.7, cy + h, cx - h * 0.7, cy - h], colour, w)
    _line(c, [cx + h * 0.15, cy - h, cx + h * 0.15, cy - h * 0.4,
              cx + h * 0.75, cy - h * 0.4], colour, w)
    _line(c, [cx + h * 0.1, cy + h * 0.35, cx + h * 0.95, cy + h * 0.35], colour, w)
    _line(c, [cx + h * 0.52, cy - h * 0.08, cx + h * 0.52, cy + h * 0.78], colour, w)


def _folder_plus(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h, cy - h * 0.35, cx - h * 0.25, cy - h * 0.35, cx - h * 0.05, cy - h * 0.6,
              cx + h * 0.6, cy - h * 0.6, cx + h * 0.6, cy + h * 0.6,
              cx - h, cy + h * 0.6, cx - h, cy - h * 0.35], colour, w)
    _line(c, [cx + h * 0.75, cy + h * 0.05, cx + h * 1.25, cy + h * 0.05], colour, w)
    _line(c, [cx + h, cy - h * 0.2, cx + h, cy + h * 0.3], colour, w)


def _minus(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h * 0.75, cy, cx + h * 0.75, cy], colour, w)
    c.create_oval(cx - h * 0.95, cy - h * 0.95, cx + h * 0.95, cy + h * 0.95,
                  outline=colour, width=w)


def _trash(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h * 0.85, cy - h * 0.45, cx + h * 0.85, cy - h * 0.45], colour, w)
    _line(c, [cx - h * 0.3, cy - h * 0.75, cx + h * 0.3, cy - h * 0.75], colour, w)
    _line(c, [cx - h * 0.6, cy - h * 0.45, cx - h * 0.45, cy + h * 0.8,
              cx + h * 0.45, cy + h * 0.8, cx + h * 0.6, cy - h * 0.45], colour, w)
    _line(c, [cx - h * 0.15, cy - h * 0.15, cx - h * 0.15, cy + h * 0.45], colour, w)
    _line(c, [cx + h * 0.15, cy - h * 0.15, cx + h * 0.15, cy + h * 0.45], colour, w)


def _play(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, _w: float) -> None:
    h = s * 0.5
    c.create_polygon(
        cx - h * 0.5, cy - h * 0.8, cx + h * 0.85, cy, cx - h * 0.5, cy + h * 0.8,
        fill=colour, outline=colour,
    )


def _stop(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, _w: float) -> None:
    h = s * 0.32
    c.create_rectangle(cx - h, cy - h, cx + h, cy + h, fill=colour, outline=colour)


def _terminal(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h, cy - h * 0.7, cx + h, cy - h * 0.7, cx + h, cy + h * 0.7,
              cx - h, cy + h * 0.7, cx - h, cy - h * 0.7], colour, w)
    _line(c, [cx - h * 0.55, cy - h * 0.25, cx - h * 0.15, cy + h * 0.1,
              cx - h * 0.55, cy + h * 0.45], colour, w)
    _line(c, [cx + h * 0.05, cy + h * 0.45, cx + h * 0.6, cy + h * 0.45], colour, w)


def _folder_open(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h, cy - h * 0.5, cx - h * 0.2, cy - h * 0.5, cx, cy - h * 0.75,
              cx + h * 0.5, cy - h * 0.75, cx + h * 0.5, cy - h * 0.3], colour, w)
    _line(c, [cx - h, cy - h * 0.5, cx - h, cy + h * 0.65, cx + h * 0.35, cy + h * 0.65,
              cx + h * 0.9, cy - h * 0.25, cx + h * 0.2, cy - h * 0.25], colour, w)


def _chevron_right(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h * 0.3, cy - h * 0.55, cx + h * 0.4, cy, cx - h * 0.3, cy + h * 0.55], colour, w)


def _chevron_down(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h * 0.55, cy - h * 0.25, cx, cy + h * 0.35, cx + h * 0.55, cy - h * 0.25], colour, w)


def _music(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx + h * 0.15, cy - h * 0.85, cx + h * 0.15, cy + h * 0.35], colour, w)
    _line(c, [cx + h * 0.15, cy - h * 0.85, cx + h * 0.85, cy - h * 0.6], colour, w)
    c.create_oval(cx - h * 0.75, cy + h * 0.2, cx + h * 0.15, cy + h * 0.85,
                  fill=colour, outline=colour)
    c.create_oval(cx - h * 0.05, cy + h * 0.45, cx + h * 0.85, cy + h * 1.05,
                  fill=colour, outline=colour)


def _brand(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    """The EchoShift mark: a geometric E whose middle arm becomes an arrow.

    Coordinates come from :mod:`echoshift.brandmark` so this and the generated
    ``.ico`` cannot drift apart; only the drawing primitives are local.
    """
    from ..brandmark import MARK_STROKE, mark_arrow, mark_strokes

    left = cx - s / 2
    top = cy - s / 2
    stroke = max(w, s * MARK_STROKE)

    for segment in mark_strokes():
        _line(
            c,
            [left + segment.x1 * s, top + segment.y1 * s,
             left + segment.x2 * s, top + segment.y2 * s],
            colour,
            stroke,
        )

    points: list[float] = []
    for x, y in mark_arrow():
        points += [left + x * s, top + y * s]
    c.create_polygon(points, fill=colour, outline=colour)


def _check(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    _line(c, [cx - h * 0.65, cy + h * 0.05, cx - h * 0.15, cy + h * 0.5,
              cx + h * 0.7, cy - h * 0.5], colour, w)


def _cross(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5 * 0.7
    _line(c, [cx - h, cy - h, cx + h, cy + h], colour, w)
    _line(c, [cx + h, cy - h, cx - h, cy + h], colour, w)


def _dash(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5 * 0.7
    _line(c, [cx - h, cy, cx + h, cy], colour, w)


def _info(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    c.create_oval(cx - h * 0.9, cy - h * 0.9, cx + h * 0.9, cy + h * 0.9,
                  outline=colour, width=w)
    _line(c, [cx, cy - h * 0.15, cx, cy + h * 0.5], colour, w)
    c.create_oval(cx - w * 0.6, cy - h * 0.55, cx + w * 0.6, cy - h * 0.55 + w * 1.2,
                  fill=colour, outline=colour)


def _refresh(c: tk.Canvas, cx: float, cy: float, s: float, colour: str, w: float) -> None:
    h = s * 0.5
    c.create_arc(
        cx - h * 0.75, cy - h * 0.75, cx + h * 0.75, cy + h * 0.75,
        start=35, extent=285, style="arc", outline=colour, width=w,
    )
    c.create_polygon(
        cx + h * 0.72, cy - h * 0.72,
        cx + h * 0.78, cy - h * 0.05,
        cx + h * 0.18, cy - h * 0.42,
        fill=colour, outline=colour,
    )


#: name -> drawing routine.  Each takes the canvas, a centre, a size, the
#: stroke colour and the stroke width.
ICONS: dict[str, Callable[[tk.Canvas, float, float, float, str, float], None]] = {
    "file-plus": _file_plus,
    "folder-plus": _folder_plus,
    "minus": _minus,
    "trash": _trash,
    "play": _play,
    "stop": _stop,
    "terminal": _terminal,
    "folder-open": _folder_open,
    "chevron-right": _chevron_right,
    "chevron-down": _chevron_down,
    "music": _music,
    "brand": _brand,
    "check": _check,
    "cross": _cross,
    "dash": _dash,
    "info": _info,
    "refresh": _refresh,
}


def icon_names() -> tuple[str, ...]:
    return tuple(sorted(ICONS))


def draw_icon(
    canvas: tk.Canvas,
    name: str,
    cx: float,
    cy: float,
    size: float,
    colour: str,
    *,
    width: float = 1.6,
) -> None:
    """Draw ``name`` centred on ``(cx, cy)``; unknown names draw nothing."""
    routine = ICONS.get(name)
    if routine is None:
        return
    routine(canvas, cx, cy, size, colour, width)
