"""RSS（ポッドキャスト配信）と、台本・出典を載せたウェブページをつくる。

iPhone では、できあがった feed.xml のURLを Apple Podcasts や Overcast に登録すれば、
毎朝その日の回が自動で降ってきます。
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any

SITE = Path("site")
EPISODES_JSON = SITE / "episodes.json"


def load_episodes() -> list[dict[str, Any]]:
    if EPISODES_JSON.exists():
        return json.loads(EPISODES_JSON.read_text(encoding="utf-8"))
    return []


def save_episodes(eps: list[dict[str, Any]]) -> None:
    SITE.mkdir(parents=True, exist_ok=True)
    EPISODES_JSON.write_text(
        json.dumps(eps, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _esc(s: str) -> str:
    return html.escape(s or "", quote=True)


def build_feed(cfg: dict[str, Any], episodes: list[dict[str, Any]], base_url: str) -> str:
    p = cfg["program"]
    items = []
    for ep in episodes:
        pub = datetime.fromisoformat(ep["published_at"])
        summary = ep.get("summary", "")
        items.append(f"""    <item>
      <title>{_esc(ep['title'])}</title>
      <description><![CDATA[{summary}]]></description>
      <pubDate>{format_datetime(pub)}</pubDate>
      <guid isPermaLink="false">{_esc(ep['id'])}</guid>
      <enclosure url="{_esc(ep['audio_url'])}" length="{ep.get('bytes', 0)}" type="audio/mpeg"/>
      <itunes:duration>{int(ep.get('duration_sec', 0))}</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <link>{_esc(base_url)}/episodes/{_esc(ep['id'])}.html</link>
    </item>""")

    now = format_datetime(datetime.now(timezone.utc))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:content="http://purl.org/rss/1.0/modules/content/">
  <channel>
    <title>{_esc(p['title'])}</title>
    <link>{_esc(base_url)}/</link>
    <description>{_esc(p.get('subtitle', ''))}</description>
    <language>{_esc(p.get('language', 'ja'))}</language>
    <lastBuildDate>{now}</lastBuildDate>
    <itunes:author>{_esc(p.get('author', ''))}</itunes:author>
    <itunes:summary>{_esc(p.get('subtitle', ''))}</itunes:summary>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="News"/>
{chr(10).join(items)}
  </channel>
</rss>
"""


_PAGE_CSS = """
  body{font-family:"Meiryo UI","Hiragino Kaku Gothic ProN",sans-serif;
       max-width:760px;margin:0 auto;padding:24px 16px;line-height:1.8;
       color:#111;background:#fff}
  h1{font-size:1.3rem;border-bottom:1px solid #111;padding-bottom:8px}
  h2{font-size:1.05rem;margin-top:2.2em;border-left:4px solid #111;padding-left:8px}
  audio{width:100%;margin:16px 0}
  .turn{margin:0 0 12px}
  .who{font-weight:bold;font-size:.82rem;color:#555}
  ul{padding-left:1.2em}
  li{margin-bottom:6px;font-size:.9rem;word-break:break-all}
  a{color:#111}
  .meta{font-size:.82rem;color:#555}
  table{border-collapse:collapse;width:100%;font-size:.9rem;margin:12px 0}
  .wish{background:#f4f4f4;padding:10px;font-size:.85rem;white-space:pre-wrap;
        font-family:inherit;border:1px solid #111}
  td,th{border:1px solid #111;padding:6px}
"""


def build_episode_page(
    cfg: dict[str, Any], ep: dict[str, Any], script: dict[str, Any]
) -> str:
    hosts = cfg["hosts"]
    seg_titles = {s["id"]: s["title"] for s in cfg["segments"]}
    seg_titles["opening"] = "オープニング"
    seg_titles["verification"] = "きょうの検証"
    seg_titles["requested"] = "指定された論点"
    seg_titles["discussion"] = (cfg.get("discussion") or {}).get("title", "きょうの論点")
    seg_titles["closing"] = "クロージング"

    def turns_html(turns: list[dict[str, str]]) -> str:
        rows = []
        for t in turns:
            who = hosts.get(t.get("speaker", ""), {}).get("label", t.get("speaker", ""))
            rows.append(
                f'<p class="turn"><span class="who">{_esc(who)}</span><br>{_esc(t.get("text", ""))}</p>'
            )
        return "\n".join(rows)

    body = [f'<h2>{_esc(seg_titles["opening"])}</h2>', turns_html(script.get("opening", []))]

    ver = script.get("verification") or {}
    if ver:
        body.append(f'<h2>{_esc(ver.get("title", "きょうの検証"))}</h2>')
        # 仮説の動きを表で添える（耳で聞いたあと、目で確かめられるように）
        rows = []
        for c in script.get("hypothesis_checks", []) or []:
            hits = "／".join(c.get("falsifier_hits", []))
            rows.append(
                f'<tr><td>{_esc(c["id"])}</td>'
                f'<td>{_esc(c["statement"].strip())}</td>'
                f'<td class="num">{c["before"]:.2f} → {c["after"]:.2f}</td>'
                f'<td>支持{c.get("support", 0)} / 反証{c.get("contradict", 0)}'
                + (f'<br>崩壊条件に接触: {_esc(hits)}' if hits else '') + '</td></tr>'
            )
        if rows:
            body.append(
                '<table><tr><th>ID</th><th>見立て</th><th>確からしさ</th><th>今日の材料</th></tr>'
                + "".join(rows) + '</table>'
            )
        body.append(turns_html(ver.get("turns", [])))

    for seg in script.get("segments", []):
        body.append(f'<h2>{_esc(seg.get("title") or seg_titles.get(seg.get("id"), ""))}</h2>')
        body.append(turns_html(seg.get("turns", [])))
    disc = script.get("discussion") or {}
    if disc:
        body.append(f'<h2>{_esc(disc.get("title", seg_titles["discussion"]))}</h2>')
        body.append(turns_html(disc.get("turns", [])))
    body.append(f'<h2>{_esc(seg_titles["closing"])}</h2>')
    body.append(turns_html(script.get("closing", [])))

    seen, src_rows = set(), []
    for s in script.get("sources", []):
        if s["url"] in seen:
            continue
        seen.add(s["url"])
        src_rows.append(
            f'<li>[{_esc(s.get("segment", ""))}] '
            f'<a href="{_esc(s["url"])}" target="_blank" rel="noopener">{_esc(s.get("title") or s["url"])}</a></li>'
        )

    wishes = script.get("requests") or ""
    if wishes:
        body.append('<h2>この回への注文</h2><pre class="wish">' + _esc(wishes) + '</pre>')

    marks = ep.get("chapters", [])
    mark_rows = "".join(
        f'<tr><td>{_esc(seg_titles.get(m["section"], m["section"]))}</td>'
        f'<td>{int(m["start_sec"] // 60):02d}:{int(m["start_sec"] % 60):02d}</td></tr>'
        for m in marks
    )

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(ep['title'])}</title>
<style>{_PAGE_CSS}</style></head>
<body>
<h1>{_esc(ep['title'])}</h1>
<p class="meta">収録 {_esc(ep['published_at'][:16].replace('T', ' '))} ／ 約{int(ep.get('duration_sec', 0) // 60)}分</p>
{f'<audio controls preload="none" src="{_esc(ep["audio_url"])}"></audio>' if ep.get('audio_url') else '<p class="meta">（この回は台本のみです。音声はまだ作られていません）</p>'}
<h2>目次</h2>
<table><tr><th>コーナー</th><th>開始</th></tr>{mark_rows}</table>
{chr(10).join(body)}
<h2>出典</h2>
<ul>{''.join(src_rows) or '<li>出典が取得できませんでした</li>'}</ul>
<p class="meta"><a href="../index.html">← 一覧に戻る</a></p>
</body></html>
"""


def build_index(cfg: dict[str, Any], episodes: list[dict[str, Any]], base_url: str) -> str:
    p = cfg["program"]
    rows = "".join(
        f'<li><a href="episodes/{_esc(e["id"])}.html">{_esc(e["title"])}</a> '
        f'<span class="meta">約{int(e.get("duration_sec", 0) // 60)}分</span></li>'
        for e in episodes
    )
    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(p['title'])}</title>
<style>{_PAGE_CSS}</style></head>
<body>
<h1>{_esc(p['title'])}</h1>
<p>{_esc(p.get('subtitle', ''))}</p>
<h2>ポッドキャストとして聞く</h2>
<p class="meta">下のURLを Apple Podcasts などの「URLから番組を追加」に貼り付けてください。</p>
<p><code>{_esc(base_url)}/feed.xml</code></p>
<h2>これまでの回</h2>
<ul>{rows}</ul>
</body></html>
"""


def publish(
    cfg: dict[str, Any],
    script: dict[str, Any],
    episode: dict[str, Any],
    base_url: str,
    keep: int = 30,
) -> None:
    episodes = [e for e in load_episodes() if e["id"] != episode["id"]]
    episodes.insert(0, episode)
    episodes = episodes[:keep]
    save_episodes(episodes)

    (SITE / "episodes").mkdir(parents=True, exist_ok=True)
    (SITE / "episodes" / f"{episode['id']}.html").write_text(
        build_episode_page(cfg, episode, script), encoding="utf-8"
    )
    (SITE / "feed.xml").write_text(build_feed(cfg, episodes, base_url), encoding="utf-8")
    (SITE / "index.html").write_text(build_index(cfg, episodes, base_url), encoding="utf-8")
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    print(f"[publish] {episode['id']} を公開設定に追加しました（保持{len(episodes)}回分）")
