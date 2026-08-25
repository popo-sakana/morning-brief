"""ニュースの在庫（ストック）を管理する。

なぜ必要か:
  調査は「直近1週間」のように幅を持たせて探しています。そのため、同じ出来事が
  何日も検索結果に出てきて、番組で繰り返し取り上げられてしまいます。

  そこで、拾ったニュースを1件ずつ在庫に登録し、「いつ番組で取り上げたか」を
  記録します。取り上げ済みのものは二度と選ばれません。

  同時に、これは「その日ネタが薄い」問題の解決にもなります。新しいニュースが
  足りない日は、まだ取り上げていない在庫から補充します。

在庫は data/stock.json に貯まります。1件あたり数百バイトなので、
1年ためても数MB程度です。
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STOCK = ROOT / "data" / "stock.json"

# 取り上げられないまま日数が経った在庫は、鮮度が落ちるので候補から外す
STALE_DAYS = 45
# 重複判定のために、直近何日ぶんの見出しを照合に使うか
RECENT_DAYS = 60


def _norm(s: str) -> str:
    """URLの表記ゆれを吸収する（末尾スラッシュ、追跡パラメータなど）。"""
    s = (s or "").strip().lower()
    s = re.sub(r"[?#].*$", "", s)
    s = re.sub(r"/+$", "", s)
    s = re.sub(r"^https?://(www\.)?", "", s)
    return s


_WS = re.compile(r"\s+")


def make_id(theme: str, headline: str, url: str) -> str:
    head = _WS.sub("", headline.replace("　", ""))[:40]
    key = f"{theme}|{_norm(url)}|{head}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def load() -> dict[str, Any]:
    if STOCK.exists():
        return json.loads(STOCK.read_text(encoding="utf-8"))
    return {"items": []}


def save(data: dict[str, Any]) -> None:
    STOCK.parent.mkdir(parents=True, exist_ok=True)
    STOCK.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def known_urls(data: dict[str, Any]) -> set[str]:
    return {_norm(i.get("source_url", "")) for i in data["items"] if i.get("source_url")}


def recent_headlines(data: dict[str, Any], today: date) -> dict[str, list[str]]:
    """テーマごとの、最近見かけた見出し。重複判定の材料として使う。"""
    cutoff = (today - timedelta(days=RECENT_DAYS)).isoformat()
    out: dict[str, list[str]] = {}
    for i in data["items"]:
        if i.get("first_seen", "") >= cutoff:
            out.setdefault(i["theme"], []).append(i["headline"])
    return out


def add(data: dict[str, Any], items: list[dict[str, Any]], today: date) -> int:
    """新しく見つかった項目を在庫に足す。すでにあるものは足さない。"""
    have = {i["id"] for i in data["items"]}
    urls = known_urls(data)
    added = 0

    for it in items:
        headline = (it.get("headline") or "").strip()
        url = it.get("source_url", "")
        if not headline:
            continue
        # 同じURLをすでに持っていれば、同じ出来事とみなす
        if url and _norm(url) in urls:
            continue

        iid = make_id(it.get("theme", ""), headline, url)
        if iid in have:
            continue

        data["items"].append({
            "id": iid,
            "theme": it.get("theme", ""),
            "headline": headline,
            "detail": (it.get("detail") or "").strip(),
            "numbers": it.get("numbers") or [],
            "source_url": url,
            "source_title": it.get("source_title", ""),
            "published": it.get("published", ""),
            "importance": int(it.get("importance", 3)),
            "first_seen": today.isoformat(),
            "aired": [],
        })
        have.add(iid)
        if url:
            urls.add(_norm(url))
        added += 1

    return added


def _age(item: dict[str, Any], today: date) -> int:
    try:
        return (today - date.fromisoformat(item["first_seen"])).days
    except Exception:  # noqa: BLE001
        return 0


def select(
    data: dict[str, Any], theme: str, want: int, today: date
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """このテーマで今日取り上げる項目を選ぶ。

    戻り値は (今日の新着から選んだもの, 在庫から補充したもの)。
    新着を優先し、足りない分だけ在庫から埋めます。
    """
    pool = [
        i for i in data["items"]
        if i["theme"] == theme and not i["aired"] and _age(i, today) <= STALE_DAYS
    ]
    fresh = [i for i in pool if _age(i, today) == 0]
    stocked = [i for i in pool if _age(i, today) > 0]

    # 新着は重要度順。在庫は「古いものから」＝寝かせすぎない
    fresh.sort(key=lambda i: (-i["importance"], i.get("published", "")))
    stocked.sort(key=lambda i: (-_age(i, today), -i["importance"]))

    chosen_fresh = fresh[:want]
    chosen_stock = stocked[: max(0, want - len(chosen_fresh))]
    return chosen_fresh, chosen_stock


def mark_aired(data: dict[str, Any], items: list[dict[str, Any]], today: date) -> None:
    by_id = {i["id"]: i for i in data["items"]}
    for it in items:
        target = by_id.get(it["id"])
        if target is not None and today.isoformat() not in target["aired"]:
            target["aired"].append(today.isoformat())


def prune(data: dict[str, Any], today: date, keep_days: int = 180) -> int:
    """古すぎるものを在庫から落とす。取り上げ済みの記録は残す価値があるので、
    しばらくは保持してから消します（同じニュースの再登録を防ぐため）。"""
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    before = len(data["items"])
    data["items"] = [i for i in data["items"] if i.get("first_seen", "") >= cutoff]
    return before - len(data["items"])


def summary(data: dict[str, Any], today: date) -> str:
    total = len(data["items"])
    unaired = sum(1 for i in data["items"] if not i["aired"])
    stale = sum(
        1 for i in data["items"]
        if not i["aired"] and _age(i, today) > STALE_DAYS
    )
    return f"在庫 {total}件（未取上 {unaired}件 / うち鮮度切れ {stale}件）"
