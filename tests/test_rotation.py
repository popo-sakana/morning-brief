"""論点が輪番で回るか、同じ根拠を二度使わないかを確かめる。"""
import sys, copy
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path("src").resolve()))
import hypotheses as H

led = H.load()
print("論点:", [(h["id"], h.get("axis"), h["theme"]) for h in led["hypotheses"]])
assert all(h["confidence"] == 0.5 for h in led["hypotheses"]), "全件0.5から始まること"

start = date(2026, 8, 27)
aired = []
for i in range(12):
    today = start + timedelta(days=i)
    # 毎日 L-1 にだけ支持材料が出る、という最悪ケースを与える
    v = [H.Verdict("L-1", "support", f"L-1を支持する材料 {i}", "https://ex/1", 2, 0.8)]
    changes = H.apply(led, v, {}, today)
    chosen = H.agenda(led, changes, today)
    ids = [c["id"] for c in chosen]
    aired.append(ids[0] if ids else None)
    if i == 0:
        c = chosen[0]
        print("初回の中身:", {k: c[k] for k in ("id", "stance", "shift")})

print("\n放送順:", aired)
assert None not in aired, "材料が無い日でも必ず1本は扱うこと"
for a, b in zip(aired, aired[1:]):
    assert a != b, f"連日同じ論点になっている: {a}"
uniq = set(aired)
assert len(uniq) >= 5, f"論点が回っていない: {uniq}"
print("連日重複: なし / 12日で扱った論点:", len(uniq), "本")

# 同じ根拠が二度使われないか
l1 = [h for h in led["hypotheses"] if h["id"] == "L-1"][0]
claims = [e["claim"] for e in l1["evidence"] if e.get("aired")]
assert len(claims) == len(set(claims)), "同じ根拠が二重に放送済みになっている"
print("L-1 が放送で使った根拠:", len(claims), "件（重複なし）")

# 放送で数字を出さないための言葉
for p, w in [(0.9, "かなり"), (0.65, "多い"), (0.5, "どちらとも"), (0.3, "疑わしく"), (0.1, "否定")]:
    assert w in H.stance_word(p), (p, H.stance_word(p))
print("言い換え:", [H.stance_word(p) for p in (0.9, 0.65, 0.5, 0.3, 0.1)])
print("動き:", H.shift_word(0.50, 0.60), "/", H.shift_word(0.60, 0.50), "/", H.shift_word(0.50, 0.505))
print("\nすべて通りました")
