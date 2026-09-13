"""「我」这一方的头像。

名字和补充设定早就存在 settings 里了（user_name / user_notes），缺的是头像这一项：
一个 IM 里只有自己那侧没有脸，怎么看都像是还没做完。处理逻辑全部借用 images.py，和
角色头像一致；差别只有两点 —— 文件固定叫 me.png / me-src.png（谁的头像都是这一张），
以及它的地址和版本号记在 settings 里（角色记在角色卡上）。
"""

from __future__ import annotations

import time
from pathlib import Path

from . import images, media

NAME = "me"
SRC_EXTS = images.SRC_EXTS
MIN_AVATAR_PX = images.MIN_AVATAR_PX


def avatar_path() -> Path:
    return media.avatar_path(NAME + ".png")


def source_path() -> Path | None:
    """上次挑中那张原图存在哪（给「回头再框一次位置」用），没有就 None。"""
    for ext in SRC_EXTS:
        path = media.avatar_path(NAME + "-src" + ext)
        if path.is_file():
            return path
    return None


def source_url() -> str | None:
    path = source_path()
    return (media.AVATAR_PREFIX + path.name) if path else None


def _patch(url: str) -> dict:
    """要写进 settings 的补丁。版本号是给前端当缓存击穿用的：换了头像必须让浏览器
    去要新图，否则同名文件会被缓存在那儿，用户只会觉得自己点了没反应。"""
    return {"user_avatar": url, "user_avatar_at": round(time.time(), 3)}


def stage_source(image: bytes, ext: str = ".png") -> tuple[str, int, int]:
    """存一份可反复重框的原图，返回 (URL, 宽, 高)。

    为什么要留：位置往往要对着侧栏那个小圆看一眼才决定得下来，而网上那张链接随时
    会换或 404 —— 存在本机才谈得上「过两天回来再调一次」。
    """
    got = images.normalized_source(image)
    if got is None:                      # 没 Pillow：原样留着，尺寸报 0 让前端自己量
        path = media.avatar_path(NAME + "-src" + (ext or ".png").lower())
        path.write_bytes(image)
        return media.AVATAR_PREFIX + path.name, 0, 0
    blob, w, h = got
    old = source_path()
    path = media.avatar_path(NAME + "-src.png")
    path.write_bytes(blob)
    if old is not None and old != path:
        old.unlink(missing_ok=True)      # 换图时把上一个别的格式的暂存清掉
    return media.AVATAR_PREFIX + path.name, w, h


def set_from_image(image: bytes, ext: str = ".png", crop=None) -> dict:
    """落地一张自己的头像（裁方、缩图），返回 settings 补丁。"""
    blob = images.square(image, ext=ext, crop=crop)
    if blob is None:                     # 没 Pillow：原样存，至少别挡住用户想用的那张
        path = media.avatar_path(NAME + (ext or ".png").lower())
        path.write_bytes(image)
        return _patch(media.AVATAR_PREFIX + path.name)
    path = avatar_path()
    path.write_bytes(blob)
    return _patch(media.AVATAR_PREFIX + path.name)


def crop_from_source(crop: tuple[float, float, float]) -> dict:
    """对着暂存的原图重新框一次：不联网，纯本机重裁。"""
    path = source_path()
    if path is None:
        raise ValueError("这张没留下原图，重新找一张吧")
    return set_from_image(path.read_bytes(), ".png", crop=crop)


def clear() -> dict:
    """回到没有头像的状态：前端会退化成用名字首字当头像。"""
    avatar_path().unlink(missing_ok=True)
    for ext in SRC_EXTS:
        media.avatar_path(NAME + "-src" + ext).unlink(missing_ok=True)
    return {"user_avatar": "", "user_avatar_at": 0.0}

