#!/usr/bin/env python3
"""用视觉模型给表情包打标签 + 判定是不是二次元。

    python scripts/tag_stickers_vision.py --limit 3 --dry-run     # 探针：先看能不能看图
    python scripts/tag_stickers_vision.py --limit 12 --dry-run     # 抽样：看打标质量与单张耗时
    python scripts/tag_stickers_vision.py --apply                  # 全量：写回库 + 隔离非二次元

只读取 settings.json 里的模型配置，绝不写它。图片读进来缩到长边<=768 转 JPEG 再
base64，省 token 也避开 GIF 多帧。默认 --dry-run：只打印结果，不改库。
--apply 才写 meta：给每张补 label/emotion/tags，判定非二次元的移进
data/stickers_quarantine/ 软隔离（不硬删，防误杀，随时可挪回）。

模型返回必须是 JSON，解析用了兜底：抽第一个 {...}，emotion 不在词表就归 neutral。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animechat import llm
from animechat.config import load_settings, user_sticker_dir
from animechat.emotion import EMOTION_KEYS
from animechat.stickers import library

QUARANTINE = user_sticker_dir().parent / "stickers_quarantine"
MAX_SIDE = 768

PROMPT = (
    "你是表情包标注助手。看这张图，只输出一个 JSON 对象，不要任何解释、不要代码块。字段：\n"
    '  "anime": true/false  图主体是否为动漫/二次元画风（真人实拍、真人明星、写实照片、'
    "纯文字截图、真实动物实拍算 false；动漫角色/手绘/赛璐璐/明显日系卡通算 true）\n"
    '  "emotion": 从这些里选一个最贴切的 key：' + ",".join(EMOTION_KEYS) + "\n"
    '  "label": 4~8 个字的中文，概括这张图在演什么（如"傲娇扭头""开心比心""无语流汗"）\n'
    '  "tags": 2~4 个中文短词，便于按情绪/动作检索\n'
    "只回 JSON。"
)


def encode_image(path: Path) -> tuple[str, str]:
    """返回 (base64, mime)。缩到长边<=MAX_SIDE，统一转 JPEG。gif 取首帧。"""
    from PIL import Image

    with Image.open(path) as im:
        im.load()
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        scale = MAX_SIDE / float(max(w, h))
        if scale < 1:
            im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
        buf = io.BytesIO()
        im.convert("RGB").save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode("ascii"), "image/jpeg"


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


def normalize(obj: dict) -> dict:
    emo = str(obj.get("emotion") or "").strip().lower()
    if emo not in EMOTION_KEYS:
        emo = "neutral"
    label = re.sub(r"\s+", "", str(obj.get("label") or ""))[:16]
    tags = [re.sub(r"\s+", "", str(t))[:12] for t in (obj.get("tags") or []) if str(t).strip()][:4]
    anime = obj.get("anime")
    anime = bool(anime) if isinstance(anime, bool) else str(anime).strip().lower() in ("true", "1", "yes", "是")
    return {"anime": anime, "emotion": emo, "label": label, "tags": tags}


async def tag_one(settings, path: Path, sem) -> dict:
    try:
        b64, mime = encode_image(path)
    except Exception as exc:
        return {"file": path.name, "ok": False, "err": "读图失败 " + str(exc)[:60]}
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
        ],
    }]
    async with sem:
        t0 = time.time()
        try:
            raw = await llm.complete_chat(settings, messages, temperature=0.2, max_tokens=180)
        except Exception as exc:
            return {"file": path.name, "ok": False,
                    "err": str(getattr(exc, "message", exc))[:120], "secs": round(time.time() - t0, 1)}
    secs = round(time.time() - t0, 1)
    obj = extract_json(raw)
    if obj is None:
        return {"file": path.name, "ok": False, "err": "非JSON " + (raw or "")[:80], "secs": secs}
    out = normalize(obj)
    out.update(file=path.name, ok=True, secs=secs)
    return out


async def run(settings, files, concurrency, apply, delete_nonanime, full_total=0,
              force=False, flush_every=50):
    sem = asyncio.Semaphore(concurrency)
    lib = library()
    # 合并视图 + 拆分写回：qq* 那批的定义住在本机专属的 local meta 里，
    # 直接读写仓库那份会把标签写到没人读的地方（下次合并时 local 那份赢）。
    meta = lib.read_meta()

    def flush() -> None:
        lib.write_meta(meta)

    # 断点续跑：apply 且未 --force 时，跳过 meta 里已标过 anime 的。跑一半断了重跑能续上。
    if apply and not force:
        kept, skipped = [], 0
        for p in files:
            info = meta.get(p.name)
            if isinstance(info, dict) and info.get("anime") is not None:
                skipped += 1
            else:
                kept.append(p)
        files = kept
        if skipped:
            print(f"跳过已打标 {skipped} 张（续跑；要全部重标加 --force）")
        total = len(files)
        if total:
            for bak in lib.backup_meta():
                print(f"已备份 meta -> {bak.name}")
    total = len(files)

    done = {"n": 0, "anime": 0, "nonanime": 0, "err": 0, "sec_sum": 0.0}
    lock = asyncio.Lock()

    async def worker(p: Path):
        r = await tag_one(settings, p, sem)
        async with lock:
            done["n"] += 1
            if r.get("ok"):
                done["sec_sum"] += r.get("secs", 0)
                done["anime" if r["anime"] else "nonanime"] += 1
                tag = "二次元" if r["anime"] else "★非二次元"
                print(f"  [{done['n']}/{total}] {tag} {r['emotion']:8} 「{r['label']}」 "
                      f"{r['tags']}  {r.get('secs')}s  {r['file'][:24]}", flush=True)
                if apply:
                    fname = p.name
                    info = meta.get(fname) if isinstance(meta.get(fname), dict) else {}
                    info["emotion"] = r["emotion"]
                    info["label"] = r["label"] or info.get("label") or fname[:12]
                    info["tags"] = r["tags"] or [r["emotion"]]
                    info["defined"] = "user"          # 挡住 backfill 再改
                    info["anime"] = r["anime"]
                    if (not r["anime"]) and delete_nonanime:
                        QUARANTINE.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(p), str(QUARANTINE / p.name))
                        meta.pop(fname, None)         # 软隔离：不硬删，可挪回
                    else:
                        meta[fname] = info
                    if done["n"] % flush_every == 0:   # 定期落盘，断了不白跑
                        flush()
            else:
                done["err"] += 1
                print(f"  [{done['n']}/{total}] 失败 {r['file'][:24]}: {r['err']}", flush=True)
        return r

    t0 = time.time()
    await asyncio.gather(*(worker(p) for p in files))
    wall = time.time() - t0
    if apply:
        flush()
        library().refresh()

    avg = (done["sec_sum"] / max(1, done["anime"] + done["nonanime"]))
    print("")
    print(f"完成 {total} 张，用时 {wall:.0f}s  |  二次元 {done['anime']} · 非二次元 {done['nonanime']} · 失败 {done['err']}")
    print(f"平均每张 {avg:.1f}s，实测吞吐 {total / max(1, wall):.2f} 张/秒（并发 {concurrency}）")
    if full_total and total:
        print(f"（本次待办 {total} 张 / 该批次共 {full_total} 张）")
    print(("已写回库" if apply else "（dry-run，未改动任何东西）")
          + ("，非二次元已隔离到 stickers_quarantine/" if (apply and delete_nonanime) else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="视觉模型给表情包打标签 / 判定二次元")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 张（0=全部）")
    ap.add_argument("--concurrency", type=int, default=4, help="并发数（默认4，看网关限流调）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写库（默认行为）")
    ap.add_argument("--apply", action="store_true", help="真正写回 meta / 隔离非二次元")
    ap.add_argument("--delete-nonanime", action="store_true", help="配合 --apply：把判定非二次元的移进隔离目录")
    ap.add_argument("--only-qq", action="store_true", help="只处理 note 以 QQ 开头的批量导入图")
    ap.add_argument("--grep", default="", help="只处理文件名含此串的图（如 收到，针对抽样验证）")
    args = ap.parse_args(argv)

    s = load_settings()
    lib = library()
    all_sts = lib.all()
    if args.only_qq:
        all_sts = [x for x in all_sts if str(x.note or "").startswith("QQ")]
    files = [lib.files[x.id] for x in all_sts if x.id in lib.files]
    if args.grep:
        files = [p for p in files if args.grep in p.name]
    files = sorted(files)
    full_total = len(files)
    if args.limit and args.limit > 0:
        files = files[:args.limit]
    print(f"待处理 {len(files)} 张（{'全部' if not args.only_qq else '仅QQ'}），"
          f"模型={s.llm_model}  地址={s.llm_base_url}")
    apply = args.apply and not args.dry_run
    if args.delete_nonanime and not apply:
        print("提示：--delete-nonanime 需要 --apply 才生效（当前是 dry-run，只报告不移动文件）")
    try:
        asyncio.run(run(s, files, args.concurrency, apply, args.delete_nonanime and apply,
                        full_total=full_total))
    except KeyboardInterrupt:
        print("\n中断。已处理的部分：dry-run 无副作用；apply 已写的 meta 有 .bak 备份。")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
