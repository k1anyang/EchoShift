"""Forensic dump of a QQ Music container.

When a ``.mflac`` refuses to decode, the useful question is *which* of the
possible layouts the file actually uses, and whether the key we found is the
right one.  Guessing from documentation is how you end up implementing a footer
format that no real file has, so this module answers it from the bytes:

* the first and last bytes, in hex and ASCII;
* which tail markers (``QTag``, ``musicex``) appear anywhere near the end;
* what :func:`~echoshift.qmc.footer.detect_footer` concluded and why;
* whether an ekey was resolved, how long it decodes to, and which cipher that
  selects;
* **the decisive test** -- decrypt the first bytes with that key and see
  whether the result carries a real container signature.

If the decrypted head is not recognisable, the key or the layout is wrong; if
it is, the problem lies further down the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .decoder import format_for_path, inspect, sniff_audio
from .footer import Footer, FooterKind, detect_footer
from .keystore import KeyStore
from .qmc1 import decrypt_in_place
from .qmc2 import Qmc2Crypto, Qmc2Error, parse_ekey

__all__ = ["Diagnosis", "diagnose", "render"]

#: How much of the tail to dump.  Big enough to show any realistic footer.
_TAIL_DUMP = 96
_TAIL_SCAN = 4096
_HEAD_DUMP = 32

_MARKERS: tuple[bytes, ...] = (b"QTag", b"musicex\x00", b"musicex")


def _hexdump(data: bytes) -> str:
    return " ".join(f"{byte:02x}" for byte in data)


def _ascii(data: bytes) -> str:
    return "".join(chr(byte) if 32 <= byte < 127 else "." for byte in data)


def _find_markers(data: bytes) -> list[tuple[bytes, int]]:
    found: list[tuple[bytes, int]] = []
    for marker in _MARKERS:
        start = 0
        while True:
            index = data.find(marker, start)
            if index < 0:
                break
            found.append((marker, index))
            start = index + 1
    return sorted(found, key=lambda item: item[1])


@dataclass
class Diagnosis:
    """Everything observed about one container."""

    path: Path
    size: int
    head: bytes = b""
    tail: bytes = b""
    head_sniff: str | None = None
    markers: list[tuple[bytes, int]] = field(default_factory=list)
    footer: Footer = field(default_factory=lambda: Footer(FooterKind.NONE))
    format_label: str = ""
    audio_length: int | None = None
    ekey_present: bool = False
    ekey_source: str | None = None
    ekey_decoded_length: int | None = None
    cipher: str | None = None
    key_error: str | None = None
    decrypted_head: bytes | None = None
    decrypted_head_sniff: str | None = None
    verdict: str = ""
    hints: list[str] = field(default_factory=list)


def diagnose(
    path: Path | str,
    keystore: KeyStore | None = None,
    *,
    ekey: str | None = None,
) -> Diagnosis:
    """Inspect ``path`` without writing anything."""
    path = Path(path)
    result = Diagnosis(path=path, size=path.stat().st_size)

    size = result.size
    with open(path, "rb") as handle:
        result.head = handle.read(_HEAD_DUMP)
        if size > _TAIL_SCAN:
            handle.seek(size - _TAIL_SCAN)
        tail_scan = handle.read(_TAIL_SCAN)
    result.tail = tail_scan[-_TAIL_DUMP:]
    result.markers = _find_markers(tail_scan)
    result.head_sniff = sniff_audio(result.head)

    fmt = format_for_path(path)
    result.format_label = fmt.label if fmt else "（不是已知的 QQ 音乐容器扩展名）"

    result.footer = detect_footer(tail_scan, size)
    result.audio_length = (
        result.footer.audio_length if result.footer.audio_length is not None else size
    )

    if fmt is None:
        result.verdict = "扩展名不认识，会被当成普通音频直接交给 ffmpeg"
        return result

    info = inspect(path, keystore, ekey=ekey)
    result.ekey_present = bool(info.ekey)
    result.ekey_source = info.ekey_source
    result.audio_length = info.audio_length

    if fmt.is_qmc1:
        result.cipher = "QMC1（无密钥）"
        _probe_key(path, info.audio_length, None, True, result)
        return result

    if not info.ekey:
        result.verdict = "没有可用的 ekey —— 这个容器无法解密"
        if result.footer.kind is FooterKind.NONE:
            result.hints.append(
                "尾部没有任何已知标记（QTag / musicex / V1 密钥长度），"
                "说明这个文件的密钥根本不在文件里。"
            )
        elif result.footer.kind is FooterKind.MUSICEX:
            result.hints.append(
                "musicex 尾部（QQ 音乐 19.57+）只存 song_id / mid / 原文件名，"
                "不存 ekey。"
            )
        result.hints.append(
            "关键：装回 19.51 并不会给**已经下载好的**文件补上 ekey —— "
            "必须用 19.51 重新下载一遍，文件尾部才会带 QTag。"
        )
        result.hints.append(
            "也可以手动提供 ekey：界面输入框、同名 .ekey 文件，或密钥库 JSON。"
        )
        return result

    try:
        decoded = parse_ekey(info.ekey)
    except Qmc2Error as exc:
        result.key_error = str(exc)
        result.verdict = f"ekey 无法解析：{exc}"
        return result

    result.ekey_decoded_length = len(decoded)
    result.cipher = f"QMC2 {'RC4（长密钥）' if len(decoded) > 300 else 'Map（短密钥）'}"
    _probe_key(path, info.audio_length, decoded, False, result)
    return result


def _probe_key(
    path: Path,
    audio_length: int,
    raw_key: bytes | None,
    is_qmc1: bool,
    result: Diagnosis,
) -> None:
    """Decrypt the first block and see whether it looks like real audio."""
    length = min(64, audio_length or 64)
    if length <= 0:
        result.verdict = "容器里没有音频数据"
        return
    with open(path, "rb") as handle:
        sample = bytearray(handle.read(length))
    if not sample:
        result.verdict = "容器里没有音频数据"
        return

    try:
        if is_qmc1:
            decrypt_in_place(sample, 0)
        else:
            if raw_key is None:
                result.verdict = "缺少密钥"
                return
            Qmc2Crypto(raw_key).decrypt(sample, 0)
    except Exception as exc:  # noqa: BLE001 - reported verbatim
        result.verdict = f"解密首块时出错：{type(exc).__name__}: {exc}"
        return

    result.decrypted_head = bytes(sample[:_HEAD_DUMP])
    result.decrypted_head_sniff = sniff_audio(result.decrypted_head)

    if result.decrypted_head_sniff:
        result.verdict = (
            f"解密成功，首块是 {result.decrypted_head_sniff} —— "
            "密钥与容器布局都正确"
        )
        return

    result.verdict = "密钥可用，但解密结果不是已知音频签名 —— 布局或密钥不对"
    result.hints.append(
        "如果这个文件来自 musicex 容器而 ekey 是别处抄来的，"
        "请确认 ekey 与文件是同一首歌。"
    )
    result.hints.append(
        "如果文件带 QTag/V1 尾部却解不开，说明真实布局与预期不同 —— "
        "把下面「尾部」那几行发出来即可定位。"
    )


def render(result: Diagnosis) -> str:
    """Format a :class:`Diagnosis` for the console."""
    lines: list[str] = []
    add = lines.append

    add(f"文件      : {result.path}")
    add(f"大小      : {result.size:,} 字节")
    add(f"扩展名    : {result.format_label}")
    add("")
    add(f"文件头    : {_hexdump(result.head)}")
    add(f"            {_ascii(result.head)}")
    add(f"头部签名  : {result.head_sniff or '（不是已知媒体签名，符合“已加密”的预期）'}")
    add("")
    add(f"尾部      : {_hexdump(result.tail)}")
    add(f"            {_ascii(result.tail)}")
    if result.markers:
        for marker, index in result.markers:
            where = "文件最末尾" if index >= len(result.tail) else "尾部扫描区"
            add(f"尾部标记  : {marker!r} 出现在最后 {_TAIL_SCAN} 字节的第 {index} 字节（{where}）")
    else:
        add(f"尾部标记  : 最后 {_TAIL_SCAN} 字节内没有 QTag / musicex")
    add("")
    add(f"尾部类型  : {result.footer.kind.label}")
    if result.footer.mid:
        add(f"            mid={result.footer.mid}")
    if result.footer.song_id:
        add(f"            song_id={result.footer.song_id}")
    if result.footer.filename:
        add(f"            原文件名={result.footer.filename}")
    if result.footer.key_size:
        add(f"            内嵌密钥长度={result.footer.key_size}")
    add(f"音频长度  : {result.audio_length:,} 字节" if result.audio_length else "音频长度  : 未知")
    add("")
    add(f"ekey      : {'有' if result.ekey_present else '没有'}")
    if result.ekey_source:
        add(f"来源      : {result.ekey_source}")
    if result.ekey_decoded_length is not None:
        add(f"解码后长度: {result.ekey_decoded_length} 字节")
    if result.cipher:
        add(f"选定算法  : {result.cipher}")
    if result.key_error:
        add(f"ekey 错误 : {result.key_error}")
    if result.decrypted_head is not None:
        add("")
        add(f"解密后头部: {_hexdump(result.decrypted_head)}")
        add(f"            {_ascii(result.decrypted_head)}")
        add(f"签名      : {result.decrypted_head_sniff or '（无法识别）'}")
    add("")
    add(f"结论      : {result.verdict}")
    for hint in result.hints:
        add(f"提示      : {hint}")
    return "\n".join(lines)
