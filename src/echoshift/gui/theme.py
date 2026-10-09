"""Colour palette, fonts and ttk styling for the dark theme.

Everything visual is decided here so the widgets stay declarative.  Tkinter
cannot round a ``Frame``, so cards use a flat fill plus a hairline border,
while the elements where rounding actually reads as polish -- buttons, pills,
progress bars -- are drawn on a ``Canvas`` by :mod:`echoshift.gui.widgets`.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import ttk

__all__ = ["Palette", "DARK", "Fonts", "build_fonts", "apply_theme", "mix"]


@dataclass(frozen=True)
class Palette:
    """One complete colour scheme."""

    bg: str
    surface: str
    surface_alt: str
    elevated: str
    border: str
    border_strong: str
    text: str
    text_muted: str
    text_faint: str
    #: Interactive-control fills, kept separate from the surface family so a
    #: button's resting/hover/pressed look never collides with an input field's
    #: background or a progress trough.
    control: str
    control_hover: str
    control_pressed: str
    control_disabled: str
    #: Text on a disabled control.  Deliberately dimmer than :attr:`text_faint`
    #: so "switched off" cannot be mistaken for "secondary information".
    disabled_ink: str
    accent: str
    accent_hover: str
    accent_active: str
    accent_text: str
    success: str
    warning: str
    error: str
    info: str
    selection: str
    row_alt: str
    row_hover: str
    scrollbar: str
    scrollbar_hover: str
    scrollbar_track: str


#: A neutral, slightly blue-tinted dark scheme.
#:
#: The three text tiers all clear WCAG AA (4.5:1) against every background they
#: are drawn on -- surface 1d1f24, surface_alt 23262c, bg 16171b, row_alt
#: 1a1c20 -- because all three carry real information: hints, log lines, row
#: numbers and disabled states.  Measured ratios on the darkest background:
#: text 15.9:1, text_muted 8.9:1, text_faint 5.6:1.
DARK = Palette(
    bg="#16171b",
    surface="#1d1f24",
    surface_alt="#23262c",
    elevated="#2b2f37",
    border="#2f333b",
    border_strong="#464c57",
    text="#e9ebee",
    text_muted="#aeb6c2",
    text_faint="#8d95a0",
    control="#2b2f37",
    control_hover="#343945",
    control_pressed="#3f4553",
    control_disabled="#1f2126",
    disabled_ink="#6c737e",
    accent="#3572d8",
    accent_hover="#3f7dea",
    accent_active="#2b5fbb",
    accent_text="#ffffff",
    success="#4ac26b",
    warning="#e0a33c",
    error="#f2685f",
    info="#58a6ff",
    selection="#2b3f61",
    row_alt="#1a1c20",
    row_hover="#262a31",
    scrollbar="#5b6472",
    scrollbar_hover="#79838f",
    scrollbar_track="#101114",
)


def mix(colour_a: str, colour_b: str, ratio: float) -> str:
    """Blend two ``#rrggbb`` colours; ``ratio`` 0 gives A, 1 gives B."""
    ratio = max(0.0, min(1.0, ratio))
    a = tuple(int(colour_a[i : i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(colour_b[i : i + 2], 16) for i in (1, 3, 5))
    blended = tuple(round(x + (y - x) * ratio) for x, y in zip(a, b))
    return "#{:02x}{:02x}{:02x}".format(*blended)


@dataclass(frozen=True)
class Fonts:
    """Resolved font tuples, chosen from what the system actually has."""

    ui: tuple
    ui_bold: tuple
    small: tuple
    title: tuple
    subtitle: tuple
    section: tuple
    button: tuple
    mono: tuple

    @property
    def family(self) -> str:
        return self.ui[0]


def _first_available(candidates: list[str], fallback: str) -> str:
    try:
        available = set(tkfont.families())
    except tk.TclError:  # pragma: no cover - no display
        return fallback
    for name in candidates:
        if name in available:
            return name
    return fallback


def build_fonts(scale: float = 1.0) -> Fonts:
    """Pick UI and monospace families and build the type scale."""

    def size(points: int) -> int:
        return max(7, round(points * scale))

    ui = _first_available(
        ["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", "Noto Sans CJK SC"],
        "TkDefaultFont",
    )
    mono = _first_available(
        ["Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New"], "TkFixedFont"
    )
    return Fonts(
        ui=(ui, size(10)),
        ui_bold=(ui, size(10), "bold"),
        small=(ui, size(9)),
        title=(ui, size(15), "bold"),
        subtitle=(ui, size(9)),
        section=(ui, size(10), "bold"),
        button=(ui, size(10)),
        mono=(mono, size(9)),
    )


def apply_theme(root: tk.Misc, palette: Palette, fonts: Fonts) -> ttk.Style:
    """Configure every ttk widget class for the dark palette."""
    style = ttk.Style(root)

    # 'clam' is the only built-in theme that honours background colours on all
    # the widget classes we use, so the dark scheme is layered on top of it.
    if "clam" in style.theme_names():
        style.theme_use("clam")

    root.configure({"background": palette.bg})

    style.configure(".", background=palette.bg, foreground=palette.text, font=fonts.ui)

    style.configure("TFrame", background=palette.bg)
    style.configure("Card.TFrame", background=palette.surface)
    style.configure("Bar.TFrame", background=palette.surface)

    style.configure("TLabel", background=palette.bg, foreground=palette.text)
    style.configure("Card.TLabel", background=palette.surface, foreground=palette.text)
    style.configure(
        "Section.TLabel",
        background=palette.surface,
        foreground=palette.text,
        font=fonts.section,
    )
    style.configure(
        "Hint.TLabel",
        background=palette.surface,
        foreground=palette.text_faint,
        font=fonts.small,
    )
    style.configure(
        "Muted.TLabel",
        background=palette.surface,
        foreground=palette.text_muted,
        font=fonts.small,
    )
    style.configure(
        "Title.TLabel",
        background=palette.bg,
        foreground=palette.text,
        font=fonts.title,
    )
    style.configure(
        "Subtitle.TLabel",
        background=palette.bg,
        foreground=palette.text_muted,
        font=fonts.subtitle,
    )
    style.configure(
        "Status.TLabel", background=palette.surface, foreground=palette.text_muted
    )

    # --- entries -------------------------------------------------------- #
    for name in ("TEntry", "TSpinbox"):
        style.configure(
            name,
            fieldbackground=palette.surface_alt,
            background=palette.surface_alt,
            foreground=palette.text,
            insertcolor=palette.text,
            bordercolor=palette.border,
            lightcolor=palette.border,
            darkcolor=palette.border,
            arrowcolor=palette.text_muted,
            relief="flat",
            padding=4,
        )
        style.map(
            name,
            fieldbackground=[("disabled", palette.surface), ("focus", palette.surface_alt)],
            foreground=[("disabled", palette.text_faint)],
            bordercolor=[("focus", palette.accent)],
            lightcolor=[("focus", palette.accent)],
            darkcolor=[("focus", palette.accent)],
            # A selection highlight that matches the field is invisible; the
            # app also clears the selection outright after a combo is picked.
            selectbackground=[("!disabled", palette.surface_alt)],
            selectforeground=[("!disabled", palette.text)],
        )

    # --- combobox ------------------------------------------------------- #
    style.configure(
        "TCombobox",
        fieldbackground=palette.surface_alt,
        background=palette.surface_alt,
        foreground=palette.text,
        arrowcolor=palette.text_muted,
        bordercolor=palette.border,
        lightcolor=palette.border,
        darkcolor=palette.border,
        relief="flat",
        padding=4,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", palette.surface_alt), ("disabled", palette.surface)],
        foreground=[("disabled", palette.text_faint)],
        bordercolor=[("focus", palette.accent)],
        arrowcolor=[("active", palette.text)],
        selectbackground=[("!disabled", palette.surface_alt)],
        selectforeground=[("!disabled", palette.text)],
    )
    root.option_add("*Entry.selectBackground", palette.surface_alt)
    root.option_add("*Entry.selectForeground", palette.text)
    # The dropdown list is a Tk (not ttk) widget and needs its own options.
    root.option_add("*TCombobox*Listbox.background", palette.elevated)
    root.option_add("*TCombobox*Listbox.foreground", palette.text)
    root.option_add("*TCombobox*Listbox.selectBackground", palette.accent)
    root.option_add("*TCombobox*Listbox.selectForeground", palette.accent_text)
    root.option_add("*TCombobox*Listbox.font", fonts.ui)

    for name, parent_bg in (
        ("TRadiobutton", palette.bg),
        ("Card.TRadiobutton", palette.surface),
    ):
        style.configure(
            name,
            background=parent_bg,
            foreground=palette.text,
            focuscolor=parent_bg,
            indicatorcolor=palette.surface_alt,
            indicatorrelief="flat",
            bordercolor=palette.border_strong,
            lightcolor=palette.surface_alt,
            darkcolor=palette.surface_alt,
            padding=2,
        )
        style.map(
            name,
            background=[("active", parent_bg)],
            foreground=[("disabled", palette.text_faint)],
            indicatorcolor=[
                ("selected", palette.accent),
                ("pressed", palette.accent_active),
                ("disabled", palette.surface),
            ],
            bordercolor=[("selected", palette.accent), ("focus", palette.accent)],
        )

    # --- scrollbars ----------------------------------------------------- #
    # Everything the app builds uses CanvasScrollbar (a Canvas cannot be left
    # unstyled the way a classic scrollbar can).  These ttk rules are a safety
    # net for any ttk widget that grows one, plus the classic option-database
    # entries below, which are what the Combobox dropdown list actually reads.
    for orient in ("Vertical", "Horizontal"):
        style.configure(
            f"{orient}.TScrollbar",
            background=palette.scrollbar,
            troughcolor=palette.surface_alt,
            bordercolor=palette.surface_alt,
            arrowcolor=palette.text_muted,
            relief="flat",
            width=11,
        )
        style.map(
            f"{orient}.TScrollbar",
            background=[("active", palette.scrollbar_hover), ("pressed", palette.accent)],
            arrowcolor=[("active", palette.text)],
        )

    # --- progress bar --------------------------------------------------- #
    style.configure(
        "Accent.Horizontal.TProgressbar",
        background=palette.accent,
        troughcolor=palette.surface_alt,
        bordercolor=palette.border,
        lightcolor=palette.accent,
        darkcolor=palette.accent,
        thickness=8,
    )

    style.configure("TSeparator", background=palette.border)

    # --- text widget defaults ------------------------------------------- #
    # Classic Tk widgets never consult ttk styles, so the dropdown list and any
    # stray tk.Scrollbar have to be themed through the option database.
    root.option_add("*Scrollbar.background", palette.scrollbar)
    root.option_add("*Scrollbar.troughColor", palette.scrollbar_track)
    root.option_add("*Scrollbar.activeBackground", palette.scrollbar_hover)
    root.option_add("*Scrollbar.highlightBackground", palette.surface_alt)
    root.option_add("*Scrollbar.highlightColor", palette.surface_alt)
    root.option_add("*Scrollbar.borderWidth", 0)
    root.option_add("*Scrollbar.relief", "flat")
    root.option_add("*Scrollbar.width", 11)

    root.option_add("*Text.background", palette.surface_alt)
    root.option_add("*Text.foreground", palette.text)
    root.option_add("*Text.insertBackground", palette.text)
    root.option_add("*Text.selectBackground", palette.selection)
    root.option_add("*Text.selectForeground", palette.text)
    root.option_add("*Text.relief", "flat")
    root.option_add("*Text.borderWidth", 0)

    return style
