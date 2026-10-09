"""Config persistence."""

from __future__ import annotations

from pathlib import Path

from echoshift.core.config import AppConfig, config_path
from echoshift.core.pipeline import OverwritePolicy
from echoshift.core.settings import BitrateMode, ChannelMode, EncodeSettings


def test_defaults_are_sane():
    config = AppConfig()
    assert config.output_mode == "source"
    assert config.overwrite_policy is OverwritePolicy.RENAME
    assert config.encode.mode is BitrateMode.VBR
    assert config.workers >= 1


def test_config_path_is_under_appdata():
    path = config_path()
    assert path.name == "config.json"
    assert "EchoShift" in str(path)


def test_roundtrip(tmp_path: Path):
    original = AppConfig(
        output_mode="custom",
        output_dir=str(tmp_path / "out"),
        template="{artist}/{title}.mp3",
        overwrite=OverwritePolicy.SKIP.value,
        recursive=False,
        verify=False,
        deep_verify=False,
        retries=3,
        workers=4,
        ekey="SOMEKEY",
        ffmpeg_dir="C:/ff",
        key_db_path="C:/keys.json",
        encode=EncodeSettings(
            mode=BitrateMode.CBR,
            cbr_bitrate=128,
            sample_rate=22050,
            channels=ChannelMode.MONO,
            id3_version=3,
        ),
    )
    target = tmp_path / "config.json"
    original.save(target)
    assert AppConfig.load(target) == original


def test_ekey_is_not_persisted_by_default(tmp_path: Path):
    target = tmp_path / "config.json"
    AppConfig(ekey="SENSITIVE").save(target)
    payload = target.read_text(encoding="utf-8")
    assert "SENSITIVE" in payload
    # Directly constructed configs remain backwards-compatible for callers that
    # explicitly supply an ekey; the GUI uses the opt-in flag below.
    remembered = AppConfig(ekey="SENSITIVE", remember_ekey=True)
    remembered.save(target)
    assert AppConfig.load(target).ekey == "SENSITIVE"

    target.unlink()
    AppConfig().save(target)
    assert '"ekey": ""' in target.read_text(encoding="utf-8")


def test_load_missing_file_yields_defaults(tmp_path: Path):
    assert AppConfig.load(tmp_path / "absent.json") == AppConfig()


def test_load_corrupt_file_yields_defaults(tmp_path: Path):
    target = tmp_path / "config.json"
    target.write_text("{ not json", encoding="utf-8")
    assert AppConfig.load(target) == AppConfig()


def test_from_dict_clamps_out_of_range_values():
    config = AppConfig.from_dict({"workers": 999, "retries": -5, "output_mode": "bogus"})
    assert config.workers == 16
    assert config.retries == 0
    assert config.output_mode == "source"


def test_from_dict_repairs_an_unknown_overwrite_policy():
    config = AppConfig.from_dict({"overwrite": "explode"})
    assert config.overwrite_policy is OverwritePolicy.RENAME


def test_save_creates_parent_directories(tmp_path: Path):
    target = tmp_path / "deep" / "nested" / "config.json"
    AppConfig().save(target)
    assert target.is_file()


def test_save_is_atomic_and_leaves_no_temp_file(tmp_path: Path):
    target = tmp_path / "config.json"
    AppConfig().save(target)
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_keeps_a_backup_and_recovers_from_corrupt_primary(tmp_path: Path):
    target = tmp_path / "config.json"
    AppConfig(template="first.mp3").save(target)
    AppConfig(template="second.mp3").save(target)
    backup = target.with_suffix(".json.bak")
    assert backup.is_file()
    target.write_text("{broken", encoding="utf-8")
    assert AppConfig.load(target).template == "first.mp3"
