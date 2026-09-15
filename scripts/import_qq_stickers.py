#!/usr/bin/env python3
"""把 QQ 本地表情导出到桌面『表情包』并按内容去重，再灌进 animechat 表情库。

跑法（先把 web 服务起起来，默认 http://127.0.0.1:8899，导入那步要用它的接口）：
    python scripts/import_qq_stickers.py --dry-run     # 只报告，不写任何东西
    python scripts/import_qq_stickers.py --copy        # 阶段1：去重后复制到桌面
    python scripts/import_qq_stickers.py --import      # 阶段2：把桌面库灌进 animechat
    python scripts/import_qq_stickers.py --copy --import # 两阶段连着跑

设计要点：
- 只读 QQ，绝不写它任何目录。
- 缩略图靠「长边 < MIN_SIDE 像素」剔，比认目录名/后缀可靠（QQ 各版本命名不一致）。
- 同名 png+jpg（QQ 常给一张图存两种格式）按基名合并，留字节大的那张（png 一般更清晰）。
- 全局按文件字节 sha1 去重：同一张图不管出现在哪个源，只留一份。
- 复制阶段把来源记进文件名前缀，导入阶段据此写 meta（origin=qq，note 记来源目录）。
- 导入阶段是「批量落 meta + 让 animechat 自己 refresh 扫盘」，不逐张调 add_file
  （那玩意每张 refresh 一次，3000 张会 O(n²) 卡死）。meta 里标 defined=user，
  免得 backfill 把那串 hash 文件名当垃圾名给重命名掉。
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

from PIL import Image

MIN_SIDE = 80                      # 长边小于此像素的当缩略图丢掉
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
QQ_ROOT = Path.home() / "Documents/Tencent Files"
DESK = Path.home() / "Desktop/表情包"
# 源目录 -> 归一化来源标签
SOURCES = {
    "personal": "收藏",
    "recv": "收到",
    "super": "超级表情",
    "market": "商店",
}


def fix_console() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:
        pass
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass


def find_qq_emoji_root() -> Path:
    """自动定位当前机器上 nt_data/Emoji。多账号时取有 personal_emoji 的那个。"""
    cands = []
    if QQ_ROOT.is_dir():
        for acct in QQ_ROOT.iterdir():
            e = acct / "nt_qq" / "nt_data" / "Emoji"
            if e.is_dir():
                cands.append(e)
    for e in cands:
        if (e / "personal_emoji").is_dir():
            return e
    if cands:
        return cands[0]
    raise SystemExit("没找到 QQ 表情目录（nt_data/Emoji）。检查是否装了新版 QQ。")


def collect():
    """扫所有源，返回 [(src_path, 来源标签)]，已做同名合并 + 尺寸过滤 + 全局sha1去重。"""
    root = find_qq_emoji_root()
    raw = []
    def add(d: Path, tag: str):
        if not d.is_dir():
            return
        for p in d.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMG_EXT:
                raw.append((p, tag))
    add(root / "personal_emoji", "personal")
    add(root / "emoji-recv", "recv")
    add(root / "emoji-related", "super")
    add(root / "marketface", "market")

    # 同名（基名）合并：QQ 一张图常存 png+jpg，留字节大的
    by_stem = {}
    loose = []
    for p, tag in raw:
        key = (tag, p.stem.lower())
        prev = by_stem.get(key)
        if prev is None:
            by_stem[key] = p
        else:
            try:
                if p.stat().st_size > prev.stat().st_size:
                    by_stem[key] = p
            except OSError:
                pass
    picked = list(by_stem.values()) + loose

    # 尺寸过滤：剔缩略图。gif 读首帧尺寸；读不出尺寸的（损坏/非图）跳过。
    kept = []
    n_small = n_bad = 0
    for p in picked:
        try:
            with Image.open(p) as im:
                w, h = im.size
        except Exception:
            n_bad += 1
            continue
        if max(w, h) < MIN_SIDE:
            n_small += 1
            continue
        kept.append((p, w, h))

    # 全局按内容 sha1 去重（by_stem 的 key=(tag,stem)，回查每张的来源标签）
    stem_tag = {p: tag for (tag, steml), p in by_stem.items()}
    seen = {}
    final = []
    for p, w, h in kept:
        try:
            d = hashlib.sha1(p.read_bytes()).hexdigest()[:12]
        except OSError:
            continue
        if d in seen:
            continue
        seen[d] = p
        final.append((p, stem_tag.get(p, "recv"), d, w, h))
    print(f"扫到候选 {len(raw)} 文件；同名合并后 {len(by_stem)}；"
          f"剔缩略图 {n_small}、坏图 {n_bad}；去重后 {len(final)} 张。")
    return final


def copy_to_desktop(items, dry=False):
    DESK.mkdir(parents=True, exist_ok=True)
    # 清掉上次导入留下的（只认我们加的前缀 qqc_，别的不动）
    if not dry:
        for old in DESK.glob("qqc_*"):
            if old.is_file():
                old.unlink()
    count = defaultdict(int)
    total_bytes = 0
    for src, tag, d, w, h in items:
        ext = src.suffix.lower()
        name = f"qqc_{tag}_{d}{ext}"                 # 前缀带来源，导入时还原
        dst = DESK / name
        total_bytes += src.stat().st_size
        count[tag] += 1
        if not dry:
            shutil.copy2(src, dst)
    mb = total_bytes / 1048576
    print(("会复制" if dry else "已复制") +
          f" {len(items)} 张（约 {mb:.0f} MB）到 {DESK}")
    print("  按来源：" + "、".join(f"{SOURCES[k]} {v}" for k, v in sorted(count.items())))


def do_import(items, base, dry=False):
    """把去重后的图导入 animechat：复制到 data/stickers + 写 meta，然后 refresh 一次。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from animechat.config import user_sticker_dir
    from animechat.stickers import library, META_NAME, _safe_id
    from animechat import emotion

    target_dir = user_sticker_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    lib = library()
    meta_path = target_dir.parent / META_NAME
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    # 注意：meta 里的 sha1 是 10 位，这里 items 的 d 是 12 位，比较用前 10 位
    have10 = {str(v.get("sha1") or "") for v in meta.values() if isinstance(v, dict)}

    added = skipped = 0
    per_tag = defaultdict(int)
    for src, tag, d, w, h in items:
        d10 = d[:10]
        if d10 in have10:
            skipped += 1
            continue
        ext = src.suffix.lower()
        # 文件名：来源+短哈希，可读且不撞
        fname = f"qq{SOURCES[tag]}-{d10}{ext}"
        dst = target_dir / fname
        if not dry:
            shutil.copy2(src, dst)
        meta[fname] = {
            "tags": [SOURCES[tag]],
            "label": f"{SOURCES[tag]}·{d10}",
            "emotion": "neutral",
            "origin": "upload",
            "note": f"QQ本地表情导入 来源{SOURCES[tag]}",
            "created_at": time.time(),
            "sha1": d10,
            "defined": "user",          # 关键：挡住 backfill 重命名
        }
        have10.add(d10)
        per_tag[tag] += 1
        added += 1

    if dry:
        print(f"[dry-run] 将新增 {added} 张，跳过已入库 {skipped} 张。")
        return

    # 真实写入前，先给含现有 77 张定义的 meta 存一份带时间戳的备份。
    if meta_path.is_file():
        bak = meta_path.with_name(META_NAME + ".bak-" + time.strftime("%Y%m%d%H%M%S"))
        shutil.copy2(meta_path, bak)
        print(f"已备份原 meta -> {bak.name}")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    lib.refresh()                       # 一次扫盘，把新文件全部纳入
    total = len(lib.all())
    print(f"导入完成：新增 {added} 张，跳过重复 {skipped} 张。")
    print("  按来源：" + "、".join(f"{SOURCES[k]} {v}" for k, v in sorted(per_tag.items())))
    print(f"  animechat 表情库现共 {total} 张。")


def main(argv=None):
    fix_console()
    ap = argparse.ArgumentParser(description="导出 QQ 表情 + 灌入 animechat")
    ap.add_argument("--copy", action="store_true", help="复制去重后的图到桌面『表情包』")
    ap.add_argument("--import", dest="do_import", action="store_true", help="导入 animechat 表情库")
    ap.add_argument("--base", default="http://127.0.0.1:8899")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if not (args.copy or args.do_import):
        ap.print_help()
        return 2

    items = collect()
    if args.copy:
        copy_to_desktop(items, dry=args.dry_run)
    if args.do_import:
        do_import(items, args.base, dry=args.dry_run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
