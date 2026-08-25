"""発言ごとの音声を、間（ま）を挟みながら1本のmp3に結合する。

ffmpeg があればそれを使い（音量や形式がきれいに揃う）、無ければ
mp3 のフレームをそのまま並べる方式に切り替えます。どちらでも同じ音になります。
手元の Mac に ffmpeg を入れなくても動作確認できるようにするための作りです。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import mp3util


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失敗:\n{' '.join(cmd)}\n{proc.stderr[-1500:]}")


def duration_seconds(path: Path) -> float:
    if mp3util.has_ffmpeg():
        return mp3util.duration_via_ffprobe(str(path))
    return mp3util.duration(path.read_bytes())


def _normalize(raw: bytes, suffix: str, bitrate: str, workdir: Path, idx: int) -> bytes:
    """どのプロバイダから来ても同じ形式に揃える（ffmpeg があるときだけ）。

    変換に失敗しても、その発言を落としてはいけません。番組に穴が空くより、
    形式が揃っていないほうがましなので、元の音声をそのまま使います。
    """
    src = workdir / f"{idx:04d}_raw{suffix}"
    dst = workdir / f"{idx:04d}.mp3"
    src.write_bytes(raw)
    try:
        _run([
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
            "-ac", "1", "-ar", "24000", "-c:a", "libmp3lame", "-b:a", bitrate, str(dst),
        ])
    except RuntimeError as exc:
        if suffix != ".mp3":
            raise
        print(f"[audio] {idx}番目の変換に失敗したため、元の音声をそのまま使います（{exc}）")
        return raw

    out = dst.read_bytes()
    # 変換後が極端に短ければ、読み取りに失敗した疑いがある。元を使う。
    if suffix == ".mp3" and mp3util.duration(out) < mp3util.duration(raw) * 0.5:
        print(f"[audio] {idx}番目の変換後が短すぎるため、元の音声をそのまま使います")
        return raw
    return out


def build_episode(
    turns: list[tuple[str, str, str]],
    engine: Any,
    cfg: dict[str, Any],
    out_path: Path,
) -> tuple[Path, list[dict[str, Any]]]:
    """turns = [(section_id, speaker, text), ...] を1本の音声にする。

    戻り値は (出力ファイル, 各コーナーの開始秒のリスト)。
    """
    tcfg = cfg.get("tts", {})
    turn_gap = int(tcfg.get("turn_gap_ms", 320))
    seg_gap = int(tcfg.get("segment_gap_ms", 900))
    bitrate = tcfg.get("bitrate", "64k")

    use_ffmpeg = mp3util.has_ffmpeg()
    if not use_ffmpeg:
        print("[audio] ffmpeg が見つからないため、mp3をそのままつなぐ方式で作ります")
        if engine.suffix != ".mp3":
            raise RuntimeError(
                "この音声合成は mp3 以外を返すため、結合に ffmpeg が必要です。"
                "brew install ffmpeg で入れてください。"
            )

    workdir = Path(tempfile.mkdtemp(prefix="brief-"))
    pause_turn = mp3util.silence(turn_gap)
    pause_seg = mp3util.silence(seg_gap)

    pieces: list[bytes] = []
    chapters: list[dict[str, Any]] = []
    prev_section: str | None = None
    total = len(turns)

    for idx, (section, speaker, text) in enumerate(turns, start=1):
        text = (text or "").strip()
        if not text:
            continue

        if prev_section is not None:
            pieces.append(pause_seg if section != prev_section else pause_turn)
        if section != prev_section:
            chapters.append({"section": section, "piece_index": len(pieces)})
        prev_section = section

        raw = engine.synthesize(text, speaker)
        if use_ffmpeg:
            raw = _normalize(raw, engine.suffix, bitrate, workdir, idx)
        pieces.append(mp3util.strip_tags(raw))

        if idx % 10 == 0 or idx == total:
            print(f"[audio] 音声合成 {idx}/{total}", flush=True)

    if not pieces:
        raise RuntimeError("音声にできる発言がありませんでした")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"".join(pieces))

    # コーナーごとの開始秒を、そこまでの断片の長さを足して求める
    lengths = [mp3util.duration(p) for p in pieces]
    marks = [
        {"section": ch["section"], "start_sec": round(sum(lengths[: ch["piece_index"]]), 1)}
        for ch in chapters
    ]

    return out_path, marks
