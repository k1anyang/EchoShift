"""Text measurement for Canvas-drawn UI.

Both the queue and the settings labels need to know how wide a string is, and
Tk defaults to one round trip per measurement.  The character table below is
what makes repaints fast: Tk sums per-character advances with no kerning, so a
string's width is exactly the sum of its characters' advances and can be cached.
"""

from __future__ import annotations

from tkinter import font as tkfont

__all__ = ["CharWidths", "ellipsize", "text_width"]


class CharWidths:
    """A per-font cache of character advances."""

    def __init__(self) -> None:
        self._tables: dict[str, dict[str, int]] = {}

    def table(self, tag: str) -> dict[str, int]:
        return self._tables.setdefault(tag, {})

    def advance(self, char: str, font: tkfont.Font, tag: str) -> int:
        table = self.table(tag)
        value = table.get(char)
        if value is None:
            value = font.measure(char)
            table[char] = value
        return value

    def width(self, text: str, font: tkfont.Font, tag: str = "ui") -> int:
        return sum(self.advance(char, font, tag) for char in text)


def text_width(
    text: str, font: tkfont.Font, tag: str = "ui", *, cache: CharWidths | None = None
) -> int:
    """Pixel width of ``text`` without a Tcl round trip per measurement."""
    if cache is not None:
        return cache.width(text, font, tag)
    return font.measure(text)


def ellipsize(
    text: str,
    max_px: int,
    font: tkfont.Font,
    tag: str = "ui",
    *,
    cache: CharWidths | None = None,
) -> str:
    """Trim ``text`` to fit ``max_px``, appending an ellipsis when it does."""
    if not text or max_px <= 0:
        return ""

    if cache is not None:
        def measure(value: str) -> int:
            return cache.width(value, font, tag)
    else:
        measure = font.measure

    if measure(text) <= max_px:
        return text

    ellipsis = "\u2026"
    if measure(ellipsis) > max_px:
        return ""

    budget = max_px - measure(ellipsis)
    if cache is not None:
        # Walk character by character off the cached advances; no per-character
        # Tcl call, which is the whole point of having the table.
        cut = 0
        consumed = 0
        for char in text:
            advance = cache.advance(char, font, tag)
            if consumed + advance > budget:
                break
            consumed += advance
            cut += 1
        return text[:cut].rstrip() + ellipsis

    # No cache: measure only the surviving prefix, once.
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if font.measure(text[:mid]) <= budget:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip() + ellipsis
