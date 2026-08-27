"""仮説まわりの検証。APIキーなしで動きます。

確かめること:
  1. 調査の問いかけに仮説の文章が混ざっていないか（探索の汚染防止）
  2. 反証を探す係が、崩壊条件だけを狙っているか
  3. 支持証拠だけを与え続けても、確からしさが際限なく上がらないか
  4. 反証が出ていない期間が続くと、支持の効きが半分になるか
  5. 証拠が来ない日は、確からしさが 0.5 のほうへ戻るか
  6. 崩壊条件に触れたら、その日の番組で必ず扱われるか
  7. 支持が長く出なければ、様子見 → 棚上げ と自動で降格するか
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

import hypotheses as H  # noqa: E402


def fresh():
    return yaml.safe_load((ROOT / "hypotheses" / "ledger.yaml").read_text(encoding="utf-8"))


def ev(hid, rel, tier=1, direct=1.0, origin="normal"):
    return H.Verdict(hid, rel, f"{rel}の証拠", "https://example.com", tier, direct, origin)


def test_search_blindness():
    led = fresh()
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))

    # 通常の4テーマ＋指標クエリに、仮説の主張が混ざっていないこと
    payload = "\n".join(s["query"] for s in cfg["segments"])
    for qs in H.neutral_queries(led).values():
        payload += "\n" + "\n".join(qs)
    assert not H.contains_hypothesis_text(payload, led), "調査の問いかけに仮説文が混入している"

    # わざと混ぜたら検知できること
    bad = payload + "\n" + led["hypotheses"][0]["statement"]
    assert H.contains_hypothesis_text(bad, led), "混入を検知できていない"
    print("1. 探索の汚染防止           OK")


def test_redteam():
    led = fresh()
    h = H.pick_redteam(led, date(2026, 8, 25))
    q = H.redteam_query(h)
    assert "反対の材料" in q and "返さないでください" in q
    for f in h["falsifiers"]:
        assert f["text"] in q, "崩壊条件が問いかけに入っていない"
    assert h["statement"].strip()[:18] not in q, "反証さがしに仮説の主張が漏れている"
    print(f"2. 反証さがし（{h['id']}）      OK")


def test_no_runaway_confidence():
    led = fresh()
    today = date(2026, 8, 25)
    hid = "L-1"
    conf = []
    for i in range(30):
        d = today + timedelta(days=i)
        H.apply(led, [ev(hid, "support"), ev(hid, "support"), ev(hid, "support")], {}, d)
        conf.append(next(h["confidence"] for h in led["hypotheses"] if h["id"] == hid))
    assert max(conf) <= 0.95, f"確からしさが上限を超えた: {max(conf)}"
    assert conf[-1] < 0.96
    print(f"3. 支持だけ30日 → {conf[0]:.2f} から {conf[-1]:.2f} で頭打ち  OK")


def test_support_discounted_without_contradiction():
    led_a, led_b = fresh(), fresh()
    d = date(2026, 8, 25)
    hid = "L-2"

    # A: 反証が一度も出ていない → 支持は半分の効き
    H.apply(led_a, [ev(hid, "support")], {}, d)
    a = next(h["confidence"] for h in led_a["hypotheses"] if h["id"] == hid)

    # B: 前日に反証が出ていた → 支持がそのまま効く
    for h in led_b["hypotheses"]:
        if h["id"] == hid:
            h["ops"]["last_contradiction"] = (d - timedelta(days=1)).isoformat()
    H.apply(led_b, [ev(hid, "support")], {}, d)
    b = next(h["confidence"] for h in led_b["hypotheses"] if h["id"] == hid)

    assert a < b, f"反証ゼロ期間の割引が効いていない（{a} vs {b}）"
    print(f"4. 反証が出ていない期間は支持を割引  {a:.3f} < {b:.3f}  OK")


def test_silence_decays():
    led = fresh()
    hid = "L-3"
    d0 = date(2026, 8, 25)
    H.apply(led, [ev(hid, "support"), ev(hid, "support")], {}, d0)
    high = next(h["confidence"] for h in led["hypotheses"] if h["id"] == hid)

    # 以後14日、何の証拠も来ない
    for i in range(1, 15):
        H.apply(led, [], {}, d0 + timedelta(days=i))
    later = next(h["confidence"] for h in led["hypotheses"] if h["id"] == hid)

    assert later < high, "証拠が来なくても確からしさが下がっていない"
    assert abs(later - 0.5) < abs(high - 0.5), "0.5 のほうへ戻っていない"
    print(f"5. 沈黙14日で {high:.3f} → {later:.3f}（0.5へ後退）  OK")


def test_falsifier_forces_on_air():
    led = fresh()
    d = date(2026, 8, 25)
    hid = "L-4"
    before = next(h["confidence"] for h in led["hypotheses"] if h["id"] == hid)

    # ほかの仮説には強い支持を与え、スコア上は上位に来るようにしておく
    verdicts = [ev("L-1", "support"), ev("L-2", "support"), ev("L-3", "support")]
    changes = H.apply(led, verdicts, {hid: ["幼児期AIが有効という査読研究が2件出た"]}, d)
    agenda = H.agenda(led, changes, d)

    after = next(h["confidence"] for h in led["hypotheses"] if h["id"] == hid)
    status = next(h["status"] for h in led["hypotheses"] if h["id"] == hid)
    ids = [c["id"] for c in agenda]

    assert after < before, "崩壊条件に触れたのに下がっていない"
    assert status == "active", f"観測に当たっただけで状態が変わっている: {status}"
    assert hid in ids, f"崩壊条件に触れた仮説が番組に入っていない: {ids}"
    assert agenda[0]["id"] == hid, "崩壊条件に触れた仮説が最優先になっていない"
    assert agenda[0]["falsifier_texts"], "崩壊条件が台本に渡っていない"
    assert after >= 0.32, f"観測1件で落としすぎ（大きな論点には強すぎる）: {after}"
    assert next(h["status"] for h in led["hypotheses"] if h["id"] == hid) == "active", \
        "観測に当たっただけで状態を凍らせている"
    print(f"6. 観測ヒットで {before:.2f} → {after:.2f}（凍結せず・当日必修）  OK")


def test_demotion():
    led = fresh()
    hid = "L-1"
    d0 = date(2026, 8, 25)
    H.apply(led, [ev(hid, "support")], {}, d0)

    seen = {}
    for i in range(1, 75):
        d = d0 + timedelta(days=i)
        H.apply(led, [], {}, d)
        st = next(h["status"] for h in led["hypotheses"] if h["id"] == hid)
        seen.setdefault(st, i)

    assert "probation" in seen, "様子見に降格していない"
    assert "archived" in seen, "棚上げになっていない"
    assert 29 <= seen["probation"] <= 31, f"降格が想定日数とずれている: {seen['probation']}日目"
    assert 59 <= seen["archived"] <= 61, f"棚上げが想定日数とずれている: {seen['archived']}日目"
    print(f"7. 支持なしで {seen['probation']}日目に様子見 / {seen['archived']}日目に棚上げ  OK")


def test_cooldown():
    led = fresh()
    d0 = date(2026, 8, 25)
    picks = []
    for i in range(8):
        d = d0 + timedelta(days=i)
        ch = H.apply(led, [ev(h["id"], "support") for h in H.active(led)], {}, d)
        picks.append([c["id"] for c in H.agenda(led, ch, d)])
    # 連日同じ論点を扱わないこと（以前は3日連続でなければ可としていた）
    for i in range(len(picks) - 1):
        both = set(picks[i]) & set(picks[i + 1])
        assert not both, f"同じ論点が連日扱われている: {both}"
    flat = [x for p in picks for x in p]
    assert len(set(flat)) >= 5, f"論点が回っていない: {set(flat)}"
    print(f"8. 論点の持ち回り  {picks}  OK")


if __name__ == "__main__":
    test_search_blindness()
    test_redteam()
    test_no_runaway_confidence()
    test_support_discounted_without_contradiction()
    test_silence_decays()
    test_falsifier_forces_on_air()
    test_demotion()
    test_cooldown()
    print("\nすべて通りました")
