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
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from animechat.config import user_sticker_dir
from animechat.stickers import library
from sticker_dupe import signature

CAT = Path.home() / "Desktop" / "表情包_已分类" / "1_二次元"
KEEP_ROOT = CAT
IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
SRC_TAG = {"personal": "收藏", "recv": "收到", "super": "超级表情", "market": "商店"}


def src_of_desktop(p: Path) -> str:
    return p.parent.name if p.parent.name in SRC_TAG.values() else "收到"


def desktop_index(root: Path) -> tuple[dict, int]:
    """桌面那批 -> (像素指纹 -> (字节 sha10, 文件), 扫过的张数)。

    按像素而不是字节认重：QQ 把同一张表情存成 .png / .jpg / .webp 三份，只比字节的
    话一份算一张。桌面目录自己也重（收到、收藏各存过同一张），这里顺带并掉。
    """
    out: dict[str, tuple[str, Path]] = {}
    scanned = 0
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMG:
            continue
        scanned += 1
        s = signature(p)
        if s:
            out.setdefault(s[1], (s[0][:10], p))
    return out, scanned


def library_index(stdir: Path, meta: dict) -> tuple[dict, dict]:
    """现场扫库，返回两份「像素指纹 -> 文件名」索引。

    全库那份用来判「这张库里到底有没有」。以前只看 note 以 QQ 开头的条目，于是撞上
    p01…p54 那批带手写标签的老图时判成没有，一次同步重导了 54 张。
    QQ 那份只管隔离：不是我们导入的批次不碰。
    """
    everywhere: dict[str, str] = {}
    ours: dict[str, str] = {}
    for p in sorted(stdir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in IMG:
            continue
        s = signature(p)
        if not s:
            continue
        everywhere.setdefault(s[1], p.name)
        info = meta.get(p.name)
        if isinstance(info, dict) and str(info.get("note", "")).startswith("QQ"):
            ours.setdefault(s[1], p.name)
    return everywhere, ours


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

    kept, scanned = desktop_index(root)
    print(f"桌面保留 {scanned} 张，内容去重后 {len(kept)} 种")

    stdir = user_sticker_dir()
    lib = library()
    # 走库的合并视图：qq* 的定义现在住在 stickers_meta.local.json。只读仓库那份的话
    # 那批条目看着像不存在 —— 该隔离的一张都不会动，桌面图会被当新图重复导入，
    # 而新条目盖掉旧条目的那一刻，打过的视觉标签就没了。
    meta = lib.read_meta()
    quar = stdir.parent / "stickers_quarantine"

    # 库里的现状不信 meta 里记的 sha1：那只是导入当时的一份抄录。
    lib_pix, qq_pix = library_index(stdir, meta)
    desk = set(kept)
    # 三分拣
    add = [kept[s] for s in kept if s not in lib_pix]                  # 库里没有
    delete = [qq_pix[s] for s in qq_pix if s not in desk]              # 没保留
    keep = [s for s in kept if s in lib_pix]                           # 已有不动
    keep_qq = [s for s in kept if s in qq_pix]                         # 其中属于 QQ 那批的

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
    for fname in delete:
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
    print(f"\n完成：新增 {added}、移入隔离 {moved}、库内 QQ 现 {qq_left} 张（应≈{len(keep_qq) + added}）。")
    print(f"库文件现 {len(list(stdir.glob('*')))}，隔离区 {len(list(quar.glob('*')))}。")
    if add:
        print(f"新增的 {added} 张尚无视觉标签（库里原本没有），如需可再跑一次 tag_stickers_vision 续跑打标。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
