"""調査結果を「ニュース1件ずつ」に切り分ける（Claude API）。

切り分けたものを在庫（stock.py）に登録し、取り上げ済みかどうかで選別します。
このとき、すでに在庫にある見出しを一緒に渡して「同じ出来事は返すな」と
指示することで、同じニュースが別のURLで再登録されるのを防ぎます。
"""

from __future__ import annotations

from typing import Any

from judge import _call_with_retry, _parse

CURATE_TOKENS = 20000


def extract_items(
    model: str,
    research: list,
    known: dict[str, list[str]],
    segment_titles: dict[str, str],
) -> list[dict[str, Any]]:
    """調査結果から、テーマごとのニュース項目を抜き出す。

    known は「すでに在庫にあるテーマ別の見出し」。これと同じ出来事は返させない。
    """
    blocks = []
    for r in research:
        if not r.ok or r.segment_id not in segment_titles:
            continue
        seen = known.get(r.segment_id, [])
        seen_block = (
            "\n【このテーマで既に拾ってある出来事（これらと同じものは返さないでください）】\n"
            + "\n".join(f"- {h}" for h in seen[:60])
            if seen else "\n（このテーマの既出はありません）"
        )
        srcs = "\n".join(f"- {s['title']} {s['url']}" for s in r.sources[:20])
        blocks.append(
            f"## theme_id: {r.segment_id}（{r.title}）\n{r.body}\n\n"
            f"【出典】\n{srcs}\n{seen_block}"
        )

    if not blocks:
        return []

    prompt = f"""次の調査結果を、ニュース1件ずつに切り分けてください。

各件は次の形にします。
- theme_id: 上に書いてある theme_id をそのまま
- headline: その出来事を1文で。40文字以内
- detail: 何が起きたか。200〜400文字。数字は単位と時点をつける
- numbers: 押さえるべき数字を文字列の配列で（例 "漁獲枠191万トン"）。無ければ空配列
- source_url: 根拠のURL。調査結果に出ているものだけ。作らない
- source_title: その出典の名前
- published: その出来事の時点（YYYY-MM-DD。分からなければ空文字）
- importance: 1〜5。この番組の聞き手にとっての重要度

聞き手は、鹿児島のぶり類養殖と水産物輸出を専門とする一人コンサルタントです。
生まれたばかりの子どもがいます。この人にとっての重要度で採点してください。

# 絶対に守ること
- **各テーマの「既に拾ってある出来事」と同じものは返さないでください。**
  表現が違っても、同じ出来事を指していれば同じとみなして除外します。
  ただし「続報」として新しい数字や決定が加わっている場合は、
  headline にその新しい点を書いて返してください。
- 調査結果に書かれていないことを足さない
- 同じ出来事を複数の記事が伝えている場合は1件にまとめる
- 意見・見通しだけの記事は importance を低くし、detail の文末を
  「〜との見方が示されている」の形にする
- 1テーマあたり最大10件。重要なものから

JSONの配列だけを出力してください。前置きも説明も、考えた過程も書かないでください。
1文字目が「[」になるようにしてください。該当が無ければ [] を返してください。

{chr(10).join(blocks)}
"""

    items = _parse(_call_with_retry(model, prompt, CURATE_TOKENS))
    if not isinstance(items, list):
        return []

    out = []
    for it in items:
        if not isinstance(it, dict) or not it.get("headline"):
            continue
        if it.get("theme_id") not in segment_titles:
            continue
        it["theme"] = it.pop("theme_id")
        out.append(it)
    return out
