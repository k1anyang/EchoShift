"""Conversion core: probing, settings, naming, encoding, verification."""

from __future__ import annotations

from .args import build_ffmpeg_args
from .config import AppConfig, config_path
from ..errors import (
    EchoShiftError,
    CancelledError,
    DecryptionError,
    ProbeError,
    SettingsError,
    ToolNotFoundError,
    UnsupportedInputError,
    VerificationError,
)
from .ffmpeg import Toolchain, find_toolchain
from .naming import DEFAULT_TEMPLATE, TEMPLATE_HELP, TEMPLATE_PRESETS, render_output_path
from .pipeline import (
    JobResult,
    JobState,
    OverwritePolicy,
    Pipeline,
    PipelineOptions,
    collect_sources,
    default_work_dir,
)
from .probe import MediaInfo, probe
from .settings import (
    PRESETS,
    SAMPLE_RATES,
    BitrateMode,
    ChannelMode,
    EncodeSettings,
    ResolvedPlan,
    allowed_bitrates,
    mpeg_version,
)
from .verify import VerificationReport, verify_output

__all__ = [
    "AppConfig",
    "EchoShiftError",
    "BitrateMode",
    "CancelledError",
    "ChannelMode",
    "DEFAULT_TEMPLATE",
    "DecryptionError",
    "EncodeSettings",
    "JobResult",
    "JobState",
    "MediaInfo",
    "OverwritePolicy",
    "PRESETS",
    "Pipeline",
    "PipelineOptions",
    "ProbeError",
    "ResolvedPlan",
    "SAMPLE_RATES",
    "SettingsError",
    "TEMPLATE_HELP",
    "TEMPLATE_PRESETS",
    "ToolNotFoundError",
    "Toolchain",
    "UnsupportedInputError",
    "VerificationError",
    "VerificationReport",
    "allowed_bitrates",
    "build_ffmpeg_args",
    "collect_sources",
    "config_path",
    "default_work_dir",
    "find_toolchain",
    "mpeg_version",
    "probe",
    "render_output_path",
    "verify_output",
]
