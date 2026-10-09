"""Output path templates.

Templates are rendered per file and may contain tag placeholders, e.g.
``{artist}/{album}/{track:02d} {title}.mp3``.  Every path *component* is
sanitised independently, because ``/`` is the template's directory separator.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .probe import MediaInfo
from .settings import ResolvedPlan

__all__ = [
    "DEFAULT_TEMPLATE",
    "TEMPLATE_PRESETS",
    "TEMPLATE_HELP",
    "render_output_path",
    "sanitize_component",
]

DEFAULT_TEMPLATE = "{artist} - {title}.mp3"

TEMPLATE_PRESETS: tuple[tuple[str, str], ...] = (
    ("{artist} - {title}.mp3", "艺术家 - 标题（默认）"),
    ("{filename}.mp3", "保持原文件名"),
    ("{artist}/{album}/{track:02d} {title}.mp3", "音乐库结构"),
    ("{albumartist}/{album}/{title}.mp3", "专辑艺术家 / 专辑"),
    ("{artist}/{album}/{disc:02d}{track:02d} {title}.mp3", "含碟号"),
)

TEMPLATE_HELP = (
    "可用变量：{filename} {title} {artist} {album} {albumartist} {track} {disc} "
    "{year} {genre} {composer} {index} {samplerate} {bitrate} {mode}\n"
    "支持格式说明，如 {track:02d}；缺失的标签会替换为空字符串。"
)

_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_TRAILING_JUNK = re.compile(r"[ .]+$")
_EDGE_JUNK = re.compile(r"^[\s\-_.]+|[\s\-_.]+$")
_WHITESPACE = re.compile(r"\s+")
_SPACE_BEFORE_DOT = re.compile(r"\s+\.")
_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

_MAX_COMPONENT = 120


class _Missing:
    """Placeholder for an absent tag; formats to an empty string."""

    __slots__ = ()

    def __str__(self) -> str:
        return ""

    def __repr__(self) -> str:
        return ""

    def __format__(self, spec: str) -> str:
        return ""

    def __bool__(self) -> bool:
        return False


_MISSING = _Missing()


class _TagMapping(dict):
    """``format_map`` source that yields empty strings for unknown keys."""

    def __missing__(self, key: str) -> _Missing:  # type: ignore[override]
        return _MISSING


def sanitize_component(value: str, *, fallback: str = "_") -> str:
    """Make ``value`` safe to use as a single Windows path component.

    Leading and trailing separators are stripped so a template like
    ``{artist} - {title}`` degrades to just the title when the artist tag is
    missing, instead of producing ``" - title.mp3"``.
    """
    cleaned = _ILLEGAL_CHARS.sub("_", value)
    cleaned = _WHITESPACE.sub(" ", cleaned)
    cleaned = _SPACE_BEFORE_DOT.sub(".", cleaned)
    cleaned = _EDGE_JUNK.sub("", cleaned)
    cleaned = _TRAILING_JUNK.sub("", cleaned)
    if len(cleaned) > _MAX_COMPONENT:
        cleaned = cleaned[:_MAX_COMPONENT]
        cleaned = _EDGE_JUNK.sub("", cleaned)
    if not cleaned:
        return fallback
    if cleaned.lower() in _RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned


def _placeholder_values(
    info: MediaInfo,
    plan: ResolvedPlan,
    *,
    index: int,
    fallback_name: str,
) -> Mapping[str, Any]:
    def _sanitize(value: str) -> str:
        # A tag containing '/' or '\\' must never become a directory separator:
        # the template's own separators are the only ones that count.
        return _ILLEGAL_CHARS.sub("_", value).strip()

    def _tag(*names: str) -> Any:
        for name in names:
            value = info.tag(name)
            if value:
                cleaned = _sanitize(value)
                if cleaned:
                    return cleaned
        return _MISSING

    def _tag_int(*names: str) -> Any:
        for name in names:
            raw = info.tag(name)
            if not raw:
                continue
            match = re.match(r"\s*(\d+)", raw)
            if match:
                return int(match.group(1))
        return _MISSING

    values: dict[str, Any] = {
        "filename": fallback_name,
        "title": _tag("title", "song") or _MISSING,
        "artist": _tag("artist", "album_artist", "performer"),
        "album": _tag("album"),
        "albumartist": _tag("album_artist", "albumartist", "artist"),
        "track": _tag_int("track", "tracknumber"),
        "disc": _tag_int("disc", "discnumber", "disk"),
        "year": _tag("date", "year"),
        "genre": _tag("genre"),
        "composer": _tag("composer"),
        "index": index,
        "bitrate": plan.nominal_kbps or _MISSING,
        "samplerate": plan.sample_rate or _MISSING,
        "mode": plan.mode.value,
    }
    if not values["title"]:
        values["title"] = fallback_name
    return _TagMapping(values)


def render_output_path(
    template: str,
    info: MediaInfo,
    plan: ResolvedPlan,
    output_dir: Path,
    *,
    index: int = 1,
    extension: str = ".mp3",
    original_path: Path | None = None,
) -> Path:
    """Render ``template`` into an absolute path under ``output_dir``.

    ``original_path`` supplies the ``{filename}`` fallback.  It matters for
    encrypted inputs: ``info`` describes the *decrypted* intermediate, whose
    random temp name must never leak into the output.
    """
    template = (template or DEFAULT_TEMPLATE).strip() or DEFAULT_TEMPLATE
    fallback_name = (original_path or info.path).stem or "output"

    try:
        rendered = template.format_map(
            _placeholder_values(info, plan, index=index, fallback_name=fallback_name)
        )
    except (ValueError, IndexError) as exc:
        raise ValueError(f"输出模板无法解析：{template}（{exc}）") from exc

    # Normalise separators, then sanitise each component on its own.
    parts = [part for part in re.split(r"[\\/]+", rendered) if part.strip()]
    safe_parts: list[str] = []
    for part in parts:
        component = sanitize_component(part)
        if component in (".", ".."):
            continue
        safe_parts.append(component)

    if not safe_parts:
        safe_parts = [sanitize_component(fallback_name)]

    # Guarantee the requested extension.
    last = safe_parts[-1]
    wanted = extension.lstrip(".")
    if not last.lower().endswith(f".{wanted}"):
        last = f"{Path(last).stem}.{wanted}"
        safe_parts[-1] = last

    return output_dir.joinpath(*safe_parts)
