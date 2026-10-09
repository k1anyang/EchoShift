"""Tkinter desktop front end.

The GUI is a thin shell over :mod:`echoshift.core`: it collects settings,
queues files, and renders whatever :class:`~echoshift.core.pipeline.JobResult`
objects come back.  All ffmpeg work happens on worker threads; the UI only ever
touches widgets from the main thread, coordinated through a queue polled by
``after()``.

The layout is a two-column split: settings on the left stay visible while the
queue on the right takes the remaining width, which is what stops long source
descriptions from being clipped.
"""

from __future__ import annotations

import queue
import json
import os
import string
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import tkinter as tk
from tkinter import filedialog, font as tkfont, messagebox, ttk

from ..core.config import AppConfig, config_path
from ..core.diagnostics import write_diagnostic
from ..errors import EchoShiftError, ToolNotFoundError
from ..core.ffmpeg import ProcessLimiter, Toolchain, find_toolchain, terminate_active_processes
from ..core.naming import TEMPLATE_HELP, TEMPLATE_PRESETS
from ..paths import resource_dir
from ..core.pipeline import (
    JobResult,
    JobState,
    OverwritePolicy,
    Pipeline,
    PipelineOptions,
    collect_sources,
    default_work_dir,
    effective_workers,
)
from ..core.probe import probe
from ..core.settings import (
    PRESETS,
    SAMPLE_RATES,
    VBR_QUALITY_TABLE,
    BitrateMode,
    ChannelMode,
    EncodeSettings,
    allowed_bitrates,
)
from ..qmc.decoder import SUPPORTED_EXTENSIONS, is_qmc_path, peek as peek_header
from ..qmc.keystore import KeyStore
from . import dnd
from .icons import draw_icon
from .metrics import (
    GAP_LG,
    GAP_MD,
    GAP_SM,
    GAP_XS,
    SETTINGS_COLUMN_WIDTH,
    SETTINGS_STACK_HEIGHT,
    STACK_BREAKPOINT,
)
from .queue_view import QueueRow, QueueView
from .text import CharWidths, ellipsize
from .theme import DARK, Fonts, Palette, apply_theme, build_fonts, mix
from .widgets import (
    CanvasScrollbar,
    Card,
    CheckBox,
    CollapsibleSection,
    IconButton,
    NumberStepper,
    ProgressBar,
    RoundedButton,
    SegmentedControl,
    ScrollableFrame,
    attach_tooltip,
    rounded_rect,
)

__all__ = ["EchoShiftApp", "APP_TITLE", "APP_TAGLINE", "APP_NAME", "main"]

APP_NAME = "EchoShift"
APP_TITLE = "EchoShift · 音频转 MP3"
APP_TAGLINE = "支持 QQ 音乐加密格式解密（MFLAC / MGG / QMC）· 兼容 ffmpeg 可读的音频与视频"

_MODE_LABELS = {
    BitrateMode.VBR: "VBR 可变码率",
    BitrateMode.ABR: "ABR 平均码率",
    BitrateMode.CBR: "CBR 固定码率",
}
_MODE_BY_LABEL = {label: mode for mode, label in _MODE_LABELS.items()}

_CHANNEL_LABELS = {
    ChannelMode.KEEP: "保持原样",
    ChannelMode.MONO: "单声道",
    ChannelMode.STEREO: "立体声",
}
_CHANNEL_BY_LABEL = {label: mode for mode, label in _CHANNEL_LABELS.items()}

_OVERWRITE_LABELS = {
    OverwritePolicy.RENAME: "自动改名（不覆盖）",
    OverwritePolicy.SKIP: "跳过已存在",
    OverwritePolicy.OVERWRITE: "直接覆盖",
}
_OVERWRITE_BY_LABEL = {label: policy for policy, label in _OVERWRITE_LABELS.items()}

KEEP_RATE_LABEL = "保持原样"

#: Primary-action label, restored whenever the button leaves its busy state.
_START_LABEL = "开始转换"

_HELP = {
    "preset": "内置的参数组合。选中后会立即写入下面的各项；再手动改动就变成「自定义」。",
    "mode": (
        "VBR：质量恒定、码率浮动，音乐库长期保存首选。\n"
        "ABR：平均码率，体积可控。\n"
        "CBR：恒定码率，老播放器与车载兼容性最好。"
    ),
    "bitrate": (
        "VBR 选质量档 q0–q9，数字越小越好（q0 约 245 kbps，q9 约 65 kbps）。\n"
        "CBR / ABR 选具体 kbps，可选范围会随采样率自动变化。"
    ),
    "rate": (
        "MP3 只支持 8 / 11.025 / 12 / 16 / 22.05 / 24 / 32 / 44.1 / 48 kHz。\n"
        "选「保持原样」时，96 kHz 这类源会自动降到 48 kHz 并在日志里提示。"
    ),
    "channels": "源文件多于 2 声道时，「保持原样」会自动下混成立体声并给出提示。",
    "joint": "联合立体声（joint stereo）在同码率下通常优于强制双声道。",
    "template": "输出路径模板，`/` 是目录分隔符。\n" + TEMPLATE_HELP,
    "overwrite": "输出文件已存在时的处理方式；自动改名会在名字后加「 (2)」。",
    "verify": "转换后重新探测产物：检查可解码性、时长偏差、采样率与声道是否符合预期。",
    "deep_verify": "额外完整解码一遍产物，能发现探测查不出的坏帧，代价是慢一点。",
    "retries": "单个文件失败后额外重试的次数。设为 1 表示最多尝试 2 次。",
    "workers": "同时转换的文件数。EchoShift 会按 CPU 自动限制到安全范围，最多 4 个。",
    "id3": "ID3v2.3 兼容性最广；2.4 支持更完整，但个别老设备读不出。",
    "tags": "把标题 / 艺术家 / 专辑 / 音轨号等标签迁移到 MP3。",
    "cover": "把内嵌封面写进 MP3 的 APIC 帧。",
    "ekey": (
        "只对不含密钥的容器（musicex 尾部，QQ 音乐 19.57+）才需要填写。\n"
        "其余情况下密钥就在文件里，这里留空即可。"
    ),
    "keydb": "可选：一个 JSON 密钥库，按 mid / song_id / 文件名登记 ekey。",
}


@dataclass
class QueueItem:
    """App-side state for one queued file; rendered as a :class:`QueueRow`."""

    #: Stable identity.  Background probing reports back by uid rather than by
    #: index or path, so a queue edit cannot make a probe land on another row.
    uid: int = 0
    path: Path = field(default_factory=Path)
    state: JobState = JobState.PENDING
    source_desc: str = ""
    duration: str = ""
    plan_desc: str = ""
    progress: float = 0.0
    message: str = ""
    output: Path | None = None
    warnings: list[str] = field(default_factory=list)
    attempts: int = 0

    def to_row(self) -> QueueRow:
        # ``source_desc`` already carries the duration when the probe reported
        # one, so this only appends it for descriptions that lack it (a queued
        # file whose probe has not finished yet).  Appending unconditionally
        # duplicated it in the column.
        source = self.source_desc
        if self.duration and self.duration not in source:
            source = f"{source} · {self.duration}" if source else self.duration
        return QueueRow(
            filename=self.path.name,
            source=source,
            state=self.state,
            progress=self.progress,
            message=self.message,
            output=self.output,
            path=self.path,
            detail=self.plan_desc,
        )


def _format_duration(seconds: float | None) -> str:
    if not seconds or seconds <= 0:
        return ""
    minutes, remainder = divmod(int(seconds), 60)
    return f"{minutes}:{remainder:02d}"


class EchoShiftApp:
    """The main window."""

    def __init__(self, root: tk.Tk, *, palette: Palette | None = None) -> None:
        self.root = root
        self.palette = palette or DARK
        self.fonts: Fonts = build_fonts()
        self.config = AppConfig.load()
        self.items: list[QueueItem] = []
        self._events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._background_events: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._progress_lock = threading.Lock()
        self._pending_progress: dict[int, tuple[float, str]] = {}
        self._progress_event_queued = False
        self._log_lock = threading.Lock()
        self._pending_logs: list[str] = []
        self._log_event_queued = False
        self._active_scans = 0
        self._scan_generation = 0
        self._cancel = threading.Event()
        #: Queue indices taking part in the current run; empty means "all".
        self._batch_indices: set[int] = set()
        self._worker: threading.Thread | None = None
        #: ``(uid, path)`` pairs; ``None`` stops the worker.
        self._probe_queue: queue.Queue[tuple[int, Path] | None] = queue.Queue()
        self._probe_thread: threading.Thread | None = None
        self._next_uid = 1
        self._process_limiter = ProcessLimiter(2)
        self._lockable_cache: list[tuple[Any, str]] | None = None
        self._locked_widget_states: dict[str, str] = {}
        self._text_widths = CharWidths()
        self._editing_locked = False
        self._terminating = False
        self._toolchain_thread: threading.Thread | None = None
        self._toolchain_start_job: str | None = None
        self._toolchain_generation = 0
        self._closing = False
        self._close_requested = False
        self._close_started = 0.0
        self._termination_started = False
        self._log_expanded = False
        self._log_error_count = 0
        self._detail_expanded = False
        self._resize_job: str | None = None
        self._responsive_mode: str | None = None

        self.toolchain: Toolchain | None = None
        self.toolchain_error: str | None = None

        apply_theme(root, self.palette, self.fonts)
        self._build_ui()
        self._load_config_into_ui()
        self._start_probe_worker()
        self._refresh_start_button()
        # Defer the first probe one idle slice.  A window that is immediately
        # closed (including automated smoke windows) never leaves a probe thread
        # behind, while normal startup still begins detection right away.
        self._toolchain_start_job = self.root.after(100, self._start_toolchain_worker)

        self.root.after_idle(self._enable_drop_target)
        self._log("正在后台检测 ffmpeg…")

        # ttk leaves the chosen text highlighted in the entry; clear it so the
        # field does not keep looking "selected" after an option change.
        self.root.bind_all("<<ComboboxSelected>>", self._clear_combobox_selection, add="+")

        self.root.after(80, self._drain_events)
        self.root.bind("<Configure>", self._on_window_resize, add="+")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _enable_drop_target(self) -> None:
        if self._closing:
            return
        drop_ok = dnd.enable_file_drop(self.root, self._on_drop)
        self._log(
            "拖拽支持：" + ("已启用（可直接把文件或文件夹拖进窗口）" if drop_ok else "不可用，请用按钮添加")
        )

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self) -> None:
        self.root.title(APP_TITLE)
        self.root.geometry("1300x870")
        self.root.minsize(960, 620)

        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        self._build_header(root)
        self._build_body(root)
        self._build_log(root)
        self._build_status(root)

    # -- header ---------------------------------------------------------- #

    def _build_header(self, parent: tk.Misc) -> None:
        palette, fonts = self.palette, self.fonts
        header = tk.Frame(parent, background=palette.bg)
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 10))

        mark = tk.Canvas(
            header, width=38, height=38, background=palette.bg,
            highlightthickness=0, borderwidth=0,
        )
        mark.pack(side="left")
        rounded_rect(mark, 1, 1, 37, 37, 10, fill=palette.accent, outline="")
        draw_icon(mark, "brand", 19, 19, 23, palette.accent_text, width=2.0)

        titles = tk.Frame(header, background=palette.bg)
        titles.pack(side="left", padx=(12, 0))
        tk.Label(
            titles, text=APP_NAME, background=palette.bg, foreground=palette.text,
            font=fonts.title, anchor="w",
        ).pack(anchor="w")
        tk.Label(
            titles, text=APP_TAGLINE, background=palette.bg,
            foreground=palette.text_muted, font=fonts.subtitle, anchor="w",
        ).pack(anchor="w", pady=(1, 0))

        self.ffmpeg_badge = tk.Canvas(
            header, width=196, height=26, background=palette.bg,
            highlightthickness=0, borderwidth=0,
        )
        self.ffmpeg_badge.pack(side="right")
        self._render_ffmpeg_badge()
        self.ffmpeg_tooltip = attach_tooltip(
            self.ffmpeg_badge,
            "正在后台检测 ffmpeg 与 libmp3lame。",
            palette=palette, fonts=fonts, wraplength=460,
        )

    def _render_ffmpeg_badge(self) -> None:
        badge, palette, fonts = self.ffmpeg_badge, self.palette, self.fonts
        badge.delete("all")
        loading = self.toolchain is None and self.toolchain_error is None
        ok = self.toolchain is not None and self.toolchain.has_libmp3lame
        colour = palette.info if loading else (palette.success if ok else palette.error)
        text = (
            "正在检测 ffmpeg"
            if loading
            else ("ffmpeg 就绪 · libmp3lame" if ok else "ffmpeg 不可用")
        )
        rounded_rect(
            badge, 1, 1, 195, 25, 12,
            fill=mix(palette.surface, colour, 0.14),
            outline=mix(palette.surface, colour, 0.32), width=1,
        )
        badge.create_oval(12, 10, 18, 16, fill=colour, outline="")
        badge.create_text(
            26, 13, text=text, anchor="w",
            fill=colour if ok else palette.text_muted, font=fonts.small,
        )
        if hasattr(self, "ffmpeg_source_var"):
            if loading:
                self.ffmpeg_source_var.set("正在检测内置目录 / PATH / 自定义目录…")
            elif ok:
                self.ffmpeg_source_var.set(self.toolchain.describe())
            else:
                self.ffmpeg_source_var.set("未找到可用 ffmpeg。请选择目录后重新检测。")

    # -- body ------------------------------------------------------------ #

    def _build_body(self, parent: tk.Misc) -> None:
        palette = self.palette
        body = tk.Frame(parent, background=palette.bg)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        body.columnconfigure(0, weight=0, minsize=SETTINGS_COLUMN_WIDTH)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        body.rowconfigure(1, weight=0)
        self.body = body

        self.settings_column = tk.Frame(body, background=palette.bg)
        self.settings_column.grid(row=0, column=0, sticky="nsew")
        self._build_settings_column(self.settings_column)
        self._build_queue_column(body)
        self.root.after_idle(lambda: self._apply_responsive_layout(self.root.winfo_width()))

    def _build_settings_column(self, parent: tk.Misc) -> None:
        palette = self.palette
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        scroller = ScrollableFrame(parent, palette=palette, width=SETTINGS_COLUMN_WIDTH)
        scroller.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.settings_scroller = scroller

        holder = scroller.inner
        holder.columnconfigure(0, weight=1)
        self._build_encode_card(holder, row=0)
        self._build_output_card(holder, row=1)
        self._build_options_card(holder, row=2)
        self._build_advanced_card(holder, row=3)

    def _on_window_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        if self._resize_job is None:
            self._resize_job = self.root.after_idle(
                lambda width=event.width: self._apply_responsive_layout(width)
            )

    def _apply_responsive_layout(self, width: int) -> None:
        self._resize_job = None
        mode = "stacked" if width < STACK_BREAKPOINT else "columns"
        if mode == self._responsive_mode:
            return
        self._responsive_mode = mode
        if mode == "stacked":
            # The settings column keeps its own scrolling, so it gets a bounded
            # slice and the queue keeps the rest: watching progress is what the
            # user is doing at that point, and a squeezed queue is useless.
            self.body.columnconfigure(0, weight=1, minsize=0)
            self.body.columnconfigure(1, weight=1, minsize=0)
            self.body.rowconfigure(0, weight=0, minsize=SETTINGS_STACK_HEIGHT)
            self.body.rowconfigure(1, weight=1, minsize=320)
            self.settings_column.grid_configure(row=0, column=0, columnspan=2, sticky="nsew")
            self.queue_column.grid_configure(row=1, column=0, columnspan=2, sticky="nsew")
        else:
            self.body.columnconfigure(0, weight=0, minsize=SETTINGS_COLUMN_WIDTH)
            self.body.columnconfigure(1, weight=1, minsize=0)
            self.body.rowconfigure(0, weight=1, minsize=0)
            self.body.rowconfigure(1, weight=0, minsize=0)
            self.settings_column.grid_configure(row=0, column=0, columnspan=1, sticky="nsew")
            self.queue_column.grid_configure(row=0, column=1, columnspan=1, sticky="nsew")

    def _card(
        self, parent: tk.Misc, title: str, row: int, *, expanded: bool = True
    ) -> CollapsibleSection:
        card = CollapsibleSection(
            parent, title, palette=self.palette, fonts=self.fonts, expanded=expanded
        )
        card.grid(row=row, column=0, sticky="ew", pady=(0, 10))
        card.body.columnconfigure(1, weight=1)
        return card

    def _field_label(self, body: tk.Misc, text: str, row: int, tip: str | None = None) -> None:
        label = ttk.Label(body, text=text, style="Card.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=2, padx=(0, 8))
        if tip:
            attach_tooltip(label, tip, palette=self.palette, fonts=self.fonts)

    def _build_encode_card(self, parent: tk.Misc, row: int) -> None:
        body = self._card(parent, "编码参数", row).body

        self._field_label(body, "预设", 0, _HELP["preset"])
        self.preset_var = tk.StringVar(value="（自定义）")
        self.preset_box = ttk.Combobox(
            body, textvariable=self.preset_var, state="readonly",
            values=["（自定义）", *[name for name, _ in PRESETS]],
        )
        self.preset_box.grid(row=0, column=1, sticky="ew", pady=2)
        self.preset_box.bind("<<ComboboxSelected>>", self._on_preset)

        self._field_label(body, "码率模式", 1, _HELP["mode"])
        self.mode_var = tk.StringVar(value=_MODE_LABELS[BitrateMode.VBR])
        self.mode_control = SegmentedControl(
            body,
            [
                (_MODE_LABELS[BitrateMode.VBR], "VBR"),
                (_MODE_LABELS[BitrateMode.ABR], "ABR"),
                (_MODE_LABELS[BitrateMode.CBR], "CBR"),
            ],
            self.mode_var,
            palette=self.palette,
            fonts=self.fonts,
            parent_bg=self.palette.surface,
            command=self._refresh_bitrate_choices,
        )
        self.mode_control.grid(row=1, column=1, sticky="ew", pady=2)

        self._field_label(body, "质量 / 码率", 2, _HELP["bitrate"])
        self.bitrate_var = tk.StringVar(value="")
        self.bitrate_box = ttk.Combobox(body, textvariable=self.bitrate_var, state="readonly")
        self.bitrate_box.grid(row=2, column=1, sticky="ew", pady=2)

        self._field_label(body, "采样率", 3, _HELP["rate"])
        self.rate_var = tk.StringVar(value=KEEP_RATE_LABEL)
        self.rate_box = ttk.Combobox(
            body, textvariable=self.rate_var, state="readonly",
            values=[KEEP_RATE_LABEL, *[f"{rate} Hz" for rate in SAMPLE_RATES]],
        )
        self.rate_box.grid(row=3, column=1, sticky="ew", pady=2)
        self.rate_box.bind("<<ComboboxSelected>>", lambda _e: self._refresh_bitrate_choices())

        self._field_label(body, "声道", 4, _HELP["channels"])
        self.channel_var = tk.StringVar(value=_CHANNEL_LABELS[ChannelMode.KEEP])
        self.channel_box = ttk.Combobox(
            body, textvariable=self.channel_var, state="readonly",
            values=list(_CHANNEL_LABELS.values()),
        )
        self.channel_box.grid(row=4, column=1, sticky="ew", pady=2)
        self.channel_box.bind(
            "<<ComboboxSelected>>",
            lambda _e: (self._refresh_dependency_state(), self._refresh_plan_label()),
        )

        # One line, never wrapped: this label is rebuilt on every option change,
        # and at wraplength 306 the "…· 8000 Hz" variants straddled the wrap
        # point, so switching the sample rate between 44100 and 48000 changed the
        # label's height and shifted every card below it by 17 px.
        self.plan_label = ttk.Label(body, text="", style="Hint.TLabel", wraplength=0)
        self.plan_label.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(GAP_XS * 2, 0))
        # The label is mapped after this runs, so the first truncation happens
        # on a guessed width; redo it once the real one is known, and again if
        # the column is ever resized.
        self._plan_text = ""
        self.plan_label.bind("<Configure>", lambda _e: self._reflow_plan_label())
        body.columnconfigure(1, weight=1)
        self._build_plan_tooltip()

    def _build_output_card(self, parent: tk.Misc, row: int) -> None:
        body = self._card(parent, "输出", row).body

        self.output_mode_var = tk.StringVar(value="source")
        self.output_mode_control = SegmentedControl(
            body,
            [("source", "源文件目录"), ("custom", "指定目录")],
            self.output_mode_var,
            palette=self.palette,
            fonts=self.fonts,
            parent_bg=self.palette.surface,
            command=self._refresh_output_state,
        )
        self.output_mode_control.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))

        picker = tk.Frame(body, background=self.palette.surface)
        picker.grid(row=1, column=1, sticky="ew")
        picker.columnconfigure(0, weight=1)
        self.output_dir_var = tk.StringVar(value="")
        self.output_entry = ttk.Entry(picker, textvariable=self.output_dir_var)
        self.output_entry.grid(row=0, column=0, sticky="ew")
        self.output_browse = IconButton(
            picker, "folder-open", size=26, command=self._choose_output_dir,
            palette=self.palette, fonts=self.fonts, parent_bg=self.palette.surface,
            tooltip="选择输出目录",
        )
        self.output_browse.grid(row=0, column=1, padx=(6, 0))
        self._field_label(body, "目录", 1)
        self.output_validation_var = tk.StringVar(value="")
        ttk.Label(
            body, textvariable=self.output_validation_var, style="Hint.TLabel", wraplength=306,
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(0, 2))
        self.output_dir_var.trace_add("write", lambda *_: self._validate_form())

        self._field_label(body, "命名模板", 3, _HELP["template"])
        self.template_var = tk.StringVar(value="")
        self.template_box = ttk.Combobox(
            body, textvariable=self.template_var,
            values=[value for value, _ in TEMPLATE_PRESETS],
        )
        self.template_box.grid(row=3, column=1, sticky="ew", pady=2)
        self.template_box.bind("<KeyRelease>", lambda _e: self._validate_form())
        self.template_box.bind("<<ComboboxSelected>>", lambda _e: self._validate_form())
        self.template_validation_var = tk.StringVar(value="")
        ttk.Label(
            body, textvariable=self.template_validation_var, style="Hint.TLabel", wraplength=306,
        ).grid(row=4, column=0, columnspan=2, sticky="w", pady=(0, 2))

        self._field_label(body, "同名文件", 5, _HELP["overwrite"])
        self.overwrite_var = tk.StringVar(value=_OVERWRITE_LABELS[OverwritePolicy.RENAME])
        self.overwrite_box = ttk.Combobox(
            body, textvariable=self.overwrite_var, state="readonly",
            values=list(_OVERWRITE_LABELS.values()),
        )
        self.overwrite_box.grid(row=5, column=1, sticky="ew", pady=2)

    def _build_options_card(self, parent: tk.Misc, row: int) -> None:
        body = self._card(parent, "常用选项", row).body
        surface = self.palette.surface

        checks = tk.Frame(body, background=surface)
        checks.grid(row=0, column=0, columnspan=2, sticky="ew")
        checks.columnconfigure(0, weight=1)
        checks.columnconfigure(1, weight=1)

        self.verify_var = tk.BooleanVar(value=True)
        self.tags_var = tk.BooleanVar(value=True)
        self.cover_var = tk.BooleanVar(value=True)
        self.verify_check = self._check(
            checks, "转换后校验", self.verify_var, 0, 0, _HELP["verify"],
            command=self._refresh_dependency_state,
        )
        self._check(checks, "保留标签", self.tags_var, 0, 1, _HELP["tags"])
        self._check(checks, "保留封面", self.cover_var, 1, 0, _HELP["cover"])

    def _build_advanced_card(self, parent: tk.Misc, row: int) -> None:
        self._advanced_card = self._card(
            parent, "高级设置", row, expanded=bool(self.config.advanced_expanded)
        )
        self._advanced_card.on_toggle = self._on_advanced_toggled
        body = self._advanced_card.body
        surface = self.palette.surface

        self.joint_var = tk.BooleanVar(value=True)
        self.deep_var = tk.BooleanVar(value=True)
        checks = tk.Frame(body, background=surface)
        checks.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 4))
        checks.columnconfigure(0, weight=1)
        checks.columnconfigure(1, weight=1)
        self.joint_check = self._check(
            checks, "联合立体声", self.joint_var, 0, 0, _HELP["joint"]
        )
        self.deep_check = self._check(
            checks, "完整解码校验", self.deep_var, 0, 1, _HELP["deep_verify"]
        )

        spins = tk.Frame(body, background=surface)
        spins.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 2))
        spins.columnconfigure(1, weight=1)
        spins.columnconfigure(3, weight=1)

        retry_label = ttk.Label(spins, text="失败重试", style="Card.TLabel")
        retry_label.grid(row=0, column=0, sticky="w", padx=(0, 6))
        attach_tooltip(retry_label, _HELP["retries"], palette=self.palette, fonts=self.fonts)
        self.retries_var = tk.IntVar(value=1)
        self.retries_stepper = NumberStepper(
            spins, self.retries_var, minimum=0, maximum=5,
            palette=self.palette, fonts=self.fonts, parent_bg=surface,
        )
        self.retries_stepper.grid(row=0, column=1, sticky="w")

        worker_label = ttk.Label(spins, text="并行任务", style="Card.TLabel")
        worker_label.grid(row=0, column=2, sticky="w", padx=(12, 6))
        attach_tooltip(worker_label, _HELP["workers"], palette=self.palette, fonts=self.fonts)
        self.workers_var = tk.IntVar(value=2)
        self.workers_stepper = NumberStepper(
            spins, self.workers_var, minimum=1, maximum=4,
            palette=self.palette, fonts=self.fonts, parent_bg=surface,
        )
        self.workers_stepper.grid(row=0, column=3, sticky="w")

        self._field_label(body, "ID3v2 版本", 2, _HELP["id3"])
        self.id3_var = tk.StringVar(value="2.4")
        self.id3_control = SegmentedControl(
            body, [("2.3", "2.3"), ("2.4", "2.4")], self.id3_var,
            palette=self.palette, fonts=self.fonts, parent_bg=surface, width=150,
        )
        self.id3_control.grid(row=2, column=1, sticky="w", pady=2)

        self._field_label(body, "ekey", 3, _HELP["ekey"])
        self.ekey_var = tk.StringVar(value="")
        self.ekey_entry = ttk.Entry(body, textvariable=self.ekey_var, show="\u2022")
        self.ekey_entry.grid(row=3, column=1, sticky="ew", pady=2)
        attach_tooltip(self.ekey_entry, _HELP["ekey"], palette=self.palette, fonts=self.fonts)

        self.remember_ekey_var = tk.BooleanVar(value=False)
        self._check(
            body, "记住 ekey（默认关闭）", self.remember_ekey_var, 4, 0,
            "ekey 可能等同于解密密钥。默认仅在本次运行有效；打开后会写入应用配置。",
        )

        self.key_db_var = tk.StringVar(value="")
        self.key_db_var.trace_add("write", lambda *_: self._refresh_key_db_status())
        RoundedButton(
            body, "导入密钥库\u2026", icon="file-plus", kind="ghost", height=28,
            command=self._choose_key_db, palette=self.palette, fonts=self.fonts,
            parent_bg=surface, tooltip=_HELP["keydb"],
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self.key_db_status_var = tk.StringVar(value="未选择密钥库")
        ttk.Label(
            body, textvariable=self.key_db_status_var, style="Hint.TLabel", wraplength=306,
        ).grid(row=6, column=0, columnspan=2, sticky="w", pady=(3, 0))

        self._field_label(body, "ffmpeg 目录", 7, "留空时按内置目录、PATH 顺序检测。选择自定义目录后可点击重新检测。")
        ffmpeg_picker = tk.Frame(body, background=surface)
        ffmpeg_picker.grid(row=7, column=1, sticky="ew", pady=2)
        ffmpeg_picker.columnconfigure(0, weight=1)
        self.ffmpeg_dir_var = tk.StringVar(value="")
        self.ffmpeg_dir_entry = ttk.Entry(ffmpeg_picker, textvariable=self.ffmpeg_dir_var)
        self.ffmpeg_dir_entry.grid(row=0, column=0, sticky="ew")
        self.ffmpeg_browse = IconButton(
            ffmpeg_picker, "folder-open", size=26, command=self._choose_ffmpeg_dir,
            palette=self.palette, fonts=self.fonts, parent_bg=surface,
            tooltip="选择包含 ffmpeg.exe 和 ffprobe.exe 的目录",
        )
        self.ffmpeg_browse.grid(row=0, column=1, padx=(6, 0))
        self.ffmpeg_recheck = RoundedButton(
            body, "重新检测", icon="refresh", kind="ghost", height=28,
            command=self._redetect_toolchain, palette=self.palette, fonts=self.fonts,
            parent_bg=surface, tooltip="重新检测当前 ffmpeg 设置",
        )
        self.ffmpeg_recheck.grid(row=8, column=1, sticky="w", pady=(2, 0))
        self.ffmpeg_source_var = tk.StringVar(value="检测中…")
        ttk.Label(
            body, textvariable=self.ffmpeg_source_var, style="Hint.TLabel", wraplength=306,
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(3, 0))

    def _check(
        self,
        parent: tk.Misc,
        text: str,
        variable: tk.BooleanVar,
        row: int,
        column: int,
        tip: str,
        command=None,
    ) -> CheckBox:
        button = CheckBox(
            parent, text, variable, palette=self.palette, fonts=self.fonts,
            parent_bg=self.palette.surface, tooltip=tip, command=command,
        )
        button.grid(row=row, column=column, sticky="w", pady=1)
        return button

    def _build_queue_column(self, parent: tk.Misc) -> None:
        palette = self.palette
        column = tk.Frame(parent, background=palette.bg)
        column.grid(row=0, column=1, sticky="nsew")
        self.queue_column = column
        column.columnconfigure(0, weight=1)
        column.rowconfigure(1, weight=1)

        toolbar = tk.Frame(column, background=palette.bg)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        def tool_button(text: str, icon: str, command, tip: str) -> RoundedButton:
            button = RoundedButton(
                toolbar, text, icon=icon, command=command, kind="secondary", height=30,
                palette=palette, fonts=self.fonts, parent_bg=palette.bg, tooltip=tip,
            )
            button.pack(side="left", padx=(0, 6))
            return button

        self.add_files_button = tool_button("添加文件", "file-plus", self._choose_files,
                    "选择音频或视频文件（可多选）")
        self.add_folder_button = tool_button("添加文件夹", "folder-plus", self._choose_folder,
                    "整目录加入队列，可递归子文件夹")
        self.remove_button = tool_button("移除选中", "minus", self._remove_selected,
                                         "从队列移除选中的条目（Delete）")
        self.clear_button = tool_button("清空", "trash", self._clear_all, "清空整个队列")
        self.retry_button = tool_button("重试失败项", "refresh", self._retry_failed,
                                        "仅重新转换失败的条目")
        self.retry_button.set_enabled(False)

        self.recurse_var = tk.BooleanVar(value=True)
        CheckBox(
            toolbar, "含子文件夹", self.recurse_var, palette=palette, fonts=self.fonts,
            parent_bg=palette.bg, tooltip="添加文件夹时是否递归子目录。",
        ).pack(side="left", padx=(6, 0))

        self.stop_button = RoundedButton(
            toolbar, "停止转换", icon="stop", kind="danger", height=30, command=self._stop,
            palette=palette, fonts=self.fonts, parent_bg=palette.bg,
            tooltip="立即请求终止当前任务，并取消尚未开始的任务",
        )
        self.stop_button.pack(side="right")
        self.stop_button.set_enabled(False)

        self.start_button = RoundedButton(
            toolbar, _START_LABEL, icon="play", kind="primary", height=30, command=self._start,
            palette=palette, fonts=self.fonts, parent_bg=palette.bg, padx=18,
            tooltip="按左侧参数转换队列中的全部文件",
        )
        self.start_button.pack(side="right", padx=(0, 8))

        holder = Card(column, palette=palette)
        holder.grid(row=1, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.queue_view = QueueView(
            holder,
            palette=palette,
            fonts=self.fonts,
            on_activate=self._on_row_activated,
            on_selection_change=self._on_selection_changed,
            on_delete=self._remove_selected,
            on_copy_paths=lambda count: self._set_status(f"已复制 {count} 个路径"),
        )
        self.queue_view.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        self._build_queue_details(holder)

    def _build_queue_details(self, parent: tk.Misc) -> None:
        palette, fonts = self.palette, self.fonts
        self.detail_panel = tk.Frame(parent, background=palette.surface_alt)
        self.detail_panel.grid(row=1, column=0, sticky="ew", padx=1, pady=(0, 1))
        self.detail_panel.columnconfigure(0, weight=1)

        tk.Frame(self.detail_panel, height=1, background=palette.border).grid(
            row=0, column=0, sticky="ew"
        )
        head = tk.Frame(self.detail_panel, background=palette.surface_alt, cursor="hand2")
        head.configure(takefocus=1)
        self.detail_header = head
        head.grid(row=1, column=0, sticky="ew", padx=10, pady=(7, 2))
        head.columnconfigure(1, weight=1)
        self.detail_chevron = tk.Canvas(
            head, width=18, height=18, background=palette.surface_alt,
            highlightthickness=0, borderwidth=0, cursor="hand2",
        )
        self.detail_chevron.grid(row=0, column=0, padx=(0, 5))
        self.detail_title_var = tk.StringVar(value="")
        self.detail_state_var = tk.StringVar(value="")
        title = tk.Label(
            head, textvariable=self.detail_title_var, background=palette.surface_alt,
            foreground=palette.text, font=fonts.ui_bold, anchor="w",
        )
        title.grid(row=0, column=1, sticky="ew")
        state = tk.Label(
            head, textvariable=self.detail_state_var, background=palette.surface_alt,
            foreground=palette.text_muted, font=fonts.small, anchor="e",
        )
        state.grid(row=0, column=2, padx=(10, 0))

        self.detail_summary_var = tk.StringVar(value="")
        summary = tk.Label(
            self.detail_panel, textvariable=self.detail_summary_var,
            background=palette.surface_alt, foreground=palette.text_muted,
            font=fonts.small, anchor="w", justify="left",
        )
        summary.grid(row=2, column=0, sticky="ew", padx=33, pady=(0, 7))

        self.detail_body = tk.Frame(self.detail_panel, background=palette.surface_alt)
        self.detail_body.grid(row=3, column=0, sticky="ew", padx=33, pady=(0, 9))
        self.detail_body.columnconfigure(0, weight=1)
        self.detail_full_var = tk.StringVar(value="")
        tk.Label(
            self.detail_body, textvariable=self.detail_full_var,
            background=palette.surface_alt, foreground=palette.text_muted,
            font=fonts.small, anchor="w", justify="left", wraplength=760,
        ).grid(row=0, column=0, sticky="ew")

        for widget in (head, self.detail_chevron, title, state, summary):
            widget.bind("<Button-1>", lambda _e: self._toggle_queue_details())
        head.bind("<Key-space>", lambda _e: self._toggle_queue_details() or "break")
        head.bind("<Key-Return>", lambda _e: self._toggle_queue_details() or "break")
        self.detail_panel.grid_remove()
        self.detail_body.grid_remove()
        self._render_detail_chevron()

    def _render_detail_chevron(self) -> None:
        if not hasattr(self, "detail_chevron"):
            return
        self.detail_chevron.delete("all")
        draw_icon(
            self.detail_chevron,
            "chevron-down" if self._detail_expanded else "chevron-right",
            9, 9, 13, self.palette.text_muted,
        )

    def _toggle_queue_details(self) -> None:
        self._detail_expanded = not self._detail_expanded
        self._render_detail_chevron()
        if self._detail_expanded:
            self.detail_body.grid()
        else:
            self.detail_body.grid_remove()

    def _refresh_queue_details(self, indices: Sequence[int] | None = None) -> None:
        if not hasattr(self, "detail_panel"):
            return
        selected = list(indices) if indices is not None else self._selected_indices()
        selected = [index for index in selected if 0 <= index < len(self.items)]
        if not selected:
            self.detail_panel.grid_remove()
            return
        self.detail_panel.grid()
        if len(selected) > 1:
            self.detail_title_var.set(f"已选择 {len(selected)} 个文件")
            self.detail_state_var.set("")
            names = "、".join(self.items[index].path.name for index in selected[:4])
            if len(selected) > 4:
                names += f" 等 {len(selected)} 个"
            self.detail_summary_var.set(names)
            self.detail_full_var.set("\n".join(str(self.items[index].path) for index in selected))
            if self._detail_expanded:
                self.detail_body.grid()
            else:
                self.detail_body.grid_remove()
            return

        item = self.items[selected[0]]
        self.detail_title_var.set(item.path.name)
        self.detail_state_var.set(item.state.label)
        self.detail_summary_var.set(item.message or item.source_desc or str(item.path))
        details = [f"源文件：{item.path}"]
        if item.source_desc:
            details.append(f"源信息：{item.source_desc}")
        if item.plan_desc:
            details.append(f"转换参数：{item.plan_desc}")
        if item.warnings:
            details.append("提示：" + "；".join(item.warnings))
        if item.attempts:
            details.append(f"尝试次数：{item.attempts}")
        if item.output:
            details.append(f"输出文件：{item.output}")
        if item.message:
            details.append(f"{item.state.label}：{item.message}")
        self.detail_full_var.set("\n".join(details))
        if self._detail_expanded:
            self.detail_body.grid()
        else:
            self.detail_body.grid_remove()

    # -- log + status ---------------------------------------------------- #

    def _build_log(self, parent: tk.Misc) -> None:
        palette, fonts = self.palette, self.fonts
        container = tk.Frame(parent, background=palette.bg)
        container.grid(row=2, column=0, sticky="ew", padx=16)
        container.columnconfigure(0, weight=1)
        self.log_container = container

        head = tk.Frame(container, background=palette.bg, cursor="hand2")
        head.configure(takefocus=1)
        self.log_header = head
        head.grid(row=0, column=0, sticky="ew")
        self.log_toggle = tk.Canvas(
            head, width=210, height=24, background=palette.bg,
            highlightthickness=0, borderwidth=0, cursor="hand2",
        )
        self.log_toggle.pack(side="left")
        for widget in (head, self.log_toggle):
            widget.bind("<Button-1>", lambda _e: self._toggle_log())
        head.bind("<Key-space>", lambda _e: self._toggle_log() or "break")
        head.bind("<Key-Return>", lambda _e: self._toggle_log() or "break")

        self.log_body = Card(container, palette=palette)
        self.log_body.columnconfigure(0, weight=1)
        self.log_body.rowconfigure(0, weight=1)

        # A plain Text plus our own CanvasScrollbar, rather than ScrolledText:
        # ScrolledText brings a classic tk.Scrollbar, which cannot be themed.
        holder = tk.Frame(self.log_body, background=palette.surface_alt)
        holder.grid(row=0, column=0, sticky="nsew", padx=1, pady=1)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)

        self.log_widget = tk.Text(
            holder, height=9, wrap="word", state="disabled", font=fonts.mono,
            background=palette.surface_alt, foreground=palette.text_muted,
            insertbackground=palette.text, relief="flat", borderwidth=0,
            highlightthickness=0, selectbackground=palette.selection,
            selectforeground=palette.text, padx=8, pady=6,
        )
        self.log_widget.grid(row=0, column=0, sticky="nsew")

        self.log_scroll = CanvasScrollbar(
            holder, palette=palette, command=self.log_widget.yview,
            thickness=11, trough=palette.surface_alt,
        )
        self.log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_widget.configure(yscrollcommand=self.log_scroll.set)
        self._render_log_toggle()
        if self.config.log_expanded:
            self._toggle_log(True)

    def _render_log_toggle(self) -> None:
        canvas, palette, fonts = self.log_toggle, self.palette, self.fonts
        canvas.delete("all")
        draw_icon(
            canvas, "chevron-down" if self._log_expanded else "chevron-right",
            10, 12, 14, palette.text_muted,
        )
        title = "运行日志"
        canvas.create_text(
            24, 12, text=title, anchor="w", fill=palette.text_muted, font=fonts.small
        )
        if self._log_error_count:
            # Position the count from the measured title instead of a fixed x:
            # the old constant left 12 px of slack at 100% scaling and would
            # overlap the title as soon as the font scaled up.
            title_width = tkfont.Font(font=fonts.small).measure(title)
            count = f"{self._log_error_count} 个错误"
            x = 24 + title_width + GAP_MD
            canvas.create_text(
                x, 12, text=count, anchor="w", fill=palette.error, font=fonts.small
            )
            required = x + tkfont.Font(font=fonts.small).measure(count) + GAP_SM
            if required > int(canvas.cget("width")):
                canvas.configure(width=required)

    def _toggle_log(self, expanded: bool | None = None) -> None:
        self._log_expanded = (not self._log_expanded) if expanded is None else bool(expanded)
        if self._log_expanded:
            self.log_body.grid(row=1, column=0, sticky="nsew", pady=(4, 0))
        else:
            self.log_body.grid_remove()
        self._render_log_toggle()
        self._on_log_toggled(self._log_expanded)

    def _on_advanced_toggled(self, expanded: bool) -> None:
        # Persisted on close along with the rest of the config.
        self.config.advanced_expanded = bool(expanded)

    def _on_log_toggled(self, expanded: bool) -> None:
        self.config.log_expanded = bool(expanded)

    def _build_status(self, parent: tk.Misc) -> None:
        palette, fonts = self.palette, self.fonts
        bar = tk.Frame(
            parent, background=palette.surface,
            highlightbackground=palette.border, highlightthickness=1,
        )
        bar.grid(row=3, column=0, sticky="ew", padx=16, pady=(8, 14))
        bar.columnconfigure(0, weight=1)

        inner = tk.Frame(bar, background=palette.surface)
        inner.grid(row=0, column=0, sticky="ew", padx=12, pady=10)
        inner.columnconfigure(0, weight=1)

        self.progress = ProgressBar(
            inner, palette=palette, height=8, parent_bg=palette.surface,
        )
        self.progress.grid(row=0, column=0, sticky="ew")

        self.status_var = tk.StringVar(value="就绪")
        ttk.Label(inner, textvariable=self.status_var, style="Status.TLabel").grid(
            row=0, column=1, padx=(12, 12)
        )

        RoundedButton(
            inner, "打开输出目录", icon="folder-open", kind="ghost", height=28,
            command=self._open_output_dir, palette=palette, fonts=fonts,
            parent_bg=palette.surface, tooltip="在资源管理器中打开输出位置",
        ).grid(row=0, column=2)

    # ------------------------------------------------------------------ #
    # config <-> widgets
    # ------------------------------------------------------------------ #

    def _load_config_into_ui(self) -> None:
        cfg = self.config
        self.output_mode_var.set(cfg.output_mode)
        self.output_dir_var.set(cfg.output_dir)
        self.template_var.set(cfg.template)
        self.overwrite_var.set(_OVERWRITE_LABELS[cfg.overwrite_policy])
        self.recurse_var.set(cfg.recursive)
        self.verify_var.set(cfg.verify)
        self.deep_var.set(cfg.deep_verify)
        self.retries_var.set(cfg.retries)
        self.workers_var.set(min(4, max(1, cfg.workers)))
        self.ekey_var.set(cfg.ekey if cfg.remember_ekey else "")
        self.remember_ekey_var.set(cfg.remember_ekey)
        self.key_db_var.set(cfg.key_db_path)
        self.ffmpeg_dir_var.set(cfg.ffmpeg_dir)
        self.id3_var.set("2.4" if cfg.encode.id3_version == 4 else "2.3")
        self.tags_var.set(cfg.encode.copy_tags)
        self.cover_var.set(cfg.encode.write_cover)
        self.joint_var.set(cfg.encode.joint_stereo)

        self.mode_var.set(_MODE_LABELS[cfg.encode.mode])
        self.channel_var.set(_CHANNEL_LABELS[cfg.encode.channels])
        self.rate_var.set(
            KEEP_RATE_LABEL if cfg.encode.sample_rate is None else f"{cfg.encode.sample_rate} Hz"
        )
        self._refresh_bitrate_choices()
        self._apply_encode_to_bitrate_widget(cfg.encode)
        self._refresh_output_state()
        self._refresh_plan_label()
        self._refresh_dependency_state()
        self._refresh_key_db_status()
        self._render_ffmpeg_badge()
        self.root.after_idle(self._validate_form)

    def _refresh_output_state(self) -> None:
        custom = self.output_mode_var.get() == "custom"
        self.output_entry.configure(state="normal" if custom else "disabled")
        self.output_browse.set_enabled(custom)
        self.root.after_idle(self._validate_form)

    def _refresh_dependency_state(self) -> None:
        if hasattr(self, "deep_check"):
            self.deep_check.set_enabled(bool(self.verify_var.get()))
        if hasattr(self, "joint_check"):
            stereo_capable = self.channel_var.get() != _CHANNEL_LABELS[ChannelMode.MONO]
            self.joint_check.set_enabled(stereo_capable)
        if getattr(self, "_editing_locked", False):
            # A batch is running: keep every locked control unavailable even if
            # its own dependency rules would otherwise switch it back on.
            for widget, kind in self._lockable_widgets():
                if widget is self.output_browse:
                    continue
                if kind == "control":
                    widget.set_enabled(False)
                else:
                    try:
                        widget.configure(state="disabled")
                    except tk.TclError:
                        pass

    def _refresh_key_db_status(self) -> None:
        if not hasattr(self, "key_db_status_var"):
            return
        text = self.key_db_var.get().strip()
        if not text:
            self.key_db_status_var.set("未选择密钥库")
            return
        path = Path(text)
        if not path.is_file():
            self.key_db_status_var.set("密钥库不存在")
            return
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                raise ValueError("根节点必须是 JSON 对象")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            self.key_db_status_var.set(f"密钥库无效：{exc}")
            return
        self.key_db_status_var.set(f"有效密钥库：{path.name}")

    def _validate_form(self) -> list[str]:
        """Update inline validation hints and return blocking errors."""
        errors: list[str] = []
        if not hasattr(self, "template_var"):
            return errors

        template = self.template_var.get().strip()
        template_message = ""
        if not template:
            template_message = "命名模板不能为空"
            errors.append(template_message)
        else:
            try:
                list(string.Formatter().parse(template))
            except ValueError as exc:
                template_message = f"模板格式无效：{exc}"
                errors.append(template_message)
            if any(ord(char) < 32 for char in template):
                template_message = "模板包含不可用控制字符"
                errors.append(template_message)
            if template_message == "":
                template_message = "示例：artist - title.mp3"
        self.template_validation_var.set(template_message)

        output_message = ""
        if self.output_mode_var.get() == "custom":
            text = self.output_dir_var.get().strip()
            if not text:
                output_message = "请选择输出目录"
                errors.append(output_message)
            else:
                path = Path(text)
                if path.exists() and not path.is_dir():
                    output_message = "输出路径不是文件夹"
                    errors.append(output_message)
                elif path.exists() and not os.access(path, os.W_OK):
                    output_message = "输出目录不可写"
                    errors.append(output_message)
                elif not path.exists():
                    output_message = "目录不存在，开始转换时将自动创建"
                else:
                    output_message = "输出目录可用"
        else:
            output_message = "将输出到源文件所在目录"
        self.output_validation_var.set(output_message)
        return errors

    def _build_plan_tooltip(self) -> None:
        self._plan_tooltip = attach_tooltip(
            self.plan_label,
            "当前编码参数。文字过长时会截断，这里始终显示完整内容。",
            palette=self.palette,
            fonts=self.fonts,
            wraplength=360,
        )

    def _refresh_plan_label(self) -> None:
        try:
            text = self.collect_settings().describe()
        except Exception:  # noqa: BLE001 - the label is advisory only
            text = ""
        self._plan_text = text
        if getattr(self, "_plan_tooltip", None) is not None:
            self._plan_tooltip.update_text(f"当前编码参数：{text}" if text else "")
        self._reflow_plan_label()

    def _reflow_plan_label(self) -> None:
        """Render ``_plan_text`` on exactly one line, trimmed to fit.

        Letting Tk wrap it changed the label's height when the text straddled
        the wrap point, which moved every card below it -- so the label is
        single-line and shortened instead, with the full text in the tooltip.

        Runs from a ``<Configure>`` binding, which Tk can still deliver while a
        window is being torn down, so a vanished widget must not raise: the
        label is advisory and a TclError here would surface as a random failure
        in whatever ran next.
        """
        text = getattr(self, "_plan_text", "")
        try:
            if not text:
                self.plan_label.configure(text="")
                return
            prefix = "当前："
            font = tkfont.Font(font=self.fonts.small)
            width = self.plan_label.winfo_width()
            if width <= 1:
                # Not laid out yet; the <Configure> binding calls us again.
                width = SETTINGS_COLUMN_WIDTH - 3 * GAP_MD
            budget = max(font.measure(prefix), width - 4)
            self.plan_label.configure(
                text=prefix + ellipsize(text, max(20, budget - font.measure(prefix)),
                                        font, cache=self._text_widths)
            )
        except tk.TclError:
            # The label (or the whole window) is already gone.
            pass

    def _current_mode(self) -> BitrateMode:
        return _MODE_BY_LABEL.get(self.mode_var.get(), BitrateMode.VBR)

    def _current_sample_rate(self) -> int | None:
        value = self.rate_var.get()
        if value == KEEP_RATE_LABEL:
            return None
        try:
            return int(value.split()[0])
        except (ValueError, IndexError):
            return None

    def _refresh_bitrate_choices(self) -> None:
        mode = self._current_mode()
        if mode is BitrateMode.VBR:
            values = [
                f"q{quality} · 约 {VBR_QUALITY_TABLE[quality]} kbps"
                + ("（最高）" if quality == 0 else "")
                for quality in range(10)
            ]
        else:
            # Constrain the list to the rates MP3 actually allows at this rate.
            rate = self._current_sample_rate() or 44100
            values = [f"{bitrate} kbps" for bitrate in allowed_bitrates(rate)]
        self.bitrate_box.configure(values=values)
        if self.bitrate_var.get() not in values:
            self.bitrate_var.set(values[0])
        self._refresh_plan_label()

    def _apply_encode_to_bitrate_widget(self, settings: EncodeSettings) -> None:
        if settings.mode is BitrateMode.VBR:
            wanted = f"q{settings.vbr_quality} · 约 {VBR_QUALITY_TABLE[settings.vbr_quality]} kbps"
            if settings.vbr_quality == 0:
                wanted += "（最高）"
        else:
            bitrate = (
                settings.cbr_bitrate
                if settings.mode is BitrateMode.CBR
                else settings.abr_bitrate
            )
            wanted = f"{bitrate} kbps"
        if wanted in list(self.bitrate_box.cget("values")):
            self.bitrate_var.set(wanted)

    def _parse_bitrate(self) -> tuple[int, int]:
        """Return ``(vbr_quality, bitrate_kbps)`` from the combined widget."""
        text = self.bitrate_var.get()
        if self._current_mode() is BitrateMode.VBR:
            digits = "".join(ch for ch in text.split("·")[0] if ch.isdigit())
            return (int(digits) if digits else 2), 0
        digits = "".join(ch for ch in text if ch.isdigit())
        return 0, int(digits) if digits else 192

    def _on_preset(self, _event: object = None) -> None:
        name = self.preset_var.get()
        for preset_name, settings in PRESETS:
            if preset_name != name:
                continue
            self.mode_var.set(_MODE_LABELS[settings.mode])
            self.channel_var.set(_CHANNEL_LABELS[settings.channels])
            self.rate_var.set(
                KEEP_RATE_LABEL
                if settings.sample_rate is None
                else f"{settings.sample_rate} Hz"
            )
            self._refresh_bitrate_choices()
            self._apply_encode_to_bitrate_widget(settings)
            self._refresh_plan_label()
            return
        self.preset_var.set("（自定义）")

    def collect_settings(self) -> EncodeSettings:
        quality, bitrate = self._parse_bitrate()
        return EncodeSettings(
            mode=self._current_mode(),
            vbr_quality=quality,
            cbr_bitrate=bitrate,
            abr_bitrate=bitrate,
            sample_rate=self._current_sample_rate(),
            channels=_CHANNEL_BY_LABEL.get(self.channel_var.get(), ChannelMode.KEEP),
            joint_stereo=bool(self.joint_var.get()),
            copy_tags=bool(self.tags_var.get()),
            write_cover=bool(self.cover_var.get()),
            id3_version=4 if self.id3_var.get() == "2.4" else 3,
        ).clamped()

    def collect_config(self) -> AppConfig:
        cfg = self.config
        cfg.output_mode = self.output_mode_var.get()
        cfg.output_dir = self.output_dir_var.get().strip()
        cfg.template = self.template_var.get().strip() or cfg.template
        cfg.overwrite = _OVERWRITE_BY_LABEL.get(
            self.overwrite_var.get(), OverwritePolicy.RENAME
        ).value
        cfg.recursive = bool(self.recurse_var.get())
        cfg.verify = bool(self.verify_var.get())
        cfg.deep_verify = bool(self.deep_var.get())
        cfg.retries = int(self.retries_var.get())
        cfg.workers = int(self.workers_var.get())
        cfg.remember_ekey = bool(self.remember_ekey_var.get())
        cfg.ekey = self.ekey_var.get().strip() if cfg.remember_ekey else ""
        cfg.ffmpeg_dir = self.ffmpeg_dir_var.get().strip()
        cfg.key_db_path = self.key_db_var.get().strip()
        cfg.encode = self.collect_settings()
        if self.toolchain is not None:
            cfg.ffmpeg_cache = self.toolchain.to_cache()
        return cfg

    # ------------------------------------------------------------------ #
    # queue management
    # ------------------------------------------------------------------ #

    def _on_drop(self, paths: Sequence[str]) -> None:
        self._schedule_add_paths([Path(p) for p in paths])

    def _choose_files(self) -> None:
        patterns = " ".join(f"*.{ext}" for ext in sorted(SUPPORTED_EXTENSIONS))
        chosen = filedialog.askopenfilenames(
            title="选择音频 / 视频 / QQ 音乐加密文件",
            filetypes=[
                ("音频与视频", patterns),
                (
                    "QQ 音乐加密文件",
                    "*.mflac *.mflac0 *.mflach *.mgg *.mgg0 *.mgg1 *.mggl "
                    "*.qmcflac *.qmc0 *.qmc3 *.qmcogg",
                ),
                ("全部文件", "*.*"),
            ],
        )
        if chosen:
            self._schedule_add_paths([Path(p) for p in chosen])

    def _choose_folder(self) -> None:
        chosen = filedialog.askdirectory(title="选择包含音频的文件夹")
        if chosen:
            self._schedule_add_paths([Path(chosen)])

    def _choose_output_dir(self) -> None:
        chosen = filedialog.askdirectory(title="选择输出目录")
        if chosen:
            self.output_dir_var.set(chosen)
            self.output_mode_var.set("custom")
            self._refresh_output_state()

    def _choose_ffmpeg_dir(self) -> None:
        chosen = filedialog.askdirectory(title="选择 ffmpeg 目录")
        if chosen:
            self.ffmpeg_dir_var.set(chosen)
            self._redetect_toolchain()

    def _redetect_toolchain(self) -> None:
        if self._is_running():
            self._set_status("转换进行中，暂不能重新检测 ffmpeg")
            return
        self.config.ffmpeg_dir = self.ffmpeg_dir_var.get().strip()
        if self._toolchain_start_job is not None:
            try:
                self.root.after_cancel(self._toolchain_start_job)
            except tk.TclError:
                pass
            self._toolchain_start_job = None
        self.toolchain = None
        self.toolchain_error = None
        self._refresh_start_button()
        self._render_ffmpeg_badge()
        self._log("正在重新检测 ffmpeg…")
        self._start_toolchain_worker()

    def _choose_key_db(self) -> None:
        chosen = filedialog.askopenfilename(
            title="选择密钥库 JSON", filetypes=[("JSON", "*.json"), ("全部文件", "*.*")]
        )
        if chosen:
            self.key_db_var.set(chosen)
            self._refresh_key_db_status()
            self._log(f"密钥库：{chosen}")

    def add_paths(self, paths: Sequence[Path]) -> None:
        """Synchronously add paths; automation uses this deterministic API."""
        self._add_found(collect_sources(paths, recursive=bool(self.recurse_var.get())))

    def _schedule_add_paths(self, paths: Sequence[Path]) -> None:
        if self._is_running():
            messagebox.showinfo(APP_TITLE, "转换进行中，无法修改队列。")
            return
        recursive = bool(self.recurse_var.get())
        self._scan_generation += 1
        generation = self._scan_generation
        self._active_scans += 1
        self._refresh_start_button()
        self._set_status("正在扫描文件…")

        def scan() -> None:
            try:
                found = collect_sources(paths, recursive=recursive)
                self._background_events.put(("sources", generation, found, None))
            except Exception as exc:  # noqa: BLE001 - surfaced in the UI
                self._background_events.put(("sources", generation, [], str(exc)))

        threading.Thread(target=scan, daemon=True, name="echoshift-scan").start()

    def _add_found(self, found: Sequence[Path]) -> None:
        if not found:
            self._log("没有找到可转换的文件（支持 ffmpeg 能读取的音频 / 视频，以及 QQ 音乐加密容器）")
            self._set_status("就绪")
            return
        known = {item.path for item in self.items}
        added = 0
        for path in found:
            if path in known:
                continue
            item = QueueItem(uid=self._next_uid, path=path)
            self._next_uid += 1
            self.items.append(item)
            known.add(path)
            added += 1
            self._probe_queue.put((item.uid, path))
        self._refresh_rows()
        self._log(f"添加 {added} 个文件（队列共 {len(self.items)} 个）")
        self._set_status(f"队列中有 {len(self.items)} 个文件")

    def _selected_indices(self) -> list[int]:
        return self.queue_view.selection()

    def _on_selection_changed(self, indices: Sequence[int]) -> None:
        self.remove_button.set_enabled(bool(indices))
        self._refresh_queue_details(indices)

    def _drop_probes(self, uids: set[int] | None = None) -> None:
        """Cancel queued probes for removed rows (``None`` cancels all).

        A probe that is already running cannot be recalled, but its result is
        matched by uid and dropped when the row is gone.
        """
        keep: list[tuple[int, Path] | None] = []
        while True:
            try:
                request = self._probe_queue.get_nowait()
            except queue.Empty:
                break
            if request is None:
                # Re-queue the shutdown sentinel; it is not a probe request.
                keep.append(None)
                continue
            uid, path = request
            if uids is not None and uid in uids:
                continue
            keep.append((uid, path))
        for request in keep:
            self._probe_queue.put(request)

    def _remove_selected(self) -> None:
        if self._is_running():
            messagebox.showinfo(APP_TITLE, "转换进行中，无法修改队列。")
            return
        doomed = set(self._selected_indices())
        if not doomed:
            return
        removed = {self.items[index].uid for index in doomed if 0 <= index < len(self.items)}
        self.items = [item for index, item in enumerate(self.items) if index not in doomed]
        self._drop_probes(removed)
        self._refresh_rows()
        self._log(f"移除 {len(doomed)} 个文件（剩余 {len(self.items)} 个）")

    def _clear_all(self) -> None:
        if self._is_running():
            messagebox.showinfo(APP_TITLE, "转换进行中，无法清空队列。")
            return
        if not self.items:
            return
        if not messagebox.askyesno(APP_TITLE, f"确定清空队列中的 {len(self.items)} 个文件吗？"):
            return
        self._scan_generation += 1
        self._batch_indices.clear()
        self.items.clear()
        self._drop_probes()
        self._refresh_rows()
        self._set_status("就绪")
        self._set_progress(0.0)
        self._log("已清空队列。")

    def _refresh_rows(self) -> None:
        self.queue_view.set_rows([item.to_row() for item in self.items])
        self.remove_button.set_enabled(bool(self.queue_view.selection()))
        self._refresh_queue_details()

    def _update_row(self, index: int) -> None:
        self.queue_view.update_row(index, self.items[index].to_row())
        if index in self.queue_view.selection():
            self._refresh_queue_details()

    def _on_row_activated(self, index: int) -> None:
        if not (0 <= index < len(self.items)):
            return
        item = self.items[index]
        if item.output and item.output.exists():
            self._reveal(item.output)
        else:
            self._reveal(item.path)

    def _open_output_dir(self) -> None:
        target = self._output_root()
        if target is None:
            messagebox.showinfo(APP_TITLE, "输出目录与源文件相同，双击队列中的条目即可定位。")
            return
        target.mkdir(parents=True, exist_ok=True)
        self._reveal(target)

    def _output_root(self) -> Path | None:
        if self.output_mode_var.get() == "custom":
            text = self.output_dir_var.get().strip()
            return Path(text) if text else None
        return None

    def _reveal(self, path: Path) -> None:
        import os
        import subprocess

        try:
            if path.is_dir():
                os.startfile(path)  # noqa: S606 - deliberate shell open
            else:
                subprocess.Popen(["explorer", "/select,", str(path)])
        except Exception as exc:  # noqa: BLE001
            self._log(f"无法打开 {path}：{exc}")

    # ------------------------------------------------------------------ #
    # running
    # ------------------------------------------------------------------ #

    def _is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _start(self, indices: Sequence[int] | None = None) -> None:
        if self._is_running():
            return
        # Everything below this point runs on the Tk thread before the worker
        # exists: form validation, reading the key database, repainting the
        # whole queue.  The button looked idle throughout, so an impatient
        # second click repeated all of it.
        self.start_button.set_busy(True, text="准备中…")
        try:
            self._start_locked(indices)
        finally:
            self.start_button.set_busy(False, text=self._START_LABEL)

    def _start_locked(self, indices: Sequence[int] | None) -> None:
        if self.toolchain is None:
            messagebox.showerror(APP_TITLE, f"无法开始：{self.toolchain_error}")
            return
        target_indices = list(indices) if indices is not None else list(range(len(self.items)))
        target_indices = [i for i in target_indices if 0 <= i < len(self.items)]
        if not target_indices:
            messagebox.showinfo(APP_TITLE, "队列为空，请先添加文件。")
            return

        form_errors = self._validate_form()
        if form_errors:
            messagebox.showerror(APP_TITLE, "请先修正设置：\n" + "\n".join(form_errors))
            return

        settings = self.collect_settings()
        try:
            settings.validate()
        except EchoShiftError as exc:
            messagebox.showerror(APP_TITLE, str(exc))
            return

        template = self.template_var.get().strip()
        if not template:
            messagebox.showerror(APP_TITLE, "命名模板不能为空。")
            return

        output_dir = self._output_root()
        if self.output_mode_var.get() == "custom" and output_dir is None:
            messagebox.showerror(APP_TITLE, "请选择输出目录，或改为与源文件同目录。")
            return

        try:
            keystore = KeyStore.build(
                explicit=self.ekey_var.get().strip() or None,
                user_database=Path(self.key_db_var.get()) if self.key_db_var.get() else None,
            )
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_TITLE, f"密钥库读取失败：{exc}")
            return

        options = PipelineOptions(
            settings=settings,
            template=template,
            output_dir=output_dir,
            overwrite=_OVERWRITE_BY_LABEL.get(
                self.overwrite_var.get(), OverwritePolicy.RENAME
            ),
            verify=bool(self.verify_var.get()),
            deep_verify=bool(self.deep_var.get()),
            retries=int(self.retries_var.get()),
            ekey=self.ekey_var.get().strip() or None,
            work_dir=default_work_dir(),
        )

        for index in target_indices:
            item = self.items[index]
            item.state = JobState.PENDING
            item.progress = 0.0
            item.message = ""
            item.output = None
            item.attempts = 0
        self._refresh_rows()

        self._cancel = threading.Event()
        self._batch_indices = set(target_indices)
        self._log_error_count = 0
        self._render_log_toggle()
        self._refresh_start_button()
        self.stop_button.set_enabled(True)
        self.stop_button.set_busy(False)
        self._terminating = False
        self._set_editing_locked(True)
        self._set_progress(0.0)
        self._set_status(f"开始转换 {len(target_indices)} 个文件…")
        self._toggle_log(False)
        self._log("=" * 60)
        self._log(f"参数：{settings.describe()}")
        self._log(f"模板：{template} · 同名文件：{self.overwrite_var.get()}")

        sources = [self.items[index].path for index in target_indices]
        # Read every Tk variable on the main thread: they are not thread-safe.
        requested_workers = max(1, int(self.workers_var.get()))
        workers = effective_workers(requested_workers)
        if workers != requested_workers:
            self._log(f"并发已调整为 {workers}（请求 {requested_workers}，按设备资源限制）")
        else:
            self._log(f"并发：{workers}（外部进程最多同时运行 2 个）")
        self._worker = threading.Thread(
            target=self._run_pipeline,
            args=(options, keystore, sources, workers, target_indices),
            daemon=True,
            name="echoshift-worker",
        )
        self._worker.start()

    def _run_pipeline(
        self,
        options: PipelineOptions,
        keystore: KeyStore,
        sources: list[Path],
        workers: int,
        source_indices: list[int],
    ) -> None:
        pipeline = Pipeline(
            self.toolchain,  # type: ignore[arg-type]
            options,
            keystore=keystore,
            logger=self._queue_background_log,
            process_limiter=self._process_limiter,
        )
        try:
            results = pipeline.run_batch(
                sources,
                on_result=lambda index, result: self._events.put(
                    ("result", source_indices[index], result)
                ),
                on_progress=lambda index, fraction, stage: self._queue_progress(
                    source_indices[index], fraction, stage
                ),
                cancel=self._cancel,
                workers=workers,
                # Log numbering has to match the queue's "#" column even when
                # only a subset runs (retry failed, or a future "convert
                # selected"), otherwise "[1]" names a file that is not row 1.
                index_labels=[index + 1 for index in source_indices],
            )
            self._events.put(("finished", results))
        except Exception as exc:  # noqa: BLE001 - surfaced in the log
            import traceback

            self._queue_background_log(f"内部错误：{type(exc).__name__}: {exc}")
            self._queue_background_log(traceback.format_exc())
            self._events.put(("finished", []))

    def _stop(self) -> None:
        if not self._is_running():
            return
        self._cancel.set()
        self._terminating = True
        self.stop_button.set_enabled(False)
        self.stop_button.set_busy(True, text="正在终止…")
        # Termination only takes effect at a job boundary: the current file may
        # be in a full-decode verification that cannot be interrupted mid-way.
        # Say so, and kill the running ffmpeg shortly after instead of leaving
        # the user staring at a progress bar that will not move for a while.
        self._set_status("正在终止当前任务…")
        self._log("正在终止当前任务；尚未开始的任务将取消。")
        self.root.after(1500, self._terminate_processes_now)

    def _terminate_processes_now(self) -> None:
        if not self._terminating:
            return
        threading.Thread(
            target=terminate_active_processes,
            daemon=True,
            name="echoshift-terminate",
        ).start()

    # ------------------------------------------------------------------ #
    # event pump
    # ------------------------------------------------------------------ #

    def _queue_progress(self, index: int, fraction: float, stage: str) -> None:
        """Keep only the newest update per row so the Tk queue cannot flood."""
        with self._progress_lock:
            self._pending_progress[index] = (fraction, stage)
            if self._progress_event_queued:
                return
            self._progress_event_queued = True
        self._events.put(("progress_batch",))

    def _queue_background_log(self, text: str) -> None:
        """Batch worker logs so a large queue cannot flood Tk events."""
        with self._log_lock:
            self._pending_logs.append(str(text))
            if self._log_event_queued:
                return
            self._log_event_queued = True
        self._events.put(("log_batch",))

    def _take_progress(self) -> dict[int, tuple[float, str]]:
        with self._progress_lock:
            pending = self._pending_progress
            self._pending_progress = {}
            self._progress_event_queued = False
        return pending

    def _take_logs(self) -> list[str]:
        with self._log_lock:
            pending = self._pending_logs
            self._pending_logs = []
            self._log_event_queued = False
        return pending

    def _drain_events(self) -> None:
        if self._closing:
            return
        processed = 0
        for events in (self._events, self._background_events):
            while processed < 120:
                try:
                    event = events.get_nowait()
                except queue.Empty:
                    break
                processed += 1
                self._handle_event(event)
        self.root.after(80, self._drain_events)

    def _handle_event(self, event: tuple[Any, ...]) -> None:
        kind = event[0]
        if kind == "log":
            self._log(str(event[1]))
        elif kind == "log_batch":
            pending_logs = self._take_logs()
            if pending_logs:
                self._log("\n".join(pending_logs))
        elif kind == "progress_batch":
            for index, (fraction, stage) in self._take_progress().items():
                self._apply_progress(index, fraction, stage, refresh_overall=False)
            self._refresh_overall_progress()
        elif kind == "progress":
            _, index, fraction, stage = event
            self._apply_progress(int(index), float(fraction), str(stage))
        elif kind == "result":
            _, index, result = event
            self._apply_result(int(index), result)
        elif kind == "finished":
            self._finish(list(event[1]) if event[1] else [])
        elif kind == "probe":
            _, index, desc, duration = event
            if 0 <= index < len(self.items):
                if desc:
                    self.items[index].source_desc = desc
                if duration:
                    self.items[index].duration = duration
                self._update_row(index)
        elif kind == "toolchain":
            _, generation, toolchain, error = event
            if generation != self._toolchain_generation:
                # A newer detection is already running; this one is stale.
                return
            self.toolchain = toolchain
            self.toolchain_error = error
            self._render_ffmpeg_badge()
            if toolchain is not None:
                self.ffmpeg_tooltip.update_text(toolchain.describe())
                self._refresh_start_button()
                self._log(f"ffmpeg：{toolchain.describe()}")
                for item in self.items:
                    if not item.source_desc:
                        self._probe_queue.put((item.uid, item.path))
            else:
                self.ffmpeg_tooltip.update_text(str(error))
                self._refresh_start_button()
                self._log(f"ffmpeg 不可用：{error}")
        elif kind == "sources":
            _, generation, found, error = event
            self._active_scans = max(0, self._active_scans - 1)
            if generation != self._scan_generation:
                return
            if error:
                self._log(f"扫描文件失败：{error}")
                self._set_status("扫描失败")
            elif self._is_running():
                self._log("扫描已完成；转换期间不修改队列，请稍后重新添加该文件夹。")
            else:
                self._add_found(found)
            if self.toolchain is not None and not self._is_running() and self._active_scans == 0:
                self._refresh_start_button()

    def _apply_progress(
        self,
        index: int,
        fraction: float,
        stage: str,
        *,
        refresh_overall: bool = True,
    ) -> None:
        if not (0 <= index < len(self.items)):
            return
        item = self.items[index]
        item.progress = float(fraction)
        stage_state = {
            "解密中": JobState.DECRYPTING,
            "转码中": JobState.ENCODING,
            "校验中": JobState.VERIFYING,
        }.get(str(stage))
        if stage_state is not None:
            item.state = stage_state
        self._update_row(index)
        if refresh_overall:
            self._refresh_overall_progress()

    def _apply_result(self, index: int, result: JobResult) -> None:
        if not (0 <= index < len(self.items)):
            return
        item = self.items[index]
        item.state = result.state
        item.progress = 1.0 if result.state.is_terminal else item.progress
        item.output = result.output
        item.warnings = list(result.warnings)
        item.attempts = result.attempts

        if result.source_info is not None:
            item.source_desc = result.source_info.describe()
            if result.source_info.duration:
                item.duration = _format_duration(result.source_info.duration)
        elif result.container is not None:
            item.source_desc = result.container.short_describe()
        if result.plan is not None:
            item.plan_desc = result.plan.describe()

        if result.state is JobState.DONE:
            item.message = f"{result.elapsed:.1f}s"
            if result.report is not None:
                item.message = f"{result.elapsed:.1f}s · {result.report.summary()}"
        else:
            item.message = result.message
            if result.attempts > 1:
                item.message = f"{result.message} · 已尝试 {result.attempts} 次"

        self._update_row(index)
        self._refresh_overall_progress()

    def _finish(self, results: list[JobResult]) -> None:
        self._set_editing_locked(False)
        self._terminating = False
        self.stop_button.set_enabled(False)
        self.retry_button.set_enabled(False)
        # The batch is over, so nothing is "in flight" any more; the progress
        # bar goes back to describing the whole queue.
        self._batch_indices.clear()
        self._refresh_start_button()
        done = sum(1 for r in results if r.state is JobState.DONE)
        failed = sum(1 for r in results if r.state is JobState.FAILED)
        skipped = sum(1 for r in results if r.state is JobState.SKIPPED)
        cancelled = sum(1 for r in results if r.state is JobState.CANCELLED)
        total = len(results)
        self._set_progress(1.0 if total and not cancelled else 0.0)

        parts = [f"完成 {done}"]
        if failed:
            parts.append(f"失败 {failed}")
        if skipped:
            parts.append(f"跳过 {skipped}")
        if cancelled:
            parts.append(f"取消 {cancelled}")
        summary = f"共 {total} 个文件 · " + " · ".join(parts)
        self._set_status(("已停止 · " if cancelled else "") + summary)
        self._log(summary)

        if failed:
            self.retry_button.set_enabled(True)
            self._log_error_count = failed
            self._render_log_toggle()
            self._toggle_log(True)
            failed_indices = [index for index, result in enumerate(results) if result.state is JobState.FAILED]
            self.queue_view.select_indices(failed_indices[:1])
            self._detail_expanded = True
            self._refresh_queue_details(failed_indices[:1])
            first = next((r for r in results if r.state is JobState.FAILED), None)
            if first is not None:
                self._log(f"首个失败原因：{first.source.name} → {first.message}")
        elif done:
            self._log("全部成功。")

    def _retry_failed(self) -> None:
        if self._is_running():
            return
        failed = [index for index, item in enumerate(self.items) if item.state is JobState.FAILED]
        if not failed:
            self._set_status("没有失败项可重试")
            return
        self.queue_view.select_indices(failed)
        self._start(failed)

    def _refresh_overall_progress(self) -> None:
        # Only the items taking part in the current run count towards the bar.
        # Averaging over the whole queue meant a subset run (retry failed) could
        # never reach 100%: the untouched entries kept contributing zero.
        active = self._batch_indices or set(range(len(self.items)))
        counted = [self.items[index] for index in active if index < len(self.items)]
        if not counted:
            self._set_progress(0.0)
            return
        self._set_progress(sum(item.progress for item in counted) / len(counted))
        if self._is_running():
            finished = sum(1 for item in counted if item.state.is_terminal)
            if self._terminating:
                self._set_status(f"正在终止 · 已完成 {finished}/{len(counted)}")
                return
            current = next(
                (item for item in counted if not item.state.is_terminal and item.state is not JobState.PENDING),
                None,
            )
            if current is None:
                self._set_status(f"进行中 {finished}/{len(counted)}")
            else:
                name = current.path.name
                if len(name) > 32:
                    name = name[:29] + "…"
                self._set_status(
                    f"{current.state.label} · {name} · {finished}/{len(counted)}"
                )

    # ------------------------------------------------------------------ #
    # background probing (fills the source/duration columns)
    # ------------------------------------------------------------------ #

    def _start_toolchain_worker(self, ffmpeg_dir: str | None = None) -> None:
        self._toolchain_start_job = None
        if self._closing:
            return
        selected_dir = ffmpeg_dir
        if selected_dir is None and hasattr(self, "ffmpeg_dir_var"):
            selected_dir = self.ffmpeg_dir_var.get().strip() or None
        # Detect runs are cheap to trigger repeatedly ("重新检测"), so tag each
        # one and drop results that a newer detection has already superseded.
        self._toolchain_generation += 1
        generation = self._toolchain_generation

        def resolve() -> None:
            try:
                toolchain = find_toolchain(
                    selected_dir,
                    cached=self.config.ffmpeg_cache,
                )
                self._background_events.put(("toolchain", generation, toolchain, None))
            except ToolNotFoundError as exc:
                self._background_events.put(("toolchain", generation, None, str(exc)))
            except Exception as exc:  # noqa: BLE001 - startup must stay alive
                self._background_events.put(
                    ("toolchain", generation, None, f"{type(exc).__name__}: {exc}")
                )

        self._toolchain_thread = threading.Thread(
            target=resolve,
            daemon=True,
            name="echoshift-toolchain",
        )
        self._toolchain_thread.start()

    def _start_probe_worker(self) -> None:
        def loop() -> None:
            while True:
                request = self._probe_queue.get()
                if request is None:
                    return
                uid, path = request
                if self.toolchain is None:
                    continue
                try:
                    # Look the row up by uid: another thread may have added or
                    # removed entries since this request was queued, and an
                    # index captured back then would name a different file.
                    index = next(
                        (i for i, item in enumerate(self.items) if item.uid == uid), None
                    )
                    if index is None:
                        continue
                    desc, duration = self._describe_source(path)
                    self._background_events.put(("probe", index, desc, duration))
                except Exception:  # noqa: BLE001 - probing is best-effort
                    continue

        self._probe_thread = threading.Thread(target=loop, daemon=True, name="echoshift-probe")
        self._probe_thread.start()

    def _describe_source(self, path: Path) -> tuple[str, str]:
        assert self.toolchain is not None
        if is_qmc_path(path):
            # Decrypt only the leading bytes: enough to fill in the duration
            # column without paying for a full decrypt just to list the queue.
            container, peeked = peek_header(path, None)
            if container is None:  # not reachable for a QMC path, but be safe
                return "", _format_duration(peeked.duration)
            desc = container.short_describe()
            if container.needs_ekey:
                desc += " · 需要 ekey"
            elif peeked.describe():
                desc += f" · {peeked.describe()}"
            return desc, _format_duration(peeked.duration)
        media = probe(
            self.toolchain.ffprobe,
            path,
            process_limiter=self._process_limiter,
        )
        return media.describe(), _format_duration(media.duration)

    # ------------------------------------------------------------------ #
    # small helpers
    # ------------------------------------------------------------------ #

    def _clear_combobox_selection(self, event: Any) -> None:
        """Drop the entry highlight a ttk.Combobox keeps after a selection."""
        widget = getattr(event, "widget", None)
        if widget is None:
            return
        try:
            widget.selection_clear()
        except Exception:  # noqa: BLE001 - purely cosmetic
            pass
        try:
            widget.icursor("end")
        except Exception:  # noqa: BLE001 - purely cosmetic
            pass

    def _start_enable_reason(self) -> str | None:
        """Why "开始转换" cannot run, or ``None`` when it can."""
        if self._closing:
            return None
        if self.toolchain is None:
            return (
                f"ffmpeg 不可用：{self.toolchain_error}"
                if self.toolchain_error
                else "正在检测 ffmpeg…"
            )
        if self._active_scans:
            return "正在扫描文件…"
        if not self.items or self._is_running():
            return "队列为空，请先添加文件" if not self.items else "转换进行中"
        return None

    def _refresh_start_button(self) -> None:
        """Keep the primary action's enabled state and its explanation in sync.

        The button is disabled from a dozen places, and a greyed-out primary
        action with a stale tooltip is a dead end: the tooltip is the only place
        the reason can be read.
        """
        if not hasattr(self, "start_button"):
            return
        reason = self._start_enable_reason()
        self.start_button.set_enabled(reason is None, reason=reason)

    def _set_editing_locked(self, locked: bool) -> None:
        """Freeze the controls whose changes cannot affect a running batch.

        Queue edits already bounced off a blocking message box mid-conversion;
        locking them turns "click, get told no" into "visibly unavailable".
        """
        reason = "转换进行中，暂不可修改" if locked else None
        self._editing_locked = bool(locked)
        for button in (
            self.add_files_button,
            self.add_folder_button,
            self.clear_button,
            self.remove_button,
        ):
            if locked:
                button.set_enabled_locked(reason)
            else:
                button.restore_enabled()
        if locked:
            self.retry_button.set_enabled_locked(reason)
        else:
            self.retry_button.restore_enabled()

        previous = self._locked_widget_states
        for widget, kind in self._lockable_widgets():
            key = str(widget)
            if kind == "input":
                try:
                    if locked:
                        previous[key] = str(widget.cget("state"))
                        widget.configure(state="disabled")
                    elif key in previous:
                        widget.configure(state=previous[key])
                except tk.TclError:
                    continue
            elif locked:
                # Canvas controls have no real "state" option: they would accept
                # state="disabled" and ignore it, so ask them directly.
                previous[key] = "enabled" if widget.enabled else "disabled"
                widget.set_enabled(False)
            elif key in previous:
                widget.set_enabled(previous[key] == "enabled")
        self._locked_widget_states = {} if not locked else previous
        # Last, because it re-applies the dependency rules (weak-verify disables
        # the deep check, mono disables joint stereo) which must still hold.
        self._refresh_dependency_state()

    def _lockable_widgets(self) -> list[tuple[Any, str]]:
        """Inputs whose value is baked into the batch at start time."""
        if self._lockable_cache is None:
            self._lockable_cache = [
                (self.preset_box, "input"),
                (self.mode_control, "control"),
                (self.bitrate_box, "input"),
                (self.rate_box, "input"),
                (self.channel_box, "input"),
                (self.output_mode_control, "control"),
                (self.output_entry, "input"),
                (self.output_browse, "control"),
                (self.template_box, "input"),
                (self.overwrite_box, "input"),
                (self.id3_control, "control"),
                (self.workers_stepper, "control"),
                (self.retries_stepper, "control"),
                (self.ekey_entry, "input"),
            ]
        return self._lockable_cache

    def _set_status(self, text: str) -> None:
        if self._closing:
            return
        self.status_var.set(text)

    def _set_progress(self, fraction: float) -> None:
        if self._closing:
            return
        self.progress.set(fraction)

    def _log(self, text: str) -> None:
        if self._closing:
            # Worker threads can still report after the window is gone; touching
            # a destroyed widget raises TclError inside that thread.
            return
        # Only follow the tail while the view is already at the bottom.  A batch
        # logs continuously, and yanking the view down on every line made it
        # impossible to read an earlier failure while the run continued.
        try:
            following = self.log_widget.yview()[1] >= 0.999
        except tk.TclError:
            following = True
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text + "\n")
        # Keep the buffer bounded so long batches stay responsive.
        if int(self.log_widget.index("end-1c").split(".")[0]) > 4000:
            self.log_widget.delete("1.0", "1000.0")
        if following:
            self.log_widget.see("end")
        self.log_widget.configure(state="disabled")
        write_diagnostic(text)

    def _on_close(self) -> None:
        if self._close_requested or self._closing:
            return
        if self._is_running():
            if not messagebox.askyesno(APP_TITLE, "转换仍在进行，确定要退出吗？"):
                return
            self._close_requested = True
            self._close_started = time.monotonic()
            self._cancel.set()
            self.stop_button.set_enabled(False)
            self.start_button.set_enabled(False)
            self._set_status("正在停止转换并退出…")
            self.root.after(50, self._poll_close)
            return
        self._finalize_close()

    def _poll_close(self) -> None:
        if self._closing:
            return
        if not self._is_running():
            self._finalize_close()
            return
        elapsed = time.monotonic() - self._close_started
        if elapsed >= 1.0 and not self._termination_started:
            self._termination_started = True
            threading.Thread(
                target=terminate_active_processes,
                daemon=True,
                name="echoshift-terminate",
            ).start()
        if elapsed >= 5.0:
            self._finalize_close()
            return
        self.root.after(50, self._poll_close)

    def _finalize_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self._toolchain_start_job is not None:
            try:
                self.root.after_cancel(self._toolchain_start_job)
            except tk.TclError:
                pass
            self._toolchain_start_job = None
        self._probe_queue.put(None)
        try:
            saved_to = self.collect_config().save()
            self._log(f"设置已保存到 {saved_to}")
        except OSError as exc:
            # Failing silently here means the user loses every setting they just
            # changed without ever being told, so say something.
            messagebox.showwarning(
                APP_TITLE,
                f"设置无法保存到 {config_path()}\n{exc}\n\n"
                "本次调整的选项在下次启动时不会保留。",
            )
        self.root.destroy()


def _window_icon() -> Path | None:
    """The .ico for the window and taskbar, if one can be found.

    The packaged exe carries the icon as a resource, but a source run does not:
    without this the window and taskbar show Tk's default feather while the
    header shows the real mark.  ``assets`` is bundled into the payload (see
    ``packaging/EchoShift.spec``) and also lives beside the source tree.
    """
    for directory in resource_dir("assets"):
        candidate = directory / "echoshift.ico"
        if candidate.is_file():
            return candidate
    return None


def _apply_window_icon(root: tk.Misc) -> None:
    """Give the window the same mark the header and the exe use.

    ``iconbitmap`` is the Windows path and picks the right size from the .ico's
    multi-resolution image; ``iconphoto`` covers everything else.  Neither is
    fatal if it fails -- an icon is not worth refusing to start over.
    """
    icon = _window_icon()
    if icon is None:
        return
    try:
        root.iconbitmap(default=str(icon))
        return
    except tk.TclError:
        pass
    try:
        photo = tk.PhotoImage(file=str(icon.with_suffix(".png")))
        root.iconphoto(True, photo)
        setattr(root, "_icon_photo", photo)  # keep a reference alive
    except (tk.TclError, OSError):
        pass


def _enable_dpi_awareness() -> None:
    """Opt into real DPI scaling before any window exists.

    Without this, Windows renders a high-DPI display by bitmap-stretching the
    whole window: text looks soft and every resize costs a scaled blit, which
    is exactly what makes dragging the window edge feel sluggish.  Declaring
    awareness lets Tk lay the widgets out at the real pixel density instead.
    """
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)  # SYSTEM_DPI_AWARE
        return
    except Exception:  # noqa: BLE001 - fall back to the older entry point
        pass
    try:
        ctypes.WinDLL("user32").SetProcessDPIAware()
    except Exception:  # noqa: BLE001 - nothing more we can do
        pass


def main() -> int:
    """Entry point for ``echoshift-gui``."""
    _enable_dpi_awareness()
    root = tk.Tk()
    _apply_window_icon(root)
    # Scaling is deliberately left to Tk: it derives it from the real DPI once
    # the process is DPI-aware.  Forcing a value made the UI the wrong size on
    # every display that was not 96 dpi.
    EchoShiftApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
