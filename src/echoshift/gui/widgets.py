"""Hand-drawn widgets: rounded buttons, tooltips, cards, collapsible sections.

ttk gives no way to round a widget or to colour a button per state beyond the
theme's defaults, so the pieces where that matters are Canvas widgets here.
They take the palette and fonts explicitly rather than reading globals, which
keeps them testable and makes a future light theme a data change.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont
from typing import Callable, Sequence

from .icons import draw_icon
from .metrics import GAP_MD, GAP_SM, GAP_XS
from .theme import Fonts, Palette, mix

__all__ = [
    "rounded_rect",
    "RoundedButton",
    "IconButton",
    "Tooltip",
    "Card",
    "CollapsibleSection",
    "CheckBox",
    "ProgressBar",
    "SegmentedControl",
    "NumberStepper",
    "CanvasScrollbar",
    "ScrollableFrame",
    "attach_tooltip",
]


def rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: float,
    **kwargs,
) -> int:
    """Draw a rounded rectangle.

    Tk has no primitive for this; a polygon with duplicated corner points and
    ``smooth=True`` renders as one, and unlike the arc-based approach it fills
    and outlines as a single item.
    """
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, splinesteps=12, **kwargs)


class Tooltip:
    """A small dark popup shown while the pointer rests on a widget."""

    _open: "Tooltip | None" = None

    def __init__(
        self,
        widget: tk.Misc,
        text: str,
        *,
        palette: Palette,
        fonts: Fonts,
        delay: int = 450,
        wraplength: int = 380,
    ) -> None:
        self.widget = widget
        self.text = text
        self.palette = palette
        self.fonts = fonts
        self.delay = delay
        self.wraplength = wraplength
        self._after: str | None = None
        self._window: tk.Toplevel | None = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<FocusIn>", self._schedule, add="+")
        widget.bind("<FocusOut>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        widget.bind("<Escape>", self._hide, add="+")
        widget.bind("<Destroy>", self._hide, add="+")

    # -- internals ------------------------------------------------------- #

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except (tk.TclError, ValueError):
                pass
            self._after = None

    def _show(self) -> None:
        if self._window is not None or not self.text:
            return
        if Tooltip._open is not None:
            Tooltip._open._hide()
        try:
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 8
        except tk.TclError:
            return

        window = tk.Toplevel(self.widget)
        window.wm_overrideredirect(True)
        window.wm_geometry(f"+{x}+{y}")
        window.configure(background=self.palette.border_strong)
        try:
            window.attributes("-topmost", True)
        except tk.TclError:
            pass

        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            background=self.palette.elevated,
            foreground=self.palette.text,
            font=self.fonts.small,
            wraplength=self.wraplength,
            padx=10,
            pady=7,
        )
        label.pack(padx=1, pady=1)
        window.update_idletasks()
        screen_w = window.winfo_screenwidth()
        screen_h = window.winfo_screenheight()
        width = window.winfo_reqwidth()
        height = window.winfo_reqheight()
        x = max(4, min(x, screen_w - width - 4))
        y = max(4, min(y, screen_h - height - 4))
        window.wm_geometry(f"+{x}+{y}")
        self._window = window
        Tooltip._open = self

    def _hide(self, _event: object = None) -> None:
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None
        if Tooltip._open is self:
            Tooltip._open = None

    def update_text(self, text: str) -> None:
        self.text = text


def attach_tooltip(
    widget: tk.Misc, text: str, *, palette: Palette, fonts: Fonts, wraplength: int = 380
) -> Tooltip:
    """Convenience wrapper returning the :class:`Tooltip` it created."""
    return Tooltip(widget, text, palette=palette, fonts=fonts, wraplength=wraplength)


class RoundedButton(tk.Canvas):
    """A flat, rounded, icon-capable button with hover and press feedback."""

    _KINDS = ("primary", "secondary", "ghost", "danger")

    def __init__(
        self,
        master: tk.Misc,
        text: str = "",
        *,
        icon: str | None = None,
        command: Callable[[], None] | None = None,
        kind: str = "secondary",
        palette: Palette,
        fonts: Fonts,
        width: int | None = None,
        height: int = 32,
        radius: int = 7,
        padx: int = 14,
        parent_bg: str | None = None,
        tooltip: str | None = None,
    ) -> None:
        self.palette = palette
        self.fonts = fonts
        self.kind = kind if kind in self._KINDS else "secondary"
        self.radius = radius
        self.icon = icon
        self.text = text
        self.command = command
        self._enabled = True
        self._hover = False
        self._pressed = False
        self._focused = False
        self._busy = False
        self._tip: Tooltip | None = None
        self._tooltip_text = tooltip or ""

        self._font = tkfont.Font(font=fonts.button)
        text_width = self._font.measure(text) if text else 0
        natural = text_width + padx * 2 + (18 if icon else 0)
        canvas_width = width if width is not None else natural
        background = parent_bg or palette.bg

        super().__init__(
            master,
            width=canvas_width,
            height=height,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            takefocus=1,
        )
        self._width = canvas_width
        self._height = height

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Key-space>", lambda _e: self.invoke())
        self.bind("<Key-Return>", lambda _e: self.invoke())
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)

        if tooltip:
            self._tip = attach_tooltip(self, tooltip, palette=palette, fonts=fonts)
        self._render()

    # -- appearance ------------------------------------------------------ #

    def _colours(self) -> tuple[str, str, str]:
        """Return ``(fill, outline, text)`` for the current state."""
        p = self.palette
        if not self._enabled:
            return (p.control_disabled, p.border, p.disabled_ink)

        if self.kind == "primary":
            fill = p.accent_active if self._pressed else (p.accent_hover if self._hover else p.accent)
            return (fill, fill, p.accent_text)
        if self.kind == "danger":
            fill = mix(p.error, p.bg, 0.78) if not self._pressed else mix(p.error, p.bg, 0.6)
            if self._hover and not self._pressed:
                fill = mix(p.error, p.bg, 0.7)
            return (fill, mix(p.error, p.bg, 0.35), p.error)
        if self.kind == "ghost":
            fill = p.control_pressed if self._pressed else (
                p.control_hover if self._hover else p.control
            )
            return (fill, fill, p.text if self._hover else p.text_muted)

        # Every secondary state is visibly different: resting, hover and press
        # each step the fill up and the outline brighter.  Sharing one fill for
        # resting and hover (the previous behaviour) left the toolbar buttons
        # with no feedback at all until they were clicked.
        if self._pressed:
            fill, outline = p.control_pressed, p.border_strong
        elif self._hover:
            fill, outline = p.control_hover, p.border_strong
        else:
            fill, outline = p.control, p.border
        return (fill, outline, p.text)

    def _render(self) -> None:
        self.delete("all")
        fill, outline, ink = self._colours()
        rounded_rect(
            self, 1, 1, self._width - 1, self._height - 1, self.radius,
            fill=fill, outline=outline, width=1,
        )
        if self._focused:
            rounded_rect(
                self, 2, 2, self._width - 2, self._height - 2,
                max(1, self.radius - 1), fill="", outline=self.palette.accent,
                width=2, tags=("focus-ring",),
            )

        content_width = (18 if self.icon else 0) + (
            self._font.measure(self.text) if self.text else 0
        )
        start = (self._width - content_width) / 2
        centre_y = self._height / 2

        if self.icon:
            draw_icon(self, self.icon, start + 8, centre_y, 15, ink)
            start += 18
        if self.text:
            self.create_text(
                start, centre_y + 1, text=self.text, anchor="w", fill=ink, font=self.fonts.button
            )

    # -- interaction ----------------------------------------------------- #

    def _on_enter(self, _event: object) -> None:
        self._hover = True
        if self._enabled and not self._busy:
            self.configure(cursor="hand2")
        self._render()

    def _on_leave(self, _event: object) -> None:
        self._hover = False
        self._pressed = False
        self.configure(cursor="")
        self._render()

    def _on_press(self, _event: object) -> None:
        if not self._enabled or self._busy:
            return
        self.focus_set()
        self._pressed = True
        self._render()

    def _on_release(self, _event: object) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._render()
        if was_pressed and self._enabled and not self._busy and self.command is not None:
            self.command()

    # -- API ------------------------------------------------------------- #

    def invoke(self) -> None:
        if self._enabled and not self._busy and self.command is not None:
            self.command()

    def set_busy(self, busy: bool, *, text: str | None = None) -> None:
        """Mark the button as working: it stops accepting clicks.

        ``_start`` does real work on the Tk thread before its worker exists
        (form validation, reading the key database, repainting the queue), and
        the button looked idle throughout, so a second click repeated all of it.
        """
        self._busy = bool(busy)
        if not self._busy:
            self._pressed = False
        if text is not None:
            self.set_text(text)
        self.configure(cursor="hand2" if self._enabled and not self._busy and self._hover else "")
        self._render()

    @property
    def busy(self) -> bool:
        return self._busy

    def set_enabled(self, enabled: bool, *, reason: str | None = None) -> None:
        """Enable or disable, optionally explaining why it is disabled.

        A greyed-out button with no explanation is a dead end: the tooltip is
        the one place a user can find out what would make it usable again.
        """
        self._enabled = bool(enabled)
        self.configure(
            cursor="hand2" if self._enabled and not self._busy and self._hover else ""
        )
        self.configure(takefocus=1 if self._enabled else 0)
        if self._tip is not None:
            if self._enabled:
                self._tip.update_text(self._tooltip_text)
            elif reason:
                self._tip.update_text(reason)
        self._render()

    def set_enabled_locked(self, reason: str | None = None) -> None:
        """Disable, remembering whether it *was* enabled.

        The app locks whole groups of controls for the duration of a batch and
        has to hand each one back in the state it found it: "移除选中" is only
        enabled while something is selected, and restoring it unconditionally
        would offer an action that does nothing.
        """
        self._enabled_before_lock = self._enabled
        self.set_enabled(False, reason=reason)

    def restore_enabled(self) -> None:
        previous = getattr(self, "_enabled_before_lock", None)
        if previous is None:
            return
        self.set_enabled(previous)

    def _on_focus_in(self, _event: object) -> None:
        self._focused = True
        self._render()

    def _on_focus_out(self, _event: object) -> None:
        self._focused = False
        self._render()

    def set_text(self, text: str) -> None:
        self.text = text
        self._render()

    def set_kind(self, kind: str) -> None:
        if kind in self._KINDS:
            self.kind = kind
            self._render()

    @property
    def enabled(self) -> bool:
        return self._enabled


class IconButton(RoundedButton):
    """A square, icon-only button (used for the collapsible headers)."""

    def __init__(
        self,
        master: tk.Misc,
        icon: str,
        *,
        size: int = 26,
        tooltip: str | None = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("kind", "ghost")
        super().__init__(
            master, "", icon=icon, width=size, height=size, radius=6,
            padx=0, tooltip=tooltip, **kwargs,
        )


class Card(tk.Frame):
    """A flat panel with a hairline border, used for every settings group."""

    def __init__(self, master: tk.Misc, *, palette: Palette, **kwargs) -> None:
        super().__init__(
            master,
            background=palette.surface,
            highlightbackground=palette.border,
            highlightcolor=palette.border,
            highlightthickness=1,
            borderwidth=0,
            **kwargs,
        )
        self.palette = palette


class CollapsibleSection(Card):
    """A card whose body can be folded away, leaving only its header."""

    #: Border width used both at rest and while focused, so taking focus never
    #: changes the card's size.
    _RING = 2

    def __init__(
        self,
        master: tk.Misc,
        title: str,
        *,
        palette: Palette,
        fonts: Fonts,
        expanded: bool = True,
        on_toggle: Callable[[bool], None] | None = None,
        **kwargs,
    ) -> None:
        kwargs.setdefault("takefocus", 1)
        super().__init__(master, palette=palette, **kwargs)
        self.fonts = fonts
        self.expanded = expanded
        self.on_toggle = on_toggle
        self._hovered = False
        self._focused = False

        self.columnconfigure(0, weight=1)

        header = tk.Frame(self, background=palette.surface, cursor="hand2")
        header.grid(row=0, column=0, sticky="ew", padx=GAP_XS, pady=(GAP_SM, 1))

        self.chevron = tk.Canvas(
            header, width=20, height=20, background=palette.surface,
            highlightthickness=0, borderwidth=0,
        )
        self.chevron.pack(side="left", padx=(8, 0))
        self.title_label = tk.Label(
            header, text=title, background=palette.surface, foreground=palette.text,
            font=fonts.section, anchor="w",
        )
        self.title_label.pack(side="left", padx=(2, 0))

        self.body = tk.Frame(self, background=palette.surface)
        self.body.grid(row=1, column=0, sticky="nsew", padx=GAP_MD, pady=(0, GAP_MD))
        self.rowconfigure(1, weight=1)

        # The focus ring is the full border width from the start, and only its
        # colour changes on focus.  Growing highlightthickness from 1 to 2 on
        # focus added 2 px to the card's requested size, which re-laid out the
        # whole settings column -- visible as a jump every time a control inside
        # the card took focus.
        self.configure(highlightthickness=self._RING)

        self._header_bg = palette.surface
        for widget in (header, self.chevron, self.title_label):
            widget.bind("<Button-1>", lambda _e: self.toggle())
            widget.bind("<Button-1>", self._focus_from_child, add="+")
            widget.bind("<Enter>", self._on_enter, add="+")
            widget.bind("<Leave>", self._on_leave, add="+")
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Key-space>", lambda _e: self._toggle_from_key())
        self.bind("<Key-Return>", lambda _e: self._toggle_from_key())
        self._render_chevron()

    def _paint_header(self, background: str) -> None:
        self._header_bg = background
        for widget in (self.chevron.master, self.chevron, self.title_label):
            widget.configure(background=background)

    def _on_enter(self, _event: object) -> None:
        # The whole title row is clickable, so it has to look clickable.
        self._hovered = True
        if not self._focused:
            self._paint_header(self.palette.row_hover)

    def _on_leave(self, _event: object) -> None:
        self._hovered = False
        if not self._focused:
            self._paint_header(self.palette.surface)

    def _focus_from_child(self, _event: object) -> None:
        self.focus_set()

    def _toggle_from_key(self) -> str:
        self.toggle()
        return "break"

    def _on_focus_in(self, _event: object) -> None:
        self._focused = True
        self.configure(
            highlightbackground=self.palette.accent,
            highlightcolor=self.palette.accent,
        )

    def _on_focus_out(self, _event: object) -> None:
        self._focused = False
        self.configure(
            highlightbackground=self.palette.border,
            highlightcolor=self.palette.border,
        )
        if not self._hovered:
            self._paint_header(self.palette.surface)

    def _render_chevron(self) -> None:
        self.chevron.delete("all")
        draw_icon(
            self.chevron,
            "chevron-down" if self.expanded else "chevron-right",
            10, 10, 14, self.palette.text_muted,
        )

    def toggle(self) -> None:
        self.set_expanded(not self.expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.expanded = bool(expanded)
        if self.expanded:
            self.body.grid()
        else:
            self.body.grid_remove()
        self._render_chevron()
        if self.on_toggle is not None:
            self.on_toggle(self.expanded)


class ProgressBar(tk.Canvas):
    """A rounded progress bar.

    ttk's Progressbar cannot be rounded and its trough colour is unreliable on
    Windows, so the status bar draws its own to match the queue rows.
    """

    def __init__(
        self, master: tk.Misc, *, palette: Palette, height: int = 8, **kwargs
    ) -> None:
        super().__init__(
            master, height=height, background=kwargs.pop("parent_bg", palette.surface),
            highlightthickness=0, borderwidth=0, **kwargs,
        )
        self.palette = palette
        self.bar_height = height
        self.fraction = 0.0
        self.bind("<Configure>", lambda _e: self._render())

    def set(self, fraction: float) -> None:
        self.fraction = max(0.0, min(1.0, fraction))
        self._render()

    def _render(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        radius = self.bar_height / 2
        rounded_rect(
            self, 0, 0, width, self.bar_height, radius,
            fill=mix(self.palette.surface, self.palette.border_strong, 0.45), outline="",
        )
        if self.fraction > 0:
            filled = max(self.bar_height, width * self.fraction)
            rounded_rect(
                self, 0, 0, filled, self.bar_height, radius,
                fill=self.palette.accent, outline="",
            )


class SegmentedControl(tk.Canvas):
    """A compact, keyboard-accessible choice between a few related modes."""

    def __init__(
        self,
        master: tk.Misc,
        choices: Sequence[tuple[str, str]],
        variable: tk.StringVar,
        *,
        palette: Palette,
        fonts: Fonts,
        command: Callable[[], None] | None = None,
        height: int = 32,
        parent_bg: str | None = None,
        **kwargs,
    ) -> None:
        self.choices = list(choices)
        self.variable = variable
        self.palette = palette
        self.fonts = fonts
        self.command = command
        self._enabled = True
        self._height = height
        self._focus_index = 0
        self._hover_index: int | None = None
        self._pressed_index: int | None = None

        super().__init__(
            master,
            height=height,
            background=parent_bg or palette.surface,
            highlightthickness=0,
            borderwidth=0,
            takefocus=1,
            **kwargs,
        )
        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<Button-1>", self._on_click)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Key-Left>", lambda _e: self._move(-1))
        self.bind("<Key-Right>", lambda _e: self._move(1))
        self.bind("<Key-space>", lambda _e: self._select(self._focus_index))
        self.bind("<FocusIn>", lambda _e: self._render())
        self.bind("<FocusOut>", lambda _e: self._render())
        self._trace = variable.trace_add("write", lambda *_: self._render())
        self._sync_focus_index()
        self._render()

    def _sync_focus_index(self) -> None:
        current = self.variable.get()
        self._focus_index = next(
            (index for index, (value, _label) in enumerate(self.choices) if value == current),
            0,
        )

    def _index_at(self, x: int) -> int:
        width = max(1, self.winfo_width())
        return min(len(self.choices) - 1, max(0, int(x / width * len(self.choices))))

    def _on_click(self, event: tk.Event) -> None:
        if not self._enabled or not self.choices:
            return
        self.focus_set()
        self._pressed_index = self._index_at(event.x)
        self._select(self._pressed_index)

    def _on_release(self, _event: object) -> None:
        if self._pressed_index is None:
            return
        self._pressed_index = None
        self._render()

    def _on_motion(self, event: tk.Event) -> None:
        if not self._enabled or not self.choices:
            return
        index = self._index_at(event.x)
        if index != self._hover_index:
            self._hover_index = index
            self.configure(cursor="hand2")
            self._render()

    def _on_leave(self, _event: object) -> None:
        if self._hover_index is None and self._pressed_index is None:
            return
        self._hover_index = None
        self._pressed_index = None
        self.configure(cursor="")
        self._render()

    def _move(self, delta: int) -> None:
        if not self._enabled or not self.choices:
            return
        self._sync_focus_index()
        self._select((self._focus_index + delta) % len(self.choices))

    def _select(self, index: int) -> None:
        if not (0 <= index < len(self.choices)):
            return
        self._focus_index = index
        value = self.choices[index][0]
        changed = self.variable.get() != value
        self.variable.set(value)
        if changed and self.command is not None:
            self.command()
        self._render()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if not self._enabled:
            self._hover_index = None
            self._pressed_index = None
        self.configure(
            cursor="hand2" if (self._enabled and self._hover_index is not None) else "",
            takefocus=1 if self._enabled else 0,
        )
        self._render()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _render(self) -> None:
        self.delete("all")
        if not self.choices:
            return
        palette = self.palette
        width = max(2, self.winfo_width())
        height = self._height
        segment = width / len(self.choices)
        selected = self.variable.get()
        rounded_rect(
            self, 1, 1, width - 1, height - 1, 6,
            fill=palette.surface_alt, outline=palette.border, width=1,
        )
        for index, (value, label) in enumerate(self.choices):
            x1, x2 = index * segment, (index + 1) * segment
            active = value == selected
            if active:
                fill = palette.accent if self._enabled else palette.border
                if self._enabled and self._pressed_index == index:
                    fill = palette.accent_active
                rounded_rect(
                    self, x1 + 2, 3, x2 - 2, height - 3, 5,
                    fill=fill, outline="",
                )
            else:
                # A hover fill on the segment under the pointer, so the choice
                # can be judged before committing to it.
                if self._enabled and self._pressed_index == index:
                    rounded_rect(
                        self, x1 + 2, 3, x2 - 2, height - 3, 5,
                        fill=palette.control_pressed, outline="",
                    )
                elif self._enabled and self._hover_index == index:
                    rounded_rect(
                        self, x1 + 2, 3, x2 - 2, height - 3, 5,
                        fill=palette.control_hover, outline="",
                    )
                if index:
                    self.create_line(x1, 7, x1, height - 7, fill=palette.border)
            colour = (
                palette.accent_text if active and self._enabled
                else palette.disabled_ink if not self._enabled
                else palette.text_muted
            )
            self.create_text(
                (x1 + x2) / 2, height / 2 + 1,
                text=label, fill=colour,
                font=self.fonts.ui_bold if active else self.fonts.ui,
            )
        if self.focus_get() is self:
            self.create_rectangle(2, 2, width - 2, height - 2, outline=palette.accent, width=1)


class NumberStepper(tk.Frame):
    """A stable minus/value/plus control for small bounded integers."""

    def __init__(
        self,
        master: tk.Misc,
        variable: tk.IntVar,
        *,
        minimum: int,
        maximum: int,
        palette: Palette,
        fonts: Fonts,
        command: Callable[[], None] | None = None,
        parent_bg: str | None = None,
    ) -> None:
        background = parent_bg or palette.surface
        super().__init__(master, background=background)
        self.variable = variable
        self.minimum = minimum
        self.maximum = maximum
        self.command = command
        self._enabled = True
        self.minus = RoundedButton(
            self, "-", width=28, height=28, padx=0, kind="secondary",
            command=lambda: self._step(-1), palette=palette, fonts=fonts,
            parent_bg=background,
        )
        self.minus.grid(row=0, column=0)
        self.value_label = tk.Label(
            self, textvariable=variable, width=3,
            background=palette.surface_alt, foreground=palette.text,
            font=fonts.mono, anchor="center", padx=2,
        )
        self.value_label.grid(row=0, column=1, padx=4, sticky="ns")
        self.plus = RoundedButton(
            self, "+", width=28, height=28, padx=0, kind="secondary",
            command=lambda: self._step(1), palette=palette, fonts=fonts,
            parent_bg=background,
        )
        self.plus.grid(row=0, column=2)
        self._trace = variable.trace_add("write", lambda *_: self._clamp())
        self._clamp()

    def _clamp(self) -> None:
        try:
            value = int(self.variable.get())
        except (tk.TclError, ValueError):
            value = self.minimum
        clamped = max(self.minimum, min(self.maximum, value))
        if clamped != value:
            self.variable.set(clamped)
            return
        self.minus.set_enabled(self._enabled and clamped > self.minimum)
        self.plus.set_enabled(self._enabled and clamped < self.maximum)

    def _step(self, delta: int) -> None:
        if not self._enabled:
            return
        self._clamp()
        self.variable.set(max(self.minimum, min(self.maximum, self.variable.get() + delta)))
        if self.command is not None:
            self.command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        self.minus.set_enabled(self._enabled and self.variable.get() > self.minimum)
        self.plus.set_enabled(self._enabled and self.variable.get() < self.maximum)

    @property
    def enabled(self) -> bool:
        return self._enabled


class CheckBox(tk.Canvas):
    """A checkbox that reads the way Windows users expect.

    ttk's clam indicator draws a cross for "on", which most people read as
    "off".  This draws a filled accent box with a white tick instead.
    """

    BOX = 17
    GAP = 8

    def __init__(
        self,
        master: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        *,
        palette: Palette,
        fonts: Fonts,
        command: Callable[[], None] | None = None,
        parent_bg: str | None = None,
        tooltip: str | None = None,
        **kwargs,
    ) -> None:
        self.palette = palette
        self.fonts = fonts
        self.variable = variable
        self.command = command
        self.text = text
        self._hover = False
        self._enabled = True
        self._focused = False

        self._font = tkfont.Font(font=fonts.ui)
        width = self.BOX + self.GAP + self._font.measure(text) + 2
        background = parent_bg or palette.surface

        super().__init__(
            master,
            width=width,
            height=self.BOX + 4,
            background=background,
            highlightthickness=0,
            borderwidth=0,
            takefocus=1,
            **kwargs,
        )

        self.bind("<Button-1>", self._on_click)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Key-space>", lambda _e: self.toggle())
        self.bind("<Key-Return>", lambda _e: self.toggle())
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self._trace = variable.trace_add("write", lambda *_: self._render())

        if tooltip:
            attach_tooltip(self, tooltip, palette=palette, fonts=fonts)
        self._render()

    # -- state ----------------------------------------------------------- #

    @property
    def checked(self) -> bool:
        return bool(self.variable.get())

    def toggle(self) -> None:
        if not self._enabled:
            return
        self.variable.set(not self.variable.get())
        if self.command is not None:
            self.command()

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        # A disabled control must not sit in the Tab order: it would take focus
        # and then swallow Space with no visible response.
        self.configure(takefocus=1 if self._enabled else 0)
        if not self._enabled:
            self._hover = False
            self.configure(cursor="")
        self._render()

    # -- painting -------------------------------------------------------- #

    def _render(self) -> None:
        self.delete("all")
        palette = self.palette
        checked = self.checked
        top = 2
        box = self.BOX

        if not self._enabled:
            fill, outline, ink = palette.control_disabled, palette.border, palette.disabled_ink
        elif checked:
            fill = palette.accent_hover if self._hover else palette.accent
            outline, ink = fill, palette.accent_text
        else:
            fill = palette.control_hover if self._hover else palette.control_disabled
            outline = palette.border_strong if not self._hover else palette.accent
            ink = palette.text

        rounded_rect(self, 1, top, 1 + box, top + box, 4, fill=fill, outline=outline, width=1)
        if self._focused:
            rounded_rect(
                self, 0, 1, self.winfo_reqwidth() - 1, self.BOX + 3, 4,
                fill="", outline=palette.accent, width=2, tags=("focus-ring",),
            )
        if checked:
            draw_icon(self, "check", 1 + box / 2, top + box / 2, box - 2, ink, width=2.2)

        self.create_text(
            1 + box + self.GAP - 1,
            top + box / 2,
            text=self.text,
            anchor="w",
            fill=ink if self._enabled else palette.text_faint,
            font=self.fonts.ui,
        )

    # -- interaction ------------------------------------------------------ #

    def _on_click(self, _event: object) -> None:
        if not self._enabled:
            return
        self.focus_set()
        self.toggle()

    def _on_enter(self, _event: object) -> None:
        self._hover = True
        if self._enabled:
            self.configure(cursor="hand2")
        self._render()

    def _on_leave(self, _event: object) -> None:
        self._hover = False
        self.configure(cursor="")
        self._render()

    def _on_focus_in(self, _event: object) -> None:
        self._focused = True
        self._render()

    def _on_focus_out(self, _event: object) -> None:
        self._focused = False
        self._render()


class CanvasScrollbar(tk.Canvas):
    """A thin, theme-matched scrollbar.

    The ttk scrollbar's trough and arrows ignore most colour options on Windows,
    so panels and lists draw their own.  It hides itself when the content fits.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        palette: Palette,
        command: Callable[[float], None] | None = None,
        thickness: int = 10,
        trough: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            master, width=thickness, background=trough or palette.bg,
            highlightthickness=0, borderwidth=0, **kwargs,
        )
        self.palette = palette
        self.thickness = thickness
        self.trough = trough or palette.bg
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._hover = False
        self._dragging = False
        self._grab_offset = 0.0

        self.bind("<Configure>", lambda _e: self._render())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # -- API ------------------------------------------------------------- #

    def set(self, first: str | float, last: str | float) -> None:
        self._first = float(first)
        self._last = float(last)
        self._render()

    # -- internals ------------------------------------------------------- #

    def _thumb_span(self) -> tuple[float, float]:
        height = max(1, self.winfo_height())
        top = self._first * height
        bottom = max(top + 32, self._last * height)
        return top, min(bottom, height - 2)

    def _render(self) -> None:
        self.delete("all")
        span = self._last - self._first
        height = max(1, self.winfo_height())
        if span >= 0.999:
            return  # nothing to scroll -- stay invisible

        # A faint full-height rail, so it is obvious the column scrolls even
        # before the pointer is anywhere near the bar.
        centre = self.thickness / 2
        rounded_rect(
            self, centre - 1.5, 2, centre + 1.5, height - 2, 1.5,
            fill=self.palette.scrollbar_track, outline="",
        )

        top, bottom = self._thumb_span()
        # Holding the thumb is an explicit "I am dragging this" state, so it
        # keeps the highlight even while the pointer wanders off the bar.
        colour = (
            self.palette.scrollbar_hover
            if (self._hover or self._dragging)
            else self.palette.scrollbar
        )
        radius = (self.thickness - 4) / 2
        rounded_rect(
            self, 2, top, self.thickness - 2, bottom,
            radius, fill=colour, outline="",
        )

    def _scroll_to(self, y: float) -> None:
        if self._command is None:
            return
        height = max(1, self.winfo_height())
        self._command(max(0.0, min(1.0, y / height)))

    def _on_press(self, event: tk.Event) -> None:
        top, bottom = self._thumb_span()
        if top <= event.y <= bottom:
            # Grabbing the thumb drags it relative to where it was grabbed, so
            # it does not jump under the pointer.
            self._dragging = True
            self._grab_offset = event.y - top
            self._render()
            return
        # Clicking the trough keeps the original jump-to-position behaviour.
        self._dragging = True
        self._grab_offset = (bottom - top) / 2
        self._scroll_to(event.y)

    def _on_drag(self, event: tk.Event) -> None:
        if not self._dragging:
            return
        self._scroll_to(event.y - self._grab_offset)

    def _on_release(self, _event: tk.Event) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._render()

    def _on_enter(self, _event: tk.Event) -> None:
        self._hover = True
        self._render()

    def _on_leave(self, _event: tk.Event) -> None:
        self._hover = False
        self._render()


class ScrollableFrame(tk.Frame):
    """A vertically scrollable container.

    The settings column can be taller than the window on a small screen, and a
    clipped settings panel is worse than a scrollbar.  The inner frame is kept
    at the width of the viewport so children can use ``sticky="ew"``.
    """

    def __init__(self, master: tk.Misc, *, palette: Palette, width: int = 360, **kwargs) -> None:
        super().__init__(master, background=palette.bg, **kwargs)
        self.palette = palette

        self.canvas = tk.Canvas(
            self, background=palette.bg, highlightthickness=0, borderwidth=0, width=width
        )
        self.canvas.pack(side="left", fill="both", expand=True)

        self.scrollbar = CanvasScrollbar(
            self, palette=palette, command=self.canvas.yview_moveto,
            thickness=13, trough=palette.bg,
        )
        self.scrollbar.pack(side="right", fill="y")

        self.inner = tk.Frame(self.canvas, background=palette.bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        # Without this the bar never learns the visible range and stays blank.
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.inner.bind("<MouseWheel>", self._on_wheel)

    def _on_inner_configure(self, _event: object) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 * (event.delta // 120 or 1), "units")
