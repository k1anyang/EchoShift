"""Exception hierarchy shared by every layer.

These live at the package root rather than under ``core`` because both the
conversion core and the QMC decoder raise them; keeping them here avoids a
circular import between the two packages.
"""

from __future__ import annotations

__all__ = [
    "EchoShiftError",
    "ToolNotFoundError",
    "ProbeError",
    "UnsupportedInputError",
    "DecryptionError",
    "SettingsError",
    "VerificationError",
    "CancelledError",
]


class EchoShiftError(Exception):
    """Base class for every error this package raises deliberately."""

    #: Short, user-facing label used by the GUI and CLI.
    label = "错误"


class ToolNotFoundError(EchoShiftError):
    """ffmpeg/ffprobe could not be located."""

    label = "缺少 ffmpeg"


class ProbeError(EchoShiftError):
    """ffprobe could not read a file, or returned something unusable."""

    label = "无法解析媒体信息"


class UnsupportedInputError(EchoShiftError):
    """The input is not something this tool knows how to handle."""

    label = "不支持的输入格式"


class DecryptionError(EchoShiftError):
    """A QQ Music encrypted container could not be decoded."""

    label = "解密失败"


class SettingsError(EchoShiftError):
    """The requested encode settings are not achievable."""

    label = "参数不合法"


class VerificationError(EchoShiftError):
    """The produced MP3 failed post-conversion checks."""

    label = "转换结果校验失败"


class CancelledError(EchoShiftError):
    """The user cancelled the job."""

    label = "已取消"
