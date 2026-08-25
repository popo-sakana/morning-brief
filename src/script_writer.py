"""台本をつくる（Claude API）。

コーナーごとに別々に書かせます。1回の指示で2万文字を配分どおりに書かせるのは
無理があり、実際どのコーナーも目標の3分の1程度しか書かれませんでした。
1コーナーずつ「これだけで3,200文字」と頼めば、配分の問題そのものが消えます。

材料は「その日取り上げると決めたニュース項目」だけを渡します。
調査結果の全文ではありません。取り上げ済みのニュースは選定の段階で
除かれているので、同じ話が繰り返されることはありません。
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


def _call(model: str, prompt: str, max_tokens: int) -> str:
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
    data = resp.json()
    text = "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")
    if not text.strip():
        kinds = [b.get("type") for b in data.get("content", [])] or ["（本文なし）"]
        raise RuntimeError(
            f"本文が空でした。stop_reason={data.get('stop_reason')} / 種類={kinds} / "
            f"max_tokens={max_tokens}"
        )
    return text


def _parse(text: str) -> Any:
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError(f"JSONが見つかりません。冒頭: {text[:200]!r}")
    start = min(starts)
    end = max(text.rfind("}"), text.rfind("]"))
    return json.loads(text[start : end + 1])


COMMON_RULES = """
# 話し方の決まり（すべてのコーナー共通）
- 「{nav}」と「{ana}」の2人による対話です。
- {nav}：{nav_persona}
- {ana}：{ana_persona}
- 耳だけで聞いて分かる日本語で書いてください。
- 下に渡した材料に書かれていない事実・数字・固有名詞を、新しく作り出さないでください。
- 数字は材料と一字一句一致させ、単位と時点を必ず添えてください。
- 材料が「見通し」「意見」としているものは、「〜という見方が出ています」の形で話し、
  事実と区別してください。
- 「最も有効」「最適」「絶対に」といった強い断定は使わないでください。
- 海外の情報は日本語に訳してください。原語を残したほうが分かるものは、
  初出時だけ「〜（英語では〜）」と添えてください。
- 専門用語や横文字は、使う前に一言で意味を説明してください。
- 読み上げられます。URL、記号、箇条書き、括弧書きの注釈、絵文字は入れないでください。
  数字は「約1万2000トン」のように読み上げやすく書いてください。
- 1つの文は原則50文字以内、長くても80文字まで。読点でつながず句点で切ってください。
- 1回の発言は40〜220文字。一方が長く喋りすぎないようにしてください。
"""


def _rules(cfg: dict[str, Any]) -> str:
    h = cfg["hosts"]
    return COMMON_RULES.format(
        nav=h["navigator"]["label"], ana=h["analyst"]["label"],
        nav_persona=h["navigator"]["persona"], ana_persona=h["analyst"]["persona"],
    )


def _turns_from(data: Any) -> list[dict[str, str]]:
    turns = data.get("turns") if isinstance(data, dict) else data
    out = []
    for t in turns or []:
        if isinstance(t, dict) and t.get("text") and t.get("speaker") in ("navigator", "analyst"):
            out.append({"speaker": t["speaker"], "text": t["text"].strip()})
    return out


def _write_segment(
    cfg: dict[str, Any], model: str, title: str, minutes: float,
    fresh: list[dict], stocked: list[dict], date_label: str,
) -> list[dict[str, str]]:
    cpm = cfg["program"].get("chars_per_minute", 640)
    target = _target_chars(minutes, cpm)

    def fmt(items, label):
        if not items:
            return ""
        rows = []
        for i, it in enumerate(items, 1):
            nums = "、".join(it.get("numbers") or []) or "（数字なし）"
            rows.append(
                f"{i}. {it['headline']}\n"
                f"   内容: {it['detail']}\n"
                f"   数字: {nums}\n"
                f"   出典: {it.get('source_title','')}\n"
                f"   時点: {it.get('published') or '不明'}"
            )
        return f"\n【{label}】\n" + "\n".join(rows)

    stock_note = ""
    if stocked:
        stock_note = (
            "\n※「少し前の話題」は、数日前に拾ったまま番組で扱っていなかったものです。"
            "『少し前の話になりますが』と前置きしてから扱ってください。"
            "今日起きたことのように話さないでください。"
        )

    prompt = f"""{date_label}の音声ニュース番組の、ひとつのコーナーの台本を書いてください。

# このコーナー
「{title}」（{minutes:.0f}分）

# 分量（重要）
このコーナーだけで、**合計{target}文字**を書いてください。
1回の発言が40〜220文字なので、**{max(8, target // 150)}〜{target // 90}回程度の発言**になります。
今回はこの分量を満たすことを最優先にしてください。
材料1件あたり{target // max(1, len(fresh) + len(stocked))}文字くらいが目安です。

分量を満たすために、次を厚く書いてください。
- 数字の意味（前年と比べてどうか、大きい数字なのか小さい数字なのか）
- 背景（なぜそうなったのか。材料に書かれている範囲で）
- 聞き手にとっての意味（ただし材料から離れた推測はしない）
- {cfg['hosts']['navigator']['label']}の素朴な問い返しを、各項目に1回は入れる

**材料を水増しして作り話をするのは禁止です。** 材料が足りなくて分量に届かない場合は、
届く範囲で終えてください。分量を満たすために事実を作るくらいなら、短いほうがましです。
{_rules(cfg)}
# 出力
JSONだけを出力してください。前置きも説明も書かないでください。
{{"turns": [{{"speaker": "navigator", "text": "..."}}, {{"speaker": "analyst", "text": "..."}}]}}

# 材料
{fmt(fresh, "今日の新しい話題")}{fmt(stocked, "少し前の話題（まだ番組で扱っていないもの）")}{stock_note}
"""
    return _turns_from(_parse(_call(model, prompt, 16000)))


def _write_verification(
    cfg: dict[str, Any], model: str, checks: list, date_label: str
) -> list[dict[str, str]]:
    cpm = cfg["program"].get("chars_per_minute", 640)
    minutes = float(cfg.get("hypothesis", {}).get("broadcast", {}).get("minutes", 5))
    target = _target_chars(minutes, cpm)

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
崩壊条件（事前に決めた「捨てる条件」）:
{chr(10).join('    - ' + t for t in c.get('falsifier_texts', []))}
{('崩壊条件に触れました: ' + ' / '.join(c['falsifier_hits'])) if c.get('falsifier_hits') else ''}
この見立てが効く判断: {c.get('decision_link', '')}
""")

    prompt = f"""{date_label}の音声ニュース番組の「きょうの検証」コーナーの台本を書いてください。

この番組は、いくつかの見立て（仮説）を立てて、毎朝それを検証しています。
今日はこの{len(checks)}件を扱います。

# 分量（重要）
このコーナーだけで、**合計{target}文字**を書いてください。
1件あたり約{target // max(1, len(checks))}文字です。
{max(8, target // 150)}回程度の発言になります。この分量を満たすことを最優先にしてください。

# 今日の材料
{''.join(rows)}

# このコーナーの書き方（厳守）
- 確からしさの数字を必ず声に出してください。「先週0.55だったものが今日0.70です」の形で。
- **支持する材料と、反する材料の両方に必ず触れてください。**
  反する材料が今日ゼロだった場合は、それを隠さず言い、そのうえで
  「反証が出ていないのは見立てが強いからかもしれないし、私たちの探し方が
  偏っているからかもしれない」という趣旨を必ず添えてください。省略できません。
- 崩壊条件を1つ読み上げてください。何が起きたらこの見立てを捨てるのかを、
  聞き手が覚えていられるようにするためです。
- {cfg['hosts']['analyst']['label']}が数字を読み、{cfg['hosts']['navigator']['label']}が疑う側に立ちます。
  {cfg['hosts']['navigator']['label']}は必ず一度「その解釈以外に説明はつかないのか」と
  別の読み方を出してください。ただの逆張りにはせず、「では何が観測されれば納得するか」を添えてください。
{_rules(cfg)}
# 出力
JSONだけを出力してください。
{{"turns": [{{"speaker": "analyst", "text": "..."}}]}}
"""
    return _turns_from(_parse(_call(model, prompt, 16000)))


def _write_discussion(
    cfg: dict[str, Any], model: str, segments: list[dict], date_label: str, wishes: str
) -> list[dict[str, str]]:
    cpm = cfg["program"].get("chars_per_minute", 640)
    hcfg = cfg.get("hypothesis", {}).get("broadcast", {})
    minutes = float(hcfg.get("discussion_minutes_when_checking", cfg["discussion"]["minutes"]))
    target = _target_chars(minutes, cpm)

    digest = "\n\n".join(
        f"## {s['title']}\n" + "\n".join(f"- {t['text'][:120]}" for t in s["turns"][:8])
        for s in segments
    )
    wish_block = f"\n# 利用者からの指定\n{wishes}\n" if wishes.strip() else ""

    prompt = f"""{date_label}の音声ニュース番組の、最後の「{cfg['discussion']['title']}」の台本を書いてください。

# 分量（重要）
**合計{target}文字**。{max(8, target // 150)}回程度の発言になります。この分量を満たしてください。

# 今日の各コーナーで話した内容
{digest}
{wish_block}
# このコーナーの狙い
{cfg['discussion']['instruction']}

無理にこじつけないでください。関係が薄い日は「今日は独立した話題です」と
正直に言って構いません。その場合は、代わりに1つのテーマを深掘りしてください。
{_rules(cfg)}
# 出力
JSONだけを出力してください。
{{"turns": [{{"speaker": "navigator", "text": "..."}}]}}
"""
    return _turns_from(_parse(_call(model, prompt, 12000)))


def _write_bookends(
    cfg: dict[str, Any], model: str, segments: list[dict], date_label: str
) -> dict[str, Any]:
    heads = "\n".join(
        f"- {s['title']}: " + (s["turns"][1]["text"][:100] if len(s["turns"]) > 1 else "")
        for s in segments
    )
    prompt = f"""{date_label}の音声ニュース番組の、オープニングとクロージングを書いてください。

# 今日扱った内容
{heads}

# 分量
- オープニング: 約200文字。日付と、今日の見出しを一言ずつ。
- クロージング: 約80文字。台本と出典が番組ページにある旨を添えて締める。
- 要点（highlights）: 今日の要点3つ。各40文字以内。これは読み上げません。
{_rules(cfg)}
# 出力
JSONだけを出力してください。
{{"opening": [{{"speaker": "navigator", "text": "..."}}],
  "closing": [{{"speaker": "navigator", "text": "..."}}],
  "highlights": ["...", "...", "..."]}}
"""
    data = _parse(_call(model, prompt, 4000))
    return {
        "opening": _turns_from(data.get("opening")),
        "closing": _turns_from(data.get("closing")),
        "highlights": [h for h in (data.get("highlights") or []) if isinstance(h, str)][:3],
    }


def write_script(
    cfg: dict[str, Any],
    date_label: str,
    picks: dict[str, dict[str, list]],
    requested: Any = None,
    wishes: str = "",
    checks: list | None = None,
) -> dict[str, Any]:
    """コーナーごとに書かせて、1本の台本に組み上げる。

    picks は {segment_id: {"fresh": [...], "stock": [...]}}。
    """
    model = cfg.get("script", {}).get("model", "claude-sonnet-5")
    script: dict[str, Any] = {"segments": []}

    if checks:
        print(f"[script] きょうの検証（{len(checks)}件）を執筆中…", flush=True)
        turns = _write_verification(cfg, model, checks, date_label)
        script["verification"] = {"title": "きょうの検証", "turns": turns}
        print(f"[script]   {len(turns)}発言 / {sum(len(t['text']) for t in turns)}文字")

    for seg in cfg["segments"]:
        p = picks.get(seg["id"], {"fresh": [], "stock": []})
        if not p["fresh"] and not p["stock"]:
            print(f"[script] {seg['title']}: 材料が無いため飛ばします")
            continue
        print(f"[script] {seg['title']}（新着{len(p['fresh'])}件 / 在庫{len(p['stock'])}件）を執筆中…",
              flush=True)
        turns = _write_segment(
            cfg, model, seg["title"], seg["minutes"], p["fresh"], p["stock"], date_label
        )
        script["segments"].append({"id": seg["id"], "title": seg["title"], "turns": turns})
        print(f"[script]   {len(turns)}発言 / {sum(len(t['text']) for t in turns)}文字")

    # 指定された論点は、調査結果をそのまま材料にして1コーナー分書く
    if requested is not None and getattr(requested, "ok", False):
        item = [{
            "headline": "利用者が指定した論点",
            "detail": requested.body[:4000],
            "numbers": [],
            "source_title": "指定論点の調査",
            "published": "",
        }]
        print("[script] 指定された論点を執筆中…", flush=True)
        turns = _write_segment(cfg, model, "指定された論点", 3, item, [], date_label)
        script["segments"].append(
            {"id": "requested", "title": "指定された論点", "turns": turns}
        )

    print("[script] きょうの論点を執筆中…", flush=True)
    disc = _write_discussion(cfg, model, script["segments"], date_label, wishes)
    script["discussion"] = {"title": cfg["discussion"]["title"], "turns": disc}
    print(f"[script]   {len(disc)}発言 / {sum(len(t['text']) for t in disc)}文字")

    script.update(_write_bookends(cfg, model, script["segments"], date_label))
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
