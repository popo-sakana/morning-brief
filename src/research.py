"""Perplexity Agent API で各テーマのニュースを集める。

出力は「本文テキスト」と「出典URLの一覧」の2つ。
台本づくり（script_writer.py）は、ここで集めた材料の外に出ないよう指示されている。
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

API_URL = "https://api.perplexity.ai/v1/agent"
TIMEOUT = 600


@dataclass
class ResearchResult:
    segment_id: str
    title: str
    body: str
    sources: list[dict[str, str]] = field(default_factory=list)
    cost_usd: float = 0.0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.body.strip())


def _extract(payload: dict[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """Agent API のレスポンスから本文と出典を取り出す。"""
    body_parts: list[str] = []
    sources: dict[str, dict[str, str]] = {}

    for item in payload.get("output", []) or []:
        itype = item.get("type")

        if itype == "message":
            for chunk in item.get("content", []) or []:
                if chunk.get("type") in ("output_text", "text"):
                    body_parts.append(chunk.get("text", ""))
                # 本文中に埋め込まれた引用も出典として拾う
                for ann in chunk.get("annotations", []) or []:
                    url = ann.get("url")
                    if url:
                        sources.setdefault(url, {"url": url, "title": ann.get("title") or url})

        elif itype == "search_results":
            for r in item.get("results", []) or []:
                url = r.get("url")
                if url:
                    sources.setdefault(url, {"url": url, "title": r.get("title") or url})

    return "\n".join(p for p in body_parts if p).strip(), list(sources.values())


def run_one(segment: dict[str, Any], cfg: dict[str, Any], api_key: str) -> ResearchResult:
    rcfg = cfg.get("research", {})

    instructions = (
        "あなたは日本語で調査結果を報告するリサーチャーです。"
        "確認できた事実と、記事が示す見通し・意見を明確に区別してください。"
        "数値は必ず単位と時点を添えてください。"
        "日本語以外の情報源を使った場合は、要点を日本語に訳したうえで、原語の固有名詞は括弧で併記してください。"
        "推測で数字を埋めないでください。分からないことは『確認できず』と書いてください。"
        "各項目の末尾に、その根拠となるURLを列挙してください。"
    )

    payload: dict[str, Any] = {
        "preset": rcfg.get("preset", "medium"),
        "input": segment["query"],
        "instructions": instructions,
        "language_preference": "ja",
        "tools": [
            {
                "type": "web_search",
                "max_results": rcfg.get("max_results", 12),
                "search_recency_filter": segment.get("recency", "week"),
                "search_context_size": rcfg.get("search_context_size", "high"),
            }
        ],
    }

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    last_err = ""
    for attempt in range(3):
        try:
            resp = requests.post(API_URL, json=payload, headers=headers, timeout=TIMEOUT)
            if resp.status_code >= 400:
                last_err = f"HTTP {resp.status_code}: {resp.text[:400]}"
                # 4xx はリトライしても直らないことが多い（キー誤りなど）
                if 400 <= resp.status_code < 500 and resp.status_code != 429:
                    break
                time.sleep(5 * (attempt + 1))
                continue

            data = resp.json()
            body, sources = _extract(data)
            cost = float((data.get("usage") or {}).get("cost", {}).get("total_cost") or 0.0)
            if not body:
                last_err = "本文が空でした"
                time.sleep(5)
                continue
            return ResearchResult(segment["id"], segment["title"], body, sources, cost)

        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(5 * (attempt + 1))

    return ResearchResult(segment["id"], segment["title"], "", [], 0.0, error=last_err)


def run_all(
    cfg: dict[str, Any],
    extra_query: str = "",
    indicators: dict[str, list[str]] | None = None,
    redteam: tuple[str, str] | None = None,
    ledger: dict[str, Any] | None = None,
) -> list[ResearchResult]:
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        raise RuntimeError("環境変数 PERPLEXITY_API_KEY が設定されていません")

    segments = []
    for seg in cfg["segments"]:
        seg = dict(seg)
        # 仮説から来るのは「中立な指標クエリ」だけ。仮説の主張そのものは渡さない。
        extra = (indicators or {}).get(seg["id"])
        if extra:
            seg["query"] = seg["query"] + (
                "\n\nあわせて、次の指標の最新値も確認してください。"
                "見つからなければ「確認できず」で構いません。\n"
                + "\n".join(f"- {q}" for q in extra)
            )
        segments.append(seg)

    # popo からの指定がある場合は、5本目の調査として別立てで走らせる。
    # 4テーマの問いに混ぜ込むと、通常の調査範囲まで指定に引っぱられるため。
    if extra_query.strip():
        import requests_inbox

        segments.append({
            "id": "requested",
            "title": "指定された論点",
            "recency": "week",
            "query": requests_inbox.as_research_query(extra_query),
        })

    # 反証だけを狙う調査。支持材料は返させない。
    if redteam:
        segments.append({
            "id": "redteam",
            "title": f"反証さがし（{redteam[0]}）",
            "recency": "month",
            "query": redteam[1],
        })

    # 安全網：調査に渡す文面に仮説の主張が混ざっていないかを確認する。
    # 混ざっていれば、探索が仮説に汚染されるので実行を止める。
    if ledger:
        import hypotheses as hyp_mod

        payload = "\n".join(s["query"] for s in segments if s["id"] != "redteam")
        if hyp_mod.contains_hypothesis_text(payload, ledger):
            raise RuntimeError(
                "調査の問いかけに仮説の文章が混ざっています。"
                "探索が仮説に引っぱられるため中止しました。"
                "config.yaml の segments か next_indicators を見直してください。"
            )

    results: list[ResearchResult] = []
    for segment in segments:
        print(f"[research] {segment['title']} を調査中…", flush=True)
        res = run_one(segment, cfg, api_key)
        if res.ok:
            print(f"[research] {segment['title']}: {len(res.body)}文字 / 出典{len(res.sources)}件 / ${res.cost_usd:.4f}")
        else:
            print(f"[research] {segment['title']}: 失敗 -> {res.error}")
        results.append(res)
    return results
