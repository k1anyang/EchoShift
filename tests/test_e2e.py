"""End-to-end conversion over real, generated audio.

The fixtures include a FLAC and three encrypted wrappers of that exact same
FLAC (keyless QMC1, QMC2 short-key Map, QMC2 long-key RC4).  Because every
cipher is a deterministic XOR, all four must decode to identical bytes and
therefore encode to byte-identical MP3s -- which is asserted below.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from echoshift.core.pipeline import (
    JobState,
    OverwritePolicy,
    Pipeline,
    PipelineOptions,
    collect_sources,
)
from echoshift.core.probe import probe
from echoshift.core.settings import BitrateMode, ChannelMode, EncodeSettings
from echoshift.qmc.keystore import KeyStore

from .conftest import (
    ENCRYPTED_FIXTURES,
    HIRES,
    MFLAC_MAP,
    MFLAC_RC4,
    QMC1,
    STANDARD,
    SURROUND,
    UNTAGGED,
)

pytestmark = pytest.mark.slow

#: Every encrypted wrapper of the same FLAC.  QTag, V1-with-raw-key and
#: V1-with-ASCII-ekey-text go through different parsers, so all are represented.
ENCRYPTED = list(ENCRYPTED_FIXTURES)


def _run(
    toolchain,
    samples: Path,
    source: str,
    settings: EncodeSettings,
    out_dir: Path,
    *,
    template: str = "{filename}.mp3",
    keystore: KeyStore | None = None,
    **overrides,
):
    options = PipelineOptions(
        settings=settings,
        template=template,
        output_dir=out_dir,
        **overrides,
    )
    pipeline = Pipeline(toolchain, options, keystore=keystore or KeyStore())
    return pipeline.run_job(samples / source)


def _audio(toolchain, path: Path):
    info = probe(toolchain.ffprobe, path)
    assert info.audio is not None
    return info


# --------------------------------------------------------------------------- #
# the core requirement: sample rate and bitrate are actually controllable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "settings,expected_rate,expected_channels",
    [
        (EncodeSettings(mode=BitrateMode.VBR, vbr_quality=2), 44100, 2),
        (EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320), 44100, 2),
        (EncodeSettings(mode=BitrateMode.ABR, abr_bitrate=192), 44100, 2),
        (EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=64, sample_rate=22050), 22050, 2),
        (EncodeSettings(mode=BitrateMode.VBR, vbr_quality=5, sample_rate=8000), 8000, 2),
        (
            EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=32, sample_rate=8000,
                           channels=ChannelMode.MONO),
            8000,
            1,
        ),
        (EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192, channels=ChannelMode.MONO), 44100, 1),
    ],
)
def test_sample_rate_and_channels_are_honoured(
    toolchain, samples, out_dir, settings, expected_rate, expected_channels
):
    result = _run(toolchain, samples, STANDARD, settings, out_dir)
    assert result.ok, result.message
    assert result.output is not None and result.output.is_file()

    info = _audio(toolchain, result.output)
    assert info.audio.codec_name == "mp3"
    assert info.audio.sample_rate == expected_rate
    assert info.audio.channels == expected_channels
    assert result.report is not None and result.report.ok
    assert result.message == result.summary()
    assert ".part.mp3" not in result.report.checks[0].detail


def test_lower_cbr_bitrate_produces_a_smaller_file(toolchain, samples, out_dir):
    high = _run(
        toolchain, samples, STANDARD,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320), out_dir / "high",
    )
    low = _run(
        toolchain, samples, STANDARD,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=64), out_dir / "low",
    )
    assert high.ok and low.ok
    assert high.output.stat().st_size > low.output.stat().st_size * 2


def test_vbr_quality_orders_file_size(toolchain, samples, out_dir):
    best = _run(
        toolchain, samples, STANDARD,
        EncodeSettings(mode=BitrateMode.VBR, vbr_quality=0), out_dir / "q0",
    )
    worst = _run(
        toolchain, samples, STANDARD,
        EncodeSettings(mode=BitrateMode.VBR, vbr_quality=9), out_dir / "q9",
    )
    assert best.ok and worst.ok
    assert best.output.stat().st_size > worst.output.stat().st_size


def test_hires_source_is_clamped_to_an_mp3_legal_rate(toolchain, samples, out_dir):
    result = _run(
        toolchain, samples, HIRES, EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320), out_dir
    )
    assert result.ok, result.message
    assert _audio(toolchain, result.output).audio.sample_rate == 48000
    assert any("96000" in w for w in result.warnings)


def test_surround_source_is_downmixed_with_a_warning(toolchain, samples, out_dir):
    result = _run(
        toolchain, samples, SURROUND,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=256), out_dir,
    )
    assert result.ok, result.message
    assert _audio(toolchain, result.output).audio.channels == 2
    assert any("声道" in w for w in result.warnings)


def test_high_bitrate_at_a_low_sample_rate_is_rejected_up_front(toolchain, samples, out_dir):
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320, sample_rate=22050)
    result = _run(toolchain, samples, STANDARD, settings, out_dir)
    assert result.state is JobState.FAILED
    assert "320" in result.message and "22050" in result.message


# --------------------------------------------------------------------------- #
# decryption equivalence
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("encrypted", ENCRYPTED)
def test_encrypted_inputs_are_byte_identical_to_the_plain_flac(
    toolchain, samples, out_dir, encrypted
):
    """All three ciphers must decrypt to the very same audio."""
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=256)
    baseline = _run(toolchain, samples, STANDARD, settings, out_dir / "plain")
    candidate = _run(toolchain, samples, encrypted, settings, out_dir / "encrypted")
    assert baseline.ok, baseline.message
    assert candidate.ok, candidate.message
    assert candidate.output.read_bytes() == baseline.output.read_bytes()


@pytest.mark.parametrize("encrypted", ENCRYPTED)
def test_encrypted_inputs_keep_their_original_filename(toolchain, samples, out_dir, encrypted):
    """The decrypted temp file's random name must not leak into the output."""
    result = _run(
        toolchain, samples, encrypted,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192),
        out_dir,
        template="{filename}.mp3",
    )
    assert result.ok, result.message
    assert result.output.name == f"{Path(encrypted).stem}.mp3"
    assert not result.output.name.startswith("echoshift_")


def test_encrypted_conversion_reports_its_container(toolchain, samples, out_dir):
    result = _run(
        toolchain, samples, MFLAC_MAP,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192), out_dir,
    )
    assert result.ok, result.message
    assert result.container is not None
    assert "MFLAC" in result.container.describe()


# --------------------------------------------------------------------------- #
# metadata
# --------------------------------------------------------------------------- #


def test_tags_and_cover_survive_the_conversion(toolchain, samples, out_dir):
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192)
    baseline = probe(toolchain.ffprobe, samples / STANDARD)
    assert baseline.cover is not None, "fixture should carry cover art"

    result = _run(toolchain, samples, MFLAC_RC4, settings, out_dir)
    assert result.ok, result.message

    info = probe(toolchain.ffprobe, result.output)
    for tag in ("title", "artist", "album", "date", "genre"):
        assert info.tag(tag) == baseline.tag(tag), f"tag {tag} was not preserved"
    assert info.cover is not None
    assert info.cover.width == baseline.cover.width
    assert info.cover.height == baseline.cover.height


def test_cover_can_be_stripped(toolchain, samples, out_dir):
    result = _run(
        toolchain, samples, STANDARD,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192, write_cover=False),
        out_dir,
    )
    assert result.ok, result.message
    assert probe(toolchain.ffprobe, result.output).cover is None


def test_tags_can_be_stripped(toolchain, samples, out_dir):
    result = _run(
        toolchain, samples, STANDARD,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192, copy_tags=False),
        out_dir,
    )
    assert result.ok, result.message
    assert probe(toolchain.ffprobe, result.output).tag("artist") == ""


def test_id3v2_3_and_4_both_produce_readable_tags(toolchain, samples, out_dir):
    for version in (3, 4):
        result = _run(
            toolchain, samples, STANDARD,
            EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192, id3_version=version),
            out_dir / f"id3v2{version}",
        )
        assert result.ok, result.message
        assert probe(toolchain.ffprobe, result.output).tag("title") == "测试曲目"


def test_untagged_input_falls_back_to_the_original_filename(toolchain, samples, out_dir):
    result = _run(
        toolchain, samples, UNTAGGED,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128),
        out_dir,
        template="{artist} - {title}.mp3",
    )
    assert result.ok, result.message
    assert result.output.name == "untagged.mp3"


# --------------------------------------------------------------------------- #
# output paths
# --------------------------------------------------------------------------- #


def test_library_template_builds_nested_directories(toolchain, samples, out_dir):
    result = _run(
        toolchain, samples, STANDARD,
        EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192),
        out_dir,
        template="{artist}/{album}/{track:02d} {title}.mp3",
        keystore=KeyStore(),
    )
    assert result.ok, result.message
    assert result.output.name == "03 测试曲目.mp3"
    assert result.output.parent.name == "Sample Album"
    assert result.output.parent.parent.name == "EchoShift 测试"


def test_source_relative_output_when_no_directory_is_given(toolchain, samples, out_dir):
    """With no output_dir, results land next to the input."""
    staged = out_dir / "staged"
    staged.mkdir(parents=True, exist_ok=True)
    local = staged / "local.flac"
    local.write_bytes((samples / UNTAGGED).read_bytes())

    options = PipelineOptions(
        settings=EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128),
        template="{filename}.mp3",
        output_dir=None,
    )
    result = Pipeline(toolchain, options).run_job(local)
    assert result.ok, result.message
    assert result.output.parent == staged
    assert result.output.name == "local.mp3"


# --------------------------------------------------------------------------- #
# overwrite policy
# --------------------------------------------------------------------------- #


def test_rename_policy_never_overwrites(toolchain, samples, out_dir):
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128)
    first = _run(toolchain, samples, UNTAGGED, settings, out_dir)
    assert first.ok
    original_bytes = first.output.read_bytes()

    second = _run(
        toolchain, samples, UNTAGGED, settings, out_dir,
        overwrite=OverwritePolicy.RENAME,
    )
    assert second.ok
    assert second.output.name == "untagged (2).mp3"
    assert first.output.read_bytes() == original_bytes


def test_skip_policy_leaves_the_existing_file_alone(toolchain, samples, out_dir):
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128)
    first = _run(toolchain, samples, UNTAGGED, settings, out_dir)
    marker = first.output
    marker.write_bytes(b"SENTINEL")

    second = _run(
        toolchain, samples, UNTAGGED, settings, out_dir,
        overwrite=OverwritePolicy.SKIP,
    )
    assert second.state is JobState.SKIPPED
    assert marker.read_bytes() == b"SENTINEL"


def test_overwrite_policy_replaces_the_existing_file(toolchain, samples, out_dir):
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128)
    first = _run(toolchain, samples, UNTAGGED, settings, out_dir)
    first.output.write_bytes(b"SENTINEL")

    second = _run(
        toolchain, samples, UNTAGGED, settings, out_dir,
        overwrite=OverwritePolicy.OVERWRITE,
    )
    assert second.ok
    assert second.output.read_bytes() != b"SENTINEL"


def test_failed_overwrite_preserves_existing_output_and_removes_partial(
    toolchain, samples, out_dir, monkeypatch
):
    target = out_dir / "untagged.mp3"
    target.write_bytes(b"SENTINEL")
    options = PipelineOptions(
        settings=EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128),
        template="{filename}.mp3",
        output_dir=out_dir,
        overwrite=OverwritePolicy.OVERWRITE,
        retries=0,
    )
    pipeline = Pipeline(toolchain, options)

    def fail_after_writing(_input, output, _plan, _index, **_kwargs):
        output.write_bytes(b"PARTIAL")
        raise EchoShiftError("模拟转码失败")

    monkeypatch.setattr(pipeline, "_encode", fail_after_writing)
    result = pipeline.run_job(samples / UNTAGGED)

    assert result.state is JobState.FAILED
    assert target.read_bytes() == b"SENTINEL"
    assert list(out_dir.glob("*.part*")) == []


# --------------------------------------------------------------------------- #
# failure handling
# --------------------------------------------------------------------------- #


def test_missing_file_fails_without_raising(toolchain, samples, out_dir):
    options = PipelineOptions(
        settings=EncodeSettings(), template="{filename}.mp3", output_dir=out_dir
    )
    result = Pipeline(toolchain, options).run_job(samples / "does-not-exist.flac")
    assert result.state is JobState.FAILED
    assert "不存在" in result.message


def test_non_media_file_fails_cleanly_at_the_probe_stage(toolchain, samples, out_dir):
    """There is no extension gate any more; ffprobe is what rejects it."""
    stray = out_dir / "notes.txt"
    stray.write_text("hello", encoding="utf-8")
    options = PipelineOptions(
        settings=EncodeSettings(), template="{filename}.mp3", output_dir=out_dir
    )
    result = Pipeline(toolchain, options).run_job(stray)
    assert result.state is JobState.FAILED
    assert "ffprobe" in result.message


def test_explicit_file_with_an_unfamiliar_extension_still_converts(
    toolchain, samples, out_dir
):
    """A FLAC named .bin must convert: ffprobe decides, not the file name."""
    disguised = out_dir / "disguised.bin"
    disguised.write_bytes((samples / STANDARD).read_bytes())

    options = PipelineOptions(
        settings=EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192),
        template="{filename}.mp3",
        output_dir=out_dir,
    )
    result = Pipeline(toolchain, options).run_job(disguised)

    assert result.ok, result.message
    assert result.output.name == "disguised.mp3"
    assert probe(toolchain.ffprobe, result.output).audio.codec_name == "mp3"


def test_collect_sources_accepts_explicit_files_of_any_extension(samples: Path, out_dir: Path):
    disguised = out_dir / "anything.xyz"
    disguised.write_bytes((samples / STANDARD).read_bytes())
    assert collect_sources([disguised]) == [disguised.resolve()]


def test_directory_scan_keeps_mislabelled_audio_but_drops_junk(
    samples: Path, out_dir: Path
):
    tree = out_dir / "mixed"
    tree.mkdir(parents=True, exist_ok=True)
    (tree / "song.flac").write_bytes((samples / UNTAGGED).read_bytes())
    (tree / "mislabelled.bin").write_bytes((samples / UNTAGGED).read_bytes())
    (tree / "cover.jpg").write_bytes(b"\xff\xd8\xff\xe0" + bytes(32))
    (tree / "notes.txt").write_text("hello", encoding="utf-8")
    (tree / "album.cue").write_text("FILE x WAVE", encoding="utf-8")

    found = {path.name for path in collect_sources([tree])}
    assert found == {"song.flac", "mislabelled.bin"}


def test_temporary_files_are_cleaned_up_after_success(toolchain, samples, work_dir):
    before = set(work_dir.glob("echoshift_*"))
    options = PipelineOptions(
        settings=EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192),
        template="{filename}.mp3",
        output_dir=work_dir / "cleanup_out",
        work_dir=work_dir,
    )
    result = Pipeline(toolchain, options, keystore=KeyStore()).run_job(samples / MFLAC_MAP)
    assert result.ok, result.message
    assert set(work_dir.glob("echoshift_*")) == before


# --------------------------------------------------------------------------- #
# batching
# --------------------------------------------------------------------------- #


def test_batch_preserves_order_and_converts_everything(toolchain, samples, out_dir):
    settings = EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192)
    sources = [samples / name for name in (STANDARD, QMC1, MFLAC_MAP, MFLAC_RC4)]

    seen: list[tuple[int, JobState]] = []
    options = PipelineOptions(
        settings=settings, template="{filename}.mp3", output_dir=out_dir
    )
    pipeline = Pipeline(toolchain, options, keystore=KeyStore())
    results = pipeline.run_batch(
        sources, on_result=lambda i, r: seen.append((i, r.state)), workers=4
    )

    assert [r.source for r in results] == sources
    assert all(r.ok for r in results), [r.message for r in results if not r.ok]
    assert len(seen) == 4
    # standard.flac and standard.qmcflac both want "standard.mp3", so the
    # rename policy must have kept them apart; the other two have distinct stems.
    names = sorted(r.output.name for r in results)
    assert names == [
        "standard (2).mp3",
        "standard.mp3",
        "standard_map.mp3",
        "standard_rc4.mp3",
    ]


def test_collect_sources_walks_directories_recursively(samples: Path, out_dir: Path):
    nested = out_dir / "tree" / "deeper"
    nested.mkdir(parents=True, exist_ok=True)
    (out_dir / "tree" / "a.flac").write_bytes((samples / UNTAGGED).read_bytes())
    (nested / "b.mflac").write_bytes((samples / MFLAC_MAP).read_bytes())
    (nested / "ignored.txt").write_text("x", encoding="utf-8")

    found = collect_sources([out_dir / "tree"])
    assert {p.name for p in found} == {"a.flac", "b.mflac"}

    shallow = collect_sources([out_dir / "tree"], recursive=False)
    assert {p.name for p in shallow} == {"a.flac"}


def test_collect_sources_deduplicates(samples: Path):
    target = samples / STANDARD
    assert collect_sources([target, target, samples]) != []
    assert len(collect_sources([target, target])) == 1


# --------------------------------------------------------------------------- #
# output-name collisions
#
# Windows and macOS filesystems fold case, so "Song.mp3" and "song.mp3" are one
# file.  A plain ``Path.exists()`` check says otherwise, which under the rename
# policy produced a duplicate and under overwrite could replace a file the user
# never asked about.
# --------------------------------------------------------------------------- #


def _zero_duration_report(*_args, **_kwargs):
    from echoshift.core.verify import VerificationReport

    return VerificationReport(ok=True, checks=())


@pytest.mark.skipif(os.name != "nt", reason="只有大小写不敏感的文件系统才会折叠文件名")
@pytest.mark.parametrize("policy", list(OverwritePolicy))
def test_a_case_variant_of_the_output_name_counts_as_taken(
    toolchain, samples, out_dir, policy, monkeypatch
):
    monkeypatch.setattr("echoshift.core.pipeline.verify_output", _zero_duration_report)
    # A template with no variables so the only difference is the case of the
    # name we ask for versus the one already on disk.
    (out_dir / "flagged.mp3").write_bytes(b"original")

    options = PipelineOptions(
        settings=EncodeSettings(),
        template="FLAGGED.mp3",
        output_dir=out_dir,
        overwrite=policy,
        verify=False,
        retries=0,
    )
    pipeline = Pipeline(toolchain, options, keystore=KeyStore())
    result = pipeline.run_batch([samples / UNTAGGED])

    assert result[0].state in (JobState.DONE, JobState.SKIPPED), result[0].message
    if policy is OverwritePolicy.SKIP:
        assert result[0].state is JobState.SKIPPED
    elif policy is OverwritePolicy.OVERWRITE:
        assert result[0].output == out_dir / "FLAGGED.mp3"
    else:
        # Renaming is the point: the case-variant file is left alone.
        assert result[0].output.name.startswith("FLAGGED (")
        assert (out_dir / "flagged.mp3").read_bytes() == b"original"
