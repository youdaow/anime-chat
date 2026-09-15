"""只读对账：统计桌面『表情包_已分类』现状 + 与 animechat 库的 sha1 匹配情况。
不写任何东西。跑：python scripts/analyze_kept_stickers.py
"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from animechat.config import user_sticker_dir

CAT = Path.home() / "Desktop" / "表情包_已分类"
IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def sha10(p: Path) -> str:
    try:
        return hashlib.sha1(p.read_bytes()).hexdigest()[:10]
    except OSError:
        return ""


def main():
    if not CAT.is_dir():
        print("没有", CAT)
        return 1

    # 1) 各子文件夹现存数量（1_二次元 下还有来源孙文件夹）
    print("=== 桌面『表情包_已分类』现状 ===")
    top = {}
    for d in sorted(CAT.iterdir()):
        if not d.is_dir():
            continue
        files = [f for f in d.rglob("*") if f.is_file() and f.suffix.lower() in IMG]
        top[d.name] = files
        print(f"  {d.name}: {len(files)} 张")

    # 2) 库现状：主库里的 QQ 条目（按 sha1）、隔离区（按文件名哈希）
    meta_path = user_sticker_dir().parent / "stickers_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.is_file() else {}
    lib_sha = {}          # sha1 -> meta 文件名
    for fname, info in meta.items():
        if isinstance(info, dict) and str(info.get("note", "")).startswith("QQ"):
            sh = str(info.get("sha1") or "")[:10]
            if sh:
                lib_sha[sh] = fname
    quar_dir = user_sticker_dir().parent / "stickers_quarantine"
    quar_sha = {}
    if quar_dir.is_dir():
        import re
        for p in quar_dir.iterdir():
            m = re.search(r"-([0-9a-f]{10})", p.name)
            if m:
                quar_sha[m.group(1)] = p.name
    print(f"\n=== animechat 库现状 ===")
    print(f"  主库 QQ 条目: {len(lib_sha)}")
    print(f"  隔离区 QQ 条目: {len(quar_sha)}")

    # 3) 对『1_二次元』保留图做 sha1 匹配
    print(f"\n=== 『1_二次元』保留图 vs 库 ===")
    kept = top.get("1_二次元", [])
    kept_sha = [sha10(p) for p in kept]
    kept_set = set(s for s in kept_sha if s)
    in_lib = sum(1 for s in kept_set if s in lib_sha)
    in_quar = sum(1 for s in kept_set if s in quar_sha)
    nowhere = len(kept_set) - in_lib - in_quar
    dup_internal = len(kept_sha) - len(kept_set)
    print(f"  保留文件: {len(kept)} 张，去重后不同内容 {len(kept_set)}（内部重复 {dup_internal} 张）")
    print(f"    已在主库: {in_lib}   在隔离区: {in_quar}   库里都没有: {nowhere}")

    # 4) 库里 QQ 条目，有多少是用户保留图没选中的（=若同步则会被清理）
    unkept_lib = sum(1 for s in lib_sha if s not in kept_set)
    print(f"\n=== 若『让库 = 你筛选的 1_二次元』，则主库将被清理 ===")
    print(f"  保留: {len(lib_sha) - unkept_lib}   删除(你没选的): {unkept_lib}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
