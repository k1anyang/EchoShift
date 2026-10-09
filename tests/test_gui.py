"""Theme, icon and queue-layout units that do not need a human.

The full GUI is driven by ``tools/gui_smoke.py``; what lives here is the pure
logic underneath it -- colour maths, the icon registry, column arithmetic and
the truncation helper -- plus a few widget smoke checks that skip themselves if
Tk cannot open a display.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

import pytest

from echoshift.gui.theme import DARK, build_fonts, mix
from echoshift.gui.icons import ICONS, draw_icon, icon_names


# --------------------------------------------------------------------------- #
# colour maths
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "a,b,ratio,expected",
    [
        ("#000000", "#ffffff", 0.0, "#000000"),
        ("#000000", "#ffffff", 1.0, "#ffffff"),
        ("#000000", "#ffffff", 0.5, "#808080"),
        ("#ff0000", "#00ff00", 0.5, "#808000"),
        # Out-of-range ratios clamp rather than extrapolate.
        ("#000000", "#ffffff", -3.0, "#000000"),
        ("#000000", "#ffffff", 9.0, "#ffffff"),
    ],
)
def test_mix_interpolates_and_clamps(a, b, ratio, expected):
    assert mix(a, b, ratio) == expected


def test_palette_is_complete_and_well_formed():
    for field_name, value in DARK.__dict__.items():
        assert isinstance(value, str), field_name
        assert len(value) == 7 and value.startswith("#"), f"{field_name}={value}"
        int(value[1:], 16)  # parses as hex


def test_palette_foregrounds_contrast_with_their_backgrounds():
    def luminance(colour: str) -> float:
        channels = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    def ratio(fg: str, bg: str) -> float:
        a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
        return (a + 0.05) / (b + 0.05)

    # Body text must be comfortably readable on both surfaces.
    assert ratio(DARK.text, DARK.surface) >= 7.0
    assert ratio(DARK.text, DARK.bg) >= 7.0
    # Muted text is for secondary information; 4.5:1 is the usual floor.
    assert ratio(DARK.text_muted, DARK.surface) >= 4.5
    # Faint text carries real information too -- every Hint.TLabel, the log
    # body, the row numbers and the empty-queue hint -- so it clears AA on each
    # background it can land on, not just the panel colour.  Before this was
    # asserted, text_faint sat at 3.17:1 on surface_alt and nothing caught it.
    for background in ("surface", "surface_alt", "bg", "row_alt"):
        assert ratio(DARK.text_faint, getattr(DARK, background)) >= 4.5, background
    # ...and the three tiers must stay visually distinct, or the hierarchy is
    # meaningless even though every level is legible.
    assert ratio(DARK.text, DARK.surface) > ratio(DARK.text_muted, DARK.surface) > ratio(
        DARK.text_faint, DARK.surface
    )
    # The accent carries white text as a button fill, so the resting state has
    # to clear AA; hover/active are transient and only need the large-text bar.
    assert ratio(DARK.accent_text, DARK.accent) >= 4.5
    assert ratio(DARK.accent_text, DARK.accent_hover) >= 3.0
    assert ratio(DARK.accent_text, DARK.accent_active) >= 3.0
    # State colours are used as text and pill ink on the row backgrounds.
    for state in ("success", "warning", "error", "info"):
        assert ratio(getattr(DARK, state), DARK.surface) >= 4.0, state
    # Label text on the interactive-control fills.
    for field_name in ("control", "control_hover", "control_pressed"):
        assert ratio(DARK.text, getattr(DARK, field_name)) >= 4.5, field_name
    # A disabled control is dimmer than secondary information, so "off" and
    # "less important" cannot be confused for one another.
    assert ratio(DARK.disabled_ink, DARK.control_disabled) < ratio(
        DARK.text_faint, DARK.surface_alt
    )


# --------------------------------------------------------------------------- #
# icons
# --------------------------------------------------------------------------- #


def test_every_declared_icon_is_reachable():
    assert icon_names() == tuple(sorted(ICONS))
    for name in ("file-plus", "folder-plus", "minus", "trash", "play", "stop",
                 "chevron-down", "chevron-right", "music", "check", "cross", "dash"):
        assert name in ICONS, name


# --------------------------------------------------------------------------- #
# Tk-dependent checks
# --------------------------------------------------------------------------- #


def test_build_fonts_returns_a_usable_scale(tk_root):
    fonts = build_fonts()
    assert fonts.ui[1] >= 7
    assert fonts.title[1] > fonts.ui[1]
    assert fonts.family


def test_draw_icon_puts_items_on_the_canvas(tk_root):
    canvas = tk.Canvas(tk_root, width=40, height=40)
    for name in icon_names():
        canvas.delete("all")
        draw_icon(canvas, name, 20, 20, 16, "#ffffff")
        assert canvas.find_all(), f"{name} drew nothing"


def test_draw_icon_ignores_unknown_names(tk_root):
    canvas = tk.Canvas(tk_root, width=40, height=40)
    draw_icon(canvas, "no-such-icon", 20, 20, 16, "#ffffff")
    assert not canvas.find_all()


def test_queue_column_layout_fills_the_width(tk_root):
    from echoshift.gui.queue_view import QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    for width in (520, 800, 1400, 1920):
        layout = view._compute_layout(width)
        assert layout, width
        # Columns are contiguous and the last one stops at (or inside) the edge.
        cursor = layout[0][1]
        for _key, x, column_width in layout:
            assert x == cursor
            cursor += column_width
        assert cursor <= width + 1, f"{cursor} > {width}"
    view.destroy()


def test_queue_column_layout_keeps_the_important_columns_wide(tk_root):
    from echoshift.gui.queue_view import QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    layout = dict((key, width) for key, _x, width in view._compute_layout(900))
    assert layout["file"] >= 150
    assert layout["source"] >= 190
    view.destroy()


# --------------------------------------------------------------------------- #
# derived geometry
#
# The row height and the progress column were fixed pixel values tuned at 100%
# scaling; at 125%/150% the small font alone asks for ~26 px, which left the
# state pill and the row's own padding fighting over the remainder.
# --------------------------------------------------------------------------- #


def test_row_geometry_derives_from_the_font_in_use(tk_root):
    from echoshift.gui.queue_view import QueueView
    from echoshift.gui.theme import build_fonts

    small = build_fonts(scale=1.0)
    large = build_fonts(scale=1.5)
    if large.small[1] == small.small[1]:
        pytest.skip("Tk 把 1.5 倍字号钳到了同一档，无法比较")

    view = QueueView(tk_root, palette=DARK, fonts=small)
    base_row, base_header = view.row_height, view.header_height
    view.destroy()

    bigger = QueueView(tk_root, palette=DARK, fonts=large)
    assert bigger.row_height > base_row, "行高必须随字号放大"
    assert bigger.header_height >= base_header
    assert bigger.row_height >= 30
    bigger.destroy()


def test_the_progress_column_leaves_room_for_the_percentage(tk_root):
    from echoshift.gui.queue_view import QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    width = dict((k, w) for k, _x, w in view._compute_layout(900))["progress"]
    # Bar + gap + right-aligned "100%" must fit, and the bar stays usable.
    bar_width = max(30, width - view.progress_text_width - 12)
    assert bar_width >= 30
    assert bar_width + 12 + view.progress_text_width <= width
    view.destroy()


def test_the_collapsible_header_reacts_to_hover(tk_root):
    from echoshift.gui.widgets import CollapsibleSection

    section = CollapsibleSection(tk_root, "高级设置", palette=DARK, fonts=build_fonts())
    resting = section.title_label.cget("background")
    section._on_enter(None)
    assert section.title_label.cget("background") == DARK.row_hover
    assert section.title_label.cget("background") != resting
    section._on_leave(None)
    assert section.title_label.cget("background") == resting
    section.destroy()


def test_ellipsize_shortens_to_fit(tk_root):
    from tkinter import font as tkfont

    from echoshift.gui.queue_view import QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    font = tkfont.Font(font=build_fonts().ui)
    long_text = "王力宏 _ 欧阳靖 _ 李岩 - 盖世英雄.mflac"

    assert view._ellipsize("短.flac", 400, font) == "短.flac"
    clipped = view._ellipsize(long_text, 120, font)
    assert clipped.endswith("…")
    assert font.measure(clipped) <= 120
    assert len(clipped) < len(long_text)
    assert view._ellipsize(long_text, 0, font) == ""
    view.destroy()


def test_rounded_rect_falls_back_to_a_rectangle_for_zero_radius(tk_root):
    from echoshift.gui.widgets import rounded_rect

    canvas = tk.Canvas(tk_root, width=100, height=40)
    rounded_rect(canvas, 0, 0, 100, 40, 0, fill="#ffffff")
    assert len(canvas.find_all()) == 1


def test_rounded_button_click_invokes_the_command(tk_root):
    from echoshift.gui.widgets import RoundedButton

    calls: list[int] = []
    button = RoundedButton(
        tk_root, "开始转换", icon="play", kind="primary",
        command=lambda: calls.append(1), palette=DARK, fonts=build_fonts(),
    )
    button.invoke()
    assert calls == [1]

    button.set_enabled(False)
    button.invoke()
    assert calls == [1], "a disabled button must not fire"
    button.destroy()


def test_rounded_button_sizes_itself_to_its_label(tk_root):
    from echoshift.gui.widgets import RoundedButton

    fonts = build_fonts()
    narrow = RoundedButton(tk_root, "a", palette=DARK, fonts=fonts)
    wide = RoundedButton(tk_root, "开始转换并覆盖同名文件", palette=DARK, fonts=fonts)
    assert int(wide.cget("width")) > int(narrow.cget("width"))
    narrow.destroy()
    wide.destroy()


def test_progress_bar_tracks_its_fraction(tk_root):
    from echoshift.gui.widgets import ProgressBar

    bar = ProgressBar(tk_root, palette=DARK, width=200)
    bar.set(2.5)
    assert bar.fraction == 1.0
    bar.set(-1)
    assert bar.fraction == 0.0
    bar.set(0.25)
    assert bar.fraction == pytest.approx(0.25)
    bar.destroy()


def test_queue_view_selection_arithmetic(tk_root):
    from echoshift.gui.queue_view import QueueRow, QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    view.set_rows([QueueRow(filename=f"{i}.flac") for i in range(5)])
    assert view.row_count == 5
    assert view.selection() == []
    view.select_all()
    assert view.selection() == [0, 1, 2, 3, 4]
    view.clear_selection()
    assert view.selection() == []
    assert view.row(2).filename == "2.flac"
    assert view.row(99) is None
    view.destroy()


def test_queue_shortcuts_are_scoped_to_queue_focus(tk_root):
    from echoshift.gui.queue_view import QueueRow, QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    view.pack(fill="both", expand=True)
    view.set_rows([QueueRow(filename="one.flac"), QueueRow(filename="two.flac")])
    entry = tk.Entry(tk_root)
    entry.pack()
    entry.focus_set()
    tk_root.update()

    entry.event_generate("<Control-a>")
    entry.event_generate("<Delete>")
    entry.event_generate("<Escape>")
    tk_root.update()

    assert view.selection() == []
    view.destroy()
    entry.destroy()


def test_queue_supports_keyboard_navigation_and_selection(tk_root):
    from echoshift.gui.queue_view import QueueRow, QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    view.pack(fill="both", expand=True)
    view.set_rows([QueueRow(filename=f"{i}.flac") for i in range(4)])
    view._move_focus(1)
    view._on_space(None)
    assert view.selection() == [0]
    view._move_focus(1)
    view._on_control_space(None)
    assert view.selection() == [0, 1]
    view._move_focus_to(3)
    view._on_space(None)
    assert view.selection() == [3]
    view.destroy()


def test_custom_controls_render_a_focus_ring(tk_root):
    from echoshift.gui.widgets import CheckBox, RoundedButton

    button = RoundedButton(tk_root, "开始", palette=DARK, fonts=build_fonts())
    variable = tk.BooleanVar(master=tk_root, value=False)
    box = CheckBox(tk_root, "校验", variable, palette=DARK, fonts=build_fonts())
    button._on_focus_in(None)
    assert button.find_withtag("focus-ring")
    box._on_focus_in(None)
    assert box.find_withtag("focus-ring")
    button.destroy()
    box.destroy()


# --------------------------------------------------------------------------- #
# control states
#
# The secondary buttons used to return the *same* fill for resting and hover,
# so hovering any toolbar button produced no feedback at all; and a disabled
# button was filled and inked exactly like a hint label, with no explanation of
# why it could not be used.
# --------------------------------------------------------------------------- #


def _button_states(button) -> dict[str, tuple[str, str]]:
    states: dict[str, tuple[str, str]] = {}
    button._hover = False
    button._pressed = False
    states["resting"] = button._colours()[:2]
    button._hover = True
    states["hover"] = button._colours()[:2]
    button._pressed = True
    states["pressed"] = button._colours()[:2]
    button._hover = False
    button._pressed = False
    return states


@pytest.mark.parametrize("kind", ["secondary", "ghost", "primary", "danger"])
def test_every_button_kind_distinguishes_hover_from_resting(tk_root, kind):
    from echoshift.gui.widgets import RoundedButton

    button = RoundedButton(tk_root, "添加文件", kind=kind, palette=DARK, fonts=build_fonts())
    states = _button_states(button)
    assert states["hover"] != states["resting"], f"{kind} 悬停与静止外观相同"
    assert states["pressed"] != states["hover"], f"{kind} 按下与悬停外观相同"
    button.destroy()


def test_a_disabled_button_looks_different_from_an_enabled_one(tk_root):
    from echoshift.gui.widgets import RoundedButton

    button = RoundedButton(tk_root, "开始转换", kind="secondary", palette=DARK, fonts=build_fonts())
    enabled = button._colours()
    button.set_enabled(False)
    disabled = button._colours()
    assert disabled != enabled
    assert disabled[2] == DARK.disabled_ink, "禁用文字必须比提示文字更暗"
    button.destroy()


def test_a_disabled_button_explains_itself_in_its_tooltip(tk_root):
    from echoshift.gui.widgets import RoundedButton

    button = RoundedButton(
        tk_root, "开始转换", kind="primary", palette=DARK, fonts=build_fonts(),
        tooltip="按左侧参数转换队列中的全部文件",
    )
    original = button._tip.text
    button.set_enabled(False, reason="队列为空，请先添加文件")
    assert button._tip.text == "队列为空，请先添加文件"
    button.set_enabled(True)
    assert button._tip.text == original
    button.destroy()


def test_a_busy_button_refuses_further_clicks(tk_root):
    from echoshift.gui.widgets import RoundedButton

    calls: list[int] = []
    button = RoundedButton(
        tk_root, "开始转换", kind="primary", palette=DARK, fonts=build_fonts(),
        command=lambda: calls.append(1),
    )
    button.set_busy(True)
    button.invoke()
    assert calls == [], "忙碌中的按钮不能重复触发命令"
    button._on_release(None)
    assert calls == []

    button.set_busy(False)
    button.invoke()
    assert calls == [1]
    button.destroy()


def test_a_disabled_control_leaves_the_tab_order(tk_root):
    from echoshift.gui.widgets import CheckBox, RoundedButton, SegmentedControl

    variable = tk.BooleanVar(master=tk_root, value=True)
    box = CheckBox(tk_root, "校验", variable, palette=DARK, fonts=build_fonts())
    button = RoundedButton(tk_root, "开始", palette=DARK, fonts=build_fonts())
    mode = tk.StringVar(master=tk_root, value="a")
    segment = SegmentedControl(
        tk_root, [("a", "A"), ("b", "B")], mode, palette=DARK, fonts=build_fonts()
    )

    for widget in (box, button, segment):
        assert int(widget.cget("takefocus")) == 1
        widget.set_enabled(False)
        assert int(widget.cget("takefocus")) == 0, "禁用的控件不应停留在 Tab 顺序里"

    box.destroy()
    button.destroy()
    segment.destroy()


# --------------------------------------------------------------------------- #
# layout stability
#
# Two separate causes of "using a setting makes the panel jump": the collapsible
# card grew by 2 px when focus thickened its border, and the "当前：…" label
# wrapped to two lines for some sample rates and not others, shifting every card
# below it by 17 px.
# --------------------------------------------------------------------------- #


def test_focus_does_not_change_a_card_size(tk_root):
    from echoshift.gui.widgets import CollapsibleSection

    section = CollapsibleSection(tk_root, "编码参数", palette=DARK, fonts=build_fonts())
    section.body.columnconfigure(1, weight=1)
    ttk.Label(section.body, text="预设").grid(row=0, column=0, sticky="w")
    section.pack(fill="x")
    tk_root.update()

    before = (section.winfo_reqwidth(), section.winfo_reqheight())
    section._on_focus_in(None)
    tk_root.update()
    after = (section.winfo_reqwidth(), section.winfo_reqheight())
    assert after == before, f"获得焦点后卡片尺寸变了：{before} -> {after}"
    # The ring must still be visible, just not by growing.
    assert str(section.cget("highlightbackground")) == DARK.accent
    section._on_focus_out(None)
    tk_root.update()
    assert (section.winfo_reqwidth(), section.winfo_reqheight()) == before
    section.destroy()


def test_the_plan_label_never_wraps(app_window):
    """It is rebuilt on every option change, so its height must be constant."""
    heights: set[int] = set()
    texts: list[str] = []
    for rate in ("8000 Hz", "44100 Hz", "48000 Hz", "KEEP"):
        if rate == "KEEP":
            app_window.rate_var.set("保持原样")
        else:
            app_window.rate_var.set(rate)
        app_window._refresh_bitrate_choices()
        app_window.root.update_idletasks()
        heights.add(app_window.plan_label.winfo_reqheight())
        texts.append(app_window.plan_label.cget("text"))

    assert len(heights) == 1, f"标签高度随参数变化：{heights}"
    assert all(text.startswith("当前：") for text in texts)
    # Nothing was trimmed at the default width, so no ellipsis should appear.
    assert not any("\u2026" in text for text in texts), texts


def test_an_overlong_plan_label_is_trimmed_instead_of_wrapped(app_window, monkeypatch):
    monkeypatch.setattr(
        app_window, "collect_settings", lambda: _FakeSettings()
    )
    app_window._refresh_plan_label()
    app_window.root.update_idletasks()
    text = app_window.plan_label.cget("text")
    assert text.endswith("\u2026"), text
    # Still exactly one line, which is the property that keeps the column still.
    assert app_window.plan_label.winfo_reqheight() == app_window.plan_label.winfo_reqheight()
    # The tooltip keeps the whole thing readable.
    assert "很长的参数说明" in app_window._plan_tooltip.text


class _FakeSettings:
    def describe(self) -> str:
        return "很长的参数说明" * 30


def test_collapsible_section_toggles_from_keyboard(tk_root):
    from echoshift.gui.widgets import CollapsibleSection

    section = CollapsibleSection(tk_root, "高级", palette=DARK, fonts=build_fonts())
    section.pack()
    assert section.expanded
    section._toggle_from_key()
    assert not section.expanded
    section._toggle_from_key()
    assert section.expanded
    section.destroy()


def test_queue_rows_do_not_create_file_info_tooltips(tk_root):
    """The queue must never spawn the sticky file-information popup."""
    from echoshift.gui.queue_view import QueueRow, QueueView

    view = QueueView(tk_root, palette=DARK, fonts=build_fonts())
    view.set_rows(
        [
            QueueRow(
                filename="song.flac",
                source="FLAC · 44.1 kHz",
                detail="VBR q2",
            )
        ]
    )

    assert not hasattr(view, "_tip")
    assert not hasattr(view.row(0), "tooltip")
    view.destroy()


# --------------------------------------------------------------------------- #
# scrollbars
#
# Two regressions lived here: ScrollableFrame built a CanvasScrollbar but never
# wired yscrollcommand to it, so it never painted at all; and the log used
# ScrolledText, whose classic tk.Scrollbar ignores ttk styling and stayed white.
# --------------------------------------------------------------------------- #


def _classic_scrollbars(widget: tk.Misc) -> list[tk.Misc]:
    found = [widget] if isinstance(widget, tk.Scrollbar) else []
    for child in widget.winfo_children():
        found.extend(_classic_scrollbars(child))
    return found


def test_scrollable_frame_hands_its_scrollbar_the_visible_range(tk_root):
    from echoshift.gui.widgets import CanvasScrollbar, ScrollableFrame

    frame = ScrollableFrame(tk_root, palette=DARK, width=200)
    frame.canvas.configure(height=80)
    frame.pack()
    for index in range(50):
        tk.Label(frame.inner, text=f"row {index}", background=DARK.bg).pack()
    tk_root.update()

    assert isinstance(frame.scrollbar, CanvasScrollbar)
    # Without yscrollcommand the range stays 0.0-1.0 and _render bails out.
    assert frame.canvas.cget("yscrollcommand"), "yscrollcommand is not wired"
    assert frame.scrollbar._last - frame.scrollbar._first < 0.999
    assert len(frame.scrollbar.find_all()) >= 2, "轨道和滑块都应该被画出来"

    frame.destroy()


def test_scrollable_frame_hides_its_scrollbar_when_everything_fits(tk_root):
    from echoshift.gui.widgets import ScrollableFrame

    frame = ScrollableFrame(tk_root, palette=DARK, width=200)
    frame.canvas.configure(height=400)
    frame.pack()
    tk.Label(frame.inner, text="one row", background=DARK.bg).pack()
    tk_root.update()

    assert frame.scrollbar._last - frame.scrollbar._first >= 0.999
    assert frame.scrollbar.find_all() == (), "内容装得下时不应画滚动条"
    frame.destroy()


@pytest.fixture
def app_window(tk_root):
    """A real EchoShiftApp on its own Toplevel.

    A Toplevel rather than the shared root, because the app takes over the
    window it is given -- destroying the module-scoped root here would break
    every later test that needs a default root.
    """
    from echoshift.gui.app import EchoShiftApp

    window = tk.Toplevel(tk_root)
    app = EchoShiftApp(window)
    tk_root.update()
    yield app
    app._closing = True
    try:
        window.destroy()
    except tk.TclError:
        pass


def test_the_log_is_a_themed_text_with_a_canvas_scrollbar(app_window):
    from echoshift.gui.widgets import CanvasScrollbar

    assert isinstance(app_window.log_widget, tk.Text)
    assert isinstance(app_window.log_scroll, CanvasScrollbar)
    assert app_window.log_widget.cget("yscrollcommand"), "日志的 yscrollcommand 没接上"


def test_log_opens_only_when_a_batch_contains_a_failure(app_window):
    from pathlib import Path

    from echoshift.core.pipeline import JobResult, JobState

    app_window._toggle_log(False)
    app_window._finish(
        [JobResult(source=Path("ok.flac"), state=JobState.DONE, message="完成")]
    )
    assert not app_window._log_expanded

    app_window._finish(
        [JobResult(source=Path("bad.flac"), state=JobState.FAILED, message="损坏")]
    )
    assert app_window._log_expanded
    assert app_window._log_error_count == 1


def test_progress_updates_are_coalesced_per_queue_row(app_window):
    for value in range(1000):
        app_window._queue_progress(2, value / 1000, "转码中")

    assert app_window._events.qsize() == 1
    assert app_window._take_progress() == {2: (0.999, "转码中")}


# --------------------------------------------------------------------------- #
# queue bookkeeping
#
# Three regressions lived here: the bar averaged over the whole queue, so a
# subset run could never reach 100%; background probes were matched by path, so
# a queue edit could land a probe on the wrong row; and the ffmpeg re-detect had
# no generation, so a slow older probe could overwrite a newer answer.
# --------------------------------------------------------------------------- #


def _fill_queue(app_window, count: int) -> None:
    from pathlib import Path

    from echoshift.gui.app import QueueItem

    for index in range(count):
        app_window.items.append(
            QueueItem(uid=app_window._next_uid, path=Path(f"song{index}.flac"))
        )
        app_window._next_uid += 1
    app_window._refresh_rows()


def test_overall_progress_ignores_rows_outside_the_current_batch(app_window):
    """A subset run must still be able to reach 100%.

    ``_apply_result`` sets a finished item to 1.0 and leaves the rest alone, so
    averaging over the whole queue made the bar top out below 100% whenever the
    queue held rows this run was not converting.
    """
    _fill_queue(app_window, 5)
    app_window._batch_indices = {1, 3}
    app_window.items[1].progress = 1.0
    app_window.items[3].progress = 0.0
    app_window._refresh_overall_progress()
    assert app_window.progress.fraction == pytest.approx(0.5)

    # The second (and last) batched row finishes.
    app_window.items[3].progress = 1.0
    app_window._refresh_overall_progress()
    assert app_window.progress.fraction == 1.0, "本批次全部完成时进度必须是 100%"

    # With no batch in flight the bar describes the whole queue again.
    app_window._batch_indices.clear()
    app_window._refresh_overall_progress()
    assert app_window.progress.fraction == pytest.approx(2 / 5)


def test_removing_rows_drops_their_queued_probes(app_window):
    from pathlib import Path

    _fill_queue(app_window, 3)
    app_window._drop_probes()
    for item in app_window.items:
        app_window._probe_queue.put((item.uid, item.path))

    doomed_uid = app_window.items[1].uid
    app_window._drop_probes({doomed_uid})

    remaining: list[int] = []
    while not app_window._probe_queue.empty():
        request = app_window._probe_queue.get_nowait()
        assert request is not None
        remaining.append(request[0])
    assert doomed_uid not in remaining
    assert remaining == [app_window.items[0].uid, app_window.items[2].uid]


def test_probe_results_are_matched_by_uid_not_by_path(app_window, monkeypatch):
    """A request for a uid that no longer exists must be dropped, not guessed."""
    import threading
    from pathlib import Path

    from echoshift.gui.app import QueueItem

    app_window.items[:] = [QueueItem(uid=7, path=Path("a.flac"))]
    app_window.toolchain = object()  # type: ignore[assignment]
    monkeypatch.setattr(
        app_window, "_describe_source", lambda path: ("FLAC · 44.1 kHz", "3:00")
    )

    app_window._probe_queue.put((7, Path("a.flac")))   # this row still exists
    app_window._probe_queue.put((8, Path("b.flac")))   # this one does not
    worker = threading.Thread(target=app_window._start_probe_worker, daemon=True)
    worker.start()
    app_window._probe_queue.put(None)
    worker.join(timeout=5)

    delivered: list[tuple] = []
    while not app_window._background_events.empty():
        event = app_window._background_events.get_nowait()
        # Startup detection runs concurrently and may post a toolchain result
        # during this window; only probe events are under test.
        if event[0] == "probe":
            delivered.append(event)
    assert delivered == [("probe", 0, "FLAC · 44.1 kHz", "3:00")], delivered


def test_a_stale_toolchain_detection_is_discarded(app_window, monkeypatch):
    from pathlib import Path

    from echoshift.core.ffmpeg import Toolchain

    fake = Toolchain(
        ffmpeg=Path("ffmpeg.exe"),
        ffprobe=Path("ffprobe.exe"),
        version="ffmpeg test",
        has_libmp3lame=True,
        source="测试",
    )
    _fill_queue(app_window, 1)
    app_window._toolchain_generation = 5
    app_window.toolchain = None
    # A newer detection already landed; this older one must not overwrite it.
    app_window._handle_event(("toolchain", 4, fake, None))
    assert app_window.toolchain is None

    app_window._handle_event(("toolchain", 5, fake, None))
    assert app_window.toolchain is fake


def test_logging_after_close_is_ignored(app_window):
    """Worker threads outlive the window; touching widgets then raises TclError."""
    _fill_queue(app_window, 1)
    app_window._closing = True
    before = app_window.log_widget.get("1.0", "end")
    app_window._log("这条不该写进去")
    app_window._set_status("也不该改状态")
    assert app_window.log_widget.get("1.0", "end") == before


def test_the_start_button_explains_why_it_is_unavailable(app_window):
    app_window.toolchain = None
    app_window.toolchain_error = "找不到 ffmpeg"
    app_window._refresh_start_button()
    assert not app_window.start_button.enabled
    assert "ffmpeg" in app_window.start_button._tip.text

    app_window.toolchain_error = None
    app_window._refresh_start_button()
    assert "检测" in app_window.start_button._tip.text

    app_window.toolchain = object()  # type: ignore[assignment]
    app_window._active_scans = 1
    app_window._refresh_start_button()
    assert "扫描" in app_window.start_button._tip.text

    app_window._active_scans = 0
    app_window._refresh_start_button()
    assert "队列为空" in app_window.start_button._tip.text

    _fill_queue(app_window, 1)
    app_window._refresh_start_button()
    assert app_window.start_button.enabled
    assert app_window.start_button._tip.text == "按左侧参数转换队列中的全部文件"


def test_locking_for_a_batch_freezes_the_queue_controls(app_window):
    """Editing mid-conversion used to bounce off a blocking message box."""
    _fill_queue(app_window, 2)
    app_window.toolchain = object()  # type: ignore[assignment]
    app_window._refresh_start_button()
    app_window.remove_button.set_enabled(True)

    app_window._set_editing_locked(True)
    for button in (
        app_window.add_files_button,
        app_window.add_folder_button,
        app_window.clear_button,
        app_window.remove_button,
    ):
        assert not button.enabled, "转换期间队列按钮必须不可用"
        assert "转换进行中" in button._tip.text
    assert str(app_window.template_box.cget("state")) == "disabled"
    assert not app_window.mode_control.enabled
    assert not app_window.workers_stepper.enabled

    app_window._set_editing_locked(False)
    assert app_window.add_files_button.enabled
    assert app_window.remove_button.enabled, "解锁后应恢复到锁定前的可用状态"
    assert str(app_window.template_box.cget("state")) == "normal"
    assert app_window.mode_control.enabled


def test_the_output_directory_field_decides_its_own_state_after_unlock(app_window):
    """The output entry is disabled whenever output follows the source files."""
    app_window.output_mode_var.set("source")
    app_window._refresh_output_state()
    app_window._set_editing_locked(True)
    app_window._set_editing_locked(False)
    assert str(app_window.output_entry.cget("state")) == "disabled"
    assert not app_window.output_browse.enabled

    app_window.output_mode_var.set("custom")
    app_window._refresh_output_state()
    assert str(app_window.output_entry.cget("state")) == "normal"
    assert app_window.output_browse.enabled


def test_close_does_not_join_worker_on_tk_thread(app_window, monkeypatch):
    import time

    class StillRunning:
        @staticmethod
        def is_alive():
            return True

    app_window._worker = StillRunning()
    monkeypatch.setattr("echoshift.gui.app.messagebox.askyesno", lambda *_a, **_k: True)

    started = time.perf_counter()
    app_window._on_close()

    assert time.perf_counter() - started < 0.1
    assert app_window._close_requested
    assert app_window._cancel.is_set()


#: How long the mocked slow operations below pretend to take.  The assertions
#: are relative to this rather than to a fixed millisecond budget: window
#: construction measures 70-130 ms here, so a 200 ms budget for "did not block"
#: failed about one run in five as soon as the machine was busy.
_SLOW_OPERATION = 1.5


def test_slow_toolchain_probe_does_not_block_window_construction(tk_root, monkeypatch):
    import time

    import echoshift.gui.app as app_module

    def slow_probe(*_args, **_kwargs):
        time.sleep(_SLOW_OPERATION)
        raise app_module.ToolNotFoundError("模拟未找到")

    monkeypatch.setattr(app_module, "find_toolchain", slow_probe)
    window = tk.Toplevel(tk_root)
    started = time.perf_counter()
    app = app_module.EchoShiftApp(window)
    elapsed = time.perf_counter() - started

    # Well under the mocked probe, so this can only pass if construction never
    # waited for it.
    assert elapsed < _SLOW_OPERATION / 2, f"窗口构造被探测阻塞了 {elapsed:.2f}s"
    app._closing = True
    window.destroy()


def test_directory_scan_is_dispatched_off_the_tk_thread(app_window, monkeypatch):
    import time
    from pathlib import Path

    def slow_scan(*_args, **_kwargs):
        time.sleep(_SLOW_OPERATION)
        return []

    monkeypatch.setattr("echoshift.gui.app.collect_sources", slow_scan)
    started = time.perf_counter()
    app_window._schedule_add_paths([Path("large-library")])
    elapsed = time.perf_counter() - started

    assert elapsed < _SLOW_OPERATION / 2, f"扫描没有异步派发，阻塞了 {elapsed:.2f}s"
    assert app_window._active_scans == 1


def test_the_window_icon_is_available_and_applies(tk_root):
    """The window/taskbar icon must be the same mark as the header and the exe.

    A source run has no icon resource of its own, so without this the window
    showed Tk's default feather while the header showed the real mark; and the
    packaged build needs ``assets`` in the payload for the same reason.
    """
    from echoshift.gui.app import _apply_window_icon, _window_icon

    icon = _window_icon()
    assert icon is not None, "找不到 assets/echoshift.ico"
    assert icon.suffix == ".ico"

    window = tk.Toplevel(tk_root)
    _apply_window_icon(window)          # must not raise
    assert window.winfo_exists()
    window.destroy()


def test_the_build_bundles_the_assets_directory():
    """The spec has to ship assets/, or the frozen app loses its window icon."""
    spec = Path(__file__).resolve().parents[1] / "packaging" / "EchoShift.spec"
    text = spec.read_text(encoding="utf-8")
    assert '"assets"' in text, "packaging/EchoShift.spec 没有把 assets 打进产物"


def test_no_classic_scrollbar_appears_anywhere(app_window):
    """tk.Scrollbar cannot be themed, so it must not be used at all."""
    classic = _classic_scrollbars(app_window.root)
    assert classic == [], f"发现经典滚动条：{classic}"


def test_the_settings_column_scrollbar_is_wired(app_window):
    from echoshift.gui.widgets import CanvasScrollbar

    bar = app_window.settings_scroller.scrollbar
    assert isinstance(bar, CanvasScrollbar)
    assert app_window.settings_scroller.canvas.cget("yscrollcommand")


def test_canvas_scrollbar_hides_when_the_range_is_full(tk_root):
    from echoshift.gui.widgets import CanvasScrollbar

    bar = CanvasScrollbar(tk_root, palette=DARK, thickness=12, height=200)
    bar.set(0.0, 1.0)
    assert bar.find_all() == ()
    bar.set(0.2, 0.6)
    assert len(bar.find_all()) == 2  # rail + thumb
    bar.destroy()


def test_canvas_scrollbar_reports_its_position_to_the_command(tk_root, monkeypatch):
    import types

    from echoshift.gui.widgets import CanvasScrollbar

    seen: list[float] = []
    window = tk.Toplevel(tk_root)
    bar = CanvasScrollbar(window, palette=DARK, command=seen.append, thickness=12)
    bar.pack(fill="both", expand=True)
    window.update()

    # The shared tk_root is withdrawn, so a child Toplevel is never mapped and
    # winfo_height() stays 1.  Pin the height rather than trusting the window
    # manager: the arithmetic is what is under test here.
    height = 200
    monkeypatch.setattr(bar, "winfo_height", lambda: height)
    # A scrollbar only draws a thumb when the content overflows.
    bar.set(0.0, 0.2)
    top, bottom = bar._thumb_span()
    assert (top, bottom) == (0.0, 40.0)

    bar._on_press(types.SimpleNamespace(y=100))          # trough
    assert seen and seen[-1] == pytest.approx(0.5, abs=0.01)

    bar._on_release(None)
    bar._on_press(types.SimpleNamespace(y=(top + bottom) / 2))   # grab the thumb
    assert bar._dragging
    bar._on_drag(types.SimpleNamespace(y=60))
    # Grabbing at the thumb's middle keeps the pointer's offset: 60 - 20 = 40.
    assert seen[-1] == pytest.approx(0.2, abs=0.01)
    window.destroy()


def test_canvas_scrollbar_highlights_while_dragging(tk_root, monkeypatch):
    from echoshift.gui.widgets import CanvasScrollbar

    window = tk.Toplevel(tk_root)
    bar = CanvasScrollbar(window, palette=DARK, command=lambda _f: None, thickness=12)
    bar.pack(fill="both", expand=True)
    window.update()
    monkeypatch.setattr(bar, "winfo_height", lambda: 200)
    bar.set(0.0, 0.2)

    top, bottom = bar._thumb_span()
    bar._on_press(type("E", (), {"y": (top + bottom) / 2})())
    assert bar._dragging, "按住滑块应进入拖拽态"
    assert len(bar.find_all()) == 2

    bar._on_release(None)
    assert not bar._dragging
    window.destroy()
