"""試作回の台本を、読みやすい1枚のページに書き出す（Artifact 公開用）。"""

from __future__ import annotations

import html
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import yaml  # noqa: E402

cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
script = json.loads((ROOT / "out/260824_morningbrief_script.json").read_text(encoding="utf-8"))
CPM = cfg["program"]["chars_per_minute"]
HOSTS = cfg["hosts"]


def e(s: str) -> str:
    return html.escape(s or "")


def mmss(sec: float) -> str:
    return f"{int(sec // 60):02d}:{int(sec % 60):02d}"


# --- 台本を、コーナー単位に組み直しながら時刻を積む ---------------------------
sections: list[dict] = []
clock = 0.0


def add(section_id: str, title: str, turns: list[dict]) -> None:
    global clock
    start = clock
    rows = []
    for t in turns:
        who = HOSTS.get(t["speaker"], {}).get("label", t["speaker"])
        rows.append({"at": mmss(clock), "who": who, "key": t["speaker"], "text": t["text"]})
        clock += len(t["text"]) / CPM * 60 + 0.4
    sections.append({
        "id": section_id, "title": title, "start": mmss(start),
        "minutes": round((clock - start) / 60, 1), "rows": rows,
    })


add("opening", "オープニング", script["opening"])
for seg in script["segments"]:
    add(seg["id"], seg["title"], seg["turns"])
add("discussion", script["discussion"]["title"], script["discussion"]["turns"])
add("closing", "クロージング", script["closing"])

total = mmss(clock)

# --- 部品 --------------------------------------------------------------------
rundown = "\n".join(
    f'<tr><td class="num">{e(s["start"])}</td>'
    f'<td><a href="#{e(s["id"])}">{e(s["title"])}</a></td>'
    f'<td class="num">{s["minutes"]:.1f}分</td></tr>'
    for s in sections
)

body = []
for s in sections:
    turns_html = "\n".join(
        f'<div class="turn {"nav" if r["key"] == "navigator" else "ana"}">'
        f'<div class="cue"><span class="at">{e(r["at"])}</span>'
        f'<span class="who">{e(r["who"])}</span></div>'
        f'<p>{e(r["text"])}</p></div>'
        for r in s["rows"]
    )
    body.append(
        f'<section id="{e(s["id"])}">'
        f'<h2><span class="mark">{e(s["start"])}</span>{e(s["title"])}</h2>'
        f'{turns_html}</section>'
    )

by_seg: dict[str, list[dict]] = {}
for src in script["sources"]:
    by_seg.setdefault(src["segment"], []).append(src)

src_html = "\n".join(
    f'<div class="srcgroup"><h3>{e(seg)}</h3><ol>'
    + "".join(
        f'<li><a href="{e(x["url"])}" target="_blank" rel="noopener">{e(x["title"])}</a></li>'
        for x in items
    )
    + "</ol></div>"
    for seg, items in by_seg.items()
)

hi = "".join(f"<li>{e(h)}</li>" for h in script["highlights"])

PAGE = f"""<title>朝のニュースブリーフ 試作回</title>
<style>
  :root {{
    --ground:#ffffff; --ink:#111111; --ink-2:#585858; --ink-3:#8a8a8a;
    --rule:#111111; --rule-faint:#d6d6d6; --surface:#f4f4f4;
    --max:44rem;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:not([data-theme="light"]) {{
      --ground:#101010; --ink:#ededed; --ink-2:#a9a9a9; --ink-3:#7c7c7c;
      --rule:#ededed; --rule-faint:#333333; --surface:#1b1b1b;
    }}
  }}
  :root[data-theme="dark"] {{
    --ground:#101010; --ink:#ededed; --ink-2:#a9a9a9; --ink-3:#7c7c7c;
    --rule:#ededed; --rule-faint:#333333; --surface:#1b1b1b;
  }}

  * {{ box-sizing:border-box; }}
  body {{
    background:var(--ground); color:var(--ink);
    font-family:"Meiryo UI","Meiryo","Hiragino Kaku Gothic ProN","Yu Gothic UI",
                system-ui,sans-serif;
    font-size:16px; line-height:1.9; margin:0;
    -webkit-font-smoothing:antialiased;
  }}
  .wrap {{ max-width:var(--max); margin:0 auto; padding:40px 20px 96px; }}
  .num {{ font-variant-numeric:tabular-nums;
         font-family:ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace; }}
  a {{ color:var(--ink); text-underline-offset:3px; }}
  a:focus-visible {{ outline:2px solid var(--ink); outline-offset:2px; }}

  header {{ border-bottom:2px solid var(--rule); padding-bottom:20px; }}
  .eyebrow {{ font-size:.72rem; letter-spacing:.18em; color:var(--ink-2);
              margin:0 0 10px; }}
  h1 {{ font-size:1.6rem; line-height:1.45; margin:0 0 14px; text-wrap:balance;
        font-weight:700; }}
  .facts {{ display:flex; flex-wrap:wrap; gap:6px 22px; margin:0;
            font-size:.82rem; color:var(--ink-2); }}

  .note {{ border:1px solid var(--rule-faint); background:var(--surface);
           padding:14px 16px; margin:26px 0 0; font-size:.86rem;
           line-height:1.8; color:var(--ink-2); }}
  .note strong {{ color:var(--ink); }}

  h2.block {{ font-size:.78rem; letter-spacing:.18em; color:var(--ink-2);
              border-bottom:1px solid var(--rule-faint); padding-bottom:8px;
              margin:56px 0 18px; font-weight:700; }}

  ul.hi {{ padding-left:1.15em; margin:0; }}
  ul.hi li {{ margin-bottom:8px; }}

  .scroll {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; font-size:.9rem; }}
  th, td {{ text-align:left; padding:9px 12px 9px 0;
            border-bottom:1px solid var(--rule-faint); }}
  th {{ font-size:.72rem; letter-spacing:.12em; color:var(--ink-3);
        font-weight:700; border-bottom:1px solid var(--rule); }}
  td.num, th.num {{ width:5.5em; color:var(--ink-2); }}
  tr td:last-child, tr th:last-child {{ text-align:right; padding-right:0; }}

  section {{ margin-top:52px; scroll-margin-top:16px; }}
  section h2 {{ font-size:1.12rem; font-weight:700; margin:0 0 22px;
                padding-bottom:10px; border-bottom:2px solid var(--rule);
                display:flex; align-items:baseline; gap:14px; }}
  .mark {{ font-size:.78rem; color:var(--ink-2); font-weight:400;
           font-variant-numeric:tabular-nums;
           font-family:ui-monospace,Menlo,Consolas,monospace; }}

  .turn {{ display:grid; grid-template-columns:6.6rem 1fr; gap:0 18px;
           margin-bottom:20px; }}
  .cue {{ display:flex; flex-direction:column; gap:2px; padding-top:.35em; }}
  .at {{ font-size:.72rem; color:var(--ink-3); font-variant-numeric:tabular-nums;
         font-family:ui-monospace,Menlo,Consolas,monospace; }}
  .who {{ font-size:.76rem; color:var(--ink-2); }}
  .turn.ana .who {{ color:var(--ink); font-weight:700; }}
  .turn p {{ margin:0; }}
  .turn.nav p {{ color:var(--ink-2); }}

  .srcgroup {{ margin-bottom:26px; }}
  .srcgroup h3 {{ font-size:.82rem; margin:0 0 8px; font-weight:700; }}
  .srcgroup ol {{ margin:0; padding-left:1.5em; }}
  .srcgroup li {{ font-size:.82rem; line-height:1.7; margin-bottom:6px;
                  color:var(--ink-2); word-break:break-word; }}

  footer {{ margin-top:64px; padding-top:18px;
            border-top:1px solid var(--rule-faint);
            font-size:.78rem; color:var(--ink-3); }}

  @media (max-width:560px) {{
    .wrap {{ padding:28px 16px 72px; }}
    .turn {{ grid-template-columns:1fr; gap:2px; }}
    .cue {{ flex-direction:row; gap:10px; align-items:baseline; padding-top:0; }}
  }}
</style>

<div class="wrap">
<header>
  <p class="eyebrow">試作回 ／ 音声はまだありません</p>
  <h1>{e(cfg['program']['title'])}<br>2026年8月24日</h1>
  <p class="facts">
    <span class="num">全{total}</span>
    <span>{len(sections)}コーナー</span>
    <span class="num">{sum(len(r['rows']) for r in sections)}発言</span>
    <span class="num">{sum(len(t['text']) for s in sections for t in s['rows'])}文字</span>
    <span>出典 {len(script['sources'])}件</span>
  </p>
</header>

<div class="note">
  <strong>この回について</strong><br>
  仕組みが毎朝つくるものと同じ形式・同じ制約で書いた台本です。時刻は1分あたり{CPM}文字で計算した見込み値で、
  実際の音声の長さとは前後します。数字は出典に当たって書いていますが、要約と翻訳の過程での取り違えは起こりえます。
  仕事に使う数字は、末尾の出典で裏を取ってください。
</div>

<h2 class="block">きょうの要点</h2>
<ul class="hi">{hi}</ul>

<h2 class="block">進行表</h2>
<div class="scroll">
<table>
  <thead><tr><th class="num">開始</th><th>コーナー</th><th class="num">尺</th></tr></thead>
  <tbody>
{rundown}
  </tbody>
</table>
</div>

{chr(10).join(body)}

<h2 class="block">出典</h2>
{src_html}

<footer>
  台本は自動生成、出典は調査時に取得したURLをそのまま並べています。
  有料記事の本文にあたる数値は「確認できず」と明記しました。
</footer>
</div>
"""

out = ROOT / "site" / "preview.html"
out.write_text(PAGE, encoding="utf-8")
print(f"書き出しました: {out} ({out.stat().st_size} bytes) / 全{total}")
