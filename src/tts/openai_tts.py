"""OpenAI の音声合成（差し替え用の選択肢）。

Google を使いたくない場合はこちら。config.yaml の tts.provider を "openai" にします。
"""

from __future__ import annotations

import os
import time

import requests

from .base import TTSEngine

ENDPOINT = "https://api.openai.com/v1/audio/speech"


class OpenAITTS(TTSEngine):
    suffix = ".mp3"

    def check(self) -> None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("環境変数 OPENAI_API_KEY が設定されていません")

    def synthesize(self, text: str, speaker: str) -> bytes:
        payload = {
            "model": self.tts_cfg.get("openai_model", "gpt-4o-mini-tts"),
            "voice": self.hosts[speaker]["openai_voice"],
            "input": text,
            "response_format": "mp3",
            "speed": self.tts_cfg.get("speaking_rate", 1.0),
        }
        headers = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}

        last = ""
        for attempt in range(4):
            resp = requests.post(ENDPOINT, json=payload, headers=headers, timeout=180)
            if resp.status_code < 400:
                return resp.content
            last = f"HTTP {resp.status_code}: {resp.text[:300]}"
            if resp.status_code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            break
        raise RuntimeError(f"OpenAI TTS エラー: {last}")
