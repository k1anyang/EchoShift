"""Persisted application settings (``%APPDATA%/EchoShift/config.json``)."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .naming import DEFAULT_TEMPLATE
from .pipeline import OverwritePolicy
from .settings import EncodeSettings

__all__ = ["AppConfig", "config_path", "legacy_config_paths"]

_APP_DIR_NAME = "EchoShift"
#: Directories used before the project was renamed.  Their settings are still
#: honoured on first run so an upgrade does not silently reset everything.
_LEGACY_DIR_NAMES = ("AudioConv",)


def _config_root() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path.home() / ".config"


def config_path() -> Path:
    """Where the config lives: ``%APPDATA%`` on Windows, else ``~/.config``."""
    return _config_root() / _APP_DIR_NAME / "config.json"


def legacy_config_paths() -> list[Path]:
    """Config files written under the old product name, newest-wins order."""
    root = _config_root()
    return [root / name / "config.json" for name in _LEGACY_DIR_NAMES]


@dataclass
class AppConfig:
    """Everything the GUI remembers between runs."""

    #: ``source`` writes next to each input; ``custom`` uses :attr:`output_dir`.
    output_mode: str = "source"
    output_dir: str = ""
    template: str = DEFAULT_TEMPLATE
    overwrite: str = OverwritePolicy.RENAME.value
    recursive: bool = True
    verify: bool = True
    deep_verify: bool = True
    retries: int = 1
    workers: int = 2
    #: Whether the "advanced" settings card starts unfolded.  Remembered because
    #: the parallel-job and retry controls live there, and re-opening it on every
    #: launch is a small tax paid by exactly the users who change those values.
    advanced_expanded: bool = False
    #: Whether the run log panel starts unfolded.
    log_expanded: bool = False
    ekey: str = ""
    #: ekey is session-only unless the user explicitly opts in.
    remember_ekey: bool = False
    ffmpeg_dir: str = ""
    #: Last ffmpeg probe, reused while the binary is unchanged.
    ffmpeg_cache: dict[str, Any] = field(default_factory=dict)
    key_db_path: str = ""
    encode: EncodeSettings = field(default_factory=EncodeSettings)

    def __post_init__(self) -> None:
        # Preserve the old Python API for callers that explicitly construct an
        # AppConfig with an ekey, while configs read from disk only retain it
        # when the opt-in flag was persisted.
        if self.ekey and not self.remember_ekey:
            self.remember_ekey = True

    # ------------------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "output_mode": self.output_mode,
            "output_dir": self.output_dir,
            "template": self.template,
            "overwrite": self.overwrite,
            "recursive": self.recursive,
            "verify": self.verify,
            "deep_verify": self.deep_verify,
            "retries": self.retries,
            "workers": self.workers,
            "advanced_expanded": self.advanced_expanded,
            "log_expanded": self.log_expanded,
            "ekey": self.ekey if self.remember_ekey else "",
            "remember_ekey": self.remember_ekey,
            "ffmpeg_dir": self.ffmpeg_dir,
            "ffmpeg_cache": self.ffmpeg_cache,
            "key_db_path": self.key_db_path,
            "encode": self.encode.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "AppConfig":
        if not isinstance(data, dict) or not data:
            return cls()
        defaults = cls()

        def _str(key: str, fallback: str) -> str:
            value = data.get(key, fallback)
            return str(value) if value is not None else fallback

        def _bool(key: str, fallback: bool) -> bool:
            return bool(data.get(key, fallback))

        def _int(key: str, fallback: int, low: int, high: int) -> int:
            try:
                return max(low, min(high, int(data.get(key, fallback))))
            except (TypeError, ValueError):
                return fallback

        try:
            overwrite = OverwritePolicy(data.get("overwrite", defaults.overwrite)).value
        except ValueError:
            overwrite = defaults.overwrite

        output_mode = _str("output_mode", defaults.output_mode)
        if output_mode not in ("source", "custom"):
            output_mode = defaults.output_mode

        remember_ekey = _bool("remember_ekey", False)
        return cls(
            output_mode=output_mode,
            output_dir=_str("output_dir", defaults.output_dir),
            template=_str("template", defaults.template) or DEFAULT_TEMPLATE,
            overwrite=overwrite,
            recursive=_bool("recursive", defaults.recursive),
            verify=_bool("verify", defaults.verify),
            deep_verify=_bool("deep_verify", defaults.deep_verify),
            retries=_int("retries", defaults.retries, 0, 5),
            workers=_int("workers", defaults.workers, 1, 16),
            advanced_expanded=_bool("advanced_expanded", defaults.advanced_expanded),
            log_expanded=_bool("log_expanded", defaults.log_expanded),
            ekey=_str("ekey", defaults.ekey) if remember_ekey else "",
            remember_ekey=remember_ekey,
            ffmpeg_dir=_str("ffmpeg_dir", defaults.ffmpeg_dir),
            ffmpeg_cache=dict(data.get("ffmpeg_cache") or {}),
            key_db_path=_str("key_db_path", defaults.key_db_path),
            encode=EncodeSettings.from_dict(data.get("encode")),
        )

    def with_changes(self, **changes: Any) -> "AppConfig":
        return replace(self, **changes)

    # ------------------------------------------------------------------ #

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        """Read the config, falling back to defaults on any problem.

        With no explicit path, the pre-rename ``AudioConv`` directory is
        consulted too, so existing settings survive the rename.
        """
        if path is not None:
            candidates = [Path(path)]
        else:
            candidates = [config_path(), *legacy_config_paths()]
        for target in candidates:
            try:
                with open(target, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if not isinstance(payload, dict):
                    continue
                return cls.from_dict(payload)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
                continue
        # A failed write or interrupted upgrade should not discard the last
        # known-good settings when the primary file is malformed.
        for target in candidates:
            backup = target.with_suffix(".json.bak")
            try:
                with open(backup, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    return cls.from_dict(payload)
            except (OSError, json.JSONDecodeError, UnicodeDecodeError, TypeError):
                continue
        return cls()

    def save(self, path: Path | None = None) -> Path:
        """Write the config atomically; returns the file it wrote."""
        target = Path(path) if path else config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".json.tmp")
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
        if target.exists():
            try:
                shutil.copy2(target, target.with_suffix(".json.bak"))
            except OSError:
                # The primary save remains atomic even when a backup cannot be
                # created (for example, on a read-only removable drive).
                pass
        os.replace(temporary, target)
        return target

    # ------------------------------------------------------------------ #

    @property
    def overwrite_policy(self) -> OverwritePolicy:
        try:
            return OverwritePolicy(self.overwrite)
        except ValueError:
            return OverwritePolicy.RENAME
