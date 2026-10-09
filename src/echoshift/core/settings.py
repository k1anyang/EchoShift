"""Encode settings: bitrate mode, sample rate, channel handling.

This module owns the rules that make an MP3 request *legal*.  MP3 is stricter
than people expect: ``libmp3lame`` only accepts nine sample rates and two
channel layouts, and the set of legal bitrates depends on which MPEG version
the chosen sample rate implies.  Everything here exists so the UI can refuse
an impossible combination up front instead of letting ffmpeg produce something
surprising.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from ..errors import SettingsError

__all__ = [
    "BitrateMode",
    "ChannelMode",
    "EncodeSettings",
    "ResolvedPlan",
    "SAMPLE_RATES",
    "MP3_BITRATES_MPEG1",
    "MP3_BITRATES_MPEG2",
    "VBR_QUALITY_TABLE",
    "mpeg_version",
    "allowed_bitrates",
    "nearest_supported_rate",
    "PRESETS",
]

#: The only sample rates libmp3lame accepts.
SAMPLE_RATES: tuple[int, ...] = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000)

#: Layer III bitrates for each MPEG version.
MP3_BITRATES_MPEG1: tuple[int, ...] = (32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)
MP3_BITRATES_MPEG2: tuple[int, ...] = (8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)

#: Approximate average bitrate LAME's VBR presets land on, for UI display.
VBR_QUALITY_TABLE: dict[int, int] = {
    0: 245,
    1: 225,
    2: 190,
    3: 175,
    4: 165,
    5: 130,
    6: 115,
    7: 100,
    8: 85,
    9: 65,
}

_MPEG2_RATES = frozenset({8000, 11025, 12000, 16000, 22050, 24000})
_MPEG1_RATES = frozenset({32000, 44100, 48000})


class BitrateMode(str, Enum):
    """How the encoder should spend bits."""

    VBR = "vbr"
    ABR = "abr"
    CBR = "cbr"

    @property
    def label(self) -> str:
        return {
            BitrateMode.VBR: "VBR 可变码率",
            BitrateMode.ABR: "ABR 平均码率",
            BitrateMode.CBR: "CBR 固定码率",
        }[self]


class ChannelMode(str, Enum):
    """What to do with the channel layout."""

    KEEP = "keep"
    MONO = "mono"
    STEREO = "stereo"

    @property
    def label(self) -> str:
        return {
            ChannelMode.KEEP: "保持原样",
            ChannelMode.MONO: "下混为单声道",
            ChannelMode.STEREO: "强制立体声",
        }[self]

    @property
    def ffmpeg_channels(self) -> int | None:
        return {ChannelMode.KEEP: None, ChannelMode.MONO: 1, ChannelMode.STEREO: 2}[self]


def mpeg_version(sample_rate: int) -> str:
    """Return the MPEG audio version implied by a sample rate."""
    if sample_rate in _MPEG1_RATES:
        return "1"
    if sample_rate in _MPEG2_RATES:
        return "2.5" if sample_rate < 16000 else "2"
    raise SettingsError(
        f"MP3 不支持 {sample_rate} Hz 采样率，可选："
        + "、".join(str(r) for r in SAMPLE_RATES)
    )


def allowed_bitrates(sample_rate: int) -> tuple[int, ...]:
    """Bitrates that are legal at ``sample_rate``."""
    return MP3_BITRATES_MPEG1 if mpeg_version(sample_rate) == "1" else MP3_BITRATES_MPEG2


def nearest_supported_rate(sample_rate: int) -> int:
    """Clamp an unsupported rate to the closest MP3 rate, never upsampling."""
    if sample_rate in SAMPLE_RATES:
        return sample_rate
    lower = [rate for rate in SAMPLE_RATES if rate <= sample_rate]
    if lower:
        return max(lower)
    return min(SAMPLE_RATES)


@dataclass(frozen=True)
class ResolvedPlan:
    """Settings applied to one specific input file."""

    sample_rate: int | None
    channels: int | None
    warnings: tuple[str, ...]
    nominal_kbps: int | None
    mode: BitrateMode
    vbr_quality: int | None = None

    @property
    def mpeg_version(self) -> str | None:
        return mpeg_version(self.sample_rate) if self.sample_rate else None

    def describe(self) -> str:
        bits: list[str] = [self.mode.label]
        if self.mode is BitrateMode.VBR and self.vbr_quality is not None:
            approx = VBR_QUALITY_TABLE.get(self.vbr_quality, 0)
            bits.append(f"-q:a {self.vbr_quality}（约 {approx} kbps）")
        elif self.nominal_kbps:
            bits.append(f"{self.nominal_kbps} kbps")
        if self.sample_rate:
            bits.append(f"{self.sample_rate / 1000:g} kHz")
        if self.channels:
            bits.append({1: "单声道", 2: "立体声"}[self.channels])
        return " · ".join(bits)


@dataclass(frozen=True)
class EncodeSettings:
    """User-facing encoder configuration."""

    mode: BitrateMode = BitrateMode.VBR
    vbr_quality: int = 2
    cbr_bitrate: int = 320
    abr_bitrate: int = 192
    sample_rate: int | None = None  # None = keep the source rate
    channels: ChannelMode = ChannelMode.KEEP
    joint_stereo: bool = True
    copy_tags: bool = True
    write_cover: bool = True
    id3_version: int = 4
    write_id3v1: bool = False

    # ------------------------------------------------------------------ #
    # serialisation
    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "vbr_quality": self.vbr_quality,
            "cbr_bitrate": self.cbr_bitrate,
            "abr_bitrate": self.abr_bitrate,
            "sample_rate": self.sample_rate,
            "channels": self.channels.value,
            "joint_stereo": self.joint_stereo,
            "copy_tags": self.copy_tags,
            "write_cover": self.write_cover,
            "id3_version": self.id3_version,
            "write_id3v1": self.write_id3v1,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EncodeSettings":
        if not data:
            return cls()
        defaults = cls()
        try:
            mode = BitrateMode(data.get("mode", defaults.mode.value))
        except ValueError:
            mode = defaults.mode
        try:
            channels = ChannelMode(data.get("channels", defaults.channels.value))
        except ValueError:
            channels = defaults.channels

        def _int(key: str, fallback: int) -> int:
            try:
                return int(data.get(key, fallback))
            except (TypeError, ValueError):
                return fallback

        raw_rate = data.get("sample_rate", defaults.sample_rate)
        try:
            sample_rate = None if raw_rate in (None, "", "keep") else int(raw_rate)
        except (TypeError, ValueError):
            sample_rate = None

        settings = cls(
            mode=mode,
            vbr_quality=_int("vbr_quality", defaults.vbr_quality),
            cbr_bitrate=_int("cbr_bitrate", defaults.cbr_bitrate),
            abr_bitrate=_int("abr_bitrate", defaults.abr_bitrate),
            sample_rate=sample_rate,
            channels=channels,
            joint_stereo=bool(data.get("joint_stereo", defaults.joint_stereo)),
            copy_tags=bool(data.get("copy_tags", defaults.copy_tags)),
            write_cover=bool(data.get("write_cover", defaults.write_cover)),
            id3_version=_int("id3_version", defaults.id3_version),
            write_id3v1=bool(data.get("write_id3v1", defaults.write_id3v1)),
        )
        return settings.clamped()

    def clamped(self) -> "EncodeSettings":
        """Return a copy with every numeric field pulled into a legal range."""
        rate = self.sample_rate
        if rate is not None and rate not in SAMPLE_RATES:
            rate = nearest_supported_rate(rate)
        return replace(
            self,
            vbr_quality=max(0, min(9, self.vbr_quality)),
            cbr_bitrate=self._clamp_bitrate(self.cbr_bitrate),
            abr_bitrate=self._clamp_bitrate(self.abr_bitrate),
            sample_rate=rate,
            id3_version=4 if self.id3_version == 4 else 3,
        )

    @staticmethod
    def _clamp_bitrate(value: int) -> int:
        if value in MP3_BITRATES_MPEG1:
            return value
        return min(MP3_BITRATES_MPEG1, key=lambda b: (abs(b - value), -b))

    # ------------------------------------------------------------------ #
    # validation
    # ------------------------------------------------------------------ #

    def validate(self) -> None:
        """Raise :class:`SettingsError` for structurally impossible settings."""
        if not 0 <= self.vbr_quality <= 9:
            raise SettingsError(f"VBR 质量必须在 0–9 之间，收到 {self.vbr_quality}")
        if self.sample_rate is not None and self.sample_rate not in SAMPLE_RATES:
            raise SettingsError(
                f"{self.sample_rate} Hz 不是 MP3 支持的采样率，可选："
                + "、".join(str(r) for r in SAMPLE_RATES)
            )
        if self.id3_version not in (3, 4):
            raise SettingsError("ID3v2 版本只能是 3 或 4")
        # Validate the explicit bitrates against the target rate when we know it.
        if self.sample_rate is not None:
            legal = allowed_bitrates(self.sample_rate)
            for mode_name, value in (
                (BitrateMode.CBR, self.cbr_bitrate),
                (BitrateMode.ABR, self.abr_bitrate),
            ):
                if self.mode is mode_name and value not in legal:
                    raise SettingsError(
                        f"{self.sample_rate} Hz（MPEG-{mpeg_version(self.sample_rate)}）下 "
                        f"{value} kbps 不可用，可选："
                        + "、".join(str(b) for b in legal)
                    )

    # ------------------------------------------------------------------ #
    # resolution against a concrete source
    # ------------------------------------------------------------------ #

    def resolve(self, source_sample_rate: int | None = None, source_channels: int | None = None) -> ResolvedPlan:
        """Combine these settings with a specific source's properties.

        Returns the ``-ar`` / ``-ac`` values to emit (``None`` means "leave it
        alone") plus any warnings worth showing the user.
        """
        self.validate()
        warnings: list[str] = []

        target_rate: int | None = self.sample_rate
        if target_rate is None and source_sample_rate is not None:
            if source_sample_rate not in SAMPLE_RATES:
                target_rate = nearest_supported_rate(source_sample_rate)
                warnings.append(
                    f"源采样率 {source_sample_rate} Hz 不被 MP3 支持，"
                    f"已改为 {target_rate} Hz"
                )

        target_channels = self.channels.ffmpeg_channels
        if target_channels is None and source_channels is not None and source_channels > 2:
            target_channels = 2
            warnings.append(
                f"源文件有 {source_channels} 声道，MP3 只支持单声道/立体声，已下混为立体声"
            )

        effective_rate = target_rate or source_sample_rate
        nominal: int | None
        if self.mode is BitrateMode.VBR:
            nominal = VBR_QUALITY_TABLE.get(self.vbr_quality)
        else:
            bitrate = self.cbr_bitrate if self.mode is BitrateMode.CBR else self.abr_bitrate
            if effective_rate is not None:
                legal = allowed_bitrates(effective_rate)
                if bitrate not in legal:
                    bitrate = min(legal, key=lambda b: (abs(b - bitrate), -b))
                    warnings.append(
                        f"{effective_rate} Hz 下该码率不可用，已调整为 {bitrate} kbps"
                    )
            nominal = bitrate

        return ResolvedPlan(
            sample_rate=target_rate,
            channels=target_channels,
            warnings=tuple(warnings),
            nominal_kbps=nominal,
            mode=self.mode,
            vbr_quality=self.vbr_quality if self.mode is BitrateMode.VBR else None,
        )

    def describe(self) -> str:
        return self.resolve().describe()


#: Ready-made configurations offered in the UI.
PRESETS: tuple[tuple[str, EncodeSettings], ...] = (
    ("V0 高保真", EncodeSettings(mode=BitrateMode.VBR, vbr_quality=0)),
    ("V2 均衡（推荐）", EncodeSettings(mode=BitrateMode.VBR, vbr_quality=2)),
    ("ABR 192", EncodeSettings(mode=BitrateMode.ABR, abr_bitrate=192)),
    ("CBR 320", EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320)),
    ("CBR 128 省空间", EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128)),
)
