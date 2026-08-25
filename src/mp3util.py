"""ffmpeg が無くても mp3 をつなげるようにするための道具。

GitHub のサーバーには ffmpeg が入っているので、本番では使いません。
これは、手元の Mac で ffmpeg を入れずに動作確認できるようにするためのものです。

mp3 は「フレーム」という小さな塊の連続でできています。塊をそのまま並べれば
音としてつながるので、変換ソフトが無くても結合できます。ただし各ファイルの先頭に
「この曲は何秒です」という札（ID3タグ・Xingヘッダ）が入っているため、
並べる前に札だけ外します。
"""

from __future__ import annotations

import base64
import shutil
import subprocess

# 24kHz・モノラル・64kbps の「無音1フレーム」。長さは24ミリ秒。
# 必要な数だけ並べて、発言と発言のあいだの「間」を作ります。
_SILENT_FRAME = base64.b64decode(
    "//OExEwAAANIAAAAAFVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
    "VVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVV"
)
FRAME_MS = 24.0

_BITRATE_V1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_BITRATE_V2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}
_SAMPLES = {3: 1152, 2: 576, 0: 576}


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def frame_info(data: bytes, i: int) -> tuple[int, float]:
    """i の位置にあるフレームの (バイト長, 秒数)。フレームでなければ (0, 0)。"""
    if i + 4 > len(data) or data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
        return 0, 0.0
    ver = (data[i + 1] >> 3) & 0x03
    layer = (data[i + 1] >> 1) & 0x03
    bi = (data[i + 2] >> 4) & 0x0F
    ri = (data[i + 2] >> 2) & 0x03
    pad = (data[i + 2] >> 1) & 0x01
    if layer != 1 or ver == 1 or bi in (0, 15) or ri == 3:
        return 0, 0.0
    rate = _RATES[ver][ri]
    kbps = (_BITRATE_V1 if ver == 3 else _BITRATE_V2)[bi] * 1000
    coef = 144 if ver == 3 else 72
    return coef * kbps // rate + pad, _SAMPLES[ver] / rate


def strip_tags(data: bytes) -> bytes:
    """ID3タグと、先頭のXing/Infoフレームを取り除く。"""
    if data[:3] == b"ID3" and len(data) > 10:
        size = 0
        for b in data[6:10]:
            size = (size << 7) | (b & 0x7F)
        data = data[10 + size :]

    if data[-128:][:3] == b"TAG":
        data = data[:-128]

    start = data.find(b"\xff")
    if start == -1:
        return data
    n, _ = frame_info(data, start)
    if n and (b"Xing" in data[start : start + n] or b"Info" in data[start : start + n]):
        return data[start + n :]
    return data[start:]


def silence(ms: int) -> bytes:
    """指定した長さ（ミリ秒）の無音。"""
    return _SILENT_FRAME * max(0, round(ms / FRAME_MS))


def join(chunks: list[bytes]) -> bytes:
    """札を外してから並べる。"""
    return b"".join(strip_tags(c) for c in chunks)


def duration(data: bytes) -> float:
    """フレームを数えて秒数を出す（ffprobe を使わない）。"""
    total, i, n = 0.0, 0, len(data)
    while i < n - 3:
        size, sec = frame_info(data, i)
        if size <= 0:
            i += 1
            continue
        total += sec
        i += size
    return total


def duration_via_ffprobe(path: str) -> float:
    import json

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return 0.0
    return float(json.loads(proc.stdout)["format"]["duration"])
