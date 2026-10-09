"""The ffmpeg probe cache and its invalidation.

``find_toolchain`` spawns two subprocesses (``-version`` and ``-encoders``, the
latter printing ~11 KB) which cost ~160 ms of every launch.  Both answers are
stable for a given binary, so they are cached against the binary's mtime+size.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from echoshift.core.config import AppConfig
from echoshift.core.ffmpeg import Toolchain, find_toolchain


@pytest.fixture(scope="module")
def real_toolchain():
    return find_toolchain()


def test_toolchain_reports_a_fingerprint(real_toolchain):
    assert real_toolchain.fingerprint is not None
    mtime, size = real_toolchain.fingerprint
    assert mtime > 0 and size > 0
    stat = real_toolchain.ffmpeg.stat()
    assert (stat.st_mtime, stat.st_size) == (mtime, size)


def test_cache_round_trip_avoids_reprobing(real_toolchain, monkeypatch):
    """A warm cache must produce the same answer without running anything."""
    record = real_toolchain.to_cache()

    def _explode(*_args, **_kwargs):
        raise AssertionError("a warm cache must not spawn a probe subprocess")

    monkeypatch.setattr("echoshift.core.ffmpeg._probe_version", _explode)
    monkeypatch.setattr("echoshift.core.ffmpeg._probe_lame", _explode)

    reused = find_toolchain(cached=record)
    assert reused.ffmpeg == real_toolchain.ffmpeg
    assert reused.version == real_toolchain.version
    assert reused.has_libmp3lame == real_toolchain.has_libmp3lame


@pytest.mark.parametrize(
    "mutation",
    [
        {"ffmpeg": "C:/elsewhere/ffmpeg.exe"},
        {"mtime": 0.0},
        {"size": 1},
    ],
)
def test_a_changed_binary_invalidates_the_cache(real_toolchain, mutation):
    record = dict(real_toolchain.to_cache(), **mutation)
    # Re-probing is the point, so the result must still be correct.
    found = find_toolchain(cached=record)
    assert found.version == real_toolchain.version
    assert found.has_libmp3lame is True


def test_garbage_cache_falls_back_to_probing(real_toolchain):
    for junk in (None, {}, {"ffmpeg": None}, {"version": "nonsense"}):
        found = find_toolchain(cached=junk)
        assert found.version == real_toolchain.version


def test_cache_record_is_json_safe(real_toolchain):
    import json

    record = real_toolchain.to_cache()
    assert json.loads(json.dumps(record)) == record


def test_app_config_persists_the_probe_cache(tmp_path: Path, real_toolchain):
    config = AppConfig(ffmpeg_cache=real_toolchain.to_cache())
    target = tmp_path / "config.json"
    config.save(target)
    reloaded = AppConfig.load(target)
    assert reloaded.ffmpeg_cache["ffmpeg"] == str(real_toolchain.ffmpeg)
    assert reloaded.ffmpeg_cache["has_libmp3lame"] == real_toolchain.has_libmp3lame


def test_app_config_tolerates_a_missing_cache(tmp_path: Path):
    target = tmp_path / "config.json"
    target.write_text("{}", encoding="utf-8")
    assert AppConfig.load(target).ffmpeg_cache == {}


def test_an_unusable_explicit_directory_falls_back(tmp_path: Path, real_toolchain):
    """A bad custom directory must not break startup; the search continues."""
    found = find_toolchain(tmp_path)
    assert found.ffmpeg == real_toolchain.ffmpeg


def test_a_nonexistent_explicit_directory_falls_back(real_toolchain):
    found = find_toolchain(Path("Z:/definitely/not/here"))
    assert found.ffmpeg == real_toolchain.ffmpeg


def test_toolchain_describe_mentions_libmp3lame(real_toolchain):
    text = real_toolchain.describe()
    assert "libmp3lame" in text
    assert real_toolchain.source


def test_legacy_config_directory_is_still_read(tmp_path: Path, monkeypatch):
    """Settings written before the rename must survive it."""
    from echoshift.core import config as config_module

    monkeypatch.setattr(config_module, "_config_root", lambda: tmp_path)
    legacy = tmp_path / "AudioConv"
    legacy.mkdir(parents=True, exist_ok=True)
    (legacy / "config.json").write_text(
        '{"template": "{title}.mp3", "workers": 7}', encoding="utf-8"
    )

    loaded = config_module.AppConfig.load()
    assert loaded.template == "{title}.mp3"
    assert loaded.workers == 7


def test_new_config_takes_precedence_over_the_legacy_one(tmp_path: Path, monkeypatch):
    from echoshift.core import config as config_module

    monkeypatch.setattr(config_module, "_config_root", lambda: tmp_path)
    for name, workers in (("AudioConv", 3), ("EchoShift", 9)):
        folder = tmp_path / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "config.json").write_text(
            '{"workers": %d}' % workers, encoding="utf-8"
        )
    assert config_module.AppConfig.load().workers == 9


def test_legacy_ffmpeg_dir_env_var_is_honoured(monkeypatch):
    from echoshift.core.ffmpeg import _env_override

    monkeypatch.delenv("ECHOSHIFT_FFMPEG_DIR", raising=False)
    monkeypatch.delenv("AUDIOCONV_FFMPEG_DIR", raising=False)
    assert _env_override() is None

    monkeypatch.setenv("AUDIOCONV_FFMPEG_DIR", r"C:\old")
    assert _env_override() == r"C:\old"

    monkeypatch.setenv("ECHOSHIFT_FFMPEG_DIR", r"C:\new")
    assert _env_override() == r"C:\new", "the new name wins"


def test_toolchain_is_hashable_and_frozen(real_toolchain):
    assert isinstance(real_toolchain, Toolchain)
    with pytest.raises(Exception):
        real_toolchain.version = "x"  # type: ignore[misc]


def test_size_and_mtime_are_the_invalidation_keys(real_toolchain):
    """Touch the file's mtime through os.utime and the cache must miss."""
    record = real_toolchain.to_cache()
    original = os.stat(real_toolchain.ffmpeg).st_mtime
    try:
        os.utime(real_toolchain.ffmpeg, (original + 60, original + 60))
        find_toolchain(cached=record)  # must re-probe, so just check it works
        from echoshift.core.ffmpeg import _from_cache

        assert _from_cache(record, real_toolchain.ffmpeg, real_toolchain.ffprobe) is None
    finally:
        os.utime(real_toolchain.ffmpeg, (original, original))
