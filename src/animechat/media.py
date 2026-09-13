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
