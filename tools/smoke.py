"""Ad-hoc smoke run over the generated fixtures (not part of the test suite)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

WORKSPACE_TMP = ROOT / ".tmp"
WORKSPACE_TMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(WORKSPACE_TMP)

from echoshift.console import enable_utf8_output
from echoshift.core.ffmpeg import find_toolchain
from echoshift.core.pipeline import Pipeline, PipelineOptions
from echoshift.core.probe import probe
from echoshift.core.settings import BitrateMode, ChannelMode, EncodeSettings

enable_utf8_output()

SAMPLES = ROOT / "samples"


def main() -> int:
    toolchain = find_toolchain()
    print("toolchain:", toolchain.describe())

    cases = [
        ("standard.flac", EncodeSettings(mode=BitrateMode.VBR, vbr_quality=2), "V2"),
        ("standard.qmcflac", EncodeSettings(mode=BitrateMode.VBR, vbr_quality=2), "QMC1→V2"),
        ("standard_map.mflac", EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=192), "MFLAC Map→CBR192"),
        ("standard_rc4.mflac", EncodeSettings(mode=BitrateMode.ABR, abr_bitrate=128), "MFLAC RC4→ABR128"),
        ("hires.flac", EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=320), "96k→CBR320"),
        ("hires.flac", EncodeSettings(mode=BitrateMode.VBR, vbr_quality=0, sample_rate=44100), "96k→44.1k V0"),
        (
            "surround.flac",
            EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=256, channels=ChannelMode.STEREO),
            "5.1→stereo",
        ),
        (
            "untagged.flac",
            EncodeSettings(mode=BitrateMode.CBR, cbr_bitrate=128, channels=ChannelMode.MONO),
            "→mono 8k",
        ),
    ]

    failures = 0
    out_root = WORKSPACE_TMP / "smoke_out"
    if out_root.exists():
        shutil.rmtree(out_root, ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)
    for name, settings, label in cases:
        source = SAMPLES / name
        if not source.is_file():
            print(f"  !! missing fixture {name}")
            failures += 1
            continue
        # Force 8 kHz for the mono case to exercise resampling.
        if "mono 8k" in label:
            settings = EncodeSettings(
                mode=BitrateMode.CBR, cbr_bitrate=32, channels=ChannelMode.MONO,
                sample_rate=8000,
            )
        options = PipelineOptions(
            settings=settings,
            template="{filename}.mp3",
            output_dir=out_root / label.replace("→", "_").replace(" ", ""),
            verify=True,
            deep_verify=True,
        )
        pipeline = Pipeline(toolchain, options, logger=lambda m: None)
        result = pipeline.run_job(source)
        status = "OK " if result.ok else "FAIL"
        if not result.ok:
            failures += 1
        print(f"[{status}] {label:20s} {name:22s} {result.summary()}")
        if not result.ok:
            print(f"        {result.message}")
        elif result.output and result.output.exists():
            info = probe(toolchain.ffprobe, result.output)
            audio = info.audio
            print(
                f"        {result.output.name} · {audio.codec_name} "
                f"{audio.sample_rate}Hz {audio.channels}ch "
                f"{round((info.bit_rate or 0)/1000)}kbps "
                f"cover={'yes' if info.cover else 'no'} "
                f"tags={info.tag('title')!r}/{info.tag('artist')!r}"
            )
            if result.report:
                print(f"        {result.report.render().replace(chr(10), ' | ')}")

    print(f"\nfixtures failing: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
