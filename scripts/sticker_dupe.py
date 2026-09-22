#!/usr/bin/env python3
"""揪出表情库里内容重复的图：同一张表情在库里存成了两个文件。

两种重复都算：
  1) 字节完全一样 —— 同一张图被两个来源各导了一次。
  2) 字节不一样、解出来的像素一模一样 —— QQ 把同一张表情同时存成 .png / .jpg /
     .webp，换壳不换图。光比文件 sha1 只能抓到第 1 类。

判据刻意用「解码后逐像素相同」而不是感知哈希（dHash 之类）：这批表情几乎都是
同尺寸、白底、单个角色，dHash 在 1719 张里报了 499 组「视觉相同」，抽查绝大多数
是不同表情（只是构图像）。宁可漏，也不能错删。

留哪一张，按这个顺序比（左优先）：收藏过 > 用过 > 标签条数多 > note 是人写的 >
label 是人写的 > 有情绪标签 > 文件更小 > 文件名靠前（pXX 那批排在 qqXX 之前，
所以名次全平时留的是 git 跟踪的那张）。

跑法：
    python scripts/sticker_dupe.py          # 干跑：只报告，什么都不动
    python scripts/sticker_dupe.py --run    # 执行：多余的移进隔离区，meta 先备份
"""
from __future__ import annotations

import argparse
import hashlib
import io
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from animechat.config import user_sticker_dir
from animechat.stickers import library

IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
AUTO_NOTE = "QQ本地表情导入"      # 同步脚本自动写的 note，认这个前缀判「没人写过」


def signature(p: Path) -> tuple[str, str] | None:
    """一张图 -> (字节 sha1, 像素 sha1)；读不动返回 None。

    像素那把钥匙跨格式：同一张表情不管存成 png 还是 jpg，解出来逐点相同就同指纹。
    动图把所有帧都算进去 —— 只看第一帧会把两套「第一帧一样、动起来不一样」的
    表情判成重复。
    """
    try:
        raw = p.read_bytes()
    except OSError:
        return None
    byte = hashlib.sha1(raw).hexdigest()
    try:
        from PIL import Image, ImageFile
        ImageFile.LOAD_TRUNCATED_IMAGES = True   # 库里有一张 jpg 尾部缺字节，不然它读不出来就永远查不重
        im = Image.open(io.BytesIO(raw))
        parts = []
        for i in range(getattr(im, "n_frames", 1)):
            im.seek(i)
            fr = im.convert("RGBA")
            parts.append("%dx%d:%s" % (fr.size[0], fr.size[1],
                                        hashlib.sha1(fr.tobytes()).hexdigest()))
        pix = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    except Exception:
        # 坏图/缺解码器：退回字节判据，等于承认「这张没法跟别处比」，不当它是重复的
        pix = "unparsed:" + byte
    return byte, pix


def keeper_rank(name: str, entry: object, size: int) -> tuple:
    e = entry if isinstance(entry, dict) else {}
    tags = e.get("tags")
    note = str(e.get("note") or "")
    label = str(e.get("label") or "")
    return (
        1 if e.get("favorite") else 0,
        int(e.get("uses") or 0),
        len(tags) if isinstance(tags, list) else 0,
        0 if note.startswith(AUTO_NOTE) else 1,
        0 if "·" in label else 1,
        0 if str(e.get("emotion") or "neutral") == "neutral" else 1,
        -size,
    )


def clusters(stdir: Path, meta: dict) -> list[dict]:
    """每个重复簇 -> {keeper, dupes, files}。名次全并时留文件名靠前的那张。"""
    by_pix: dict[str, list[Path]] = {}
    for p in sorted(stdir.iterdir()):
        if not p.is_file() or p.suffix.lower() not in IMG:
            continue
        s = signature(p)
        if s:
            by_pix.setdefault(s[1], []).append(p)
    out = []
    for g in by_pix.values():
        if len(g) < 2:
            continue
        scored = [(keeper_rank(p.name, meta.get(p.name), p.stat().st_size), -i, p)
                  for i, p in enumerate(g)]
        keeper = max(scored)[2]
        out.append({"keeper": keeper, "dupes": [p for _, _, p in scored if p is not keeper],
                    "all": g})
    return sorted(out, key=lambda c: c["keeper"].name)


def tracked() -> set[str]:
    """git 跟踪着的表情文件名。移走它们会让仓库少文件，得单独说一声。"""
    try:
        raw = subprocess.run(["git", "ls-files", "data/stickers"], cwd=sys.path[0],
                             capture_output=True, text=True, encoding="utf-8", check=True).stdout
    except Exception:
        return set()
    return {line.split("/")[-1] for line in raw.splitlines()}


def brief(name: str, meta: dict) -> str:
    e = meta.get(name)
    e = e if isinstance(e, dict) else {}
    tags = "/".join([str(t) for t in (e.get("tags") or [])][:4]) or "无标签"
    return f"{name}({tags})"


def main() -> int:
    ap = argparse.ArgumentParser(description="表情库像素级查重（默认干跑）")
    ap.add_argument("--run", action="store_true", help="真正执行（默认干跑）")
    ap.add_argument("--list", type=int, default=15, help="干跑时打印前几组，0=全打")
    args = ap.parse_args()

    stdir = user_sticker_dir()
    if not stdir.is_dir():
        print("没有表情库目录：", stdir)
        return 1
    lib = library()
    meta = lib.read_meta()
    cs = clusters(stdir, meta)
    total = len([p for p in stdir.iterdir() if p.is_file() and p.suffix.lower() in IMG])
    dupes = [d for c in cs for d in c["dupes"]]
    saved = sum(p.stat().st_size for p in dupes)
    print(f"库里 {total} 张图：内容重复 {len(cs)} 组，多余 {len(dupes)} 张（约 {saved/1048576:.1f} MB）")
    shown = cs if args.list == 0 else cs[:args.list]
    for c in shown:
        print(f"  留 {brief(c['keeper'].name, meta)}  <-  去 " +
              "、".join(brief(p.name, meta) for p in c["dupes"]))
    if len(cs) > len(shown):
        print(f"  …另有 {len(cs)-len(shown)} 组（--list 0 全打）")
    if not dupes:
        return 0

    tr = sorted(p.name for p in dupes if p.name in tracked())
    if tr:
        print(f"\n注意：其中 {len(tr)} 张是 git 跟踪的文件（移走 = 仓库少文件）：")
        for n in tr[:10]:
            print("   ", n)

    if not args.run:
        print("\n（干跑，未改动任何东西。加 --run 执行。）")
        return 0

    for bak in lib.backup_meta():
        print(f"已备份 meta -> {bak.name}")

    quar = stdir.parent / "stickers_quarantine"
    quar.mkdir(parents=True, exist_ok=True)
    uses = meta.get("__uses__")
    uses = dict(uses) if isinstance(uses, dict) else {}

    for c in cs:
        keeper = c["keeper"]
        k_uses = int(uses.get(keeper.name) or (meta.get(keeper.name) or {}).get("uses") or 0)
        for victim in c["dupes"]:
            k_uses += int(uses.pop(victim.name, 0) or 0)
            dest = _unique(quar / victim.name)
            shutil.move(str(victim), str(dest))
            # 缩略图是可再生的，源文件一走它就没人要了，不必跟着占隔离区
            (stdir / "thumbs" / (victim.name + ".w220.webp")).unlink(missing_ok=True)
            meta.pop(victim.name, None)
        if k_uses:
            entry = meta.get(keeper.name)
            if isinstance(entry, dict):
                entry["uses"] = k_uses
            else:
                uses[keeper.name] = k_uses

    if uses:
        meta["__uses__"] = uses
    else:
        meta.pop("__uses__", None)
    lib.write_meta(meta)
    left = len([p for p in stdir.iterdir() if p.is_file() and p.suffix.lower() in IMG])
    print(f"\n完成：{len(dupes)} 张移入 {quar}（没有硬删，想捞回来直接挪回 {stdir.name}/）。")
    print(f"库文件现 {left} 张。跑着的服务下一次列表情时会按目录 mtime 自己重扫，不用重启。")
    return 0


def _unique(p: Path) -> Path:
    if not p.exists():
        return p
    n = 1
    while True:
        cand = p.with_name(f"{p.stem}__dup{n}{p.suffix}")
        if not cand.exists():
            return cand
        n += 1


if __name__ == "__main__":
    sys.exit(main())
