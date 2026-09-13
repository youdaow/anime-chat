"""本机图片处理：把外面来的一张图变成能当头像用的东西。

角色头像和「我」的头像需要的是同一套处理（裁成正方形、缩到小尺寸、另外留一份可反复
重框的原图），所以抽在这里共用；两边的差别只在「存到哪个文件名、地址记在哪」。
Pillow 不在时统一返回 None，让调用方决定怎么退化（至少别挡住用户想用的那张图）。
"""

from __future__ import annotations

import io

# 搜索结果里混着一堆站点头像和缩略图，比这还小的直接不收
MIN_AVATAR_PX = 140
# 暂存的原图压到长边 1024：够来回框 256 的头像，又不会为了一张侧栏 36px 的圆
# 把用户的硬盘占了（官方立绘动辄 2000×4000、好几 MB）
SRC_MAX_EDGE = 1024
SIDE = 256
SRC_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def pillow():
    try:
        from PIL import Image
        return Image
    except Exception:
        return None


def _open(image: bytes):
    """返回 (Image, im)。没 Pillow 给 None，打不开抛 ValueError（界面直接能显示这句）。"""
    Image = pillow()
    if Image is None:
        return None, None
    try:
        im = Image.open(io.BytesIO(image))
        im.load()
    except Exception as exc:
        raise ValueError("这张图打不开，换一张：" + str(exc)[:80]) from exc
    return Image, im


def square(image: bytes, ext: str = ".png", side: int = SIDE,
           crop: tuple[float, float, float] | None = None,
           min_px: int = MIN_AVATAR_PX) -> bytes | None:
    """裁成正方形、缩到 side，统一 PNG。

    必须裁：搜回来的尺寸千奇百怪，横长条立绘直接放进侧栏就是个被压扁的脸。
    必须缩：原图常常是 2MB 的官方大图，本机用不着，侧栏只有 36px。
    crop=(x, y, 边长) 指定「在原图像素里取哪一块」，不给就居中。越界钳回来而不是
    报错：手拖出来的数差一两个像素太正常了，为这个弹个错误只会烦人。
    """
    if not image:
        raise ValueError("图片内容为空")
    Image, im = _open(image)
    if im is None:
        return None
    w, h = im.size
    if min(w, h) < min_px:
        raise ValueError("图太小了（%d×%d），换一张大点的" % (w, h))
    n = min(w, h)
    x, y = (w - n) // 2, (h - n) // 2
    if crop is not None:
        cx, cy, cn = crop
        n = int(max(min_px, min(cn, n)))
        x = int(max(0, min(cx, w - n)))
        y = int(max(0, min(cy, h - n)))
    im = im.crop((x, y, x + n, y + n))
    im = im.convert("RGBA" if (im.mode in ("RGBA", "LA", "P") or ext == ".png") else "RGB")
    im = im.resize((side, side), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def normalized_source(image: bytes, max_edge: int = SRC_MAX_EDGE) -> tuple[bytes, int, int] | None:
    """原图转 PNG、长边压到 max_edge，返回 (字节, 宽, 高)。没 Pillow 给 None。"""
    if not image:
        raise ValueError("图片内容为空")
    Image, im = _open(image)
    if im is None:
        return None
    w, h = im.size
    edge = max(w, h)
    if edge > max_edge:
        k = max_edge / edge
        im = im.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
        w, h = im.size
    im = im.convert("RGBA" if im.mode in ("RGBA", "LA", "P") else "RGB")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), w, h

