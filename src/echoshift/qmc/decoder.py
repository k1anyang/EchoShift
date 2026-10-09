"""Turning a QQ Music container into a file ffmpeg can read.

The decoder never loads a whole track into memory: it reads the tail to
classify the container, then streams the payload through the cipher in fixed
chunks.  Each QMC cipher exposes the same ``decrypt(buf, offset)`` shape, which
makes the streaming loop cipher-agnostic -- and, later, lets the RC4 path be
split across processes.
"""

from __future__ import annotations

import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

from ..errors import DecryptionError, UnsupportedInputError
from . import qmc1
from .footer import Footer, FooterKind, detect_footer
from .keystore import KeyStore
from .parallel import decrypt_parallel
from .qmc2 import Qmc2Crypto, Qmc2Error, parse_ekey

__all__ = [
    "QmcFormat",
    "QMC_FORMATS",
    "PLAIN_AUDIO_EXTENSIONS",
    "VIDEO_CONTAINER_EXTENSIONS",
    "SUPPORTED_EXTENSIONS",
    "ContainerInfo",
    "PreparedInput",
    "format_for_path",
    "is_qmc_path",
    "is_supported_path",
    "inspect",
    "decrypt_to",
    "prepare_input",
    "sniff_audio",
    "looks_like_audio",
    "peek",
    "StreamPeek",
    "DEFAULT_CHUNK_SIZE",
]

DEFAULT_CHUNK_SIZE = 4 << 20
_TAIL_READ = 1 << 20
_HEAD_READ = 16


class _Cipher(Protocol):
    def decrypt(self, buf: bytearray, offset: int = 0) -> bytearray: ...


@dataclass(frozen=True)
class QmcFormat:
    """One QQ Music container flavour."""

    extension: str
    is_qmc1: bool
    audio_extension: str
    label: str

    @property
    def cipher_name(self) -> str:
        return "QMC1" if self.is_qmc1 else "QMC2"


#: Every extension this tool recognises as a QQ Music container.
QMC_FORMATS: dict[str, QmcFormat] = {
    fmt.extension: fmt
    for fmt in (
        QmcFormat("qmc0", True, "mp3", "QMC0（QMC1 加密 MP3）"),
        QmcFormat("qmc3", True, "mp3", "QMC3（QMC1 加密 MP3）"),
        QmcFormat("qmc2", True, "ogg", "QMC2（QMC1 加密 OGG）"),
        QmcFormat("qmcogg", True, "ogg", "QMCOGG（QMC1 加密 OGG）"),
        QmcFormat("qmcflac", True, "flac", "QMCFLAC（QMC1 加密 FLAC）"),
        QmcFormat("mflac", False, "flac", "MFLAC（QMC2 加密 FLAC）"),
        QmcFormat("mflac0", False, "flac", "MFLAC0（QMC2 加密 FLAC）"),
        QmcFormat("mflach", False, "flac", "MFLACH（QMC2 加密 FLAC）"),
        QmcFormat("mgg", False, "ogg", "MGG（QMC2 加密 OGG）"),
        QmcFormat("mgg0", False, "ogg", "MGG0（QMC2 加密 OGG）"),
        QmcFormat("mgg1", False, "ogg", "MGG1（QMC2 加密 OGG）"),
        QmcFormat("mggl", False, "ogg", "MGGL（QMC2 加密 OGG）"),
    )
}

#: Straight-through audio containers ffmpeg handles without our help.
#:
#: This list is a *hint for scanning directories*, never a capability limit:
#: ffprobe is the real authority on whether something is decodable, and files
#: the user names explicitly are always attempted.
PLAIN_AUDIO_EXTENSIONS: frozenset[str] = frozenset(
    {
        # lossless
        "flac", "wav", "wave", "w64", "rf64", "aiff", "aif", "aifc", "ape",
        "wv", "tak", "tta", "shn", "ofr", "mpc", "mpp", "dsf", "dff", "alac",
        # lossy
        "mp3", "mp2", "mpa", "aac", "m4a", "m4b", "m4r", "m4p",
        "ogg", "oga", "opus", "spx", "ra", "rm", "wma", "asf", "ac3", "eac3",
        "ac4", "dts", "dtshd", "thd", "mlp", "amr", "awb", "gsm", "au", "snd",
        "voc", "caf", "mka", "oma", "at3", "aa", "aa3", "3gp", "3g2",
        # MIDI and tracked music
        "mid", "midi", "kar", "mod", "s3m", "xm", "it",
    }
)

#: Containers that may carry video; we pull the first audio stream out of them.
VIDEO_CONTAINER_EXTENSIONS: frozenset[str] = frozenset(
    {"mp4", "mkv", "webm", "mov", "avi", "flv", "ts", "m2ts", "mts", "mpg",
     "mpeg", "vob", "wmv", "ogv", "3gpp", "mxf", "dv"}
)

SUPPORTED_EXTENSIONS: frozenset[str] = (
    frozenset(QMC_FORMATS) | PLAIN_AUDIO_EXTENSIONS | VIDEO_CONTAINER_EXTENSIONS
)

_MAGIC_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"fLaC", "flac"),
    (b"OggS", "ogg"),
    (b"ID3", "mp3"),
    (b"RIFF", "wav"),
    (b"FORM", "aiff"),
    (b"wvpk", "wavpack"),
    (b"MAC ", "ape"),
    (b"DSD ", "dsf"),
    (b"FRM8", "dff"),
    (b"\x1aE\xdf\xa3", "matroska"),
    (b"#!AMR", "amr"),
    (b"TTA1", "tta"),
    (b"caff", "caf"),
    (b".snd", "au"),
    (b"MThd", "midi"),
    (b"MPCK", "musepack"),
    (b"MP+", "musepack"),
    (b"OFR ", "optimfrog"),
    (b"ajkg", "shorten"),
    (b"FLV\x01", "flv"),
    (b"Creative Voice File", "voc"),
    (b".ra\xfd", "realmedia"),
    (b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", "asf"),
    (b"\x0b\x77", "ac3"),
    (b"\x7f\xfe\x80\x01", "dts"),
)

#: Extensions that mean "an MP4-family container" and put ``ftyp`` at offset 4.
_ISOBMFF_TAIL = b"ftyp"


def sniff_audio(head: bytes) -> str | None:
    """Identify an audio/video container from its first bytes, if possible."""
    for magic, name in _MAGIC_SIGNATURES:
        if head.startswith(magic):
            return name
    # ISO base media file format: [size][ftyp][brand] -- m4a, mp4, mov, 3gp...
    if len(head) >= 8 and head[4:8] == _ISOBMFF_TAIL:
        return "isobmff"
    # MPEG audio frame sync (MP3/MP2 without an ID3 tag).
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3"
    # MPEG transport stream: sync bytes every 188 bytes.
    if len(head) >= 1 and head[0] == 0x47:
        return "mpegts"
    return None


def looks_like_audio(path: Path, *, probe_bytes: int = 16) -> bool:
    """Cheap content check used to accept files with unfamiliar extensions.

    Directory scans use this so a stray ``.bin`` or extension-less file that is
    really audio is not silently dropped, while a ``.jpg`` still is.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(probe_bytes)
    except OSError:
        return False
    return sniff_audio(head) is not None


def format_for_path(path: Path | str) -> QmcFormat | None:
    return QMC_FORMATS.get(Path(path).suffix.lower().lstrip("."))


def is_qmc_path(path: Path | str) -> bool:
    return format_for_path(path) is not None


def is_supported_path(path: Path | str) -> bool:
    return Path(path).suffix.lower().lstrip(".") in SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class ContainerInfo:
    """What :func:`inspect` learned about one container."""

    path: Path
    format: QmcFormat
    footer: Footer
    audio_length: int
    ekey: str | None = None
    #: Alternative readings of the same trailer; the first that decrypts wins.
    ekey_candidates: tuple[str, ...] = ()
    ekey_source: str | None = None
    already_plain: bool = False

    @property
    def needs_ekey(self) -> bool:
        return (
            not self.format.is_qmc1
            and not self.already_plain
            and not (self.ekey or self.ekey_candidates)
        )

    @property
    def all_ekeys(self) -> tuple[str, ...]:
        if self.ekey_candidates:
            return self.ekey_candidates
        return (self.ekey,) if self.ekey else ()

    def describe(self) -> str:
        if self.already_plain:
            return f"{self.format.label}（未加密，直接转换）"
        bits = [self.format.label, self.footer.kind.label]
        if self.ekey_source:
            bits.append(self.ekey_source)
        return " · ".join(bits)

    def short_describe(self) -> str:
        """A compact one-liner for the queue, where horizontal space is tight."""
        if self.already_plain:
            return f"{self.format.extension.upper()} · 未加密"
        bits = [self.format.extension.upper(), self.footer.kind.short_label]
        if self.ekey_source:
            bits.append("内嵌 ekey" if "内嵌" in self.ekey_source else self.ekey_source)
        elif self.needs_ekey:
            bits.append("需要 ekey")
        return " · ".join(bits)


@dataclass
class PreparedInput:
    """A file ready to hand to ffmpeg, plus how to clean up after it."""

    path: Path
    original: Path
    container: ContainerInfo | None = None
    temporary: bool = False
    _cleaned: bool = field(default=False, repr=False)

    def cleanup(self) -> None:
        if self._cleaned or not self.temporary:
            return
        self._cleaned = True
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def __enter__(self) -> "PreparedInput":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.cleanup()


def _read_head(path: Path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read(_HEAD_READ)


def _read_tail(path: Path, size: int) -> bytes:
    with open(path, "rb") as handle:
        if size > _TAIL_READ:
            handle.seek(size - _TAIL_READ)
        return handle.read(_TAIL_READ)


def inspect(
    path: Path | str,
    keystore: KeyStore | None = None,
    *,
    ekey: str | None = None,
) -> ContainerInfo:
    """Classify ``path`` and resolve the ekey it needs, if any.

    Precedence is deliberate: an ekey embedded in the container always wins,
    because it is verifiably the right one.  ``ekey`` and ``keystore`` are only
    consulted for containers that carry no key of their own.
    """
    path = Path(path)
    fmt = format_for_path(path)
    if fmt is None:
        raise UnsupportedInputError(f"{path.name} 不是受支持的 QQ 音乐加密格式")

    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UnsupportedInputError(f"无法读取 {path.name}：{exc}") from exc
    if size == 0:
        raise UnsupportedInputError(f"{path.name} 是空文件")

    tail = _read_tail(path, size)
    footer = detect_footer(tail, size)
    audio_length = footer.audio_length
    if audio_length is None:
        audio_length = size
    audio_length = max(0, min(audio_length, size))

    if fmt.is_qmc1:
        return ContainerInfo(
            path=path, format=fmt, footer=footer, audio_length=audio_length
        )

    if footer.ekey:
        return ContainerInfo(
            path=path,
            format=fmt,
            footer=footer,
            audio_length=audio_length,
            ekey=footer.ekey,
            ekey_candidates=footer.ekey_candidates,
            ekey_source="文件内嵌 ekey",
        )

    if ekey:
        return ContainerInfo(
            path=path,
            format=fmt,
            footer=footer,
            audio_length=audio_length,
            ekey=ekey,
            ekey_source="手动指定",
        )

    if footer.kind is FooterKind.NONE:
        head = _read_head(path)
        if sniff_audio(head) is not None:
            return ContainerInfo(
                path=path,
                format=fmt,
                footer=footer,
                audio_length=size,
                already_plain=True,
            )

    hit = None
    if keystore is not None:
        hit = keystore.lookup(
            path,
            mid=footer.mid,
            song_id=footer.song_id,
            filename=footer.filename,
        )
    if hit is not None:
        return ContainerInfo(
            path=path,
            format=fmt,
            footer=footer,
            audio_length=audio_length,
            ekey=hit.ekey,
            ekey_source=hit.source,
        )

    return ContainerInfo(path=path, format=fmt, footer=footer, audio_length=audio_length)


@dataclass(frozen=True)
class _KeyChoice:
    """The ekey reading that was found to work, plus the raw key it yields."""

    ekey: str
    raw_key: bytes | None  # None for QMC1, which needs no key


class _Qmc1Cipher:
    """Adapter giving QMC1 the same shape as :class:`Qmc2Crypto`."""

    is_rc4 = False

    def decrypt(self, buf: bytearray, offset: int = 0) -> bytearray:
        return qmc1.decrypt_in_place(buf, offset)


def _decrypts_to_audio(raw_key: bytes, head: bytes) -> bool:
    """True when ``raw_key`` turns the first bytes into a known container."""
    if not head:
        return False
    probe = bytearray(head)
    try:
        Qmc2Crypto(raw_key).decrypt(probe, 0)
    except Exception:  # noqa: BLE001 - any failure just means "not this key"
        return False
    return sniff_audio(bytes(probe[:16])) is not None


def _choose_key(info: ContainerInfo, *, probe_bytes: int = 64) -> _KeyChoice:
    """Pick the trailer reading that actually decrypts this container.

    A V1 trailer is ambiguous -- real QQ Music files store an ASCII base64 ekey
    there, while other tooling writes the raw cipher key -- so every reading is
    tried against the file's own first bytes.  This makes the decoder
    self-correcting instead of depending on getting the guess right.
    """
    if info.format.is_qmc1:
        return _KeyChoice(ekey="", raw_key=None)

    candidates = info.all_ekeys
    if not candidates:
        raise DecryptionError(_missing_key_message(info))

    length = min(probe_bytes, info.audio_length or probe_bytes)
    with open(info.path, "rb") as handle:
        head = handle.read(length) if length > 0 else b""

    parsed: list[tuple[str, bytes]] = []
    first_error: DecryptionError | None = None
    for candidate in candidates:
        try:
            parsed.append((candidate, parse_ekey(candidate)))
        except Qmc2Error as exc:
            first_error = first_error or DecryptionError(
                f"ekey 无法解析（{info.path.name}）：{exc}"
            )

    if not parsed:
        raise first_error or DecryptionError(f"{info.path.name} 的 ekey 无法使用")

    for candidate, raw_key in parsed:
        if _decrypts_to_audio(raw_key, head):
            return _KeyChoice(ekey=candidate, raw_key=raw_key)

    # Nothing verified; hand back the best guess so the caller raises the
    # usual "wrong key" error with the decrypted bytes for diagnosis.
    return _KeyChoice(ekey=parsed[0][0], raw_key=parsed[0][1])


def _missing_key_message(info: ContainerInfo) -> str:
    footer = info.footer
    detail = ""
    if footer.kind is FooterKind.MUSICEX:
        detail = (
            f"\n该文件使用 musicex 尾部（QQ 音乐 19.57+），ekey 不存放在文件里。"
            f"\nsong_id={footer.song_id or '?'}  mid={footer.mid or '?'}"
        )
    return (
        f"{info.path.name} 需要 ekey 才能解密。{detail}\n"
        "可任选一种方式提供：\n"
        "  1. 在界面「ekey」输入框粘贴该文件的 ekey；\n"
        "  2. 在音频文件旁放一个同名 .ekey 文件；\n"
        "  3. 在密钥库 JSON 中按 mid / song_id / 文件名登记。"
    )


@dataclass(frozen=True)
class StreamPeek:
    """Cheap facts about the audio inside a container, without decoding it."""

    sniffed: str | None = None
    duration: float | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bits_per_sample: int | None = None

    def describe(self) -> str:
        bits: list[str] = []
        if self.sniffed:
            bits.append(self.sniffed.upper())
        if self.sample_rate:
            bits.append(f"{self.sample_rate / 1000:g} kHz")
        if self.channels:
            bits.append({1: "单声道", 2: "立体声"}.get(self.channels, f"{self.channels} 声道"))
        if self.bits_per_sample:
            bits.append(f"{self.bits_per_sample} bit")
        return " · ".join(bits)


def _parse_streaminfo(head: bytes) -> StreamPeek:
    """Read FLAC's STREAMINFO block, which carries the sample count.

    FLAC's mandatory first metadata block packs everything we need into the
    bytes right after the signature, so the duration is available after
    decrypting only the first few dozen bytes -- no need to touch the whole
    file just to fill in a queue column.
    """
    if not head.startswith(b"fLaC") or len(head) < 26:
        return StreamPeek(sniffed=sniff_audio(head[:16]) or "flac")
    if (head[4] & 0x7F) != 0:  # first block must be STREAMINFO
        return StreamPeek(sniffed="flac")

    packed = int.from_bytes(head[18:26], "big")
    sample_rate = (packed >> 44) & 0xFFFFF
    channels = ((packed >> 41) & 0x7) + 1
    bits_per_sample = ((packed >> 36) & 0x1F) + 1
    total_samples = packed & 0xFFFFFFFFF

    duration = total_samples / sample_rate if sample_rate and total_samples else None
    return StreamPeek(
        sniffed="flac",
        duration=duration,
        sample_rate=sample_rate or None,
        channels=channels,
        bits_per_sample=bits_per_sample,
    )


def peek(
    path: Path | str,
    keystore: KeyStore | None = None,
    *,
    ekey: str | None = None,
    sample_bytes: int = 64,
) -> tuple[ContainerInfo | None, StreamPeek]:
    """Decrypt just enough of a container to describe its audio.

    Used by the UI to fill in the source/duration columns before a job runs.
    Returns ``(container, peek)``; the container is ``None`` for files that are
    not QQ Music containers at all.
    """
    path = Path(path)
    if not is_qmc_path(path):
        try:
            with open(path, "rb") as handle:
                return None, _parse_streaminfo(handle.read(sample_bytes))
        except OSError:
            return None, StreamPeek()

    info = inspect(path, keystore, ekey=ekey)
    if info.already_plain:
        with open(info.path, "rb") as handle:
            return info, _parse_streaminfo(handle.read(sample_bytes))

    try:
        choice = _choose_key(info, probe_bytes=sample_bytes)
    except DecryptionError:
        return info, StreamPeek()

    length = min(sample_bytes, info.audio_length or sample_bytes)
    if length <= 0:
        return info, StreamPeek()
    with open(info.path, "rb") as handle:
        sample = bytearray(handle.read(length))

    if choice.raw_key is None:
        qmc1.decrypt_in_place(sample, 0)
    else:
        Qmc2Crypto(choice.raw_key).decrypt(sample, 0)

    sniffed = sniff_audio(bytes(sample[:16]))
    if sniffed == "flac":
        return info, _parse_streaminfo(bytes(sample))
    return info, StreamPeek(sniffed=sniffed)


def _verify_decrypted_head(info: ContainerInfo, head: bytes) -> None:
    """Fail fast when the key was wrong instead of emitting garbage."""
    if not head:
        raise DecryptionError(f"{info.path.name} 解密后为空")
    if sniff_audio(head) is None:
        hint = "（ekey 很可能不正确）" if not info.format.is_qmc1 else ""
        raise DecryptionError(
            f"{info.path.name} 解密结果不是可识别的音频数据{hint}："
            f"{head[:8].hex(' ')}\n"
            "排查：用 --diagnose 查看尾部布局（.mflac 的密钥存放方式不止一种）"
        )


def decrypt_to(
    info: ContainerInfo,
    destination: Path,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    workers: int = 1,
    on_progress: Callable[[float], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Stream-decrypt ``info`` into ``destination``.

    Large payloads are split across ``workers`` processes; anything that goes
    wrong there falls back to the serial loop below.

    The destination is removed if the decryption does not complete, so a
    failure never leaves a half-written file behind.
    """
    if info.already_plain:
        raise DecryptionError(f"{info.path.name} 未加密，无需解密")

    total = info.audio_length
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        _decrypt_to(info, destination, total, chunk_size, workers, on_progress, on_warning, cancel)
    except BaseException:
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise
    return destination


def _decrypt_to(
    info: ContainerInfo,
    destination: Path,
    total: int,
    chunk_size: int,
    workers: int,
    on_progress: Callable[[float], None] | None,
    on_warning: Callable[[str], None] | None,
    cancel: threading.Event | None,
) -> None:
    choice = _choose_key(info)

    if workers > 1:
        # decrypt_parallel owns the "is this worth a process pool?" policy and
        # returns (False, None) when it declines; only real problems warn.
        # It also returns the exception the head probe raised, if any: a wrong
        # key has to reach the caller untouched, because falling through to the
        # serial loop below would decrypt the whole file just to fail again and
        # would leave the half-written destination behind.
        handled, error, probe_error = decrypt_parallel(
            source=info.path,
            destination=destination,
            total=total,
            key=choice.raw_key,
            is_qmc1=info.format.is_qmc1,
            workers=workers,
            chunk_bytes=chunk_size,
            on_progress=on_progress,
            should_stop=(cancel.is_set if cancel is not None else None),
            head_probe=lambda head: _verify_decrypted_head(info, head),
        )
        if handled:
            return
        if probe_error is not None:
            raise probe_error
        if error and on_warning is not None:
            on_warning(f"{info.path.name} 并行解密不可用（{error}），改用单进程")

    cipher: _Cipher = (
        _Qmc1Cipher() if choice.raw_key is None else Qmc2Crypto(choice.raw_key)
    )

    with open(info.path, "rb") as source, open(destination, "wb") as sink:
        position = 0
        first = True
        while position < total:
            if cancel is not None and cancel.is_set():
                raise DecryptionError("解密已取消")
            span = min(chunk_size, total - position)
            source.seek(position)
            buffer = bytearray(source.read(span))
            if not buffer:
                break
            cipher.decrypt(buffer, position)
            if first:
                _verify_decrypted_head(info, bytes(buffer[:16]))
                first = False
            sink.write(buffer)
            position += len(buffer)
            if on_progress is not None and total:
                on_progress(min(1.0, position / total))

    if position == 0:
        raise DecryptionError(f"{info.path.name} 解密后没有任何音频数据")


def prepare_input(
    path: Path | str,
    work_dir: Path,
    *,
    keystore: KeyStore | None = None,
    ekey: str | None = None,
    workers: int = 1,
    on_progress: Callable[[float], None] | None = None,
    on_warning: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> PreparedInput:
    """Return a file ffmpeg can read, decrypting first when necessary.

    Non-QMC inputs are passed straight through.  The caller owns the result and
    should call :meth:`PreparedInput.cleanup` (or use it as a context manager).
    """
    path = Path(path)
    if not is_qmc_path(path):
        return PreparedInput(path=path, original=path, container=None, temporary=False)

    info = inspect(path, keystore, ekey=ekey)
    if info.already_plain:
        return PreparedInput(path=path, original=path, container=info, temporary=False)

    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    handle, raw_path = tempfile.mkstemp(
        prefix="echoshift_", suffix=f".{info.format.audio_extension}", dir=str(work_dir)
    )
    os.close(handle)
    temp_path = Path(raw_path)
    try:
        decrypt_to(
            info,
            temp_path,
            chunk_size=chunk_size,
            workers=workers,
            on_progress=on_progress,
            on_warning=on_warning,
            cancel=cancel,
        )
    except BaseException:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise

    return PreparedInput(
        path=temp_path, original=path, container=info, temporary=True
    )
