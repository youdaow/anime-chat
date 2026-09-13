"""给「网上抓来的图」写一份模型看得懂的定义。

联网搜表情拿回来的文件名往往是 CDN 哈希（b284e22577144f5daa67.png），直接当 label 存进库，
提示词里就是一串乱码；情绪又认不出来（一律 neutral），角色按情绪挑图时永远绕开它 ——
用户看到的「表情加进去了却从来不发」就是这么来的。这里把「搜索词 + 结果标题 + 远程文件名」
整理成一个中文名、一组标签、一个情绪；已经有用的值一律保住，只补烂的。
"""

from __future__ import annotations

import re

from .emotion import EMOTION_KEYS, aliases, guess

# 搜图时挂在关键词后面那类废话：留在标签里只会让下次搜索更糊
NOISE = {
    "表情", "表情包", "梗图", "动图", "图片", "图", "高清", "素材", "下载", "大全", "合集",
    "透明", "抠图", "无背景", "免抠", "壁纸", "头像", "贴纸", "戳图", "水贴", "套图", "原图",
    "sticker", "stickers", "emoji", "emotes", "emote", "meme", "memes", "gif", "gifs",
    "png", "jpg", "jpeg", "webp", "image", "images", "pic", "pics", "pack", "free", "hd", "4k",
    # 图片 URL 上挂的处理参数（?x-oss-process=image/format,gif），拆出来全是废话
    "format", "quality", "resize", "process", "orient", "auto", "watermark", "sign",
    "expires", "oss", "cdn", "static", "upload", "thumb", "preview", "width", "height", "size",
}

_EXT = re.compile(r"\.(png|jpe?g|gif|webp|bmp|svg)$", re.I)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_TOKEN = re.compile(r"[0-9A-Za-z_\u4e00-\u9fff]{1,24}")
_HEX = re.compile(r"[0-9a-fA-F]{6,}")


_NOISE_BY_LEN = sorted((w for w in NOISE if _CJK.match(w)), key=len, reverse=True)


def looks_junk(name: str) -> bool:
    """这个名字有没有当「定义」的价值：一串哈希 / 带数字的 CDN id 就没有。"""
    s = _EXT.sub("", (name or "").strip()).strip()
    if not s:
        return True
    if _CJK.search(s):
        return False        # 带中文就保住：那是用户起的名字或搜索词本身
    if _HEX.fullmatch(s):
        return True
    digits = sum(1 for ch in s if ch.isdigit())
    if digits >= 3:
        return True
    body = re.sub(r"[ _\-.]", "", s)
    # 远程站那种 base62 串：整条是哈希，或者拆成单词后每个单词都带数字 / 中间夹大写
    # （"9lddQjsw"、"bootZcT3cSr"）—— 一个都当不了「定义」
    if digits and len(body) >= 6:
        return True
    if len(body) >= 6 and re.search(r"[A-Z]", s[1:]):
        return True
    if len(body) >= 12 and not re.search(r"[aeiou]{2}", body.lower()):
        return True
    return False


# 中文词往往是「表情包」「高清图」这种后缀粘着实义词的，逐层剥掉；只剩个空壳就丢掉
_SITE_TAIL = re.compile(r"[网站]$", re.UNICODE)


def _shrink(token: str) -> str:
    t = token
    changed = True
    while t and changed:
        changed = False
        low = t.lower()
        if low in NOISE or low.rstrip("s") in NOISE or t.isdigit() or _SITE_TAIL.search(t):
            t = t[:-1] if _SITE_TAIL.search(t) else ""
            changed = True
            continue
        for noise in _NOISE_BY_LEN:      # 「高清表情包」→「高清」→ 空
            if t.endswith(noise) and len(t) > len(noise):
                t = t[: -len(noise)]
                changed = True
                break
    return t


def clean(text: str) -> list[str]:
    """拆成能当标签用的词：去扩展名、去噪音词、去纯数字，保序去重。"""
    s = _EXT.sub(" ", text or "")
    out: list[str] = []
    seen: set[str] = set()
    for tok in _TOKEN.findall(s):
        raw = tok.strip("_")
        t = _shrink(raw)
        if not t or t.isdigit():
            continue
        if t != raw and len(t) == 1:
            continue         # 「千图网」剥到最后剩个「千」，那是残渣不是词
        if not _CJK.search(t) and len(t) < 3:
            continue         # 英文两三个字母的多半是站点名的碎片
        if looks_junk(t):
            continue         # 标题里夹着的哈希（IMG_2024 5533aaff.png）也别当标签
        if t.lower() in seen:
            continue         # Tsundere / tsundere 记一份就够
        seen.add(t.lower())
        out.append(t)
    return out[:10]


def define(query: str = "", title: str = "", filename: str = "", label: str = "",
           tags: list[str] | tuple[str, ...] | None = None, emotion: str = "") -> dict:
    """返回这张图该用的 {label, tags, emotion}。"""
    given = [str(t).strip() for t in (tags or []) if str(t).strip()]
    # 文件名 / 标题本身就是一串 CDN 哈希时，拆出来的碎片（9lddQjsw）也别要
    words = clean(query) + (clean(title) if not looks_junk(title) else []) \
        + (clean(filename) if not looks_junk(filename) else []) + given
    emo = (emotion or "").strip()
    if emo not in EMOTION_KEYS or emo == "neutral":
        emo = guess(" ".join([query, title, filename, label] + given)) or "neutral"
    name = (label or "").strip()
    if looks_junk(name):
        # 中文词最能说明「这是哪张图」；没有中文就退到情绪名
        cjk = next((w for w in words if _CJK.search(w)), "")
        name = cjk or (aliases(emo, 1)[0] if emo != "neutral" else "") or next(iter(words), "")
    if looks_junk(name) and emo != "neutral":
        name = aliases(emo, 1)[0]
    # 用户 / 前端给的标签原样保住（「233」这种也算话），只过滤派生出来的碎片
    merged: list[str] = list(dict.fromkeys(given))
    for extra in [name] + aliases(emo, 3) + words:
        if extra and extra not in merged and not looks_junk(extra):
            merged.append(extra)
    return {"label": name[:24], "tags": merged[:16], "emotion": emo}


def backfill(info: dict) -> dict:
    """库里已有的图：只补「情绪是 neutral」和「名字是垃圾」这两样，别的不动。

    返回要写回 meta 的补丁（空 = 没得补）。用户手动改过的那张会被标成 defined=user，
    自动补的那张标 defined=auto，下次扫描就不再反复改。
    """
    label = str(info.get("label") or "")
    cur_emo = str(info.get("emotion") or "neutral")
    if cur_emo != "neutral" and not looks_junk(label):
        return {}
    tags = [str(t).strip() for t in (info.get("tags") or []) if str(t).strip()]
    note = str(info.get("note") or "")
    # 只认路径最后一段：米游社那种 …gif?x-oss-process=image/format,gif 的尾巴会被当成文件名
    fname = note.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] if note.startswith("http") else ""
    d = define(query=tags[0] if tags else "", title=label, filename=fname, tags=tags, emotion=cur_emo)
    patch: dict = {}
    if cur_emo == "neutral" and d["emotion"] != "neutral":
        patch["emotion"] = d["emotion"]
    if looks_junk(label) and not looks_junk(d["label"]):
        patch["label"] = d["label"]
    extra = [t for t in d["tags"] if t not in tags]
    if extra:
        patch["tags"] = (tags + extra)[:16]
    if patch:
        patch["defined"] = "auto"
    return patch
