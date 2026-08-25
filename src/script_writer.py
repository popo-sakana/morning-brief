"""集めた材料から、2人の対話形式の日本語台本をつくる（Claude API）。

大事な制約：
  - 調査結果に書かれていない事実を足さない
  - 数字は調査結果と一致させる
  - 断定しすぎない（「最も有効」「絶対に」等を使わない）
  - 海外の話は日本語に訳して話す
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests

API_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT = 900


def _target_chars(minutes: float, cpm: int) -> int:
    return int(minutes * cpm)


def _build_prompt(
    cfg: dict[str, Any], research: list, date_label: str,
    wishes: str = "", checks: list | None = None,
) -> str:
    cpm = cfg["program"].get("chars_per_minute", 320)
    hosts = cfg["hosts"]
    disc = cfg["discussion"]

    blocks = []
    for res in research:
        if not res.ok:
            blocks.append(
                f"### {res.title}\n（このテーマは調査に失敗しました。理由: {res.error}）\n"
                "この場合、そのコーナーは『本日は材料が集まらなかった』と正直に短く伝えて次に進んでください。"
            )
            continue
        srcs = "\n".join(f"- {s['title']} {s['url']}" for s in res.sources[:25])
        blocks.append(f"### {res.title}\n{res.body}\n\n【このテーマの出典】\n{srcs}")

    seg_specs = "\n".join(
        f"- id: {s['id']} / タイトル: {s['title']} / 目安 {s['minutes']}分（約{_target_chars(s['minutes'], cpm)}文字）"
        for s in cfg["segments"]
    )

    wish_block = ""
    if wishes.strip():
        import requests_inbox

        wish_block = requests_inbox.as_script_instruction(wishes)

    hcfg = cfg.get("hypothesis", {}).get("broadcast", {})
    check_min = float(hcfg.get("minutes", 5))
    disc_min = float(hcfg.get("discussion_minutes_when_checking", 5)) if checks else disc["minutes"]

    check_block = ""
    if checks:
        rows = []
        for c in checks:
            ev = "\n".join(
                f"    - {'支持' if e['relation'] == 'support' else '反する'}: {e['claim']}"
                for e in c.get("evidence_today", [])
            ) or "    - （今日はこの見立てを動かす材料は出ていません）"
            rows.append(f"""
## {c['id']}
見立て: {c['statement'].strip()}
確からしさ: {c['before']:.2f} → {c['after']:.2f}
今日の材料:
{ev}
崩壊条件（この番組が事前に決めた「捨てる条件」）:
{chr(10).join('    - ' + t for t in c.get('falsifier_texts', []))}
{('崩壊条件に触れました: ' + ' / '.join(c['falsifier_hits'])) if c.get('falsifier_hits') else ''}
この見立てが効く判断: {c.get('decision_link', '')}
""")

        check_block = f"""
# 「きょうの検証」コーナー（4テーマの前に置く。合計{check_min:.0f}分・約{_target_chars(check_min, cpm)}文字）
この番組は、いくつかの見立て（仮説）を立てて、毎朝それを検証しています。
今日はこの{len(checks)}件を扱ってください。1件あたり約{_target_chars(check_min / len(checks), cpm)}文字。
{''.join(rows)}

## このコーナーの書き方（厳守）
- 確からしさの数字を必ず声に出して読んでください。「先週0.55だったものが今日0.61です」の形で。
- **支持する材料と、反する材料の両方に必ず触れてください。**
  反する材料が今日ゼロだった場合は、それを隠さず言ってください。そのうえで
  「反証が出ていないのは見立てが強いからかもしれないし、私たちの探し方が
  偏っているからかもしれない」という趣旨を必ず添えてください。これは省略できません。
- 崩壊条件を1つ読み上げてください。「何が起きたらこの見立てを捨てるか」を
  聞き手が覚えていられるようにするためです。
- 解説役が数字を読み、進行役が疑う側に立ってください。進行役は必ず一度
  「その解釈以外に説明はつかないのか」と別の読み方を出してください。
  ただし、ただの逆張りにはせず、「では何が観測されれば納得するか」を添えてください。
- 崩壊条件に触れた見立てがある場合は、そのことを最優先で扱ってください。
"""

    parts = ["1. オープニング（約30秒／約160文字）: 日付と、その日の見出しを一言ずつ。"]
    n = 2
    if checks:
        parts.append(f"{n}. 「きょうの検証」（{check_min:.0f}分）: 上の指示に従ってください。")
        n += 1
    parts.append(f"{n}. 以下の4コーナー:\n{seg_specs}")
    n += 1
    parts.append(
        f"{n}. 「{disc['title']}」（{disc_min:.0f}分／約{_target_chars(disc_min, cpm)}文字）\n"
        f"{disc['instruction']}"
    )
    parts.append(f"{n + 1}. クロージング（約15秒／約80文字）")
    order = "\n".join(parts)

    return f"""あなたは、毎朝配信される音声ニュース番組の構成作家です。
{date_label}の放送分の台本を書いてください。
{wish_block}{check_block}

# 番組の形
- 「{hosts['navigator']['label']}」と「{hosts['analyst']['label']}」の2人による対話形式です。
- {hosts['navigator']['label']}：{hosts['navigator']['persona']}
- {hosts['analyst']['label']}：{hosts['analyst']['persona']}
- 全編、耳だけで聞いて理解できる日本語で書いてください。

# 構成と長さ
{order}

# 絶対に守ること
- 下の【調査結果】に書かれていない事実・数字・固有名詞を、新しく作り出さないでください。
- 数字は調査結果と一字一句一致させてください。単位と時点（「〜年〜月時点」）を必ず添えてください。
- 調査結果が「見通し」「意見」として書いていることは、「〜という見方が出ています」「〜と報じられています」の形で話し、事実と区別してください。
- 「最も有効」「最適」「絶対に」といった強い断定は使わないでください。数字の裏づけがあるときだけ、数字とともに述べてください。
- 海外の情報は日本語に訳して話してください。企業名・魚種名・制度名など原語を残したほうが分かるものは、初出時だけ「〜（英語では〜）」と添えてください。
- 専門用語や横文字は、使う前に一言で意味を説明してください。いきなり専門的な話から入らないでください。
- 音声で読み上げられます。URL、記号、箇条書き、括弧書きの注釈、絵文字は本文に入れないでください。数字は「約1万2000トン」のように読み上げやすく書いてください。
- 1つの文は原則50文字以内、長くても80文字までにしてください。読点でつないで長く続けず、句点で切ってください。読み上げの仕組みが長い文を受け付けないことがあり、また耳で聞くときも短い文のほうが理解しやすいためです。
- 材料が薄いコーナーは、無理に引き伸ばさず短く終えて構いません。長さの目安に届かないことより、中身のない話をすることのほうが問題です。

# 出力の形式
次のJSONだけを出力してください。前置きも説明も、コードブロックの記号も付けないでください。

{{
  "opening": [{{"speaker": "navigator", "text": "..."}}],
  {("" if not checks else chr(34) + "verification" + chr(34) + ": {{" + chr(34) + "title" + chr(34) + ": " + chr(34) + "きょうの検証" + chr(34) + ", " + chr(34) + "turns" + chr(34) + ": [...]}}," + chr(10) + "  ")}"segments": [
    {{"id": "jp_fisheries", "title": "日本の水産",
      "turns": [{{"speaker": "navigator", "text": "..."}}, {{"speaker": "analyst", "text": "..."}}]}}
  ],
  "discussion": {{"title": "{disc['title']}",
      "turns": [{{"speaker": "navigator", "text": "..."}}]}},
  "closing": [{{"speaker": "navigator", "text": "..."}}],
  "highlights": ["その日の要点を3つ、各40文字以内で"]
}}

speaker は "navigator" か "analyst" のどちらかだけです。
1回の発言は原則40〜220文字にしてください。一方が長く喋りすぎないようにしてください。

# 調査結果
{chr(10).join(blocks)}
"""


def _parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    # ```json ... ``` で包まれていた場合に備える
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"JSONが見つかりませんでした: {text[:300]}")
    return json.loads(text[start : end + 1])


def write_script(
    cfg: dict[str, Any], research: list, date_label: str,
    wishes: str = "", checks: list | None = None,
) -> dict[str, Any]:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 ANTHROPIC_API_KEY が設定されていません")

    scfg = cfg.get("script", {})
    payload = {
        "model": scfg.get("model", "claude-sonnet-5"),
        "max_tokens": scfg.get("max_output_tokens", 32000),
        "messages": [
            {"role": "user", "content": _build_prompt(cfg, research, date_label, wishes, checks)}
        ],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    print("[script] 台本を生成中…", flush=True)
    resp = requests.post(API_URL, json=payload, headers=headers, timeout=TIMEOUT)
    if resp.status_code >= 400:
        raise RuntimeError(f"Claude API エラー HTTP {resp.status_code}: {resp.text[:500]}")

    data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    script = _parse_json(text)

    # 出典は調査結果側から引き継ぐ（台本には書かせない＝捏造を防ぐ）
    script["sources"] = [
        {"segment": r.title, **s} for r in research if r.ok for s in r.sources
    ]
    usage = data.get("usage", {})
    script["_usage"] = {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "model": payload["model"],
    }
    return script


def iter_turns(script: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(section_id, speaker, text) の並びに平らにする。"""
    out: list[tuple[str, str, str]] = []
    for t in script.get("opening", []):
        out.append(("opening", t["speaker"], t["text"]))
    ver = script.get("verification") or {}
    for t in ver.get("turns", []):
        out.append(("verification", t["speaker"], t["text"]))
    for seg in script.get("segments", []):
        for t in seg.get("turns", []):
            out.append((seg.get("id", "segment"), t["speaker"], t["text"]))
    disc = script.get("discussion") or {}
    for t in disc.get("turns", []):
        out.append(("discussion", t["speaker"], t["text"]))
    for t in script.get("closing", []):
        out.append(("closing", t["speaker"], t["text"]))
    return out
