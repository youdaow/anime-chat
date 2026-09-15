#!/usr/bin/env python3
"""把桌面『表情包』里的 QQ 图，按视觉打标结果分类复制，方便人工浏览挑选。

跑法：
    python scripts/sort_desktop_stickers.py            # 干跑，只报告分类数量对不对
    python scripts/sort_desktop_stickers.py --run      # 真正复制到 表情包_已分类/

分类依据（不重跑模型，纯靠已有的打标结果对账）：
- 隔离区 data/stickers_quarantine/ 里的文件名 = 判定为「非二次元」的哈希 → 归 0_建议剔除
- 主库 meta 里 anime=True 的 → 归 二次元/{来源}，并把中文标签写进文件名
- 对不上标签的（10 张审核失败等）→ 归 2_未识别_需人工看

复制目标：桌面 表情包_已分类/，每张重命名为「情绪标签_描述.扩展名」，
资源管理器里一眼看懂是什么，确认后直接把要的拖进聊天软件表情库。
"""
import argparse
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animechat.config import user_sticker_dir
from animechat.emotion import EMOTION_LABELS

DESK = Path.home() / "Desktop" / "表情包"
OUT = Path.home() / "Desktop" / "表情包_已分类"
SRC_TAG = {"personal": "收藏", "recv": "收到", "super": "超级表情", "market": "商店"}
FN_RE = re.compile(r"^qqc_(personal|recv|super|market)_([0-9a-f]{12})", re.I)


def load_d10_index():
    """从主库 meta + 隔离区文件名，建两张哈希(前10位)索引。"""
    meta_path = user_sticker_dir().parent / "stickers_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    anime = {}          # d10 -> (来源中文, emotion, label, tags)  判定二次元、有标签
    for fname, info in meta.items():
        if not isinstance(info, dict) or info.get("anime") is not True:
            continue
        d10 = str(info.get("sha1") or fname.split("-")[-1].split(".")[0])[:10]
        src = fname[2:].split("-")[0] if fname.startswith("qq") else "其它"
        anime[d10] = (src, str(info.get("emotion", "")), str(info.get("label", "")),
                      [str(t) for t in (info.get("tags") or [])])
    quar_dir = user_sticker_dir().parent / "stickers_quarantine"
    quarantine = set()   # 判定非二次元的 d10
    if quar_dir.is_dir():
        for p in quar_dir.iterdir():
            m = re.search(r"-([0-9a-f]{10})", p.name)
            if m:
                quarantine.add(m.group(1))
    return anime, quarantine


def classify(run=False):
    if not DESK.is_dir():
        raise SystemExit("没找到桌面『表情包』文件夹：" + str(DESK))
    files = [p for p in DESK.iterdir() if p.is_file() and p.suffix.lower()
             in (".png", ".jpg", ".jpeg", ".gif", ".webp")]
    anime, quarantine = load_d10_index()

    buckets = defaultdict(list)   # 相对路径 -> [桌面源文件]
    for p in files:
        m = FN_RE.match(p.name)
        if not m:
            continue              # 非 qqc_ 前缀（用户原来手放的），本次不动
        d10 = m.group(2)[:10]
        src = SRC_TAG[m.group(1).lower()]
        ext = p.suffix.lower()
        if d10 in quarantine:
            buckets[("0_建议剔除_非二次元", "")].append((p, ""))
        elif d10 in anime:
            _src2, emo, label, tags = anime[d10]
            safe = re.sub(r'[\\/:*?"<>|]', "", label).strip()[:14] or emo
            emo_cn = EMOTION_LABELS.get(emo, "平常") if emo else "平常"
            buckets[("1_二次元", src)].append((p, f"{emo_cn}_{safe}"))
        else:
            buckets[("2_未识别_需人工看", "")].append((p, ""))

    # 报告
    print("分类结果（复制目标：表情包_已分类/）")
    total = 0
    for (top, sub), items in sorted(buckets.items()):
        path = top + ("/" + sub if sub else "")
        total += len(items)
        print(f"  {path:32} {len(items):>4} 张")
    print(f"  {'合计':32} {total:>4} 张（桌面共 {len(files)} 张）")

    if not run:
        print("\n（干跑，未复制任何东西。加 --run 才真正建文件夹并复制。）")
        return

    if OUT.exists():
        raise SystemExit("目标已存在，先手动删掉再跑，避免混入旧结果：" + str(OUT))
    OUT.mkdir(parents=True)
    for (top, sub), items in buckets.items():
        d = OUT / top / sub if sub else OUT / top
        d.mkdir(parents=True, exist_ok=True)
        n = 0
        for src, prefix in items:
            ext = src.suffix.lower()
            name = f"{prefix}{('_' if prefix else '')}{n:03d}{ext}" if prefix else f"{n:03d}{ext}"
            shutil.copy2(src, d / name)
            n += 1
    print(f"\n已复制到 {OUT}")
    print("打开各文件夹看缩略图，把确认要用的从『1_二次元』拖进聊天软件表情库即可。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="真正复制（默认只干跑报告）")
    raise SystemExit(classify(run=ap.parse_args().run) or 0)
