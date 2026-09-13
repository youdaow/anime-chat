#!/usr/bin/env python
"""按 assets/stickers/manifest.json 生成整套内置表情包 PNG。

manifest 是唯一事实源：加一条 sticker 再跑本脚本即可，代码里不复制任何标签。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from animechat.chibi import draw_sticker  # noqa: E402
from animechat.config import BUILTIN_STICKER_DIR, STICKER_MANIFEST  # noqa: E402


def main() -> int:
    if not STICKER_MANIFEST.is_file():
        print("找不到 manifest：" + str(STICKER_MANIFEST))
        return 1
    data = json.loads(STICKER_MANIFEST.read_text(encoding="utf-8"))
    palettes = data.get("palettes") or {}
    canvas = data.get("canvas") or {}
    size = int(canvas.get("size") or 512)
    radius = float(canvas.get("radius") or 96) / size
    BUILTIN_STICKER_DIR.mkdir(parents=True, exist_ok=True)

    made, failed = 0, []
    for spec in data.get("stickers") or []:
        sid = str(spec.get("id") or "").strip()
        if not sid:
            continue
        try:
            img = draw_sticker(spec, palettes, size=size, radius=radius)
            img.save(BUILTIN_STICKER_DIR / (sid + ".png"), format="PNG", optimize=True)
            made += 1
        except Exception as exc:  # 单张失败不该拖垮整套
            failed.append(sid + ": " + str(exc))
    print("已生成 " + str(made) + " 张表情 → " + str(BUILTIN_STICKER_DIR))
    for line in failed:
        print("  [失败] " + line)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
