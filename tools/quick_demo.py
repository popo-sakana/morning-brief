"""Google の音声合成だけを、その場で試すための1ファイル。

追加インストールは何も要りません（Python 3 の標準機能だけを使います）。
ffmpeg も不要です。音声はそのままつなぎ合わせます。

使い方:
  # 1) キーが使えるか確認して、選べる声の一覧を出す
  GOOGLE_TTS_API_KEY=xxxx python3 tools/quick_demo.py --list

  # 2) 短い文を1つだけ音声にして聞く
  GOOGLE_TTS_API_KEY=xxxx python3 tools/quick_demo.py --text "おはようございます。テストです。"

  # 3) 今日の台本の頭6発言だけを、2人の声で音声にする
  GOOGLE_TTS_API_KEY=xxxx python3 tools/quick_demo.py --script out/260824_morningbrief_script.json --limit 6

  # 4) 25分の番組をまるごと作る
  GOOGLE_TTS_API_KEY=xxxx python3 tools/quick_demo.py --script out/260824_morningbrief_script.json
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://texttospeech.googleapis.com/v1"

# 使いたい声の優先順。上にあるものから順に探します。
PREFERRED = ["Chirp3-HD", "Neural2", "Wavenet", "Standard"]


class SentenceTooLong(Exception):
    """Chirp 3: HD が「1文が長すぎる」と返してきたときの合図。"""


# 1文の上限（文字数）。実測では75文字は通り、122文字は弾かれました。
# まず切らずに投げ、弾かれたらこの順に細かくしていきます。
FALLBACK_LIMITS = [90, 55, 35]
SENT_END = re.compile(r"(?<=[。！？])")


def split_sentences(text: str, limit: int) -> list[str]:
    """長すぎる文を、読点のところで切って句点に置き換える。"""
    out: list[str] = []
    for raw in SENT_END.split(text):
        raw = raw.strip()
        if not raw:
            continue
        if len(raw) <= limit:
            out.append(raw)
            continue
        buf = ""
        parts = raw.split("、")
        for i, part in enumerate(parts):
            piece = part if i == len(parts) - 1 else part + "、"
            if buf and len(buf) + len(piece) > limit:
                out.append(buf[:-1] + "。" if buf.endswith("、") else buf)
                buf = piece
            else:
                buf += piece
        if buf:
            out.append(buf)
    return out or [text]



# --- mp3をきれいにつなぐための下ごしらえ -------------------------------------
# 音声を1つずつ取り寄せてそのまま繋ぐと、各ファイルの先頭に入っている
# 「この曲は何秒です」という札（ID3タグ / Xingヘッダ）が途中に何枚も挟まります。
# 再生ソフトによっては最初の札を信じて、途中で止まったように見えることがあります。
# そこで、繋ぐ前に札だけ外します。音そのものは一切変えません。

_BITRATE_V1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320]
_BITRATE_V2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]
_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _frame_length(data: bytes, i: int) -> int:
    """i の位置にあるMPEGフレームの長さ（バイト）。フレームでなければ0。"""
    if i + 4 > len(data) or data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
        return 0
    ver = (data[i + 1] >> 3) & 0x03          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    layer = (data[i + 1] >> 1) & 0x03        # 1=Layer3
    bi = (data[i + 2] >> 4) & 0x0F
    ri = (data[i + 2] >> 2) & 0x03
    pad = (data[i + 2] >> 1) & 0x01
    if layer != 1 or ver == 1 or bi in (0, 15) or ri == 3:
        return 0
    rate = _RATES[ver][ri]
    kbps = (_BITRATE_V1 if ver == 3 else _BITRATE_V2)[bi] * 1000
    coef = 144 if ver == 3 else 72
    return coef * kbps // rate + pad


def strip_mp3_tags(data: bytes) -> bytes:
    """ID3タグと、先頭のXing/Infoフレームを取り除く。"""
    # 先頭のID3v2タグ
    if data[:3] == b"ID3" and len(data) > 10:
        size = 0
        for b in data[6:10]:
            size = (size << 7) | (b & 0x7F)
        data = data[10 + size :]

    # 末尾のID3v1タグ
    if data[-128:][:3] == b"TAG":
        data = data[:-128]

    # 最初のフレームがXing/Infoの札なら丸ごと落とす
    start = data.find(b"\xff")
    if start != -1:
        n = _frame_length(data, start)
        if n and b"Xing" in data[start : start + n] or (n and b"Info" in data[start : start + n]):
            data = data[start + n :]
        elif start:
            data = data[start:]

    return data


def api(path: str, key: str, payload: dict | None = None, params: dict | None = None) -> dict:
    query = {"key": key, **(params or {})}
    url = f"{BASE}/{path}?{urllib.parse.urlencode(query)}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"},
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        if exc.code == 400 and "too long" in body:
            raise SentenceTooLong(body) from None
        raise SystemExit(explain_error(exc.code, body)) from None


def explain_error(code: int, body: str) -> str:
    try:
        msg = json.loads(body)["error"]["message"]
    except Exception:  # noqa: BLE001
        msg = body[:400]

    hints = {
        403: (
            "・プロジェクトで「Cloud Text-to-Speech API」が有効になっていない\n"
            "・課金（Billing）が有効になっていない\n"
            "・APIキーの制限が Text-to-Speech 以外に絞られている\n"
            "のいずれかが多いです。メッセージ本文に activate や enable と出ていれば、\n"
            "そこに書かれたURLを開いて有効化すれば通ります。"
        ),
        400: ("1文が長すぎるか、声の名前が間違っている可能性があります。\n"
              "メッセージに too long と出ていれば前者です（このツールは自動で切り直します）。"),
        401: "APIキーが正しくありません。値をもう一度確認してください。",
        429: "短時間に呼びすぎです。少し待ってからもう一度実行してください。",
    }
    hint = hints.get(code, "")
    return f"\nエラー HTTP {code}\n{msg}\n\n{hint}\n"


def fetch_voices(key: str) -> list[dict]:
    return api("voices", key, params={"languageCode": "ja-JP"}).get("voices", [])


def tier_of(name: str) -> str:
    for t in PREFERRED:
        if t.lower() in name.lower():
            return t
    return "その他"


def pick_two(voices: list[dict]) -> tuple[str, str]:
    """一番良い階層から、なるべく声質の違う2つを選ぶ。"""
    for tier in PREFERRED:
        pool = [v for v in voices if tier_of(v["name"]) == tier]
        if len(pool) < 2:
            continue
        female = [v["name"] for v in pool if v.get("ssmlGender") == "FEMALE"]
        male = [v["name"] for v in pool if v.get("ssmlGender") == "MALE"]
        if female and male:
            return sorted(female)[0], sorted(male)[0]
        names = sorted(v["name"] for v in pool)
        return names[0], names[1]
    raise SystemExit("日本語の声が見つかりませんでした。--list の出力を確認してください。")


def _speak_once(key: str, text: str, voice: str, rate: float) -> bytes:
    payload = {
        "input": {"text": text},
        "voice": {"languageCode": "ja-JP", "name": voice},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": rate,
                        "sampleRateHertz": 24000},
    }
    return base64.b64decode(api("text:synthesize", key, payload)["audioContent"])


def speak(key: str, text: str, voice: str, rate: float) -> tuple[bytes, int]:
    """音声にする。長すぎて弾かれたら、文を切り直して投げ直す。

    戻り値は (音声, 切り直した回数)。0 なら手を加えずに通っています。
    """
    try:
        return _speak_once(key, text, voice, rate), 0
    except SentenceTooLong:
        pass

    for n, limit in enumerate(FALLBACK_LIMITS, start=1):
        pieces = split_sentences(text, limit)
        try:
            return b"".join(_speak_once(key, p, voice, rate) for p in pieces), n
        except SentenceTooLong:
            continue

    raise SystemExit(
        "\n文を短く切っても音声合成が通りませんでした。\n"
        f"該当箇所: {text[:60]}…\n"
    )


def turns_of(script: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for t in script.get("opening", []):
        out.append((t["speaker"], t["text"]))
    for seg in script.get("segments", []):
        for t in seg.get("turns", []):
            out.append((t["speaker"], t["text"]))
    for t in (script.get("discussion") or {}).get("turns", []):
        out.append((t["speaker"], t["text"]))
    for t in script.get("closing", []):
        out.append((t["speaker"], t["text"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="使える声の一覧を出す")
    ap.add_argument("--text", help="この文を1つだけ音声にする")
    ap.add_argument("--script", help="台本JSONを音声にする")
    ap.add_argument("--limit", type=int, help="台本の先頭から何発言だけ作るか")
    ap.add_argument("--rate", type=float, default=1.05, help="話す速さ（1.0が標準）")
    ap.add_argument("--out", default="demo.mp3", help="書き出すファイル名")
    ap.add_argument("--voices", help="声を指定する場合。カンマ区切りで2つ")
    args = ap.parse_args()

    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not key:
        print("環境変数 GOOGLE_TTS_API_KEY を設定してください。", file=sys.stderr)
        print('例: GOOGLE_TTS_API_KEY=あなたのキー python3 tools/quick_demo.py --list',
              file=sys.stderr)
        return 1

    voices = fetch_voices(key)
    print(f"キーは有効です。日本語の声が {len(voices)} 種類使えます。\n")

    if args.list:
        groups: dict[str, list[tuple[str, str]]] = {}
        for v in voices:
            groups.setdefault(tier_of(v["name"]), []).append(
                (v["name"], {"FEMALE": "女性", "MALE": "男性"}.get(v.get("ssmlGender", ""), "―"))
            )
        for tier in PREFERRED + ["その他"]:
            if tier not in groups:
                continue
            print(f"■ {tier}（{len(groups[tier])}種類）")
            for name, g in sorted(groups[tier]):
                print(f"   {name:<38} {g}")
            print()
        a, b = pick_two(voices)
        print(f"自動で選ぶならこの2つです:\n   進行役 {a}\n   解説役 {b}")
        return 0

    if args.voices:
        va, vb = [s.strip() for s in args.voices.split(",")][:2]
    else:
        va, vb = pick_two(voices)
    print(f"使う声:  進行役 {va}  /  解説役 {vb}\n")

    if args.text:
        items = [("navigator", args.text)]
    elif args.script:
        script = json.loads(Path(args.script).read_text(encoding="utf-8"))
        items = turns_of(script)
        if args.limit:
            items = items[: args.limit]
    else:
        items = [("navigator", "おはようございます。8月24日、月曜日の朝のブリーフです。"),
                 ("analyst", "今日は、養殖の餌になるペルーのカタクチイワシ漁が、"
                             "漁獲枠の4分の1で終わったという話から入ります。")]

    chars = sum(len(t) for _, t in items)
    print(f"{len(items)}発言 / {chars}文字 を音声にします。")
    print(f"（月100万文字の無料枠に対して {chars / 10_000:.2f}％ を使います）\n")

    chunks: list[bytes] = []
    resplit = 0
    for i, (speaker, text) in enumerate(items, start=1):
        voice = va if speaker == "navigator" else vb
        audio, n = speak(key, text, voice, args.rate)
        chunks.append(strip_mp3_tags(audio))
        resplit += 1 if n else 0
        note = "  ← 長い文を切り直しました" if n else ""
        print(f"  {i}/{len(items)}  {speaker:<10} {len(text):>4}文字{note}", flush=True)

    out = Path(args.out)
    out.write_bytes(b"".join(chunks))
    if resplit:
        print(f"\n{resplit}発言で長い文を自動で切り直しました（音声は通しでつながっています）")
    mb = out.stat().st_size / 1_000_000
    print(f"\n書き出しました: {out.resolve()}  （{mb:.1f}MB）")
    print(f"内容: {len(items)}発言 / {chars}文字 / 想定の長さ 約{chars / 320:.1f}分")
    print(f"再生するには:  afplay {out}      ← Mac の場合")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
