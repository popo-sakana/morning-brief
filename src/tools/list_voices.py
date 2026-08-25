"""日本語の音声（声）の一覧を出す。config.yaml の google_voice を選ぶときに使います。

  GOOGLE_TTS_API_KEY=xxxx python src/tools/list_voices.py
"""

from __future__ import annotations

import os
import sys

import requests


def main() -> int:
    key = os.environ.get("GOOGLE_TTS_API_KEY")
    if not key:
        print("環境変数 GOOGLE_TTS_API_KEY を設定してください", file=sys.stderr)
        return 1

    resp = requests.get(
        "https://texttospeech.googleapis.com/v1/voices",
        params={"key": key, "languageCode": "ja-JP"},
        timeout=60,
    )
    if resp.status_code >= 400:
        print(f"エラー HTTP {resp.status_code}: {resp.text[:500]}", file=sys.stderr)
        return 1

    voices = resp.json().get("voices", [])
    groups: dict[str, list[tuple[str, str]]] = {}
    for v in voices:
        name = v["name"]
        tier = "Chirp3-HD" if "Chirp3-HD" in name else name.split("-")[2] if len(name.split("-")) > 2 else "その他"
        groups.setdefault(tier, []).append((name, v.get("ssmlGender", "")))

    for tier in sorted(groups, key=lambda t: (t != "Chirp3-HD", t)):
        print(f"\n■ {tier}（{len(groups[tier])}種類）")
        for name, gender in sorted(groups[tier]):
            label = {"FEMALE": "女性", "MALE": "男性"}.get(gender, gender or "不明")
            print(f"  {name:<40} {label}")

    print("\nこの中から2つ選んで config.yaml の google_voice に書いてください。")
    print("Chirp3-HD が最も自然です（月100万文字までは無料枠の想定）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
