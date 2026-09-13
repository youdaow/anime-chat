#!/usr/bin/env python3
"""联网抓一批二次元表情包，灌进 animechat 的表情库。

跑之前先把服务起起来（默认 http://127.0.0.1:8899）：
    python scripts/fetch_stickers.py                  # 每种情绪 3 张，约 48 张
    python scripts/fetch_stickers.py --per 5          # 每种情绪多来几张
    python scripts/fetch_stickers.py --only sad angry # 只补某几种情绪
    python scripts/fetch_stickers.py --dry-run        # 只看会抓到什么

它不自己乱抓：走的是应用自己的两个接口
    POST /api/stickers/search-web   （默认 provider=auto → Bing，免 Key）
    POST /api/stickers/import       （下载、校验、按 sha1 去重、记出处）
所以每跑一次都会往库里补一批新图（同一个 URL 不会重复入库，图片内容按 sha1
去重；Bing 每次给的结果本来就不一样，想停就少跑几次，或在表情包库里删掉）。

情绪键必须是 animechat.emotion.EMOTION_KEYS 里的那些，中文标签是角色挑图的依据。
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36 animechat-fetch/1.0"
SLEEP = 0.35  # 对公共接口的礼貌间隔


def fix_console() -> None:
    """Windows 控制台默认不是 UTF-8，中文进度会糊成乱码（和 animechat run 里同一套）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass

# 情绪 -> (搜索词, 中文标签, 中文名前缀)
QUERIES = {
    "happy":    [("anime happy emoji cute gif", ["开心", "高兴", "好耶"], "开心"),
                 ("anime laugh emoji cute gif", ["哈哈", "笑死", "愉快"], "笑出声")],
    "love":     [("anime love heart emoji cute gif", ["比心", "喜欢", "爱你"], "比心"),
                 ("anime hug emoji cute gif", ["抱抱", "贴贴", "抱住"], "抱抱")],
    "tsundere": [("anime blush emoji cute gif", ["害羞", "脸红", "别误会"], "脸红了"),
                 ("anime tsundere hmph emoji gif", ["傲娇", "才不是", "哼"], "才不稀罕")],
    "sad":      [("anime crying emoji cute gif", ["哭", "呜呜", "委屈"], "委屈哭哭"),
                 ("anime sad lonely emoji gif", ["难过", "失落", "心疼"], "难过")],
    "angry":    [("anime angry emoji gif", ["生气", "达咩", "火大"], "炸毛"),
                 ("anime rage punch emoji gif", ["揍你", "打脸", "哼"], "一拳")],
    "shock":    [("anime shocked surprised emoji gif", ["震惊", "什么", "石化"], "震惊")],
    "awkward":  [("anime awkward sweat emoji gif", ["尴尬", "汗", "无语"], "好尴尬")],
    "sleepy":   [("anime sleepy yawn emoji gif", ["困", "打哈欠", "想睡"], "困了啦")],
    "think":    [("anime thinking emoji gif", ["思考", "想想", "分析"], "让我想想")],
    "hype":     [("anime excited sparkle emoji gif", ["冲", "期待", "兴奋"], "冲冲冲"),
                 ("anime dance emoji cute gif", ["跳舞", "庆祝", "开心"], "跳起来")],
    "neutral":  [("anime wave hello emoji cute gif", ["打招呼", "挥手", "嗨"], "打招呼"),
                 ("anime shrug emoji cute gif", ["摊手", "无所谓", "无奈"], "摊手")],
}

STATIC_OK = re.compile(r"\.(gif|png|webp)(\?|$)", re.I)


def call(base, path, payload=None, timeout=90):
    url = base.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={
        "User-Agent": UA, "Content-Type": "application/json",
        "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main(argv=None):
    fix_console()
    ap = argparse.ArgumentParser(description="联网抓二次元表情包入库")
    ap.add_argument("--base", default="http://127.0.0.1:8899")
    ap.add_argument("--per", type=int, default=3, help="每个搜索词最多导入几张（1-6）")
    ap.add_argument("--cap", type=int, default=80, help="本轮最多导入多少张")
    ap.add_argument("--only", default="", help="只跑这些情绪，逗号分隔，如 sad,angry,tsundere")
    ap.add_argument("--dry-run", action="store_true", help="只列将要导入的图")
    args = ap.parse_args(argv)

    try:
        call(args.base, "/api/health", timeout=6)
    except Exception as exc:
        print("连不上 " + args.base + "：先跑 animechat run --port 8899 （" + str(exc)[:80] + "）")
        return 2

    only = {s.strip() for s in args.per and args.only.split(",") if s.strip()} or None
    # 已导入过的出处（note 含图片 URL），跳过省流量；内容层面服务端还会再按 sha1 去重。
    # 同名前缀的编号从库里已有的往后接，重号在界面上和提示词里都会混淆。
    try:
        existing = call(args.base, "/api/stickers?limit=800").get("stickers") or []
    except Exception:
        existing = []
    have = {str(s.get("note") or "") for s in existing if s.get("note")}
    used = {}
    for s in existing:
        lab = str(s.get("label") or "")
        if "·" in lab:
            head = lab.split("·", 1)[0]
            used[head] = max(used.get(head, 0), int(re.sub(r"\D", "", lab.split("·", 1)[1]) or 0))

    per = max(1, min(6, args.per))
    made = skipped = failed = 0
    tally = {}
    for emo, entries in QUERIES.items():
        if only and emo not in only:
            continue
        for query, tags, name in entries:
            if made >= args.cap:
                break
            try:
                res = call(args.base, "/api/stickers/search-web",
                           {"q": query, "limit": per + 6}, timeout=60)
            except Exception as exc:
                print("  搜索失败 [" + query + "]：" + str(exc)[:100])
                failed += 1
                continue
            results = res.get("results") or []
            picked = 0
            for item in results:
                if picked >= per or made >= args.cap:
                    break
                url = str(item.get("image_url") or "")
                if not url or not STATIC_OK.search(url):
                    continue          # jpg 多半带背景/水印，只要 gif/png/webp
                if any(url in h for h in have):
                    skipped += 1
                    continue
                label = name + "·" + str(used.get(name, 0) + picked + 1)
                note = (res.get("provider") or "?") + " 搜索「" + query + "」 " + url
                if args.dry_run:
                    print("  会导入 " + emo + " " + label + " <- " + url[:82])
                    picked += 1
                    made += 1
                    continue
                try:
                    st = call(args.base, "/api/stickers/import",
                              {"url": url, "tags": tags + [emo], "label": label,
                               "emotion": emo, "note": note})
                except urllib.error.HTTPError as exc:
                    print("  导入失败 HTTP " + str(exc.code) + " " + url[:60] + " "
                          + exc.read()[:90].decode("utf-8", "replace"))
                    failed += 1
                    continue
                except Exception as exc:
                    print("  导入失败 " + url[:60] + "：" + str(exc)[:90])
                    failed += 1
                    continue
                got = st.get("sticker") or {}
                if got.get("id"):
                    picked += 1
                    made += 1
                    tally[emo] = tally.get(emo, 0) + 1
                    have.add(note)
                time.sleep(SLEEP)
            time.sleep(SLEEP)
    print("")
    print(("演练完成" if args.dry_run else "导入完成") + "：处理 " + str(made) + " 张，跳过重复 "
          + str(skipped) + " 张，失败 " + str(failed) + " 次")
    if tally:
        print("按情绪：" + "、".join(k + " " + str(v) for k, v in sorted(tally.items())))
    if not args.dry_run:
        try:
            d = call(args.base, "/api/stickers?limit=800")
            sts = d.get("stickers") or []
            web = [s for s in sts if s.get("origin") != "builtin"]
            print("表情库共 " + str(len(sts)) + " 张（联网 " + str(len(web)) + " + 内置 "
                  + str(len(sts) - len(web)) + " 张，内置那批是离线兜底，不想要可以在表情包库里改标签）")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
