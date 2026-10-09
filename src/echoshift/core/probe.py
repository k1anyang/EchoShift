"""Typed view over ``ffprobe`` output."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import threading
from typing import Any

from ..errors import UnsupportedInputError
from .ffmpeg import ProcessLimiter, ffprobe_json

__all__ = [
    "AudioStreamInfo",
    "CoverInfo",
    "MediaInfo",
    "probe",
    "LOSSLESS_CODECS",
]

#: Codecs treated as lossless, so the UI can warn before a lossy re-encode.
LOSSLESS_CODECS = frozenset(
    {
        "flac",
        "alac",
        "ape",
        "wavpack",
        "tta",
        "tak",
        "truehd",
        "mlp",
        "shorten",
        "als",
        "dst",
    }
)


def _as_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalise_tags(*sources: dict[str, Any] | None) -> dict[str, str]:
    """Merge tag dictionaries and lower-case the keys.

    ffprobe reports ID3 keys upper-cased and Vorbis comment keys lower-cased,
    so anything that reads tags has to be case-insensitive.
    """
    merged: dict[str, str] = {}
    for source in sources:
        if not source:
            continue
        for key, value in source.items():
            if value is None:
                continue
            merged[str(key).lower()] = str(value)
    return merged


@dataclass(frozen=True)
class AudioStreamInfo:
    index: int
    codec_name: str
    codec_long_name: str
    sample_rate: int | None
    channels: int | None
    channel_layout: str | None
    bit_rate: int | None
    duration: float | None
    bits_per_raw_sample: int | None
    tags: dict[str, str] = field(default_factory=dict)

    @property
    def is_lossless(self) -> bool:
        name = (self.codec_name or "").lower()
        return name in LOSSLESS_CODECS or name.startswith(("pcm_", "dsd_"))

    def describe(self) -> str:
        parts = [self.codec_name.upper() or "?"]
        if self.sample_rate:
            parts.append(f"{self.sample_rate / 1000:g} kHz")
        if self.channels:
            parts.append({1: "单声道", 2: "立体声"}.get(self.channels, f"{self.channels} 声道"))
        if self.bits_per_raw_sample:
            parts.append(f"{self.bits_per_raw_sample} bit")
        if self.bit_rate:
            parts.append(f"{round(self.bit_rate / 1000)} kbps")
        return " · ".join(parts)


@dataclass(frozen=True)
class CoverInfo:
    index: int
    codec_name: str
    width: int | None
    height: int | None
    mime: str | None

    def describe(self) -> str:
        size = f"{self.width}×{self.height}" if self.width and self.height else "?"
        return f"{self.codec_name.upper()} 封面 {size}"


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    format_name: str
    format_long_name: str
    duration: float
    size: int
    bit_rate: int | None
    tags: dict[str, str] = field(default_factory=dict)
    audio: AudioStreamInfo | None = None
    cover: CoverInfo | None = None
    stream_count: int = 0

    def tag(self, name: str, default: str = "") -> str:
        return self.tags.get(name.lower(), default) or default

    @property
    def is_lossless(self) -> bool:
        return bool(self.audio and self.audio.is_lossless)

    def describe(self) -> str:
        bits: list[str] = []
        # For an MP3 the container and codec are both "mp3"; say it once.
        container = (self.format_name or "").split(",")[0].upper()
        codec = (self.audio.codec_name if self.audio else "").upper()
        if container and container != codec:
            bits.append(container)
        if self.duration:
            minutes, seconds = divmod(int(self.duration), 60)
            bits.append(f"{minutes}:{seconds:02d}")
        if self.audio:
            bits.append(self.audio.describe())
        return " · ".join(bits)


def probe(
    ffprobe: Path,
    path: Path | str,
    *,
    timeout: float = 120,
    cancel: threading.Event | None = None,
    process_limiter: ProcessLimiter | None = None,
) -> MediaInfo:
    """Read ``path`` with ffprobe and return a :class:`MediaInfo`."""
    path = Path(path)
    document = ffprobe_json(
        ffprobe,
        path,
        timeout=timeout,
        cancel=cancel,
        process_limiter=process_limiter,
    )

    streams: list[dict[str, Any]] = document.get("streams") or []
    fmt: dict[str, Any] = document.get("format") or {}

    if not streams:
        raise UnsupportedInputError(f"{path.name} 里没有任何可识别的流")

    audio_raw = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if audio_raw is None:
        raise UnsupportedInputError(f"{path.name} 不包含音频流，无法转换")

    cover_raw = None
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition") or {}
        if disposition.get("attached_pic") in (1, "1", True):
            cover_raw = stream
            break

    format_tags = _normalise_tags(fmt.get("tags"))
    audio_tags = _normalise_tags(audio_raw.get("tags"))
    tags = {**format_tags, **{k: v for k, v in audio_tags.items() if k not in format_tags}}

    duration = _as_float(fmt.get("duration"))
    if duration is None:
        duration = _as_float(audio_raw.get("duration"))
    if duration is None:
        duration = 0.0

    audio = AudioStreamInfo(
        index=_as_int(audio_raw.get("index")) or 0,
        codec_name=str(audio_raw.get("codec_name") or ""),
        codec_long_name=str(audio_raw.get("codec_long_name") or ""),
        sample_rate=_as_int(audio_raw.get("sample_rate")),
        channels=_as_int(audio_raw.get("channels")),
        channel_layout=audio_raw.get("channel_layout"),
        bit_rate=_as_int(audio_raw.get("bit_rate")),
        duration=_as_float(audio_raw.get("duration")),
        bits_per_raw_sample=_as_int(audio_raw.get("bits_per_raw_sample"))
        or _as_int(audio_raw.get("bits_per_sample")),
        tags=audio_tags,
    )

    cover = None
    if cover_raw is not None:
        cover = CoverInfo(
            index=_as_int(cover_raw.get("index")) or 0,
            codec_name=str(cover_raw.get("codec_name") or ""),
            width=_as_int(cover_raw.get("width")),
            height=_as_int(cover_raw.get("height")),
            mime=(cover_raw.get("tags") or {}).get("mimetype"),
        )

    return MediaInfo(
        path=path,
        format_name=str(fmt.get("format_name") or ""),
        format_long_name=str(fmt.get("format_long_name") or ""),
        duration=duration,
        size=_as_int(fmt.get("size")) or (path.stat().st_size if path.exists() else 0),
        bit_rate=_as_int(fmt.get("bit_rate")),
        tags=tags,
        audio=audio,
        cover=cover,
        stream_count=len(streams),
    )
