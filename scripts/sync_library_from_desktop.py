#!/usr/bin/env python3
"""把 animechat 表情库同步成「桌面 表情包_已分类/1_二次元 里保留的图」。

模式（用户已选：同步·只留我筛的）：
  - 保留图已在库里的  → 不动，视觉标签原样保留
  - 保留图库里没有的  → 新增导入（写 meta，defined=user）
  - 库里 QQ 但没保留的 → 移到隔离区 + 删 meta 条目（软删除，可挪回）

跑法：
    python scripts/sync_library_from_desktop.py --dir ~/Desktop/表情包        # 干跑：只报告
    python scripts/sync_library_from_desktop.py --dir ~/Desktop/表情包 --run  # 执行 + 备份 meta
不给 --dir 时用下面那个老默认路径（分好类的子目录那版）。
"""
import argparse
import hashlib
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from animechat.config import user_sticker_dir
from animechat.stickers import library

CAT = Path.home() / "Desktop" / "表情包_已分类" / "1_二次元"
KEEP_ROOT = CAT
IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
SRC_TAG = {"personal": "收藏", "recv": "收到", "super": "超级表情", "market": "商店"}


def sha10(p: Path) -> str:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()[:10]
    except OSError:
        return ""


def src_of_desktop(p: Path) -> str:
    return p.parent.name if p.parent.name in SRC_TAG.values() else "收到"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true", help="真正执行（默认干跑）")
    ap.add_argument("--dir", default="",
                    help="保留图所在目录。以前是「表情包_已分类/1_二次元」那种分好类的子目录，"
                         "现在人直接摊平放一个目录里（如 ~/Desktop/表情包），得能指过去")
    args = ap.parse_args()

    root = Path(args.dir).expanduser() if args.dir else KEEP_ROOT
    if not root.is_dir():
        print("没有保留图目录：", root)
        return 1

    kept = [f for f in root.rglob("*") if f.is_file() and f.suffix.lower() in IMG]
    kept_by_sha = {}
    for p in kept:
        s = sha10(p)
        if s:
            kept_by_sha.setdefault(s, p)   # 内容去重：同 sha 只认第一张
    print(f"桌面保留 {len(kept)} 张，内容去重后 {len(kept_by_sha)} 种")

    stdir = user_sticker_dir()
    lib = library()
    # 走库的合并视图：qq* 的定义现在住在 stickers_meta.local.json。只读仓库那份的话
    # 那批条目看着像不存在 —— 该隔离的一张都不会动，桌面图会被当新图重复导入，
    # 而新条目盖掉旧条目的那一刻，打过的视觉标签就没了。
    meta = lib.read_meta()
    quar = stdir.parent / "stickers_quarantine"

    # 库里的 QQ 条目：sha1 -> 文件名
    lib_sha = {}
    for fname, info in meta.items():
        if isinstance(info, dict) and str(info.get("note", "")).startswith("QQ"):
            s = str(info.get("sha1") or "")[:10]
            if s:
                lib_sha[s] = fname

    # 三分拣
    add = [(s, kept_by_sha[s]) for s in kept_by_sha if s not in lib_sha]              # 库里没有
    delete = [(s, lib_sha[s]) for s in lib_sha if s not in kept_by_sha]                # 没保留
    keep = [s for s in kept_by_sha if s in lib_sha]                                    # 已有不动

    print(f"\n将新增导入 : {len(add)}")
    print(f"将保留不动 : {len(keep)}")
    print(f"将移入隔离 : {len(delete)}")

    if not args.run:
        print("\n（干跑，未改动任何东西。加 --run 执行。）")
        if add:
            print("新增预览：")
            for s, p in add[:10]:
                print(f"   {s}  {p.name[:24]}")
        return 0

    # 备份两份 meta（仓库那份 + 本机那份），这份脚本会同时改到它们
    for bak in lib.backup_meta():
        print(f"已备份 meta -> {bak.name}")

    quar.mkdir(parents=True, exist_ok=True)

    # 1) 删除（没保留的 QQ 图移入隔离）
    moved = 0
    for s, fname in delete:
        src = stdir / fname
        if src.is_file():
            dest = quar / fname
            n = 1
            while dest.exists():
                dest = quar / f"{fname[:-len(src.suffix)]}__dup{n}{src.suffix}"
                n += 1
            shutil.move(str(src), str(dest))
            moved += 1
        meta.pop(fname, None)

    # 2) 新增（桌面独有的图，补进库 + 写 meta）
    added = 0
    for s, p in add:
        ext = p.suffix.lower()
        src_tag = src_of_desktop(p)
        fname = f"qq{src_tag}-{s}{ext}"
        # 极端兜底：目标重名（库里该 sha 判为没有但文件名被别的条目占了）
        if (stdir / fname).exists():
            fname = f"qq{src_tag}-{s}n{ext}"
        shutil.copy2(p, stdir / fname)
        meta[fname] = {
            "tags": [src_tag], "label": f"{src_tag}·{s}", "emotion": "neutral",
            "origin": "upload", "note": f"QQ本地表情导入 来源{src_tag}",
            "created_at": time.time(), "sha1": s, "defined": "user",
        }
        added += 1

    # 写回 meta（按归属拆成两份）+ 刷新
    lib.write_meta(meta)
    lib.refresh()

    qq_left = sum(1 for v in meta.values() if isinstance(v, dict) and str(v.get("note", "")).startswith("QQ"))
    print(f"\n完成：新增 {added}、移入隔离 {moved}、库内 QQ 现 {qq_left} 张（应≈{len(keep) + added}）。")
    print(f"库文件现 {len(list(stdir.glob('*')))}，隔离区 {len(list(quar.glob('*')))}。")
    if add:
        print(f"新增的 {added} 张尚无视觉标签（库里原本没有），如需可再跑一次 tag_stickers_vision 续跑打标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
