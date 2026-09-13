#!/usr/bin/env python
"""按 assets/characters.json 的 appearance 生成内置角色头像（含一张 default）。

外观参数只有一份：角色文件里改配色/发型，重跑本脚本就换脸。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from animechat.chibi import draw_avatar  # noqa: E402
from animechat.config import ASSET_DIR, BUILTIN_STICKER_DIR  # noqa: E402

AVATAR_DIR = ASSET_DIR / "avatars"
DEFAULT_APPEARANCE = {
    "style": "short", "hair_color": "#8E9BB3", "hair_color2": "#C9D4E8",
    "eye_color": "#7E8CA8", "accessory": "none", "expression": "happy", "bg_color": "#EFF1F6",
}


def main() -> int:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    chars_path = BUILTIN_STICKER_DIR.parent / "characters.json"
    data = json.loads(chars_path.read_text(encoding="utf-8"))
    chars = data.get("characters") if isinstance(data, dict) else data
    made, failed = 0, []
    for char in chars or []:
        spec = dict(DEFAULT_APPEARANCE)
        spec.update(char.get("appearance") or {})
        spec["name"] = char.get("name") or ""
        try:
            draw_avatar(spec, size=256).save(AVATAR_DIR / (char["id"] + ".png"), format="PNG", optimize=True)
            made += 1
        except Exception as exc:
            failed.append(str(char.get("id")) + ": " + str(exc))
    try:
        draw_avatar(DEFAULT_APPEARANCE, size=256).save(AVATAR_DIR / "default.png", format="PNG", optimize=True)
        made += 1
    except Exception as exc:
        failed.append("default: " + str(exc))
    print("已生成 " + str(made) + " 张头像 → " + str(AVATAR_DIR))
    for line in failed:
        print("  [失败] " + line)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
