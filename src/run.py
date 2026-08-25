"""毎朝の1本を、調査→台本→音声→配信 の順に作る入口。

使い方:
  python src/run.py                 通常実行（1本つくる）
  python src/run.py --dry-run       調査と台本だけ（音声を作らない＝費用も時間も小さい）
  python src/run.py --script-only   すでにある台本JSONから音声だけ作り直す
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from deps import ensure  # noqa: E402

ensure()

import yaml  # noqa: E402

import curator  # noqa: E402
import hypotheses as hyp_mod  # noqa: E402
import judge  # noqa: E402
import stock  # noqa: E402
import requests_inbox  # noqa: E402
import research as research_mod  # noqa: E402
import script_writer  # noqa: E402
from audio import build_episode, duration_seconds  # noqa: E402
from publish import publish  # noqa: E402
from tts import get_engine  # noqa: E402

JST = timezone(timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent


def load_config(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def next_version(out_dir: Path, stem_prefix: str) -> int:
    """同じ日に作り直した場合、上書きせず v2, v3 … と増やす。"""
    v = 1
    while (out_dir / f"{stem_prefix}_v{v}_AI.mp3").exists():
        v += 1
    return v


def _save_facts(cards: list[dict], when: datetime) -> None:
    """抜き出した事実を、日付つきで表に積んでいく。

    台本は要約なので、あとから「あの数字なんだったか」を掘り返すときは
    こちらのほうが役に立ちます。CSVにしておくと Numbers でそのまま開けます。
    """
    import csv

    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)

    (data_dir / f"{when:%y%m%d}_facts.json").write_text(
        json.dumps(cards, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    csv_path = data_dir / "facts.csv"
    is_new = not csv_path.exists()
    with csv_path.open("a", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        if is_new:
            w.writerow(["取得日", "事実の時点", "確度", "内容", "出典URL"])
        for c in cards:
            tier = {1: "一次情報", 2: "報道", 3: "伝聞・意見"}.get(int(c.get("tier", 3)), "")
            w.writerow([
                f"{when:%Y-%m-%d}", c.get("date", ""), tier,
                c.get("claim", ""), c.get("source_url", ""),
            ])
    print(f"[run] 事実 {len(cards)}件を data/facts.csv に積みました")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--dry-run", action="store_true", help="音声を作らず台本まで")
    ap.add_argument("--script-only", metavar="JSON", help="既存の台本JSONから音声を作り直す")
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", ""))
    args = ap.parse_args()

    cfg = load_config(Path(args.config))
    now = datetime.now(JST)
    date_label = f"{now.year}年{now.month}月{now.day}日"
    stem_prefix = f"{now:%y%m%d}_morningbrief"
    out_dir = ROOT / "out"
    out_dir.mkdir(exist_ok=True)

    # --- 1. 調査 と 2. 台本 -------------------------------------------------
    if args.script_only:
        script = json.loads(Path(args.script_only).read_text(encoding="utf-8"))
        print(f"[run] 既存の台本を読み込みました: {args.script_only}")
    else:
        # popo が requests/next.md に書き置きした指定を拾う
        wishes = requests_inbox.load()
        if wishes:
            print(f"[run] 指定を受け取りました:\n{wishes}\n")

        # 仮説台帳を読む。ただし調査に渡すのは「中立な指標クエリ」だけで、
        # 仮説の文章そのものは絶対に渡さない（探索が仮説に引っぱられるのを防ぐ）
        ledger = hyp_mod.load() if cfg.get("hypothesis", {}).get("enabled", True) else None
        redteam = hyp_mod.pick_redteam(ledger, now.date()) if ledger else None

        results = research_mod.run_all(
            cfg,
            extra_query=wishes,
            indicators=hyp_mod.neutral_queries(ledger) if ledger else None,
            redteam=(redteam["id"], hyp_mod.redteam_query(redteam)) if redteam else None,
            ledger=ledger,
        )
        if not any(r.ok for r in results):
            print("[run] すべてのテーマで調査に失敗したため、中止します", file=sys.stderr)
            return 1

        # --- 仮説の照合。ここで初めて仮説文を見る ---------------------------
        checks: list = []
        if ledger:
            model = cfg.get("script", {}).get("model", "claude-sonnet-5")
            try:
                cards = judge.extract_cards(model, results)
                # 反証専用の調査から来たカードには印をつけ、割り引かずに効かせる
                for c in cards:
                    c.setdefault("origin", "normal")
                print(f"[hyp] 事実カード {len(cards)}枚を抽出しました")
                _save_facts(cards, now)

                verdicts, hits = judge.map_to_hypotheses(
                    model, cards, hyp_mod.active(ledger)
                )
                print(f"[hyp] 判定: 支持{sum(1 for v in verdicts if v.relation=='support')}件 / "
                      f"反証{sum(1 for v in verdicts if v.relation=='contradict')}件 / "
                      f"崩壊条件ヒット{sum(len(v) for v in hits.values())}件")

                changes = hyp_mod.apply(ledger, verdicts, hits, now.date())
                checks = hyp_mod.agenda(ledger, changes, now.date())
                if redteam:
                    hyp_mod.mark_redteam(ledger, redteam["id"], now.date())
                hyp_mod.save(ledger)

                for c in changes:
                    arrow = "→" if c["after"] != c["before"] else "＝"
                    print(f"[hyp] {c['id']} {c['before']:.2f} {arrow} {c['after']:.2f}"
                          + (f"  {c['moved']}" if c["moved"] else ""))
            except Exception as exc:  # noqa: BLE001
                # 仮説の照合に失敗しても、番組そのものは作る
                print(f"[hyp] 照合に失敗しました（番組は通常どおり作ります）: {exc}",
                      file=sys.stderr)

        # 調査結果そのものを残しておく。台本は要約なので、
        # あとから掘り下げるときの材料は、こちらの生データのほうが役に立つ。
        data_dir = ROOT / "data"
        data_dir.mkdir(exist_ok=True)
        research_path = data_dir / f"{now:%y%m%d}_research.json"
        research_path.write_text(
            json.dumps(
                {
                    "date": now.isoformat(timespec="seconds"),
                    "requests": wishes,
                    "segments": [
                        {
                            "id": r.segment_id,
                            "title": r.title,
                            "body": r.body,
                            "sources": r.sources,
                            "cost_usd": r.cost_usd,
                            "error": r.error,
                        }
                        for r in results
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        total_cost = sum(r.cost_usd for r in results)
        print(f"[run] 調査結果を保存しました: {research_path.name} "
              f"（${total_cost:.3f} 相当）")

        # --- ニュースを1件ずつに切り分け、在庫に足して、今日の分を選ぶ -------
        seg_titles = {s["id"]: s["title"] for s in cfg["segments"]}
        inv = stock.load()
        stock.prune(inv, now.date())
        model = cfg.get("script", {}).get("model", "claude-sonnet-5")

        items = curator.extract_items(
            model, results, stock.recent_headlines(inv, now.date()), seg_titles
        )
        added = stock.add(inv, items, now.date())
        print(f"[stock] 新しく {added}件を登録（切り出し {len(items)}件）／ "
              f"{stock.summary(inv, now.date())}")

        picks: dict[str, dict[str, list]] = {}
        aired: list[dict] = []
        for seg in cfg["segments"]:
            want = int(seg.get("items", max(3, round(seg["minutes"]))))
            fresh, stocked = stock.select(inv, seg["id"], want, now.date())
            picks[seg["id"]] = {"fresh": fresh, "stock": stocked}
            aired.extend(fresh + stocked)
            note = "（材料なし）" if not fresh and not stocked else ""
            print(f"[stock] {seg['title']}: 新着{len(fresh)}件 + 在庫{len(stocked)}件 {note}")

        requested = next((r for r in results if r.segment_id == "requested"), None)

        script = script_writer.write_script(
            cfg, date_label, picks,
            requested=requested, wishes=wishes, checks=checks,
        )
        script["requests"] = wishes
        script["hypothesis_checks"] = checks
        script["aired_items"] = [
            {"id": i["id"], "theme": i["theme"], "headline": i["headline"],
             "source_url": i.get("source_url", ""), "source_title": i.get("source_title", ""),
             "from_stock": i["first_seen"] != now.date().isoformat()}
            for i in aired
        ]
        # 出典は在庫から引き継ぐ（台本には書かせない＝捏造を防ぐ）
        script["sources"] = [
            {"segment": seg_titles.get(i["theme"], i["theme"]),
             "title": i.get("source_title") or i["headline"], "url": i.get("source_url", "")}
            for i in aired if i.get("source_url")
        ]

        stock.mark_aired(inv, aired, now.date())
        stock.save(inv)
        requests_inbox.archive(wishes, now)
        script_path = out_dir / f"{stem_prefix}_script.json"
        script_path.write_text(
            json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[run] 台本を保存しました: {script_path}")

    turns = script_writer.iter_turns(script)
    chars = sum(len(t[2]) for t in turns)
    cpm = cfg["program"].get("chars_per_minute", 320)
    print(f"[run] 台本 {len(turns)}発言 / {chars}文字 / 想定 約{chars / cpm:.1f}分")

    if args.dry_run:
        print("[run] --dry-run のため、ここで終了します")
        return 0

    # --- 3. 音声 ------------------------------------------------------------
    engine = get_engine(cfg)
    engine.check()

    version = next_version(out_dir, stem_prefix)
    audio_path = out_dir / f"{stem_prefix}_v{version}_AI.mp3"
    audio_path, chapters = build_episode(turns, engine, cfg, audio_path)
    dur = duration_seconds(audio_path)
    size = audio_path.stat().st_size
    print(f"[run] 音声を書き出しました: {audio_path.name} / {dur / 60:.1f}分 / {size / 1e6:.1f}MB")

    # 音声さえできていれば、後続の工程は動かせる。
    # BASE_URL の設定漏れで音声の公開まで止まってしまわないよう、ここで先に伝える。
    if gh_out := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"audio_path={audio_path}\n")
            fh.write(f"episode_id={audio_path.stem}\n")

    # --- 4. 配信用ファイル --------------------------------------------------
    base_url = args.base_url.rstrip("/")
    if not base_url:
        print("[run] BASE_URL が未設定のため、配信ファイルの作成は省略しました。"
              "音声はできているので、リポジトリの Settings > Secrets and variables > "
              "Actions > Variables に BASE_URL を登録して、もう一度実行してください。")
        return 0

    highlights = script.get("highlights") or []
    summary = "<br>".join(f"・{h}" for h in highlights)

    # 音声ファイルは GitHub のリリース置き場に上げる。
    # こうするとリポジトリ本体が毎日14MBずつ太っていくのを避けられる。
    episode_id = audio_path.stem
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        audio_url = f"https://github.com/{repo}/releases/download/{episode_id}/{audio_path.name}"
    else:
        audio_url = f"{base_url}/audio/{audio_path.name}"

    episode = {
        "id": episode_id,
        "title": f"{now.month}月{now.day}日のブリーフ",
        "published_at": now.isoformat(timespec="seconds"),
        "audio_url": audio_url,
        "duration_sec": round(dur),
        "bytes": size,
        "summary": summary,
        "chapters": chapters,
    }

    publish(cfg, script, episode, base_url)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        raise SystemExit(1)
