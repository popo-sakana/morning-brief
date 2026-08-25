"""APIキーなしで、音声の結合とRSS・ページ生成が正しく動くかを確かめるテスト。

読み上げの部分だけ「無音＋合図音」に差し替えて、配管が通っているかを見ます。
  python tests/test_pipeline.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

import publish as publish_mod  # noqa: E402
from audio import build_episode, duration_seconds  # noqa: E402
from script_writer import iter_turns  # noqa: E402
from tts.base import TTSEngine  # noqa: E402


class FakeTTS(TTSEngine):
    """文字数に比例した長さの音を返すだけの、にせ音声合成。"""

    suffix = ".mp3"

    def synthesize(self, text: str, speaker: str) -> bytes:
        seconds = max(0.4, len(text) / 320 * 60)
        freq = 330 if speaker == "navigator" else 220
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as fh:
            path = Path(fh.name)
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
             "-i", f"sine=frequency={freq}:duration={seconds:.2f}",
             "-ac", "1", "-ar", "24000", "-b:a", "64k", str(path)],
            check=True, capture_output=True,
        )
        data = path.read_bytes()
        path.unlink()
        return data


SAMPLE_SCRIPT = {
    "opening": [{"speaker": "navigator", "text": "おはようございます。" + "あ" * 140}],
    "segments": [
        {"id": "jp_fisheries", "title": "日本の水産",
         "turns": [{"speaker": "analyst", "text": "い" * 200},
                   {"speaker": "navigator", "text": "う" * 120}]},
        {"id": "genai", "title": "生成AIの重大ニュース",
         "turns": [{"speaker": "analyst", "text": "え" * 180}]},
    ],
    "discussion": {"title": "きょうの論点",
                   "turns": [{"speaker": "navigator", "text": "お" * 150},
                             {"speaker": "analyst", "text": "か" * 150}]},
    "closing": [{"speaker": "navigator", "text": "き" * 60}],
    "highlights": ["要点その1", "要点その2", "要点その3"],
    "sources": [{"segment": "日本の水産", "url": "https://example.com/a", "title": "記事A"},
                {"segment": "生成AIの重大ニュース", "url": "https://example.com/b", "title": "記事B"}],
}


def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    turns = iter_turns(SAMPLE_SCRIPT)
    assert len(turns) == 7, f"発言数が想定と違います: {len(turns)}"

    workdir = Path(tempfile.mkdtemp(prefix="brieftest-"))
    out = workdir / "260824_morningbrief_v1_AI.mp3"
    out, chapters = build_episode(turns, FakeTTS(cfg), cfg, out)

    dur = duration_seconds(out)
    assert out.exists() and out.stat().st_size > 0, "音声ファイルができていません"
    assert dur > 5, f"音声が短すぎます: {dur}秒"

    sections = [c["section"] for c in chapters]
    assert sections == ["opening", "jp_fisheries", "genai", "discussion", "closing"], sections
    assert all(chapters[i]["start_sec"] <= chapters[i + 1]["start_sec"]
               for i in range(len(chapters) - 1)), "目次の時刻が順番になっていません"
    assert chapters[-1]["start_sec"] < dur, "目次の時刻が音声の長さを超えています"

    # 配信ファイルの生成を、作業用フォルダの中で試す
    publish_mod.SITE = workdir / "site"
    publish_mod.EPISODES_JSON = publish_mod.SITE / "episodes.json"
    episode = {
        "id": out.stem, "title": "8月24日のブリーフ",
        "published_at": "2026-08-24T04:00:00+09:00",
        "audio_url": "https://example.com/audio.mp3",
        "duration_sec": round(dur), "bytes": out.stat().st_size,
        "summary": "・要点", "chapters": chapters,
    }
    publish_mod.publish(cfg, SAMPLE_SCRIPT, episode, "https://example.com")

    feed = (publish_mod.SITE / "feed.xml").read_text(encoding="utf-8")
    page = (publish_mod.SITE / "episodes" / f"{out.stem}.html").read_text(encoding="utf-8")
    index = (publish_mod.SITE / "index.html").read_text(encoding="utf-8")

    import xml.etree.ElementTree as ET
    ET.fromstring(feed)  # RSSとして壊れていないか
    assert "<enclosure" in feed and "audio/mpeg" in feed, "音声の紐づけがありません"
    assert "https://example.com/a" in page, "出典がページに出ていません"
    assert "日本の水産" in page and "きょうの論点" in page, "コーナーが欠けています"
    assert "feed.xml" in index, "一覧ページに購読URLがありません"

    # 同じ回をもう一度公開しても重複しないこと
    publish_mod.publish(cfg, SAMPLE_SCRIPT, episode, "https://example.com")
    eps = json.loads(publish_mod.EPISODES_JSON.read_text(encoding="utf-8"))
    assert len(eps) == 1, f"同じ回が重複しています: {len(eps)}"

    print(f"OK: 音声 {dur:.1f}秒 / 目次 {len(chapters)}箇所 / RSS・ページの生成も正常")
    print(f"    確認用フォルダ: {workdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
