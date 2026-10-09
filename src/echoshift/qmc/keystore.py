"""Resolving ekeys for QMC2 containers.

QMC2 needs a per-file encryption key.  This module implements a *pluggable*
lookup chain rather than shipping any extracted key material:

1. an ekey the user supplied explicitly (pasted in the UI, or ``--ekey``);
2. a sidecar file next to the audio (``song.mflac.ekey``, ``song.ekey``);
3. a JSON key database, either the user's own or the bundled
   ``vendor/keys/qmc_keys.json``, matched by mid, song id, or filename.

QMC1 containers need no key at all, and QTag / V1 containers carry theirs
inline, so this chain only matters for modern ``musicex`` files.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..paths import resource_dir

__all__ = ["KeyHit", "KeyStore", "default_database_paths", "load_database"]

_BUNDLED_DB = ("vendor", "keys", "qmc_keys.json")
_SIDECAR_SUFFIXES = (".ekey", ".mflac.ekey", ".mgg.ekey", ".key")


def default_database_paths() -> list[Path]:
    """Candidate locations of the bundled key database."""
    paths: list[Path] = []
    for root in resource_dir("vendor"):
        paths.append(root / "keys" / "qmc_keys.json")
    return paths


def _coerce_database(document: Any) -> dict[str, str]:
    """Accept the several shapes a hand-written key DB tends to take."""
    entries: dict[str, str] = {}

    def _add(key: Any, value: Any) -> None:
        if key is None or value is None:
            return
        text = str(value).strip()
        if text:
            entries.setdefault(str(key).strip(), text)

    if isinstance(document, dict):
        payload = document.get("keys", document)
        if isinstance(payload, dict):
            for key, value in payload.items():
                if isinstance(value, dict):
                    _add(key, value.get("ekey"))
                else:
                    _add(key, value)
        elif isinstance(payload, list):
            document = payload

    if isinstance(document, list):
        for item in document:
            if not isinstance(item, dict):
                continue
            ekey = item.get("ekey")
            for field in ("mid", "song_id", "songid", "filename", "name", "path"):
                _add(item.get(field), ekey)

    return entries


def load_database(path: Path) -> dict[str, str]:
    """Read one key database; unreadable files yield an empty mapping."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return _coerce_database(json.load(handle))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}


@dataclass(frozen=True)
class KeyHit:
    """A resolved ekey plus where it came from."""

    ekey: str
    source: str

    def describe(self) -> str:
        return f"ekey 来源：{self.source}"


def _extract_ekey(text: str) -> str | None:
    """Pull an ekey out of a sidecar file's contents."""
    text = text.strip().lstrip("\ufeff")
    if not text:
        return None
    if text[:1] in "{[":
        try:
            document = json.loads(text)
        except json.JSONDecodeError:
            document = None
        if isinstance(document, dict):
            for field in ("ekey", "key", "value"):
                if document.get(field):
                    return str(document[field]).strip()
    # QTag-style "<ekey>,<song id>,…"
    if "," in text:
        text = text.split(",", 1)[0]
    return text.strip() or None


class KeyStore:
    """A chain of ekey sources consulted in priority order."""

    def __init__(
        self,
        *,
        explicit: str | None = None,
        database: Mapping[str, str] | None = None,
        database_sources: Iterable[tuple[Path, Mapping[str, str]]] = (),
        use_sidecar: bool = True,
    ) -> None:
        self.explicit = (explicit or "").strip() or None
        self.database: dict[str, str] = dict(database or {})
        self.database_sources = list(database_sources)
        self.use_sidecar = use_sidecar

    # ------------------------------------------------------------------ #

    @classmethod
    def build(
        cls,
        *,
        explicit: str | None = None,
        user_database: Path | None = None,
        use_bundled: bool = True,
        use_sidecar: bool = True,
        extra_databases: Iterable[Path] = (),
    ) -> "KeyStore":
        """Assemble a store from the configured database files."""
        sources: list[tuple[Path, Mapping[str, str]]] = []
        paths: list[Path] = []
        if user_database is not None:
            paths.append(Path(user_database))
        if use_bundled:
            paths.extend(default_database_paths())
        paths.extend(Path(p) for p in extra_databases)

        merged: dict[str, str] = {}
        for path in paths:
            if not path.is_file():
                continue
            entries = load_database(path)
            if not entries:
                continue
            sources.append((path, entries))
            for key, value in entries.items():
                merged.setdefault(key, value)

        return cls(
            explicit=explicit,
            database=merged,
            database_sources=sources,
            use_sidecar=use_sidecar,
        )

    # ------------------------------------------------------------------ #

    @property
    def size(self) -> int:
        return len(self.database)

    def describe(self) -> str:
        if self.database_sources:
            names = "、".join(p.name for p, _ in self.database_sources)
            return f"密钥库 {self.size} 条（{names}）"
        return f"密钥库 {self.size} 条"

    def _sidecar_hit(self, path: Path) -> KeyHit | None:
        for suffix in _SIDECAR_SUFFIXES:
            candidate = path.with_name(path.name + suffix)
            if candidate.is_file():
                ekey = _extract_ekey(_read_text(candidate))
                if ekey:
                    return KeyHit(ekey, f"同名密钥文件 {candidate.name}")
        for suffix in _SIDECAR_SUFFIXES:
            candidate = path.with_suffix(suffix)
            if candidate.is_file():
                ekey = _extract_ekey(_read_text(candidate))
                if ekey:
                    return KeyHit(ekey, f"同名密钥文件 {candidate.name}")
        return None

    def _database_hit(self, identifiers: Iterable[str | None]) -> KeyHit | None:
        for identifier in identifiers:
            if not identifier:
                continue
            value = self.database.get(identifier)
            if value:
                return KeyHit(value, f"密钥库匹配 {identifier}")
        return None

    def lookup(
        self,
        path: Path,
        *,
        mid: str | None = None,
        song_id: str | None = None,
        filename: str | None = None,
    ) -> KeyHit | None:
        """Find an ekey for a file, or return ``None``."""
        if self.explicit:
            return KeyHit(self.explicit, "手动指定")

        if self.use_sidecar:
            hit = self._sidecar_hit(path)
            if hit:
                return hit

        return self._database_hit(
            [
                mid,
                song_id,
                filename,
                path.name,
                path.stem,
            ]
        )


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return path.read_text(encoding=encoding)
        except (OSError, UnicodeDecodeError):
            continue
    return ""
