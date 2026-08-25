"""次回の番組で扱ってほしい論点を、popo が書き置きするための仕組み。

requests/next.md に書いておくと、翌朝の番組がそれを拾います。
拾われた指定は requests/archive/ に日付つきで移され、next.md は空に戻ります。
（同じ指定が翌日以降もくり返し使われるのを防ぐため）

iPhone からは、GitHub のウェブ版で requests/next.md を開き、
鉛筆マークから直接書き換えられます。アプリは不要です。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "requests" / "next.md"
ARCHIVE = ROOT / "requests" / "archive"

TEMPLATE = """# 次回の番組で扱ってほしいこと

ここに書いた内容が、次の放送に反映されます。放送後は自動で空に戻ります。

書き方は自由です。箇条書きでも、文章でも構いません。例:

- ぶりの対米輸出単価が実際に上がっているのか、数量と金額の両方で確かめてほしい
- ペルーの2期漁の枠が決まったかどうか
- 教育の話は今日はいらない。かわりに水産を厚めに

<!-- ここから下に書いてください -->
"""

MARKER = "<!-- ここから下に書いてください -->"


def load() -> str:
    """書き置きされた指定を取り出す。無ければ空文字。"""
    if not INBOX.exists():
        return ""

    text = INBOX.read_text(encoding="utf-8")
    if MARKER in text:
        text = text.split(MARKER, 1)[1]

    # 説明文の名残や空行を落とす
    lines = [ln.rstrip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln.strip() and not ln.strip().startswith("#")]
    body = "\n".join(lines).strip()

    # テンプレートの例文がそのまま残っている場合は指定なしとみなす
    if body.startswith("- ぶりの対米輸出単価が実際に"):
        return ""
    return body


def archive(body: str, when: datetime) -> None:
    """使い終わった指定を日付つきで保管し、受け皿を空に戻す。"""
    if not body:
        return
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    path = ARCHIVE / f"{when:%y%m%d}_requests.md"
    # 同じ日に作り直した場合は追記する（上書きしない）
    prev = path.read_text(encoding="utf-8") if path.exists() else ""
    stamp = f"\n\n---\n（{when:%Y-%m-%d %H:%M} に反映）\n"
    path.write_text(prev + stamp + body + "\n", encoding="utf-8")

    INBOX.parent.mkdir(parents=True, exist_ok=True)
    INBOX.write_text(TEMPLATE, encoding="utf-8")


def as_research_query(body: str) -> str:
    """指定を、調査用の問いかけに直す。"""
    return f"""次の点について調べてください。利用者が明示的に知りたいと指定した事項です。

{body}

それぞれについて「何が分かったか」「具体的な数字（単位と時点つき）」「出典URL」を示してください。
調べても分からなかったものは、無理に埋めず「確認できず」と明記してください。
指定が4つ以上ある場合も、すべてに触れてください。"""


def as_script_instruction(body: str) -> str:
    """指定を、台本を書く側への指示に直す。"""
    return f"""
# 利用者からの指定（最優先）
利用者が、今日の放送で扱ってほしいことを次のように書き置きしています。

{body}

これらは通常のコーナー構成より優先してください。指定された論点は、
関係するコーナーの中か、最後の「きょうの論点」で必ず触れてください。
調べても分からなかった指定については、ごまかさず「確認できませんでした」と
はっきり言ってください。指定が構成の変更（このテーマを減らす等）を含む場合は、
その指示に従って尺を配分し直してください。
"""


def summarize(body: str, limit: int = 3) -> list[str]:
    """指定を短い箇条書きにして、番組ページに載せる用。"""
    items = [re.sub(r"^[-・*]\s*", "", ln).strip() for ln in body.splitlines()]
    return [x for x in items if x][:limit]
