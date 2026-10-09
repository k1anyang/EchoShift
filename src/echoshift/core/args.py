"""Build the ffmpeg command line for one conversion."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .settings import BitrateMode, EncodeSettings, ResolvedPlan

__all__ = ["build_ffmpeg_args", "build_decode_check_args", "describe"]

#: Baseline flags: no banner, never read stdin (the GUI owns the console),
#: and only surface real errors on stderr.
_BASE = ("-hide_banner", "-nostdin", "-loglevel", "error")


def _bitrate_args(settings: EncodeSettings, plan: ResolvedPlan) -> list[str]:
    if settings.mode is BitrateMode.VBR:
        # libmp3lame treats -q:a as the LAME VBR quality (0 best .. 9 worst).
        return ["-q:a", str(settings.vbr_quality)]

    bitrate = settings.cbr_bitrate if settings.mode is BitrateMode.CBR else settings.abr_bitrate
    if plan.nominal_kbps is not None:
        bitrate = plan.nominal_kbps
    args = ["-b:a", f"{bitrate}k"]
    if settings.mode is BitrateMode.ABR:
        args += ["-abr", "1"]
    return args


def build_ffmpeg_args(
    ffmpeg: Path,
    input_path: Path,
    output_path: Path,
    settings: EncodeSettings,
    plan: ResolvedPlan,
    *,
    overwrite: bool = True,
    progress: bool = True,
) -> list[str]:
    """Assemble the full ffmpeg argv for a FLAC/WAV/... to MP3 conversion."""
    args: list[str] = [str(ffmpeg), *_BASE]
    args.append("-y" if overwrite else "-n")

    args += ["-i", str(input_path)]

    # Audio first so the MP3's stream 0 is always the music.
    args += ["-map", "0:a:0"]
    if settings.write_cover:
        # '?' makes the mapping optional: plain FLACs have no video stream.
        args += [
            "-map",
            "0:v?",
            "-c:v",
            "copy",
            "-disposition:v:0",
            "attached_pic",
            "-metadata:s:v",
            "title=Album cover",
        ]

    args += ["-map_metadata", "0" if settings.copy_tags else "-1"]

    args += ["-c:a", "libmp3lame"]
    args += _bitrate_args(settings, plan)
    args += ["-joint_stereo", "1" if settings.joint_stereo else "0"]

    if plan.sample_rate is not None:
        args += ["-ar", str(plan.sample_rate)]
    if plan.channels is not None:
        args += ["-ac", str(plan.channels)]

    args += ["-id3v2_version", str(settings.id3_version)]
    args += ["-write_id3v1", "1" if settings.write_id3v1 else "0"]

    if progress:
        args += ["-progress", "pipe:1", "-nostats"]

    args.append(str(output_path))
    return args


def build_decode_check_args(ffmpeg: Path, path: Path) -> list[str]:
    """argv for a full decode of ``path`` with decoding errors treated as fatal."""
    return [
        str(ffmpeg),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-xerror",
        "-i",
        str(path),
        "-f",
        "null",
        "-",
    ]


def describe(cmd: Sequence[str]) -> str:
    """Render an argv for logs, quoting only the parts that need it."""
    out: list[str] = []
    for token in cmd:
        out.append(f'"{token}"' if " " in token else token)
    return " ".join(out)
