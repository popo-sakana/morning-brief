"""VOICEVOX（無料・自前で動かす日本語音声合成）。

新しいAPIアカウントを一切増やしたくない場合の選択肢です。音声合成の費用はゼロになります。
そのかわり、実行のたびに VOICEVOX エンジンを起動する必要があり、声質は合成音声寄りです。

利用規約上、生成した音声を公開する場合はキャラクターのクレジット表記が必要です。
本人だけが聞く限定配信であっても、規約は事前に確認してください。
  https://voicevox.hiroshiba.jp/term/
"""

from __future__ import annotations

import os
import time

import requests

from .base import TTSEngine


class VoicevoxTTS(TTSEngine):
    suffix = ".wav"

    @property
    def base_url(self) -> str:
        return os.environ.get("VOICEVOX_URL", "http://127.0.0.1:50021")

    def check(self) -> None:
        try:
            requests.get(f"{self.base_url}/version", timeout=10).raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"VOICEVOX エンジンに接続できません（{self.base_url}）。"
                "docker run -p 50021:50021 voicevox/voicevox_engine で起動してください。"
            ) from exc

    def synthesize(self, text: str, speaker: str) -> bytes:
        sid = self.hosts[speaker]["voicevox_speaker"]

        last = ""
        for attempt in range(3):
            try:
                q = requests.post(
                    f"{self.base_url}/audio_query",
                    params={"text": text, "speaker": sid},
                    timeout=120,
                )
                q.raise_for_status()
                query = q.json()
                query["speedScale"] = self.tts_cfg.get("speaking_rate", 1.0)

                s = requests.post(
                    f"{self.base_url}/synthesis",
                    params={"speaker": sid},
                    json=query,
                    timeout=300,
                )
                s.raise_for_status()
                return s.content
            except Exception as exc:  # noqa: BLE001
                last = f"{type(exc).__name__}: {exc}"
                time.sleep(2 * (attempt + 1))

        raise RuntimeError(f"VOICEVOX エラー: {last}")
