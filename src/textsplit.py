"""長い発言を、音声合成に投げられる大きさに切り分ける。

Chirp 3: HD には「1文が長すぎると受け付けない」制限があります。
上限値は公開されていないため、余裕をもった長さで区切ります。
あわせて、1回のリクエストが大きくなりすぎないよう、文をまとめ直します。

ここで切った断片は、あとで音声としてつなぎ直すので、
聞こえ方は元の1つの発言と変わりません。
"""

from __future__ import annotations

import re

# 1文の上限（文字数）。これを超える文は、読点のところでさらに切ります。
# 実測では75文字は通り、122文字は弾かれました。まず90で試し、
# それでも弾かれたら 55、35 と細かくしていきます。
FALLBACK_LIMITS = [90, 55, 35]
MAX_SENTENCE_CHARS = FALLBACK_LIMITS[0]

# 1回のリクエストに詰め込む上限（バイト数）。APIの上限は5000バイトですが、
# 大きな塊は不安定なので、かなり手前で区切ります。
MAX_REQUEST_BYTES = 1200

_SENT_END = re.compile(r"(?<=[。！？])")
_COMMA = "、"


def _split_long_sentence(sentence: str, limit: int = MAX_SENTENCE_CHARS) -> list[str]:
    """上限を超える1文を、読点の位置で分ける。

    分けた区切りは句点に置き換えます。読点のまま切ると、
    語尾が言い切りにならず、不自然に途切れて聞こえるためです。
    """
    if len(sentence) <= limit:
        return [sentence]

    parts = sentence.split(_COMMA)
    out: list[str] = []
    buf = ""

    for i, part in enumerate(parts):
        is_last = i == len(parts) - 1
        piece = part if is_last else part + _COMMA
        if buf and len(buf) + len(piece) > limit:
            out.append(buf)
            buf = piece
        else:
            buf += piece
    if buf:
        out.append(buf)

    # 末尾の読点を句点に直す（最後の断片は元の句点を保つ）
    fixed = []
    for i, s in enumerate(out):
        if i < len(out) - 1 and s.endswith(_COMMA):
            s = s[:-1] + "。"
        fixed.append(s)

    # 読点が無く1文が長すぎる場合は、やむをえず文字数で切る
    result: list[str] = []
    for s in fixed:
        if len(s) <= limit * 2:
            result.append(s)
            continue
        for j in range(0, len(s), limit):
            result.append(s[j : j + limit])
    return result


def split_for_tts(text: str, limit: int = MAX_SENTENCE_CHARS) -> list[str]:
    """1発言を、音声合成に投げる単位のリストにする。"""
    text = (text or "").strip()
    if not text:
        return []

    sentences: list[str] = []
    for raw in _SENT_END.split(text):
        raw = raw.strip()
        if raw:
            sentences.extend(_split_long_sentence(raw, limit))

    chunks: list[str] = []
    buf = ""
    for s in sentences:
        if buf and len((buf + s).encode("utf-8")) > MAX_REQUEST_BYTES:
            chunks.append(buf)
            buf = s
        else:
            buf += s
    if buf:
        chunks.append(buf)

    return chunks
