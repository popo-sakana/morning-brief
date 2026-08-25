"""同じニュースが2日続けて取り上げられないことを、通しで確かめる。

調査は「直近1週間」で探すので、同じ出来事が翌日も検索結果に出てきます。
その状態を再現し、2日目の番組が1日目と同じ項目を扱わないことを見ます。
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

STUB = '''
import base64, json
from pathlib import Path
import requests

class R:
    def __init__(self, p): self.status_code, self._p, self.text = 200, p, ""
    def json(self): return self._p

# 毎日まったく同じ検索結果が返ってくる状況を作る
ITEMS = []
for tid in ["jp_fisheries", "world_fisheries", "genai", "education"]:
    for k in range(7):
        ITEMS.append({"theme_id": tid, "headline": f"{tid}の話題{k}",
                      "detail": "内容" * 40, "numbers": [],
                      "source_url": f"https://ex.com/{tid}/{k}",
                      "source_title": "出典", "published": "2026-08-26",
                      "importance": 5 - (k % 3)})

def fake_post(url, json=None, headers=None, params=None, timeout=None, **kw):
    import json as J
    if "perplexity" in url:
        return R({"output": [
            {"type": "message", "content": [{"type": "output_text", "text": "調査本文"}]},
            {"type": "search_results", "results": [{"url": "https://ex.com/s", "title": "出典"}]}],
            "usage": {"cost": {"total_cost": 0.01}}})
    if "anthropic" in url:
        prompt = json["messages"][0]["content"]
        if "ニュース1件ずつに切り分けて" in prompt:
            return R({"content": [{"type": "text", "text": J.dumps(ITEMS)}], "usage": {}})
        if "オープニングとクロージング" in prompt:
            return R({"content": [{"type": "text", "text": J.dumps(
                {"opening": [{"speaker": "navigator", "text": "お" * 100}],
                 "closing": [{"speaker": "navigator", "text": "し" * 50}],
                 "highlights": ["a", "b", "c"]})}], "usage": {}})
        if "事実の主張を1件ずつカードに" in prompt:
            return R({"content": [{"type": "text", "text": "[]"}], "usage": {}})
        if "判定のしかた" in prompt:
            return R({"content": [{"type": "text", "text": J.dumps(
                {"verdicts": [], "falsifier_hits": []})}], "usage": {}})
        return R({"content": [{"type": "text", "text": J.dumps(
            {"turns": [{"speaker": "navigator", "text": "あ" * 150}] * 4})}], "usage": {}})
    if "texttospeech" in url:
        import mp3util
        return R({"audioContent": base64.b64encode(mp3util.silence(500)).decode()})
    raise AssertionError(url)

requests.post = fake_post
'''


def run_once(repo: Path, empty_bin: Path) -> None:
    env = {
        "PATH": str(empty_bin),
        "PERPLEXITY_API_KEY": "x", "ANTHROPIC_API_KEY": "y", "GOOGLE_TTS_API_KEY": "z",
        "PYTHONPATH": str(repo),
    }
    proc = subprocess.run([sys.executable, "src/run.py"], cwd=repo, env=env,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-1500:]); print(proc.stderr[-2000:])
        raise SystemExit("実行に失敗しました")
    for line in proc.stdout.splitlines():
        if line.startswith("[stock]"):
            print("   " + line)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="norepeat-"))
    repo = work / "morning-brief"
    shutil.copytree(ROOT, repo, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", "out", "site", "data", ".git"))
    (repo / "sitecustomize.py").write_text(STUB, encoding="utf-8")
    empty_bin = work / "bin"
    empty_bin.mkdir()

    print("■ 1日目")
    run_once(repo, empty_bin)
    day1 = json.loads(next((repo / "out").glob("*_script.json")).read_text(encoding="utf-8"))
    aired1 = {i["id"] for i in day1["aired_items"]}

    # 翌日を装う（日付を1日進める）
    patch = (repo / "src" / "run.py").read_text(encoding="utf-8").replace(
        "now = datetime.now(JST)", "now = datetime.now(JST) + timedelta(days=1)")
    (repo / "src" / "run.py").write_text(patch, encoding="utf-8")

    print("\n■ 2日目（検索結果はまったく同じ）")
    run_once(repo, empty_bin)
    scripts = sorted((repo / "out").glob("*_script.json"))
    day2 = json.loads(scripts[-1].read_text(encoding="utf-8"))
    aired2 = {i["id"] for i in day2["aired_items"]}

    inv = json.loads((repo / "data" / "stock.json").read_text(encoding="utf-8"))

    overlap = aired1 & aired2
    assert not overlap, f"同じニュースが2日続けて取り上げられた: {overlap}"
    assert aired2, "2日目に何も取り上げられていない（在庫から補充できていない）"
    assert len(inv["items"]) == 28, f"在庫が二重登録された: {len(inv['items'])}件"
    assert all(len(i["aired"]) <= 1 for i in inv["items"]), "同じ項目が複数回記録された"

    from_stock = sum(1 for i in day2["aired_items"] if i["from_stock"])
    print(f"\n1日目に取り上げた項目: {len(aired1)}件")
    print(f"2日目に取り上げた項目: {len(aired2)}件（うち在庫からの補充 {from_stock}件）")
    print(f"重複: {len(overlap)}件")
    print(f"在庫の総数: {len(inv['items'])}件（同じURLは二重登録されていない）")
    print("\n同じニュースの繰り返しは起きませんでした")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
