"""Google Cloud Text-to-Speech（REST・APIキー方式）。

Chirp 3: HD の日本語音声を使います。
月100万文字までは無料枠に収まる想定です（30分×毎日で月およそ30万文字）。
無料枠と単価は変わりうるので、稼働前に公式ページで確認してください。
  https://cloud.google.com/text-to-speech/pricing

Chirp 3: HD には「1文が長すぎると受け付けない」制限があります（上限値は非公開）。
そのため、まず発言をそのまま投げ、弾かれたときだけ文を細かく切って投げ直します。
こうすると、必要のない場所で文が切れて不自然に聞こえるのを避けられます。
"""

from __future__ import annotations

import base64
import os
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from textsplit import FALLBACK_LIMITS, split_for_tts  # noqa: E402

from .base import TTSEngine  # noqa: E402

ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize"

# このメッセージが返ってきたら「文が長すぎる」と判断する
TOO_LONG = "too long"


class SentenceTooLong(Exception):
    pass


class GoogleTTS(TTSEngine):
    suffix = ".mp3"

    def check(self) -> None:
        if not os.environ.get("GOOGLE_TTS_API_KEY"):
            raise RuntimeError("環境変数 GOOGLE_TTS_API_KEY が設定されていません")

    def _request(self, text: str, voice: str) -> bytes:
        api_key = os.environ["GOOGLE_TTS_API_KEY"]
        payload = {
            "input": {"text": text},
            "voice": {"languageCode": "ja-JP", "name": voice},
            "audioConfig": {
                "audioEncoding": "MP3",
                "speakingRate": self.tts_cfg.get("speaking_rate", 1.0),
                "sampleRateHertz": 24000,
            },
        }

        last = ""
        for attempt in range(4):
            resp = requests.post(
                ENDPOINT, params={"key": api_key}, json=payload, timeout=120
            )
            if resp.status_code < 400:
                return base64.b64decode(resp.json()["audioContent"])

            last = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code == 400 and TOO_LONG in resp.text:
                raise SentenceTooLong(last)
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            break

        raise RuntimeError(f"Google TTS エラー: {last}")

    def synthesize(self, text: str, speaker: str) -> bytes:
        voice = self.hosts[speaker]["google_voice"]

        # まずはそのまま投げる（不要な分割をしないため）
        try:
            return self._request(text, voice)
        except SentenceTooLong:
            pass

        # 弾かれたら、文を段階的に短く切って投げ直す
        for limit in FALLBACK_LIMITS:
            pieces = split_for_tts(text, limit)
            try:
                return b"".join(self._request(p, voice) for p in pieces)
            except SentenceTooLong:
                continue

        raise RuntimeError(
            "文を短く切っても音声合成が通りませんでした。"
            f"該当箇所: {text[:60]}…"
        )
