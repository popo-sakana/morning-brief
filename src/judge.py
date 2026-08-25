"""今日の調査結果を、仮説に照らして判定する（Claude API）。

順番が大事です。
  調査（仮説を知らない） → 事実の抜き出し（仮説を知らない） → 判定（ここで初めて仮説を見る）

同じ1回の呼び出しで「探して・要約して・仮説に照らす」を全部やらせると、
要約の時点で仮説に都合よく歪みます。だから工程を分けています。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

from hypotheses import Verdict

API_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT = 600


def _call(model: str, prompt: str, max_tokens: int = 8000) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 ANTHROPIC_API_KEY が設定されていません")
    resp = requests.post(
        API_URL,
        json={"model": model, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]},
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        timeout=TIMEOUT,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Claude API エラー HTTP {resp.status_code}: {resp.text[:400]}")
    return "".join(b.get("text", "") for b in resp.json().get("content", [])
                   if b.get("type") == "text")


def _parse(text: str) -> Any:
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    start = min([i for i in (text.find("{"), text.find("[")) if i != -1], default=-1)
    if start == -1:
        raise ValueError(f"JSONが見つかりません: {text[:200]}")
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])


def extract_cards(model: str, research: list) -> list[dict[str, Any]]:
    """調査結果から「事実カード」を抜き出す。この工程は仮説を見ない。"""
    blocks = "\n\n".join(
        f"### {r.title}\n{r.body}\n\n出典:\n"
        + "\n".join(f"- {s['title']} {s['url']}" for s in r.sources[:20])
        for r in research if r.ok
    )

    prompt = f"""次の調査結果から、事実の主張を1件ずつカードに分けてください。

各カードは次の形にします。
- claim: 事実を1文で。数字は単位と時点をつける
- source_url: その根拠となるURL（調査結果に出ているものだけ。作らない）
- tier: 1（政府・国際機関・企業の公式発表・査読論文）/ 2（業界紙・一般紙の報道）/ 3（伝聞・推測・意見）
- date: その事実の時点（分かれば YYYY-MM-DD、分からなければ空文字）

守ること:
- 調査結果に書かれていないことを足さない
- 意見・見通しは tier 3 にし、claim の文末を「〜との見方が示されている」の形にする
- 同じ出来事を複数の記事が伝えている場合は、1枚にまとめる（同じ話で証拠が何倍にもならないように）
- 20〜40枚を目安に

JSONの配列だけを出力してください。説明は不要です。

{blocks}
"""
    cards = _parse(_call(model, prompt, 12000))
    return [c for c in cards if isinstance(c, dict) and c.get("claim")]


def map_to_hypotheses(
    model: str, cards: list[dict[str, Any]], hyps: list[dict[str, Any]]
) -> tuple[list[Verdict], dict[str, list[str]]]:
    """事実カードを仮説に照らす。ここで初めて仮説文を見る。"""
    if not cards or not hyps:
        return [], {}

    h_block = "\n\n".join(
        f"""{h['id']}（テーマ: {h['theme']}）
見立て: {h['statement'].strip()}
崩壊条件:
""" + "\n".join(f"  - [{h['id']}-F{i}] {f['text']}" for i, f in enumerate(h["falsifiers"], 1))
        for h in hyps
    )

    c_block = "\n".join(
        f"[C{i}] {c['claim']}  （tier {c.get('tier', 3)} / {c.get('source_url', '')}）"
        for i, c in enumerate(cards, 1)
    )

    prompt = f"""次の「事実カード」を、それぞれの「見立て」に照らして判定してください。

# 見立て
{h_block}

# 事実カード
{c_block}

# 判定のしかた
各カードと各見立ての組み合わせについて、次の3つのどれかを選びます。
- support   : その見立てを支持する
- contradict: その見立てに反する
- neutral   : どちらとも言えない

**neutral を既定にしてください。** support か contradict と判定するには、
カードの文言そのものが根拠になっている必要があります。
「関係がありそう」「文脈的に効きそう」程度なら neutral です。

directness は、その見立てにどれだけ直接効くかを 0.2 / 0.5 / 0.8 / 1.0 から選びます。
- 1.0: 見立ての中心をそのまま裏づける／覆す
- 0.5: 周辺の条件を動かす
- 0.2: 遠い関連

崩壊条件については、その条件が実際に起きたことをカードが示しているかを別に判定します。
「起きそうだ」ではなく「起きた」と読める場合だけヒットとします。

# 出力
neutral は出力しないでください。support と contradict だけを出します。

{{
  "verdicts": [
    {{"hypothesis_id": "H-001", "card": 3, "relation": "contradict",
      "directness": 0.8, "rationale": "40字以内で理由"}}
  ],
  "falsifier_hits": [
    {{"hypothesis_id": "H-001", "falsifier": "H-001-F1", "card": 7,
      "reason": "この条件が実際に起きたと読める根拠"}}
  ]
}}

JSONだけを出力してください。該当がなければ空の配列にしてください。
無理にヒットを作らないでください。「今日は該当なし」が正しい日は多くあります。
"""

    data = _parse(_call(model, prompt, 8000))

    verdicts: list[Verdict] = []
    for v in data.get("verdicts", []):
        idx = int(v.get("card", 0)) - 1
        if not (0 <= idx < len(cards)):
            continue
        c = cards[idx]
        verdicts.append(Verdict(
            hypothesis_id=v["hypothesis_id"],
            relation=v["relation"],
            claim=c["claim"],
            source_url=c.get("source_url", ""),
            tier=int(c.get("tier", 3)),
            directness=float(v.get("directness", 0.5)),
            origin=c.get("origin", "normal"),
        ))

    hits: dict[str, list[str]] = {}
    for f in data.get("falsifier_hits", []):
        idx = int(f.get("card", 0)) - 1
        detail = f.get("reason", "")
        if 0 <= idx < len(cards):
            detail = f"{cards[idx]['claim']}（{detail}）"
        hits.setdefault(f["hypothesis_id"], []).append(detail)

    return verdicts, hits
