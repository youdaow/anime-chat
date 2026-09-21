"""媒体路径解析：把 /media/... 的 URL 安全映射到磁盘文件。

只允许三类前缀，并强制 resolve 后仍在允许目录内（防 ../ 穿越）。
"""

from __future__ import annotations

from pathlib import Path

from .config import AVATAR_DIR, DATA_DIR, PKG_DIR

BUILTIN_PREFIX = "/media/builtin/"     # 随包发的素材（内置表情、内置头像）
STICKER_PREFIX = "/media/stickers/"    # 用户表情库
AVATAR_PREFIX = "/media/avatars/"      # 导入/程序生成的角色头像


def is_media(url: str) -> bool:
    return bool(url) and url.startswith((BUILTIN_PREFIX, STICKER_PREFIX, AVATAR_PREFIX))


def resolve(url: str) -> Path | None:
    """URL -> 真实文件路径；不存在或不安全时返回 None。"""
    if not url or "?" in url or "#" in url:
        return None
    if url.startswith(BUILTIN_PREFIX):
        root, rel = PKG_DIR / "assets", url[len(BUILTIN_PREFIX):]
    elif url.startswith(STICKER_PREFIX):
        root, rel = DATA_DIR / "stickers", url[len(STICKER_PREFIX):]
    elif url.startswith(AVATAR_PREFIX):
        root, rel = AVATAR_DIR, url[len(AVATAR_PREFIX):]
    else:
        return None
    rel = rel.replace("\\", "/")
    if rel.startswith("/") or ".." in Path(rel).parts:
        return None
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def avatar_path(name: str) -> Path:
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_.") or "avatar.png"
    return AVATAR_DIR / safe


# ---------------------------------------------------------------- 表情缩略图
# 库里最大的原图有 13MB（QQ 导入那批），而表情面板一次要画 120 格 —— 全按原图拉就是
# 几十兆，手机上必卡。缩略图按需生成、落盘缓存，路径镜像源文件（thumbs/原名.jpg.w220.webp），
# 所以从缩略图反推源文件不用查表，也不会有中文/连字符文件名解析歧义。
# 放在 stickers/thumbs/ 子目录里是安全的：库扫盘时 `is_file()` 会跳过目录。
THUMB_DIR = "thumbs"
THUMB_WIDTH = 220
THUMB_SUFFIX = ".w%d.webp" % THUMB_WIDTH
THUMB_MIN_BYTES = 60 * 1024      # 比这还小的图，压了也省不了几 KB，直接用原图


def thumb_url(url: str) -> str | None:
    """该用哪张缩略图；不需要/不支持时返回 None（前端就退回原图）。"""
    if not url or not url.startswith(STICKER_PREFIX):
        return None
    name = url[len(STICKER_PREFIX):]
    if "/" in name or not name or name.startswith("."):
        return None
    if name.lower().endswith(".gif"):
        return None          # gif 压成 webp 只剩第一帧，动画就没了
    src = resolve(url)
    if src is None:
        return None
    try:
        if src.stat().st_size < THUMB_MIN_BYTES:
            return None      # 小图压了也省不了几 KB，别多一次生成
    except OSError:
        return None
    return STICKER_PREFIX + THUMB_DIR + "/" + name + THUMB_SUFFIX


def thumb_pair(turl: str) -> tuple[Path, Path] | None:
    """缩略图 URL -> (要写的缩略图路径, 它的源文件)。"""
    if not turl or not turl.startswith(STICKER_PREFIX + THUMB_DIR + "/"):
        return None
    rel = turl[len(STICKER_PREFIX):]
    name = rel[len(THUMB_DIR) + 1:]
    if not name.endswith(THUMB_SUFFIX) or "/" in name:
        return None
    root = DATA_DIR / "stickers"
    src = resolve(STICKER_PREFIX + name[:-len(THUMB_SUFFIX)])
    if src is None:
        return None
    out = (root / THUMB_DIR / name).resolve()
    try:
        out.relative_to(root.resolve())
    except ValueError:
        return None
    return out, src


def make_thumb(src: Path, out: Path) -> bool:
    """用 Pillow 压一张。缺 Pillow / 图坏了就 False —— 调用方退回原图，不能因此少一张表情。"""
    try:
        from PIL import Image
        out.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as im:
            im.load()
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "transparency" in im.info or im.mode == "P" else "RGB")
            im.thumbnail((THUMB_WIDTH, THUMB_WIDTH * 4))
            tmp = out.with_suffix(out.suffix + ".tmp")
            im.save(tmp, "WEBP", quality=72)
            tmp.replace(out)          # 半张图不该被缓存住
    except Exception:
        return False
    return True


def needs_thumb(turl: str) -> bool:
    """缩略图缺着、或者比源文件旧，都算要重做。"""
    pair = thumb_pair(turl)
    if pair is None:
        return False
    out, src = pair
    if not out.is_file():
        return True
    try:
        return out.stat().st_mtime < src.stat().st_mtime
    except OSError:
        return False
