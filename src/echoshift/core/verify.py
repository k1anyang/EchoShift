"""Post-conversion verification.

A converter that silently emits a truncated or undecodable MP3 is worse than
one that fails loudly, so every job ends with the checks below: the file must
exist, ffprobe must agree it is MP3 with the requested sample rate and channel
count, its duration must match the source, and (optionally) a full decode must
run clean.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

from .args import build_decode_check_args
from ..errors import ProbeError
from .ffmpeg import ProcessLimiter, Toolchain, run
from .probe import MediaInfo, probe
from .settings import BitrateMode, EncodeSettings, ResolvedPlan

__all__ = ["Check", "VerificationReport", "verify_output"]

#: MP3 adds encoder delay/padding, so exact duration equality is not expected.
_MIN_DURATION_TOLERANCE = 1.5
_DURATION_TOLERANCE_RATIO = 0.02


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str

    def __str__(self) -> str:
        return f"{'✓' if self.ok else '✗'} {self.name}：{self.detail}"


@dataclass(frozen=True)
class VerificationReport:
    ok: bool
    checks: tuple[Check, ...]
    info: MediaInfo | None = None

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def summary(self) -> str:
        if self.ok:
            return f"校验通过（{len(self.checks)} 项）"
        return "；".join(f"{c.name}: {c.detail}" for c in self.failures)

    def render(self) -> str:
        return "\n".join(str(c) for c in self.checks)


def _duration_tolerance(source_seconds: float) -> float:
    return max(_MIN_DURATION_TOLERANCE, source_seconds * _DURATION_TOLERANCE_RATIO)


def verify_output(
    toolchain: Toolchain,
    source: MediaInfo,
    output: Path,
    settings: EncodeSettings,
    plan: ResolvedPlan,
    *,
    deep: bool = True,
    cancel: threading.Event | None = None,
    process_limiter: ProcessLimiter | None = None,
    display_name: str | None = None,
) -> VerificationReport:
    """Run the full battery of checks against ``output``."""
    checks: list[Check] = []

    # 1. The file must exist and be non-trivial.
    if not output.is_file():
        return VerificationReport(
            ok=False,
            checks=(Check("文件存在", False, f"没有生成 {output}"),),
        )
    size = output.stat().st_size
    checks.append(
        Check(
            "文件存在",
            size > 1024,
            f"{display_name or output.name} · {size / 1024:.0f} KiB",
        )
    )

    # 2. ffprobe must be able to read it back.
    try:
        info = probe(
            toolchain.ffprobe,
            output,
            cancel=cancel,
            process_limiter=process_limiter,
        )
    except ProbeError as exc:
        checks.append(Check("可解析", False, str(exc).splitlines()[0]))
        return VerificationReport(ok=False, checks=tuple(checks), info=None)
    checks.append(Check("可解析", True, info.describe()))

    # 3. It must actually be MP3.
    codec = (info.audio.codec_name if info.audio else "") or ""
    checks.append(
        Check("编码格式", codec == "mp3", f"codec = {codec or '未知'}")
    )

    # 4. Requested sample rate honoured.
    if plan.sample_rate is not None:
        actual = info.audio.sample_rate if info.audio else None
        checks.append(
            Check(
                "采样率",
                actual == plan.sample_rate,
                f"期望 {plan.sample_rate} Hz，实际 {actual} Hz",
            )
        )

    # 5. Requested channel count honoured.
    if plan.channels is not None:
        actual = info.audio.channels if info.audio else None
        checks.append(
            Check(
                "声道数",
                actual == plan.channels,
                f"期望 {plan.channels}，实际 {actual}",
            )
        )

    # 6. Duration must track the source.
    if source.duration > 0 and info.duration > 0:
        delta = abs(info.duration - source.duration)
        tolerance = _duration_tolerance(source.duration)
        checks.append(
            Check(
                "时长",
                delta <= tolerance,
                f"源 {source.duration:.2f}s，输出 {info.duration:.2f}s，"
                f"偏差 {delta:.2f}s（容差 {tolerance:.2f}s）",
            )
        )

    # 7. CBR output should actually sit near the requested bitrate.
    if settings.mode is BitrateMode.CBR and plan.nominal_kbps and info.bit_rate:
        measured = info.bit_rate / 1000
        drift = abs(measured - plan.nominal_kbps) / plan.nominal_kbps
        checks.append(
            Check(
                "码率",
                drift <= 0.12,
                f"目标 {plan.nominal_kbps} kbps，实测 {measured:.0f} kbps",
            )
        )

    # 8. Full decode, to catch corrupted frames a probe would not reveal.
    if deep:
        result = run(
            build_decode_check_args(toolchain.ffmpeg, output),
            check=False,
            timeout=120,
            cancel=cancel,
            process_limiter=process_limiter,
        )
        stderr = (result.stderr or "").strip()
        checks.append(
            Check(
                "完整解码",
                result.returncode == 0 and not stderr,
                "无解码错误" if result.returncode == 0 and not stderr
                else (stderr.splitlines()[-1] if stderr else f"exit {result.returncode}"),
            )
        )

    return VerificationReport(
        ok=all(c.ok for c in checks), checks=tuple(checks), info=info
    )
