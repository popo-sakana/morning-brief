"""必要な部品がそろっているかを、分かりやすいことばで確かめる。"""

from __future__ import annotations

import sys

NEEDED = [
    ("yaml", "PyYAML", "設定ファイルを読むため"),
    ("requests", "requests", "各社のAPIを呼ぶため"),
]


def ensure() -> None:
    missing = []
    for module, package, why in NEEDED:
        try:
            __import__(module)
        except ImportError:
            missing.append((package, why))

    if not missing:
        return

    names = " ".join(p for p, _ in missing)
    print("\n必要な部品が入っていません。\n", file=sys.stderr)
    for package, why in missing:
        print(f"  {package}  … {why}", file=sys.stderr)
    print("\n次のコマンドで入ります（1回だけでOKです）:\n", file=sys.stderr)
    print(f"  python3 -m pip install --user {names}\n", file=sys.stderr)
    if sys.version_info < (3, 10):
        print(f"（いま使っている Python は {sys.version_info.major}.{sys.version_info.minor} です。"
              "3.9 以上なら動きます）\n", file=sys.stderr)
    raise SystemExit(1)
