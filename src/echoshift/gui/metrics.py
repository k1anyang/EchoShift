"""Layout constants shared by the GUI.

The window is laid out on a 2-pixel rhythm with three stepped gaps.  Naming them
keeps a future spacing change to one edit instead of a search for stray ``pady``
literals, and makes it obvious when a value is *not* part of the scale.
"""

from __future__ import annotations

__all__ = [
    "GAP_XS",
    "GAP_SM",
    "GAP_MD",
    "GAP_LG",
    "SETTINGS_COLUMN_WIDTH",
    "QUEUE_MIN_WIDTH",
    "STACK_BREAKPOINT",
    "SETTINGS_STACK_HEIGHT",
]

#: Inside a control: label to its indicator, text to its box.
GAP_XS = 2
#: Inside a group: between stacked fields, between a card's edge and its body.
GAP_SM = 6
#: Between sibling groups inside one card.
GAP_MD = 10
#: Between top-level regions (header, body, log, status bar).
GAP_LG = 16

#: Width of the settings column in the two-column layout.
SETTINGS_COLUMN_WIDTH = 366
#: Narrowest the queue column may get before the layout stacks instead.  Below
#: this the "source" column clips the format/rate/channel summary it exists to
#: show, which is worse than stacking.
QUEUE_MIN_WIDTH = 760
#: Width at which the settings column moves above the queue.
STACK_BREAKPOINT = SETTINGS_COLUMN_WIDTH + QUEUE_MIN_WIDTH
#: Height the settings column gives up to the queue once stacked.
SETTINGS_STACK_HEIGHT = 220
