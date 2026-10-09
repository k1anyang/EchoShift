"""Command line interface.

Offers the same capabilities as the GUI so batches can be scripted and the
tool can be verified headlessly.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .console import enable_utf8_output
from .errors import EchoShiftError
from .core.ffmpeg import find_toolchain
from .core.naming import TEMPLATE_PRESETS
from .core.pipeline import (
    JobResult,
    JobState,
    OverwritePolicy,
    Pipeline,
    PipelineOptions,
    collect_sources,
    default_work_dir,
)
from .core.probe import probe
from .core.settings import (
    PRESETS,
    SAMPLE_RATES,
    VBR_QUALITY_TABLE,
    BitrateMode,
    ChannelMode,
    EncodeSettings,
)
from .qmc.decoder import SUPPORTED_EXTENSIONS, prepare_input
from .qmc.keystore import KeyStore

__all__ = ["build_parser", "main", "run"]


def _sample_rate(value: str) -> int | None:
    text = value.strip().lower()
    if text in ("keep", "source", "auto", "0"):
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"采样率必须是数字或 keep，收到 {value!r}"
        ) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="echoshift",
        description="把 ffmpeg 能读的音频/视频以及 QQ 音乐加密文件（MFLAC/MGG/QMC）转成 MP3，可控制比特率与采样率。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  echoshift song.flac -o out --mode vbr -q 0\n"
            "  echoshift locked.mflac --ekey <EKEY> -o out --mode cbr -b 320\n"
            "  echoshift D:\\Music -o D:\\MP3 -r --sample-rate 44100 --channels stereo\n"
            "  echoshift --list-presets\n"
        ),
    )
    parser.add_argument("inputs", nargs="*", help="音频文件或目录")
    parser.add_argument("-o", "--output-dir", help="输出目录；省略则与源文件同目录")
    parser.add_argument(
        "-t", "--template", default="{filename}.mp3",
        help="输出命名模板（默认 {filename}.mp3）",
    )
    parser.add_argument(
        "--mode", choices=[m.value for m in BitrateMode], default=BitrateMode.VBR.value,
        help="码率模式：vbr（默认）/ cbr / abr",
    )
    parser.add_argument(
        "-q", "--quality", type=int, metavar="0-9",
        help="VBR 质量，0 最高、9 最低（默认 2）",
    )
    parser.add_argument(
        "-b", "--bitrate", type=int, metavar="KBPS",
        help="CBR / ABR 目标码率，单位 kbps（默认 CBR 320、ABR 192）",
    )
    parser.add_argument(
        "--sample-rate", type=_sample_rate, default=None, metavar="HZ",
        help=f"目标采样率，取值 {'、'.join(str(r) for r in SAMPLE_RATES)} 或 keep（默认 keep）",
    )
    parser.add_argument(
        "--channels", choices=[c.value for c in ChannelMode], default=ChannelMode.KEEP.value,
        help="声道处理：keep（默认）/ mono / stereo",
    )
    parser.add_argument(
        "--overwrite", choices=[p.value for p in OverwritePolicy],
        default=OverwritePolicy.RENAME.value,
        help="同名文件策略：rename（默认）/ overwrite / skip",
    )
    parser.add_argument("--no-verify", action="store_true", help="跳过转换后校验")
    parser.add_argument("--no-deep-verify", action="store_true", help="校验时不完整解码")
    parser.add_argument("--retries", type=int, default=1, metavar="N", help="失败重试次数（默认 1）")
    parser.add_argument(
        "-j", "--workers", type=int, default=1, metavar="N", help="并行任务数（默认 1）"
    )
    parser.add_argument("-r", "--recursive", action="store_true", default=True,
                        help="递归处理目录（默认开启）")
    parser.add_argument("--no-recursive", dest="recursive", action="store_false",
                        help="不递归子目录")
    parser.add_argument("--ekey", help="手动指定 ekey（用于 musicex 等不含密钥的容器）")
    parser.add_argument("--key-db", help="额外的密钥库 JSON 路径")
    parser.add_argument("--ffmpeg-dir", help="ffmpeg / ffprobe 所在目录")
    parser.add_argument(
        "--id3", choices=["2.3", "2.4"], default="2.4", help="ID3v2 版本（默认 2.4）"
    )
    parser.add_argument("--no-tags", action="store_true", help="不保留标签")
    parser.add_argument("--no-cover", action="store_true", help="不保留封面")
    parser.add_argument("--no-joint-stereo", dest="joint_stereo",
                        action="store_false", default=True, help="关闭联合立体声")

    parser.add_argument("--dry-run", action="store_true", help="只解析文件与参数，不实际转换")
    parser.add_argument(
        "--diagnose", action="store_true",
        help="诊断加密容器：打印文件头尾、尾部布局与密钥判定（不转换，用于排查 mflac 打不开）",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果摘要")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细信息")
    parser.add_argument("--list-presets", action="store_true", help="列出内置预设与输出模板")
    parser.add_argument("--version", action="store_true", help="显示版本后退出")
    return parser


def _settings_from_args(args: argparse.Namespace) -> EncodeSettings:
    mode = BitrateMode(args.mode)
    if args.quality is not None and mode is not BitrateMode.VBR:
        print("提示：-q/--quality 只在 VBR 模式下生效，已忽略。", file=sys.stderr)
    if args.bitrate is not None and mode is BitrateMode.VBR:
        print("提示：-b/--bitrate 在 VBR 模式下无效，请用 -q 指定质量。", file=sys.stderr)

    cbr = args.bitrate if (args.bitrate is not None and mode is BitrateMode.CBR) else 320
    abr = args.bitrate if (args.bitrate is not None and mode is BitrateMode.ABR) else 192
    return EncodeSettings(
        mode=mode,
        vbr_quality=args.quality if args.quality is not None else 2,
        cbr_bitrate=cbr,
        abr_bitrate=abr,
        sample_rate=args.sample_rate,
        channels=ChannelMode(args.channels),
        joint_stereo=args.joint_stereo,
        copy_tags=not args.no_tags,
        write_cover=not args.no_cover,
        id3_version=4 if args.id3 == "2.4" else 3,
    ).clamped()


def _print_presets() -> None:
    print("内置预设：")
    for name, settings in PRESETS:
        print(f"  {name:20s} {settings.describe()}")
    print("\n输出命名模板：")
    for template, description in TEMPLATE_PRESETS:
        print(f"  {description:24s} {template}")
    print(
        "\n可用变量：{filename} {title} {artist} {album} {albumartist} {track} {disc} "
        "{year} {genre} {composer} {index} {samplerate} {bitrate} {mode}"
    )
    print("\n支持的采样率：" + "、".join(str(r) for r in SAMPLE_RATES))
    print("VBR 质量参考：" + "、".join(f"q{q}≈{kbps}k" for q, kbps in VBR_QUALITY_TABLE.items()))


def _result_to_dict(result: JobResult) -> dict:
    payload: dict[str, object] = {
        "source": str(result.source),
        "state": result.state.value,
        "ok": result.ok,
        "elapsed": round(result.elapsed, 3),
        "attempts": result.attempts,
        "message": result.message,
    }
    if result.output is not None:
        payload["output"] = str(result.output)
    if result.plan is not None:
        payload["plan"] = result.plan.describe()
        payload["sample_rate"] = result.plan.sample_rate
        payload["channels"] = result.plan.channels
        payload["nominal_kbps"] = result.plan.nominal_kbps
    if result.source_info is not None:
        payload["source_format"] = result.source_info.format_name
        payload["duration"] = round(result.source_info.duration, 3)
    if result.output_info is not None:
        audio = result.output_info.audio
        payload["output_sample_rate"] = audio.sample_rate if audio else None
        payload["output_channels"] = audio.channels if audio else None
        payload["output_bit_rate"] = result.output_info.bit_rate
    if result.warnings:
        payload["warnings"] = list(result.warnings)
    if result.container is not None:
        payload["container"] = result.container.describe()
    if result.report is not None:
        payload["checks"] = [
            {"name": c.name, "ok": c.ok, "detail": c.detail} for c in result.report.checks
        ]
    return payload


def _diagnosis_to_dict(result) -> dict:
    """Serialise a :class:`~echoshift.qmc.diagnose.Diagnosis` for ``--json``."""
    return {
        "source": str(result.path),
        "size": result.size,
        "format": result.format_label,
        "head_hex": result.head.hex(" "),
        "head_ascii": "".join(
            chr(b) if 32 <= b < 127 else "." for b in result.head
        ),
        "tail_hex": result.tail.hex(" "),
        "tail_ascii": "".join(
            chr(b) if 32 <= b < 127 else "." for b in result.tail
        ),
        "markers": [
            {"marker": marker.decode("latin-1"), "offset_in_tail_scan": index}
            for marker, index in result.markers
        ],
        "footer_kind": result.footer.kind.value,
        "footer": {
            "mid": result.footer.mid,
            "song_id": result.footer.song_id,
            "filename": result.footer.filename,
            "key_size": result.footer.key_size,
        },
        "audio_length": result.audio_length,
        "ekey_present": result.ekey_present,
        "ekey_source": result.ekey_source,
        "ekey_decoded_length": result.ekey_decoded_length,
        "cipher": result.cipher,
        "key_error": result.key_error,
        "decrypted_head_hex": (
            result.decrypted_head.hex(" ") if result.decrypted_head else None
        ),
        "decrypted_head_sniff": result.decrypted_head_sniff,
        "verdict": result.verdict,
        "hints": list(result.hints),
    }


def _describe_dry_run(toolchain, source: Path, settings: EncodeSettings, keystore: KeyStore,
                      work_dir: Path) -> dict:
    with prepare_input(source, work_dir, keystore=keystore) as prepared:
        info = probe(toolchain.ffprobe, prepared.path)
        plan = settings.resolve(
            info.audio.sample_rate if info.audio else None,
            info.audio.channels if info.audio else None,
        )
        payload = {
            "source": str(source),
            "container": prepared.container.describe() if prepared.container else None,
            "source_info": info.describe(),
            "duration": round(info.duration, 3),
            "source_sample_rate": info.audio.sample_rate if info.audio else None,
            "source_channels": info.audio.channels if info.audio else None,
            "tags": {k: v for k, v in sorted(info.tags.items()) if k in
                     ("title", "artist", "album", "album_artist", "track", "date", "genre")},
            "cover": info.cover.describe() if info.cover else None,
            "plan": plan.describe(),
        }
        return payload


def run(argv: Sequence[str] | None = None) -> int:
    enable_utf8_output()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from . import __version__

        print(f"echoshift {__version__}")
        return 0
    if args.list_presets:
        _print_presets()
        return 0

    if not args.inputs:
        parser.print_help()
        return 2

    try:
        settings = _settings_from_args(args)
        settings.validate()
    except EchoShiftError as exc:
        print(f"参数错误：{exc}", file=sys.stderr)
        return 2

    try:
        toolchain = find_toolchain(args.ffmpeg_dir or None)
    except EchoShiftError as exc:
        print(f"{exc}", file=sys.stderr)
        return 3

    if args.verbose:
        print(f"ffmpeg：{toolchain.describe()}")
    if not toolchain.has_libmp3lame:
        print("错误：当前 ffmpeg 不含 libmp3lame，无法编码 MP3。", file=sys.stderr)
        return 3

    keystore = KeyStore.build(
        explicit=args.ekey,
        user_database=Path(args.key_db) if args.key_db else None,
    )
    if args.verbose:
        print(f"{keystore.describe()}")

    sources = collect_sources(args.inputs, recursive=args.recursive)
    if not sources:
        print("没有找到受支持的文件。", file=sys.stderr)
        print(f"支持的扩展名：{'、'.join(sorted(SUPPORTED_EXTENSIONS))}", file=sys.stderr)
        return 4

    work_dir = default_work_dir()

    if args.diagnose:
        from .qmc.diagnose import diagnose, render

        rows = []
        for source in sources:
            try:
                result = diagnose(source, keystore)
            except EchoShiftError as exc:
                rows.append({"source": str(source), "error": str(exc)})
                continue
            rows.append(_diagnosis_to_dict(result))
            if not args.json:
                print(render(result))
                print()
        if args.json:
            print(json.dumps({"diagnose": True, "items": rows}, ensure_ascii=False, indent=2))
        return 0

    if args.dry_run:
        rows = []
        for source in sources:
            try:
                rows.append(_describe_dry_run(toolchain, source, settings, keystore, work_dir))
            except EchoShiftError as exc:
                rows.append({"source": str(source), "error": str(exc)})
        if args.json:
            print(json.dumps({"dry_run": True, "items": rows}, ensure_ascii=False, indent=2))
        else:
            for row in rows:
                if "error" in row:
                    print(f"✗ {Path(row['source']).name}: {row['error']}")
                    continue
                print(f"• {Path(row['source']).name}")
                if row.get("container"):
                    print(f"    容器：{row['container']}")
                print(f"    源：{row['source_info']}")
                print(f"    参数：{row['plan']}")
                if row.get("cover"):
                    print(f"    封面：{row['cover']}")
        return 0

    output_dir = Path(args.output_dir) if args.output_dir else None
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    options = PipelineOptions(
        settings=settings,
        template=args.template,
        output_dir=output_dir,
        overwrite=OverwritePolicy(args.overwrite),
        verify=not args.no_verify,
        deep_verify=not args.no_deep_verify,
        retries=max(0, args.retries),
        ekey=args.ekey,
        work_dir=work_dir,
    )

    quiet = args.json
    logger = (lambda text: print(text)) if args.verbose and not quiet else (lambda _text: None)

    def on_result(index: int, result: JobResult) -> None:
        if quiet:
            return
        mark = {
            JobState.DONE: "✓",
            JobState.FAILED: "✗",
            JobState.SKIPPED: "−",
            JobState.CANCELLED: "·",
        }.get(result.state, "?")
        name = result.source.name
        if result.state is JobState.DONE and result.output is not None:
            detail = f"{result.output.name} · {result.summary()}"
        else:
            detail = result.summary()
        print(f"[{index + 1}/{len(sources)}] {mark} {name} — {detail}")

    pipeline = Pipeline(toolchain, options, keystore=keystore, logger=logger)
    results = pipeline.run_batch(
        sources, on_result=on_result, workers=max(1, args.workers)
    )

    done = sum(1 for r in results if r.state is JobState.DONE)
    failed = sum(1 for r in results if r.state is JobState.FAILED)
    skipped = sum(1 for r in results if r.state is JobState.SKIPPED)
    cancelled = sum(1 for r in results if r.state is JobState.CANCELLED)
    elapsed = sum(r.elapsed for r in results)

    if args.json:
        print(json.dumps(
            {
                "summary": {
                    "total": len(results),
                    "succeeded": done,
                    "failed": failed,
                    "skipped": skipped,
                    "cancelled": cancelled,
                    "cpu_seconds": round(elapsed, 2),
                },
                "items": [_result_to_dict(r) for r in results],
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        parts = [f"完成 {done}"]
        if failed:
            parts.append(f"失败 {failed}")
        if skipped:
            parts.append(f"跳过 {skipped}")
        if cancelled:
            parts.append(f"取消 {cancelled}")
        print("\n汇总：" + " · ".join(parts) + f"（共 {len(results)} 个文件，耗时 {elapsed:.1f}s）")

    return 1 if failed else 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
