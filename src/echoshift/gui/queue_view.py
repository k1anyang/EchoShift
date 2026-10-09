"""The queue list, drawn on a Canvas.

``ttk.Treeview`` cannot host a progress bar in a cell, cannot colour a single
cell, and gives no control over row hover, so the list is painted directly.
Rows are virtualised -- only the visible slice is drawn -- so a queue of a few
thousand files still scrolls smoothly.
"""

from __future__ import annotations

import tkinter as tk
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import font as tkfont
from typing import Callable, Sequence

from ..core.pipeline import JobState
from .icons import draw_icon
from .text import CharWidths, ellipsize
from .theme import Fonts, Palette, mix
from .widgets import CanvasScrollbar, rounded_rect

__all__ = ["QueueRow", "QueueView"]


@dataclass
class QueueRow:
    """One visible row of the queue."""

    filename: str = ""
    source: str = ""
    state: JobState = JobState.PENDING
    progress: float = 0.0
    message: str = ""
    output: Path | None = None
    path: Path | None = None
    detail: str = field(default="", repr=False)

#: (key, heading, weight, minimum width).  Weight 0 means a fixed-width column.
#: Weights are tuned so the two free-text columns that actually get clipped --
#: the file name and the source description -- win the space.
_COLUMNS: tuple[tuple[str, str, float, int], ...] = (
    ("index", "#", 0, 36),
    ("file", "文件", 2.1, 170),
    ("source", "来源", 2.9, 190),
    ("state", "状态", 0, 82),
    ("progress", "进度", 0, 116),
    ("message", "结果", 2.0, 120),
)

_STATE_COLOURS: dict[JobState, str] = {
    JobState.PENDING: "text_faint",
    JobState.DECRYPTING: "accent",
    JobState.ENCODING: "accent",
    JobState.VERIFYING: "info",
    JobState.DONE: "success",
    JobState.FAILED: "error",
    JobState.SKIPPED: "warning",
    JobState.CANCELLED: "text_muted",
}

_STATE_ICONS: dict[JobState, str] = {
    JobState.DONE: "check",
    JobState.FAILED: "cross",
    JobState.SKIPPED: "dash",
    JobState.CANCELLED: "dash",
}


class QueueView(tk.Frame):
    """A scrollable, selectable, hover-aware list of :class:`QueueRow`."""

    #: Fallbacks for callers that read the geometry before a view exists.  A
    #: real instance derives both from the font metrics (see :meth:`_measure`).
    ROW_HEIGHT = 32
    HEADER_HEIGHT = 30
    _PAD = 10
    #: Repaints are coalesced so progress bursts cannot monopolise Tk.
    _REDRAW_INTERVAL_MS = 33

    def __init__(
        self,
        master: tk.Misc,
        *,
        palette: Palette,
        fonts: Fonts,
        on_activate: Callable[[int], None] | None = None,
        on_selection_change: Callable[[Sequence[int]], None] | None = None,
        on_delete: Callable[[], None] | None = None,
        on_copy_paths: Callable[[int], None] | None = None,
        empty_hint: str = "把文件或文件夹拖到这里，或点上面的「添加」",
    ) -> None:
        super().__init__(master, background=palette.surface, highlightthickness=0)

        self.palette = palette
        self.fonts = fonts
        self.on_activate = on_activate
        self.on_selection_change = on_selection_change
        self.on_delete = on_delete
        self.on_copy_paths = on_copy_paths
        self.empty_hint = empty_hint

        self._rows: list[QueueRow] = []
        self._selected: set[int] = set()
        self._anchor: int | None = None
        self._focus_index: int | None = None
        self._hover: int | None = None
        self._layout: list[tuple[str, int, int]] = []
        self._width = 0
        self._widths = CharWidths()
        self._redraw_job: str | None = None

        self._font = tkfont.Font(font=fonts.ui)
        self._font_bold = tkfont.Font(font=fonts.ui_bold)
        self._font_small = tkfont.Font(font=fonts.small)
        self._font_mono = tkfont.Font(font=fonts.mono)
        self._measure()

        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self.header = tk.Canvas(
            self, height=self.header_height, background=palette.surface,
            highlightthickness=0, borderwidth=0,
        )
        self.header.grid(row=0, column=0, sticky="ew")

        self.canvas = tk.Canvas(
            self, background=palette.surface, highlightthickness=0, borderwidth=0,
            takefocus=1,
        )
        self.canvas.grid(row=1, column=0, sticky="nsew")

        self.scrollbar = CanvasScrollbar(
            self, palette=palette, command=self._scroll_to,
            thickness=10, trough=palette.surface,
        )
        self.scrollbar.grid(row=1, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.bind("<Configure>", self._on_resize)
        self.header.bind("<Configure>", self._on_resize)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Leave>", self._on_leave)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Delete>", self._on_delete_key)
        self.canvas.bind("<Control-a>", self._on_select_all)
        self.canvas.bind("<Escape>", self._on_escape)
        self.canvas.bind("<Up>", lambda _e: self._move_focus(-1))
        self.canvas.bind("<Down>", lambda _e: self._move_focus(1))
        self.canvas.bind("<Shift-Up>", lambda _e: self._move_focus(-1, extend=True))
        self.canvas.bind("<Shift-Down>", lambda _e: self._move_focus(1, extend=True))
        self.canvas.bind("<Home>", lambda _e: self._move_focus_to(0))
        self.canvas.bind("<End>", lambda _e: self._move_focus_to(len(self._rows) - 1))
        self.canvas.bind("<space>", self._on_space)
        self.canvas.bind("<Control-Key-space>", self._on_control_space)
        self.canvas.bind("<Return>", self._on_enter)
        self.canvas.bind("<Shift-F10>", self._on_context_key)
        self.canvas.bind("<FocusIn>", lambda _e: self._schedule_redraw())
        self.canvas.bind("<FocusOut>", lambda _e: self._schedule_redraw())

    # ------------------------------------------------------------------ #
    # data
    # ------------------------------------------------------------------ #

    def set_rows(self, rows: Sequence[QueueRow]) -> None:
        self._rows = list(rows)
        self._selected = {i for i in self._selected if 0 <= i < len(self._rows)}
        if self._focus_index is not None and self._focus_index >= len(self._rows):
            self._focus_index = len(self._rows) - 1 if self._rows else None
        if self._anchor is not None and self._anchor >= len(self._rows):
            self._anchor = self._focus_index
        self._hover = None
        self._redraw()

    def update_row(self, index: int, row: QueueRow) -> None:
        if 0 <= index < len(self._rows):
            self._rows[index] = row
            self._redraw_row(index)

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def row(self, index: int) -> QueueRow | None:
        return self._rows[index] if 0 <= index < len(self._rows) else None

    # ------------------------------------------------------------------ #
    # selection
    # ------------------------------------------------------------------ #

    def selection(self) -> list[int]:
        return sorted(self._selected)

    def clear_selection(self) -> None:
        if self._selected:
            self._selected.clear()
            self._redraw()
            self._notify_selection()

    def select_all(self) -> None:
        self._selected = set(range(len(self._rows)))
        self._redraw()
        self._notify_selection()

    def select_indices(self, indices: Sequence[int]) -> None:
        """Select valid row indices and give keyboard focus to the first one."""
        selected = sorted({int(index) for index in indices if 0 <= int(index) < len(self._rows)})
        self._selected = set(selected)
        self._focus_index = selected[0] if selected else None
        self._anchor = self._focus_index
        self._redraw()
        self._notify_selection()

    def _notify_selection(self) -> None:
        if self.on_selection_change is not None:
            self.on_selection_change(self.selection())

    # ------------------------------------------------------------------ #
    # geometry
    # ------------------------------------------------------------------ #

    def _measure(self) -> None:
        """Derive the row geometry from the fonts actually in use.

        Fixed pixel heights were tuned at 100% scaling: the state pill is 20 px
        tall and the small font asks for ~21 px, so a 32 px row left 11 px for
        everything else and looked cramped at 125%/150% DPI.  The progress column
        also has to fit "100%" plus a gap before the bar starts.
        """
        line = self._font_small.metrics("linespace")
        self.row_height = max(30, line + 16)
        self.header_height = max(28, line + 12)
        # bar width -> right-aligned "100%" -> a gap, all inside the column.
        self.progress_text_width = self._font_mono.measure("100%")
        # bar + a 12 px gap + the right-aligned percentage.
        self.progress_column_width = max(96, self.progress_text_width + 12 + 30 + 12)

    def _compute_layout(self, width: int) -> list[tuple[str, int, int]]:
        """Assign each column an x and a width that together fit ``width``.

        Flexible columns get a share of whatever is left after the fixed ones.
        When even the minimums do not fit, every column is scaled down and the
        widest is trimmed, so the row can never spill past the right edge.
        """
        width = max(180, width)
        available = max(0, width - self._PAD * 2)

        # The progress column has to hold the bar plus its right-aligned "100%",
        # and that depends on the mono font in use rather than on a fixed 116.
        minimums = {
            key: (self.progress_column_width if key == "progress" else minimum)
            for key, _heading, _weight, minimum in _COLUMNS
        }

        fixed_total = sum(minimums[k] for k, _h, w, _m in _COLUMNS if w == 0)
        flexible = [(k, w, m) for k, _h, w, m in _COLUMNS if w > 0]
        flex_space = max(0, available - fixed_total)
        weight_total = sum(w for _k, w, _m in flexible) or 1.0

        widths: dict[str, int] = {}
        for key, _heading, weight, minimum in _COLUMNS:
            if weight == 0:
                widths[key] = minimums[key]
            else:
                widths[key] = max(minimums[key], int(flex_space * (weight / weight_total)))

        total = sum(widths.values())
        if total > available:
            factor = available / total
            for key in widths:
                widths[key] = max(28, int(widths[key] * factor))
            overflow = sum(widths.values()) - available
            while overflow > 0:
                widest = max(widths, key=lambda k: widths[k])
                if widths[widest] <= 28:
                    break
                cut = min(overflow, widths[widest] - 28)
                widths[widest] -= cut
                overflow -= cut

        layout: list[tuple[str, int, int]] = []
        cursor = self._PAD
        for key, _heading, _weight, _minimum in _COLUMNS:
            layout.append((key, cursor, widths[key]))
            cursor += widths[key]
        return layout
    def _column(self, key: str) -> tuple[int, int]:
        for name, x, width in self._layout:
            if name == key:
                return x, width
        return 0, 0

    def _on_resize(self, event: tk.Event) -> None:
        if event.width == self._width and self._layout:
            return
        self._width = max(event.width, 320)
        self._layout = self._compute_layout(self._width)
        # Deliberately NOT calling configure(width=...) here: grid already sizes
        # both canvases, and setting it re-fires <Configure>, which used to
        # ping-pong between the header and the body during a resize drag.
        self._draw_header()
        self._schedule_redraw()

    # ------------------------------------------------------------------ #
    # painting
    # ------------------------------------------------------------------ #

    #: Widths are measured through :mod:`echoshift.gui.text`; the tests call
    #: ``_text_width`` / ``_ellipsize`` directly, so they stay as thin wrappers.
    def _text_width(self, text: str, font: tkfont.Font, tag: str = "ui") -> int:
        return self._widths.width(text, font, tag)

    def _ellipsize(
        self, text: str, max_px: int, font: tkfont.Font, tag: str = "ui"
    ) -> str:
        return ellipsize(text, max_px, font, tag, cache=self._widths)

    def _draw_header(self) -> None:
        self.header.delete("all")
        if not self._layout:
            return
        height = self.header_height
        self.header.create_rectangle(
            0, 0, self._width, height, fill=self.palette.surface, outline=""
        )
        left_aligned = ("file", "source", "message")
        for key, x, width in self._layout:
            heading = next(h for k, h, _w, _m in _COLUMNS if k == key)
            if key in left_aligned:
                self.header.create_text(
                    x, height / 2, text=heading, anchor="w",
                    fill=self.palette.text_muted, font=self.fonts.small,
                )
            else:
                self.header.create_text(
                    x + width / 2, height / 2, text=heading, anchor="center",
                    fill=self.palette.text_muted, font=self.fonts.small,
                )
        self.header.create_line(
            0, height - 1, self._width, height - 1, fill=self.palette.border
        )

    def _visible_range(self) -> tuple[int, int]:
        if not self._rows:
            return 0, 0
        top = self.canvas.canvasy(0)
        height = self.canvas.winfo_height()
        first = max(0, int(top // self.row_height))
        last = min(len(self._rows), int((top + height) // self.row_height) + 2)
        return first, max(first, last)

    def _total_height(self) -> int:
        return max(1, len(self._rows) * self.row_height)

    def _scroll_to(self, fraction: float) -> None:
        self.canvas.yview_moveto(max(0.0, min(1.0, fraction)))
        self._redraw()

    def _sync_scrollbar(self) -> None:
        height = max(1, self.canvas.winfo_height())
        total = self._total_height()
        if total <= height:
            self.scrollbar.set(0.0, 1.0)
            return
        top = max(0.0, self.canvas.canvasy(0))
        self.scrollbar.set(top / total, min(1.0, (top + height) / total))

    def _on_resize_scroll(self, _event: object = None) -> None:
        self._sync_scrollbar()

    def _redraw(self) -> None:
        self.canvas.delete("all")
        if not self._layout:
            self._layout = self._compute_layout(self._width or 800)

        total = self._total_height()
        # Tk *centres* a scrollregion that is shorter than the viewport, which
        # would float the rows in the middle of an empty card.  Padding the
        # region out to the full height keeps them anchored at the top.
        height = max(total, self.canvas.winfo_height(), 1)
        self.canvas.configure(scrollregion=(0, 0, self._width, height))
        self._sync_scrollbar()

        if not self._rows:
            self.canvas.create_text(
                self._width / 2, 60, text=self.empty_hint,
                fill=self.palette.text_faint, font=self.fonts.ui, anchor="center",
            )
            return

        first, last = self._visible_range()
        for index in range(first, last):
            self._draw_row(index)

    def _redraw_row(self, index: int) -> None:
        first, last = self._visible_range()
        if first <= index < last:
            # Repainting one row means erasing it first; the simplest correct
            # way is to repaint the visible slice, coalesced so a burst of
            # progress events costs one repaint rather than dozens.
            self._schedule_redraw()

    def _schedule_redraw(self) -> None:
        """Coalesce repaint requests to at most one per frame."""
        if self._redraw_job is None:
            self._redraw_job = self.after(self._REDRAW_INTERVAL_MS, self._flush_redraw)

    def _flush_redraw(self) -> None:
        self._redraw_job = None
        self._redraw()

    def _row_background(self, index: int) -> str:
        palette = self.palette
        if index in self._selected:
            return palette.selection
        if index == self._hover:
            return palette.row_hover
        return palette.surface if index % 2 == 0 else palette.row_alt

    def _draw_row(self, index: int) -> None:
        row = self._rows[index]
        y = index * self.row_height
        palette = self.palette
        state_colour = getattr(palette, _STATE_COLOURS.get(row.state, "text_faint"))
        background = self._row_background(index)

        self.canvas.create_rectangle(
            0, y, self._width, y + self.row_height, fill=background, outline=""
        )

        # A hairline under every row that is not already separated by striping.
        if index % 2 == 0 and index not in self._selected and index != self._hover:
            self.canvas.create_line(
                0, y + self.row_height - 1, self._width, y + self.row_height - 1,
                fill=mix(palette.surface, palette.border, 0.45),
            )

        centre = y + self.row_height / 2

        x, width = self._column("index")
        self.canvas.create_text(
            x + width / 2, centre, text=str(index + 1), anchor="center",
            fill=palette.text_faint, font=self.fonts.small,
        )

        x, width = self._column("file")
        self.canvas.create_text(
            x, centre, text=self._ellipsize(row.filename, width - 8, self._font, "ui"),
            anchor="w", fill=palette.text, font=self.fonts.ui,
        )

        x, width = self._column("source")
        self.canvas.create_text(
            x, centre, text=self._ellipsize(row.source, width - 8, self._font_small, "sm"),
            anchor="w", fill=palette.text_muted, font=self.fonts.small,
        )

        x, width = self._column("state")
        self._draw_state_pill(x, width, centre, row, state_colour)

        x, width = self._column("progress")
        self._draw_progress(x, width, centre, row, state_colour)

        x, width = self._column("message")
        message_colour = state_colour if row.state in (
            JobState.FAILED, JobState.DONE, JobState.SKIPPED
        ) else palette.text_muted
        self.canvas.create_text(
            x, centre, text=self._ellipsize(row.message, width - 8, self._font_small, "sm"),
            anchor="w", fill=message_colour, font=self.fonts.small,
        )

        if (
            index == self._focus_index
            and self.canvas.focus_get() is self.canvas
        ):
            self.canvas.create_rectangle(
                1, y + 1, self._width - 1, y + self.row_height - 1,
                outline=palette.accent, width=2, tags=("focus-ring",),
            )

    def _draw_state_pill(
        self, x: int, width: int, centre: float, row: QueueRow, colour: str
    ) -> None:
        label = row.state.label
        font = self._font_small
        icon = _STATE_ICONS.get(row.state)
        text_width = self._text_width(label, font, "sm")
        pill_width = min(width - 4, text_width + (30 if icon else 20))
        left = x + (width - pill_width) / 2
        fill = mix(self.palette.surface, colour, 0.16)

        rounded_rect(
            self.canvas, left, centre - 10, left + pill_width, centre + 10, 10,
            fill=fill, outline=mix(self.palette.surface, colour, 0.34), width=1,
        )
        text_left = left + 9
        if icon:
            draw_icon(self.canvas, icon, left + 12, centre, 12, colour, width=1.8)
            text_left = left + 22
        self.canvas.create_text(
            text_left, centre, text=label, anchor="w", fill=colour, font=self.fonts.small
        )

    def _draw_progress(
        self, x: int, width: int, centre: float, row: QueueRow, colour: str
    ) -> None:
        # The percentage is right-aligned just inside the column, so the bar has
        # to stop well before it.  Ending it flush against the next column made
        # "100%" read as the start of the message ("100%3.2s · 校验通过").
        right_margin = 8
        bar_width = max(30, width - self.progress_text_width - right_margin - 12)
        bar_x = x + 2
        bar_y = centre - 3.5
        bar_height = 7

        rounded_rect(
            self.canvas, bar_x, bar_y, bar_x + bar_width, bar_y + bar_height, 3.5,
            fill=mix(self.palette.surface, self.palette.border_strong, 0.45), outline="",
        )
        fraction = max(0.0, min(1.0, row.progress))
        if fraction > 0:
            filled = max(bar_height, bar_width * fraction)
            rounded_rect(
                self.canvas, bar_x, bar_y, bar_x + filled, bar_y + bar_height, 3.5,
                fill=colour, outline="",
            )
        self.canvas.create_text(
            x + width - right_margin, centre, text=f"{fraction * 100:.0f}%", anchor="e",
            fill=self.palette.text_muted, font=self.fonts.mono,
        )

    # ------------------------------------------------------------------ #
    # interaction
    # ------------------------------------------------------------------ #

    def _index_at(self, event: tk.Event) -> int | None:
        if not self._rows:
            return None
        y = self.canvas.canvasy(event.y)
        index = int(y // self.row_height)
        if 0 <= index < len(self._rows):
            return index
        return None

    def _on_wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 * (event.delta // 120 or 1), "units")
        self._redraw()

    def _on_motion(self, event: tk.Event) -> None:
        index = self._index_at(event)
        if index != self._hover:
            self._hover = index
            self.canvas.configure(cursor="hand2" if index is not None else "")
            self._schedule_redraw()

    def _on_leave(self, _event: tk.Event) -> None:
        if self._hover is not None:
            self._hover = None
            self.canvas.configure(cursor="")
            self._schedule_redraw()

    def _on_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        index = self._index_at(event)
        if index is None:
            self.clear_selection()
            return
        self._focus_index = index
        modifiers = int(event.state)
        ctrl = bool(modifiers & 0x0004)
        shift = bool(modifiers & 0x0001)
        if ctrl:
            self._selected.symmetric_difference_update({index})
            self._anchor = index
        elif shift and self._anchor is not None:
            low, high = sorted((self._anchor, index))
            self._selected = set(range(low, high + 1))
        else:
            self._selected = {index}
            self._anchor = index
        self._redraw()
        self._notify_selection()

    def _on_double_click(self, event: tk.Event) -> None:
        index = self._index_at(event)
        if index is not None and self.on_activate is not None:
            self.on_activate(index)

    def _on_right_click(self, event: tk.Event) -> None:
        self.canvas.focus_set()
        index = self._index_at(event)
        if index is None:
            return
        if index not in self._selected:
            self._selected = {index}
            self._anchor = index
            self._notify_selection()
        self._focus_index = index
        self._schedule_redraw()
        self._show_context_menu(event)

    def _show_context_menu(self, event: tk.Event) -> None:
        self._show_context_menu_at(event.x_root, event.y_root)

    def _show_context_menu_at(self, x_root: int, y_root: int) -> None:
        menu = tk.Menu(
            self, tearoff=0,
            background=self.palette.elevated, foreground=self.palette.text,
            activebackground=self.palette.accent, activeforeground=self.palette.accent_text,
            borderwidth=0, font=self.fonts.ui,
        )
        menu.add_command(label="打开文件位置", command=lambda: self._activate_selected())
        menu.add_command(label="复制完整路径", command=self._copy_selected_paths)
        menu.add_separator()
        menu.add_command(
            label="从队列移除", command=lambda: self.on_delete() if self.on_delete else None
        )
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _activate_selected(self) -> None:
        if self.on_activate is not None and self._selected:
            self.on_activate(min(self._selected))

    def _copy_selected_paths(self) -> None:
        paths = [
            str(self._rows[i].path or self._rows[i].filename)
            for i in sorted(self._selected)
            if 0 <= i < len(self._rows)
        ]
        if not paths:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(paths))
        if self.on_copy_paths is not None:
            self.on_copy_paths(len(paths))

    def _on_delete_key(self, _event: tk.Event) -> None:
        if self._selected and self.on_delete is not None:
            self.on_delete()
        return "break"

    def _on_select_all(self, _event: tk.Event) -> None:
        self.select_all()
        return "break"

    def _on_escape(self, _event: tk.Event) -> str:
        self.clear_selection()
        return "break"

    def _ensure_focus_index(self) -> int | None:
        if not self._rows:
            self._focus_index = None
            return None
        if self._focus_index is None:
            self._focus_index = self._anchor if self._anchor is not None else 0
        self._focus_index = max(0, min(len(self._rows) - 1, self._focus_index))
        return self._focus_index

    def _move_focus(self, delta: int, *, extend: bool = False) -> str:
        if not self._rows:
            return "break"
        if self._focus_index is None:
            target = 0 if delta >= 0 else len(self._rows) - 1
        else:
            target = self._focus_index + delta
        self._move_focus_to(target, extend=extend)
        return "break"

    def _move_focus_to(self, index: int, *, extend: bool = False) -> None:
        if not self._rows:
            return
        index = max(0, min(len(self._rows) - 1, index))
        if extend:
            anchor = self._anchor if self._anchor is not None else self._ensure_focus_index()
            if anchor is not None:
                low, high = sorted((anchor, index))
                self._selected = set(range(low, high + 1))
        self._focus_index = index
        if not extend:
            self._anchor = index
        self.canvas.focus_set()
        y0 = index * self.row_height
        y1 = y0 + self.row_height
        top = self.canvas.canvasy(0)
        bottom = top + max(1, self.canvas.winfo_height())
        if y0 < top:
            self.canvas.yview_moveto(y0 / max(1, self._total_height()))
        elif y1 > bottom:
            self.canvas.yview_moveto((y1 - self.canvas.winfo_height()) / max(1, self._total_height()))
        self._redraw()
        if extend:
            self._notify_selection()

    def _on_space(self, _event: tk.Event) -> str:
        index = self._ensure_focus_index()
        if index is not None:
            self._selected = {index}
            self._anchor = index
            self._redraw()
            self._notify_selection()
        return "break"

    def _on_control_space(self, _event: tk.Event) -> str:
        index = self._ensure_focus_index()
        if index is not None:
            self._selected.symmetric_difference_update({index})
            self._anchor = index
            self._redraw()
            self._notify_selection()
        return "break"

    def _on_enter(self, _event: tk.Event) -> str:
        index = self._ensure_focus_index()
        if index is not None and self.on_activate is not None:
            self.on_activate(index)
        return "break"

    def _on_context_key(self, _event: tk.Event) -> str:
        index = self._ensure_focus_index()
        if index is None:
            return "break"
        if index not in self._selected:
            self._selected = {index}
            self._anchor = index
            self._notify_selection()
        x = self.canvas.winfo_rootx() + 20
        y = self.canvas.winfo_rooty() + (index * self.row_height - int(self.canvas.canvasy(0))) + self.row_height
        self._show_context_menu_at(x, y)
        return "break"
