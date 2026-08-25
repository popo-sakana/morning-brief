"""音声合成の共通インターフェース。

ここを差し替えるだけで、Google / OpenAI / VOICEVOX を行き来できます。
新しいサービスを使いたくなったら、synthesize() を持つクラスを1つ足すだけです。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class TTSEngine(ABC):
    """テキスト1発言ぶんを音声データ（bytes）に変える。"""

    #: 出力される音声の拡張子（ffmpeg での結合時に使う）
    suffix: str = ".mp3"

    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self.tts_cfg = cfg.get("tts", {})
        self.hosts = cfg.get("hosts", {})

    @abstractmethod
    def synthesize(self, text: str, speaker: str) -> bytes:
        """speaker は config.yaml の hosts のキー（navigator / analyst）。"""

    def check(self) -> None:
        """必要なキーが揃っているかを事前に確認する。足りなければ例外。"""


def get_engine(cfg: dict[str, Any]) -> TTSEngine:
    provider = (cfg.get("tts", {}).get("provider") or "google").lower()
    if provider == "google":
        from .google_tts import GoogleTTS

        return GoogleTTS(cfg)
    if provider == "openai":
        from .openai_tts import OpenAITTS

        return OpenAITTS(cfg)
    if provider == "voicevox":
        from .voicevox_tts import VoicevoxTTS

        return VoicevoxTTS(cfg)
    raise ValueError(f"未知の音声合成プロバイダです: {provider}")
