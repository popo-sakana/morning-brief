"""毎朝の流れ全体を、APIを呼ばずに通しで確かめる。

Perplexity / Claude / 音声合成をすべて偽物に差し替え、
調査 → 仮説の照合 → 台本 → 音声 → 配信 がつながっているかを見ます。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from deps import ensure  # noqa: E402

ensure()


def main(hide_ffmpeg: bool = False) -> int:
    work = Path(tempfile.mkdtemp(prefix="dailyrun-"))
    repo = work / "morning-brief"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "out", "site", "data", ".git"))
    (repo / "out").mkdir(exist_ok=True)

    # 注文を1件置いておく
    (repo / "requests").mkdir(exist_ok=True)
    (repo / "requests" / "next.md").write_text(
        "# 次回の番組で扱ってほしいこと\n\n<!-- ここから下に書いてください -->\n"
        "- ぶりの輸出単価を数量と金額の両方で確かめてほしい\n",
        encoding="utf-8")

    stub = repo / "sitecustomize.py"
    stub.write_text('''
import base64, json, subprocess, sys, tempfile
from pathlib import Path
import requests

CALLS = {"perplexity": 0, "claude": 0, "tts": 0, "queries": []}
Path("/tmp/dailyrun_calls.json").write_text(json.dumps(CALLS))

def _save():
    Path("/tmp/dailyrun_calls.json").write_text(json.dumps(CALLS))

class R:
    def __init__(self, code, payload=None, content=b""):
        self.status_code, self._p, self.content = code, payload, content
        self.text = json.dumps(payload, ensure_ascii=False) if payload else ""
    def json(self): return self._p

def fake_post(url, json=None, headers=None, params=None, timeout=None, **kw):
    import json as J
    if "perplexity" in url:
        CALLS["perplexity"] += 1
        CALLS["queries"].append(json["input"]); _save()
        return R(200, {"output": [
            {"type": "message", "content": [{"type": "output_text",
              "text": "2026年8月時点で、ペルーのアンチョビ1期漁は漁獲枠191万トンに対し46万トンで終漁した。"
                      "日本のぶり輸出は上半期434億円、前年同期比プラス69%だった。"}]},
            {"type": "search_results", "results": [
                {"url": "https://example.go.jp/a", "title": "水産庁 発表"},
                {"url": "https://example.com/b", "title": "業界紙 記事"}]}],
            "usage": {"cost": {"total_cost": 0.01}}})

    if "anthropic" in url:
        CALLS["claude"] += 1; _save()
        prompt = json["messages"][0]["content"]
        def turns(n, size=180):
            return {"turns": [{"speaker": "analyst" if i % 2 else "navigator",
                               "text": "あ" * size} for i in range(n)]}

        if "事実の主張を1件ずつカードに" in prompt:
            body = J.dumps([
                {"claim": "ペルーのアンチョビ1期漁は枠191万トンに対し46万トンで終漁した",
                 "source_url": "https://example.go.jp/a", "tier": 1, "date": "2026-08-19"},
                {"claim": "日本のぶり輸出は上半期434億円、前年同期比プラス69%だった",
                 "source_url": "https://example.go.jp/a", "tier": 1, "date": "2026-08-04"}])
        elif "事実カード" in prompt and "崩壊条件" in prompt and "判定のしかた" in prompt:
            body = J.dumps({"verdicts": [
                {"hypothesis_id": "H-001", "card": 1, "relation": "support",
                 "directness": 0.8, "rationale": "飼料原料の供給制約を裏づける"}],
                "falsifier_hits": []})
        elif "ニュース1件ずつに切り分けて" in prompt:
            CALLS["curate_prompt"] = prompt
            out = []
            for tid in ["jp_fisheries", "world_fisheries", "genai", "education"]:
                for k in range(6):
                    out.append({"theme_id": tid, "headline": f"{tid}の話題{k}",
                                "detail": "内容" * 40, "numbers": ["191万トン"],
                                "source_url": f"https://ex.com/{tid}/{k}",
                                "source_title": "出典", "published": "2026-08-26",
                                "importance": 5 - (k % 3)})
            body = J.dumps(out)
        elif "オープニングとクロージング" in prompt:
            body = J.dumps({"opening": [{"speaker": "navigator", "text": "お" * 200}],
                            "closing": [{"speaker": "navigator", "text": "し" * 80}],
                            "highlights": ["要点1", "要点2", "要点3"]})
        else:
            # コーナーごとの執筆。指示された文字数をそのまま満たす形で返す
            import re as RE
            m = RE.search(r"合計(\d+)文字", prompt)
            target = int(m.group(1)) if m else 3200
            n = max(2, target // 180)
            CALLS.setdefault("segment_targets", []).append(target)
            body = J.dumps(turns(n, 180))
        return R(200, {"content": [{"type": "text", "text": body}],
                       "usage": {"input_tokens": 1000, "output_tokens": 500}})

    if "texttospeech" in url:
        CALLS["tts"] += 1; _save()
        text = json["input"]["text"]
        sec = max(0.4, len(text) / 640 * 60)
        import mp3util
        d = mp3util.silence(int(sec * 1000))
        return R(200, {"audioContent": base64.b64encode(d).decode()})

    raise AssertionError("想定外のURL: " + url)

requests.post = fake_post
''', encoding="utf-8")

    empty_bin = work / "bin"
    empty_bin.mkdir(exist_ok=True)
    env = {
        "PATH": (str(empty_bin) if hide_ffmpeg else "/usr/bin:/bin:/usr/local/bin"),
        "PERPLEXITY_API_KEY": "x", "ANTHROPIC_API_KEY": "y", "GOOGLE_TTS_API_KEY": "z",
        "BASE_URL": "https://example.github.io/mb",
        "PYTHONPATH": str(repo),
    }
    sys.path.insert(0, str(repo / "src"))
    proc = subprocess.run([sys.executable, "src/run.py"], cwd=repo, env=env,
                          capture_output=True, text=True)
    print(proc.stdout[-2500:])
    if proc.returncode != 0:
        print("--- stderr ---\n" + proc.stderr[-3000:])
        return 1

    calls = json.loads(Path("/tmp/dailyrun_calls.json").read_text())

    # 1) 調査は 4テーマ ＋ 注文 ＋ 反証さがし の6本
    assert calls["perplexity"] == 6, f"調査の本数が想定と違う: {calls['perplexity']}"

    # 2) 通常の調査の問いかけに、仮説の主張が混ざっていないこと
    import yaml
    led = yaml.safe_load((repo / "hypotheses" / "ledger.yaml").read_text(encoding="utf-8"))
    normal = [q for q in calls["queries"] if "反対の材料" not in q]
    for h in led["hypotheses"]:
        head = h["statement"].strip().replace("\n", "")[:18]
        for q in normal:
            assert head not in q, f"調査に仮説文が漏れている: {head}"

    # 3) 反証さがしの問いかけが1本あり、崩壊条件だけを狙っていること
    red = [q for q in calls["queries"] if "反対の材料" in q]
    assert len(red) == 1, f"反証さがしが{len(red)}本"
    assert "返さないでください" in red[0]

    # 4) 台帳が更新され、確からしさが動いていること
    assert led["hypotheses"][0]["confidence"] != 0.55, "H-001 が動いていない"
    assert led["hypotheses"][0]["evidence"], "証拠が記録されていない"
    assert led["hypotheses"][0]["ops"]["last_redteam"], "反証さがしの記録がない"

    # 5) 台本に検証コーナーと各コーナーが入っていること
    script = json.loads(next((repo / "out").glob("*_script.json")).read_text(encoding="utf-8"))
    assert script.get("verification"), "検証コーナーが台本にない"
    assert script.get("hypothesis_checks"), "仮説の変化が台本に渡っていない"
    assert script.get("requests"), "注文が台本に渡っていない"
    assert len(script["segments"]) >= 4, f"コーナー数が足りない: {len(script['segments'])}"

    # 5b) 尺が目標どおりか（コーナー分割の効果）
    import yaml as Y
    cfgy = Y.safe_load((repo / "config.yaml").read_text(encoding="utf-8"))
    cpm = cfgy["program"]["chars_per_minute"]
    import script_writer as SW
    turns_all = SW.iter_turns(script)
    total = sum(len(t) for _, _, t in turns_all)
    assert total > 15000, f"台本が短すぎる: {total}文字"

    # 5c) 在庫が作られ、取り上げ実績が記録されたこと
    inv = json.loads((repo / "data" / "stock.json").read_text(encoding="utf-8"))
    aired = [i for i in inv["items"] if i["aired"]]
    assert len(inv["items"]) == 24, f"在庫の件数が想定と違う: {len(inv['items'])}"
    assert aired, "取り上げ実績が記録されていない"
    assert all(len(i["aired"]) == 1 for i in aired), "同じ項目が複数回記録されている"

    # 5d) 切り分けの指示に、既出の見出しを渡す仕組みが入っていること
    assert "既に拾ってある出来事" in calls.get("curate_prompt", ""), "既出の照合が渡っていない"

    # 6) 注文は使い終わったら空に戻り、控えが残ること
    inbox = (repo / "requests" / "next.md").read_text(encoding="utf-8")
    assert "ぶりの輸出単価" not in inbox, "注文が空に戻っていない"
    assert list((repo / "requests" / "archive").glob("*.md")), "注文の控えがない"

    # 7) 調査の生データが残ること
    raw = json.loads(next((repo / "data").glob("*_research.json")).read_text(encoding="utf-8"))
    assert len(raw["segments"]) == 6, "生データの本数が合わない"

    # 8) 音声と配信ファイルができていること
    mp3 = next((repo / "out").glob("*_AI.mp3"))
    assert mp3.stat().st_size > 0
    page = next((repo / "site" / "episodes").glob("*.html")).read_text(encoding="utf-8")
    assert "きょうの検証" in page and "確からしさ" in page, "検証がページに出ていない"
    assert "この回への注文" in page, "注文がページに出ていない"
    import xml.etree.ElementTree as ET
    ET.fromstring((repo / "site" / "feed.xml").read_text(encoding="utf-8"))

    import mp3util
    mins = mp3util.duration(mp3.read_bytes()) / 60

    mode = "ffmpeg なし" if hide_ffmpeg else "ffmpeg あり"
    print(f"\n通し実行 OK（{mode}）")
    print(f"  調査 {calls['perplexity']}本（4テーマ＋注文＋反証さがし）")
    print(f"  Claude {calls['claude']}回（カード抽出・仮説照合・台本）")
    print(f"  音声合成 {calls['tts']}回 / 完成 {mins:.1f}分")
    print(f"  台本 {total}文字 / 在庫 {len(inv['items'])}件（取上済 {len(aired)}件）")
    print(f"  作業フォルダ: {repo}")
    return 0


if __name__ == "__main__":
    # GitHub のサーバーには ffmpeg があり、手元の Mac には無いことが多い。
    # どちらでも通ることを確認する。
    rc = main(hide_ffmpeg=False)
    if rc == 0:
        print()
        rc = main(hide_ffmpeg=True)
    raise SystemExit(rc)
