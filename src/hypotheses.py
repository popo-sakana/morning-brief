"""論点台帳の読み書きと、見え方の更新。

ここで持つのは「検定したい命題」ではなく、その日のニュースを置く座標軸です。
生産・流通・販売にまたがる大きな論点を数本だけ持ち、毎朝そのうち1本に
その日のニュースを当てます。狙いは、場当たり的なニュース羅列にしないこと。
細かい数字を正確に当てにいくことではありません。

番組では確からしさの数字は読み上げません（stance_word / shift_word で言葉に直します）。
数字は内部の目安としてだけ使い、同じ論点を連日こすらないよう輪番で回します。

考え方は3つだけです。

1. 探索を論点で汚さない
   毎朝の4テーマの検索には、仮説の文章を一切渡しません。仮説を知った状態で
   探すと、それに合う材料ばかりが集まります。仮説を見るのは、材料が出そろった
   あとの「判定」の段階だけです。

2. 反証を探す係を別に立てる
   毎朝1本、仮説の「崩壊条件」だけを狙った検索を回します。支持材料は返すなと
   明示します。バランスを取ろうとして反証を弱く扱うのが、いちばんよくある失敗です。

3. 放っておくと確からしさは下がる
   支持も反証も出ない仮説は、静かに正しく見えてしまいます。証拠が来ない日は
   自動で 0.5 のほうへ戻し、21日出なければ様子見、45日で棚上げにします。
   「放置＝確信の維持」にしないための仕掛けです。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
LEDGER = ROOT / "hypotheses" / "ledger.yaml"

# 証拠の重み。一次情報ほど強く効かせる。
TIER_WEIGHT = {1: 1.0, 2: 0.6, 3: 0.3}
BASE_STEP = 0.8          # 証拠1件あたりの基本の効き
DAILY_CAP = 1.0          # 1日で動かせる上限（ロジット）
SILENCE_DECAY = 0.02     # 証拠が来ない日の減衰率
NO_CONTRA_DAYS = 7       # 反証がこの日数出ていなければ、支持の効きを半分にする
LOGIT_LIMIT = 2.9        # 確からしさが 0.05〜0.95 を超えないようにする


def _logit(p: float) -> float:
    p = min(max(p, 0.05), 0.95)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def _days(a: str | None, b: date) -> int | None:
    if not a:
        return None
    return (b - date.fromisoformat(a)).days


@dataclass
class Verdict:
    """1つの証拠が、1つの仮説に対してどう働いたか。"""

    hypothesis_id: str
    relation: str            # support / contradict / neutral
    claim: str               # 証拠の中身（1文）
    source_url: str
    tier: int                # 1=一次情報 2=業界紙 3=伝聞
    directness: float        # 0〜1。仮説にどれだけ直接効くか
    origin: str = "normal"   # normal / redteam


def load() -> dict[str, Any]:
    return yaml.safe_load(LEDGER.read_text(encoding="utf-8"))


def save(ledger: dict[str, Any]) -> None:
    LEDGER.write_text(
        yaml.safe_dump(ledger, allow_unicode=True, sort_keys=False, width=100),
        encoding="utf-8",
    )


def active(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    return [h for h in ledger["hypotheses"] if h["status"] in ("active", "review_required")]


def neutral_queries(ledger: dict[str, Any]) -> dict[str, list[str]]:
    """テーマごとの「中立な指標クエリ」。仮説文は含まない。

    これは調査側に渡してよい唯一の情報です。結論を含む語を入れないこと。
    """
    out: dict[str, list[str]] = {}
    for h in active(ledger):
        for ind in h.get("next_indicators") or []:
            out.setdefault(h["theme"], []).append(ind["neutral_query"])
    # 1テーマに指標を積みすぎると、調査が指標探しに寄ってニュースが薄くなる
    return {k: v[:3] for k, v in out.items()}


def pick_redteam(ledger: dict[str, Any], today: date) -> dict[str, Any] | None:
    """今日、反証を探しにいく仮説を1つ選ぶ。前回から最も間が空いたもの。"""
    pool = active(ledger)
    if not pool:
        return None
    return min(
        pool,
        key=lambda h: (h["ops"].get("last_redteam") or "0000-00-00", h["id"]),
    )


def redteam_query(h: dict[str, Any]) -> str:
    """崩壊条件だけを狙った検索の問いかけを作る。"""
    lines = "\n".join(f"- {f['text']}" for f in h["falsifiers"])
    return f"""次の出来事が起きたことを示す材料だけを探してください。

{lines}

これらは、ある見立てが誤りであることを示す事象です。
反対の材料（見立てを支持する材料）は不要です。返さないでください。
見つかったものについて、事実・数字・時点・出典URLを示してください。
探しても見つからなければ「該当なし」と明記してください。無理に近い話で埋めないでください。"""


def apply(
    ledger: dict[str, Any],
    verdicts: list[Verdict],
    falsifier_hits: dict[str, list[str]],
    today: date,
) -> list[dict[str, Any]]:
    """今日の判定を台帳に反映する。戻り値は変化の一覧（番組で読む用）。"""
    changes: list[dict[str, Any]] = []
    by_h: dict[str, list[Verdict]] = {}
    for v in verdicts:
        if v.relation in ("support", "contradict"):
            by_h.setdefault(v.hypothesis_id, []).append(v)

    for h in ledger["hypotheses"]:
        if h["status"] in ("archived", "graduated"):
            continue

        before = float(h["confidence"])
        lg = _logit(before)
        ops = h["ops"]
        mine = by_h.get(h["id"], [])
        hits = falsifier_hits.get(h["id"], [])

        # (a) 証拠が来ていない日数ぶん、0.5 のほうへ戻す
        last_ev = max(
            [d for d in (ops.get("last_support"), ops.get("last_contradiction")) if d],
            default=None,
        )
        idle = _days(last_ev, today)
        if idle and idle > 0 and not mine:
            lg *= (1 - SILENCE_DECAY) ** idle

        # (b) 反証が長く出ていないときは、支持の効きを半分にする
        #     （本当に強いのか、探し方が偏っているのかを機械は区別できないため）
        no_contra = _days(ops.get("last_contradiction"), today)
        discount = 0.5 if (no_contra is None or no_contra >= NO_CONTRA_DAYS) else 1.0

        for v in mine:
            sign = 1 if v.relation == "support" else -1
            step = TIER_WEIGHT.get(v.tier, 0.3) * v.directness * BASE_STEP
            if sign > 0:
                step *= discount
            lg += sign * step
            h.setdefault("evidence", []).append({
                "date": today.isoformat(),
                "relation": v.relation,
                "claim": v.claim,
                "source_url": v.source_url,
                "tier": v.tier,
                "origin": v.origin,
            })
            if sign > 0:
                ops["last_support"] = today.isoformat()
            else:
                ops["last_contradiction"] = today.isoformat()

        # (c) 崩壊条件に触れたら、大きく下げたうえで当日の番組で必ず扱う
        for _ in hits:
            lg -= 1.2
        if hits:
            h["status"] = "review_required"
            h["review_note"] = " / ".join(hits)

        # (d) 1日の変動幅と、上下の頭打ち
        lg = max(_logit(before) - DAILY_CAP, min(_logit(before) + DAILY_CAP, lg))
        lg = max(-LOGIT_LIMIT, min(LOGIT_LIMIT, lg))
        after = round(_sigmoid(lg), 3)
        h["confidence"] = after

        # (e) 状態の遷移
        moved = _transition(h, today, ledger["settings"])

        # 証拠が多すぎると読みにくいので、直近40件だけ残す
        h["evidence"] = (h.get("evidence") or [])[-40:]

        if abs(after - before) >= 0.005 or hits or moved:
            changes.append({
                "id": h["id"], "theme": h["theme"], "statement": h["statement"],
                "before": before, "after": after,
                "evidence_today": [
                    {"relation": v.relation, "claim": v.claim,
                     "source_url": v.source_url, "origin": v.origin}
                    for v in mine
                ],
                "support": sum(1 for v in mine if v.relation == "support"),
                "contradict": sum(1 for v in mine if v.relation == "contradict"),
                "falsifier_hits": hits, "status": h["status"], "moved": moved,
            })

    return changes


def _transition(h: dict[str, Any], today: date, st: dict[str, Any]) -> str | None:
    """状態を進める。戻り値は変わった場合だけその内容。"""
    idle = _days(h["ops"].get("last_support"), today)
    conf = float(h["confidence"])

    if h["status"] == "review_required":
        return None  # 人が見るまでこのまま

    if conf >= st["graduate_confidence"]:
        h.setdefault("_sustain", 0)
        h["_sustain"] += 1
        if h["_sustain"] >= st["sustain_days"]:
            h["status"] = "graduated"
            return "前提として扱う段階に上がりました"
    elif conf <= st["refute_confidence"]:
        h.setdefault("_sustain", 0)
        h["_sustain"] += 1
        if h["_sustain"] >= st["sustain_days"]:
            h["status"] = "archived"
            h["archive_reason"] = "反証された"
            return "反証されたため棚上げにしました"
    else:
        h["_sustain"] = 0

    if idle is not None:
        if idle >= st["archive_days"]:
            h["status"] = "archived"
            h["archive_reason"] = "材料が尽きた"
            return f"{idle}日支持材料が出ないため棚上げにしました"
        if idle >= st["probation_days"] and h["status"] == "active":
            h["status"] = "probation"
            return f"{idle}日支持材料が出ないため様子見に下げました"

    if h.get("horizon_end") and today.isoformat() > h["horizon_end"]:
        h["status"] = "probation"
        return "設定した検証期限を過ぎました"

    return None


def stance_word(conf: float) -> str:
    """確からしさを言葉に直す。番組で数字を読み上げないための対応表。"""
    if conf >= 0.75:
        return "支持する材料がかなり積み上がっている"
    if conf >= 0.60:
        return "支持する材料のほうが多い"
    if conf > 0.40:
        return "まだどちらとも言えない"
    if conf > 0.25:
        return "疑わしくなってきている"
    return "ほぼ否定されている"


def shift_word(before: float, after: float) -> str:
    """今日の動きを言葉に直す。"""
    d = after - before
    if d >= 0.06:
        return "今日の材料で、この見方は少し強まりました"
    if d <= -0.06:
        return "今日の材料で、この見方は弱まりました"
    if abs(d) < 0.015:
        return "今日の材料では、見方は動いていません"
    return "今日の材料での動きはごくわずかです"


def _norm_claim(s: str) -> str:
    return "".join(ch for ch in str(s) if ch.isalnum())[:40]


def agenda(ledger: dict[str, Any], changes: list[dict[str, Any]], today: date) -> list[dict[str, Any]]:
    """今日の番組で扱う論点を選ぶ。

    以前は「今日動いた仮説」からしか選ばなかったため、材料が出た仮説だけが
    毎日出てくる（＝同じ話をこすり続ける）状態になっていました。
    いまは有効な論点すべてを対象にし、直近で扱っていないものを優先します。
    材料が無い日でも1本は扱います。「今日は動きませんでした」も情報だからです。
    """
    st = ledger["settings"]
    by_id = {h["id"]: h for h in ledger["hypotheses"]}
    by_change = {c["id"]: c for c in changes}
    scored: list[tuple[float, dict[str, Any]]] = []

    for h in active(ledger):
        c = by_change.get(h["id"])
        since_air = _days(h["ops"].get("last_on_air"), today)
        never_aired = since_air is None
        if c is None:
            # 動きが無かった論点も候補にする（この場合の中身は空）
            c = {
                "id": h["id"], "theme": h["theme"], "statement": h["statement"],
                "before": float(h["confidence"]), "after": float(h["confidence"]),
                "evidence_today": [], "support": 0, "contradict": 0,
                "falsifier_hits": [], "status": h["status"], "moved": None,
            }

        # 「間が空いていること」を最優先にして、輪番に近い挙動にする
        gap = 30 if never_aired else min(since_air, 30)
        score = (
            10.0 * gap
            + 60 * (1 if c["falsifier_hits"] else 0)
            + 25 * (1 if c["moved"] else 0)
            + 18 * (1 if c["contradict"] else 0)
            + 20 * abs(c["after"] - c["before"])
            + 6 * (1 if c["support"] else 0)
        )
        # 連日同じ論点は扱わない（見立てが崩れる材料が出た日だけ例外）
        if not never_aired and since_air < st["cooldown_days"] and not c["falsifier_hits"]:
            score -= 1000
        scored.append((score, c))

    if not scored:
        return []

    scored.sort(key=lambda x: -x[0])
    chosen = [c for _, c in scored[: max(1, int(st.get("deep_dive_slots", 1)))]]

    for c in chosen:
        h = by_id[c["id"]]
        # すでに番組で話した根拠は、二度は使わない
        said = [e["claim"] for e in (h.get("evidence") or []) if e.get("aired")]
        c["already_said"] = said[-15:]
        seen = {_norm_claim(s) for s in said}
        c["evidence_today"] = [
            e for e in c.get("evidence_today", []) if _norm_claim(e.get("claim", "")) not in seen
        ]
        for e in h.get("evidence") or []:
            if e.get("date") == today.isoformat():
                e["aired"] = True

        h["ops"]["last_on_air"] = today.isoformat()
        h["ops"]["on_air_count"] = h["ops"].get("on_air_count", 0) + 1
        c["axis"] = h.get("axis", "")
        c["falsifier_texts"] = [f["text"] for f in h["falsifiers"]]
        c["decision_link"] = h.get("decision_link", "")
        c["stance"] = stance_word(float(h["confidence"]))
        c["shift"] = shift_word(c["before"], c["after"])
        c["on_air_count"] = h["ops"]["on_air_count"]

    return chosen


def mark_redteam(ledger: dict[str, Any], hid: str, today: date) -> None:
    for h in ledger["hypotheses"]:
        if h["id"] == hid:
            h["ops"]["last_redteam"] = today.isoformat()


def contains_hypothesis_text(payload: str, ledger: dict[str, Any]) -> bool:
    """調査側に渡す文字列に、仮説の文章が混ざっていないかの検査。

    探索の汚染を防ぐための安全網です。混ざっていれば実行を止めます。
    """
    for h in ledger["hypotheses"]:
        head = h["statement"].strip().replace("\n", "")[:18]
        if head and head in payload:
            return True
    return False
