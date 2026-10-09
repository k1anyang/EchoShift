"""Ekey resolution: explicit, sidecar, database."""

from __future__ import annotations

import json
from pathlib import Path

from echoshift.qmc.keystore import KeyStore, load_database

EKEY = "QUJDREVGR0hJSktMTU5PUFFSU1RVVldYWVo="


def test_explicit_ekey_wins():
    store = KeyStore(explicit="  EXPLICIT  ", database={"mid1": "FROM_DB"})
    hit = store.lookup(Path("song.mflac"), mid="mid1")
    assert hit is not None
    assert hit.ekey == "EXPLICIT"
    assert "手动" in hit.source


def test_database_lookup_by_mid():
    store = KeyStore(database={"mid1": "FROM_DB"})
    hit = store.lookup(Path("song.mflac"), mid="mid1")
    assert hit is not None and hit.ekey == "FROM_DB"


def test_database_lookup_by_song_id_and_filename():
    store = KeyStore(database={"4242": "BY_ID", "song.mflac": "BY_NAME"})
    assert store.lookup(Path("other.mflac"), song_id="4242").ekey == "BY_ID"
    assert store.lookup(Path("song.mflac")).ekey == "BY_NAME"


def test_no_match_returns_none():
    assert KeyStore().lookup(Path("song.mflac"), mid="unknown") is None


def test_sidecar_file_is_found(tmp_path: Path) -> None:
    audio = tmp_path / "song.mflac"
    audio.write_bytes(b"x")
    (tmp_path / "song.mflac.ekey").write_text(EKEY, encoding="utf-8")
    hit = KeyStore().lookup(audio)
    assert hit is not None
    assert hit.ekey == EKEY
    assert "同名密钥文件" in hit.source


def test_stem_sidecar_is_found(tmp_path: Path) -> None:
    audio = tmp_path / "song.mflac"
    audio.write_bytes(b"x")
    (tmp_path / "song.ekey").write_text(f"  {EKEY}\n", encoding="utf-8")
    assert KeyStore().lookup(audio).ekey == EKEY


def test_sidecar_accepts_qtag_style_content(tmp_path: Path) -> None:
    audio = tmp_path / "song.mflac"
    audio.write_bytes(b"x")
    (tmp_path / "song.ekey").write_text(f"{EKEY},998877", encoding="utf-8")
    assert KeyStore().lookup(audio).ekey == EKEY


def test_sidecar_accepts_json_content(tmp_path: Path) -> None:
    audio = tmp_path / "song.mflac"
    audio.write_bytes(b"x")
    (tmp_path / "song.ekey").write_text(json.dumps({"ekey": EKEY}), encoding="utf-8")
    assert KeyStore().lookup(audio).ekey == EKEY


def test_sidecar_can_be_disabled(tmp_path: Path) -> None:
    audio = tmp_path / "song.mflac"
    audio.write_bytes(b"x")
    (tmp_path / "song.mflac.ekey").write_text(EKEY, encoding="utf-8")
    assert KeyStore(use_sidecar=False).lookup(audio) is None


# --------------------------------------------------------------------------- #
# database parsing
# --------------------------------------------------------------------------- #


def test_load_database_plain_mapping(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"mid1": "E1", "mid2": "E2"}), encoding="utf-8")
    assert load_database(path) == {"mid1": "E1", "mid2": "E2"}


def test_load_database_nested_under_keys(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text(json.dumps({"keys": {"mid1": "E1"}}), encoding="utf-8")
    assert load_database(path) == {"mid1": "E1"}


def test_load_database_list_of_records(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text(
        json.dumps(
            [
                {"mid": "midA", "song_id": "111", "filename": "a.mflac", "ekey": "EA"},
                {"mid": "midB", "ekey": "EB"},
            ]
        ),
        encoding="utf-8",
    )
    assert load_database(path) == {
        "midA": "EA",
        "111": "EA",
        "a.mflac": "EA",
        "midB": "EB",
    }


def test_load_database_tolerates_broken_json(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_database(path) == {}


def test_load_database_tolerates_a_missing_file(tmp_path: Path) -> None:
    assert load_database(tmp_path / "nope.json") == {}


def test_build_merges_database_files(tmp_path: Path) -> None:
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"shared": "USER", "user_only": "U"}), encoding="utf-8")
    extra = tmp_path / "extra.json"
    extra.write_text(json.dumps({"shared": "EXTRA", "extra_only": "E"}), encoding="utf-8")

    store = KeyStore.build(
        user_database=user, use_bundled=False, extra_databases=[extra]
    )
    # The user's database is consulted first and wins on conflicts.
    assert store.database["shared"] == "USER"
    assert store.database["user_only"] == "U"
    assert store.database["extra_only"] == "E"
    assert store.size == 3
    assert "user.json" in store.describe()
