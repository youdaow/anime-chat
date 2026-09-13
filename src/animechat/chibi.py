"""程序化 Q 版表情与头像（Pillow 画的，不依赖任何外部素材）。

约定：
- 所有坐标都按边长归一化（0..1），所以 512 的表情和 256 的头像共用同一套布局。
- 先按 SUPER 倍超采样作画，再 LANCZOS 缩回去，边缘才不会有锯齿。
- 组件名与 assets/stickers/manifest.json 的 component_vocabulary 一一对应。
  认不出的名字**不抛异常**：回退到中性画法，并且每种只警告一次。

明确用简化几何近似的组件（够用且风格统一，不是半成品）：
  extras 的 smoke_puff / flame / moon / question_marks / zzz_bubble 用圆形、月牙遮罩、
  圆弧拼的问号、折线 z 表示；gesture 全部用「粗线段 + 圆手」的统一语法画。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

SUPER = 3                      # 超采样倍数
INK = (62, 48, 78, 255)        # 线稿
MOUTH_DARK = (122, 52, 68, 255)
TONGUE = (248, 141, 158, 255)
SKIN = (255, 247, 241, 255)
WHITE = (255, 255, 255, 255)

# 表情小人的发色：按 id 稳定轮换（不用 hash()，那个每进程加盐，重新生成会跳色）
_HAIR_CYCLE = ("#5C4B63", "#3F3A4E", "#7A5C6E", "#4E6377", "#6E5A46", "#52604F", "#6B4A57", "#465A6E")


def _hair_of(sid: str) -> tuple:
    idx = sum(ord(ch) for ch in str(sid or "")) % len(_HAIR_CYCLE)
    return _rgba(_HAIR_CYCLE[idx])

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/STZHONGS.TTF",
    "/System/Library/Fonts/PingFang.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
]

_warned: set[str] = set()
_font_cache: dict[int, object] = {}
_font_missing_warned = False


# ------------------------------------------------------------------ 小工具

def _warn(kind: str, name: str) -> None:
    key = kind + ":" + name
    if key in _warned:
        return
    _warned.add(key)
    print("[chibi] 未实现的组件 " + key + "，已回退为中性画法")


def _rgba(value, fallback=(255, 233, 240, 255)) -> tuple:
    s = str(value or "").strip()
    if not s.startswith("#"):
        return fallback
    s = s[1:]
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    if len(s) != 6:
        return fallback
    try:
        return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16), 255)
    except ValueError:
        return fallback


def _mix(c: tuple, other: tuple, k: float) -> tuple:
    return tuple(int(round(c[i] + (other[i] - c[i]) * k)) for i in range(3)) + (c[3],)


def _dark(c: tuple, k: float = 0.55) -> tuple:
    return _mix(c, (48, 36, 62, 255), k)


def _light(c: tuple, k: float = 0.55) -> tuple:
    return _mix(c, (255, 255, 255, 255), k)


def _font(px: int, ratio: float = 0.13):
    key = int(px * ratio)
    if key in _font_cache:
        return _font_cache[key]
    global _font_missing_warned
    font = None
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                font = ImageFont.truetype(path, key)
            except OSError:
                font = None
            else:
                break
    if font is None and not _font_missing_warned:
        _font_missing_warned = True
        print("[chibi] 找不到中文字体，表情上不再写字（只警告这一次）")
    _font_cache[key] = font
    return font


def _star(x: float, y: float, r: float, points: int = 4, rot: float = -math.pi / 2,
          inner: float = 0.42) -> list:
    pts = []
    for i in range(points * 2):
        ang = rot + i * math.pi / points
        rr = r if i % 2 == 0 else r * inner
        pts.append((x + math.cos(ang) * rr, y + math.sin(ang) * rr))
    return pts


def _heart(x: float, y: float, r: float) -> list:
    pts = [(x, y + r * 0.95)]
    for i in range(15):
        t = math.pi + i * math.pi / 14
        pts.append((x + math.cos(t) * r * 0.95, y - r * 0.25 + math.sin(t) * r * 0.62))
    return pts


def _ellipse_box(cx: float, cy: float, rx: float, ry: float, rot: float = 0.0) -> list:
    """旋转椭圆的外接多边形（Pillow 不支持椭圆旋转，用 polygon 画才有力学感）。"""
    pts = []
    for i in range(24):
        a = i * math.tau / 24
        x, y = math.cos(a) * rx, math.sin(a) * ry
        pts.append((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)))
    return pts


def _pt(p, s, r, cx, cy, lw):
    """把点沿远离中轴的方向推 lw，给马尾做描边。"""
    x, y = p
    dx = (x - cx) or (s * 1.0)
    dy = (y - cy) or 1.0
    n = math.hypot(dx, dy) or 1.0
    return (x + dx / n * lw, y + dy / n * lw)


def _limb(d: ImageDraw.ImageDraw, p1, p2, w: float, color, hand: float | None = None) -> None:
    d.line([p1, p2], fill=color, width=int(w))
    d.ellipse([p1[0] - w / 2, p1[1] - w / 2, p1[0] + w / 2, p1[1] + w / 2], fill=color)
    r = hand if hand is not None else w * 0.78
    d.ellipse([p2[0] - r, p2[1] - r, p2[0] + r, p2[1] + r], fill=color, outline=INK, width=max(2, int(w * 0.16)))


# ------------------------------------------------------------------ 底板

def _plate(img: Image.Image, d: ImageDraw.ImageDraw, px: int, color, radius: float) -> None:
    """die-cut 贴纸感：白边 + 彩色底 + 顶部高光。"""
    pad = px * 0.022
    d.rounded_rectangle([pad, pad, px - pad, px - pad], radius=radius * 1.06, fill=WHITE)
    inner = px * 0.055
    plate = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    pd = ImageDraw.Draw(plate)
    pd.rounded_rectangle([inner, inner, px - inner, px - inner], radius=radius, fill=color)
    shape = plate.getchannel("A")
    grad = Image.new("L", (px, px), 0)
    gd = ImageDraw.Draw(grad)
    step = max(1, px // 220)
    for y in range(0, px, step):
        gd.line([(0, y), (px, y)], fill=int(78 * max(0.0, 1 - y / (px * 0.62))))
    band = Image.new("RGBA", (px, px), (255, 255, 255, 255))
    lit = Image.composite(band, Image.new("RGBA", (px, px), (0, 0, 0, 0)), ImageChops.multiply(grad, shape))
    lit.putalpha(ImageChops.multiply(lit.getchannel("A"), shape))
    img.alpha_composite(plate)
    img.alpha_composite(lit)
    d.rounded_rectangle([inner, inner, px - inner, px - inner], radius=radius, outline=_dark(color, 0.3),
                        width=max(2, int(px * 0.006)))


# ------------------------------------------------------------------ 眼睛

def _eye(d, cx, cy, r, kind, ink, iris, side=0) -> None:
    """side: -1 左眼，1 右眼。kind 见 manifest.component_vocabulary.eyes。"""
    w = max(2, int(r * 0.16))

    def ball(fill=WHITE, outline=None):
        d.ellipse([cx - r, cy - r * 1.12, cx + r, cy + r * 1.12], fill=fill, outline=outline, width=w)

    if kind == "dot":
        d.ellipse([cx - r * 0.5, cy - r * 0.5, cx + r * 0.5, cy + r * 0.5], fill=ink)
    elif kind in ("happy_arc", "closed_up"):
        box = [cx - r, cy - r * (0.5 if kind == "happy_arc" else 0.22), cx + r, cy + r * 0.95]
        d.arc(box, 180, 360, fill=ink, width=w)
        if kind == "closed_up":
            d.arc([box[0] * 1.06 - r * 0.06, box[1] + r * 0.3, box[2] * 0.94 + r * 0.06, box[3] + r * 0.3],
                  180, 360, fill=_mix(ink, WHITE, 0.35), width=max(2, w // 2))
    elif kind == "sleep_closed":
        d.line([cx - r, cy, cx + r, cy], fill=ink, width=w)
        for i in (-1, 0, 1):
            d.line([cx + i * r * 0.6, cy, cx + i * r * 0.6 - r * 0.22, cy + r * 0.42], fill=ink, width=max(2, w // 2))
    elif kind == "wink":
        if side < 0:
            d.arc([cx - r, cy - r * 0.5, cx + r, cy + r * 0.95], 180, 360, fill=ink, width=w)
        else:
            ball(outline=ink)
            d.ellipse([cx - r * 0.5, cy - r * 0.55, cx + r * 0.5, cy + r * 0.55], fill=iris)
            d.ellipse([cx - r * 0.2, cy - r * 0.5, cx + r * 0.16, cy - r * 0.16], fill=WHITE)
    elif kind == "heart":
        d.polygon(_heart(cx, cy, r * 1.15), fill=(238, 92, 132, 255), outline=ink, width=w)
        d.ellipse([cx - r * 0.55, cy - r * 0.62, cx - r * 0.12, cy - r * 0.2], fill=(255, 225, 235, 235))
    elif kind == "sparkle":
        d.polygon(_star(cx, cy, r * 1.45, 4), fill=WHITE, outline=ink, width=w)
        d.polygon(_star(cx, cy, r * 0.72, 4, rot=-math.pi / 2 + math.pi / 4), fill=iris)
    elif kind == "wide_shock":
        ball(outline=ink)
        d.ellipse([cx - r * 0.34, cy - r * 0.36, cx + r * 0.34, cy + r * 0.36], fill=ink)
        d.ellipse([cx - r * 0.1, cy - r * 0.42, cx + r * 0.14, cy - r * 0.16], fill=WHITE)
    elif kind == "teary":
        ball(outline=ink)
        d.ellipse([cx - r * 0.58, cy - r * 0.6, cx + r * 0.58, cy + r * 0.6], fill=iris)
        d.ellipse([cx - r * 0.2, cy - r * 0.52, cx + r * 0.1, cy - r * 0.2], fill=WHITE)
        d.ellipse([cx - r * 0.9, cy + r * 0.4, cx + r * 0.9, cy + r * 1.5],
                  fill=(198, 228, 250, 190), outline=None)
    elif kind == "angry_slant":
        ball(outline=ink)
        d.ellipse([cx - r * 0.5, cy - r * 0.3, cx + r * 0.5, cy + r * 0.8], fill=ink)
        d.polygon([(cx - r * 1.12, cy - r * 1.25), (cx + r * 1.12, cy - r * 0.55),
                   (cx + r * 1.12, cy - r * 1.35), (cx - r * 1.12, cy - r * 1.75)], fill=SKIN)
        d.line([cx - r * 1.1, cy - r * 1.2, cx + r * 1.1, cy - r * 0.55], fill=ink, width=w)
    elif kind == "half_liddroop":
        ball(outline=ink)
        d.ellipse([cx - r * 0.5, cy - r * 0.2, cx + r * 0.5, cy + r * 0.75], fill=iris)
        d.polygon([(cx - r * 1.15, cy - r * 1.3), (cx + r * 1.15, cy - r * 1.3),
                   (cx + r * 1.15, cy - r * 0.05), (cx - r * 1.15, cy - r * 0.45)], fill=SKIN)
        d.line([cx - r * 1.15, cy - r * 0.38, cx + r * 1.15, cy - r * 0.05], fill=ink, width=w)
    elif kind == "side_eye":
        ball(outline=ink)
        off = r * 0.42 * (1 if side >= 0 else -1)
        d.ellipse([cx + off - r * 0.46, cy - r * 0.5, cx + off + r * 0.46, cy + r * 0.6], fill=ink)
        d.ellipse([cx + off - r * 0.14, cy - r * 0.42, cx + off + r * 0.14, cy - r * 0.1], fill=WHITE)
    elif kind in ("normal", "round", ""):
        ball(outline=ink)
        d.ellipse([cx - r * 0.52, cy - r * 0.56, cx + r * 0.52, cy + r * 0.6], fill=iris)
        d.ellipse([cx - r * 0.18, cy - r * 0.48, cx + r * 0.16, cy - r * 0.14], fill=WHITE)
    else:
        _warn("eyes", kind)
        _eye(d, cx, cy, r, "normal", ink, iris, side)


def _eyes(d, cx, cy, r, kind, ink, iris, gap) -> None:
    _eye(d, cx - gap, cy, r, kind, ink, iris, -1)
    _eye(d, cx + gap, cy, r, kind, ink, iris, 1)


# ------------------------------------------------------------------ 嘴 / 眉

def _mouth(d, cx, cy, w, kind, ink) -> None:
    lw = max(2, int(w * 0.13))

    def open_half(depth, teeth=True):
        box = [cx - w, cy - depth * 0.28, cx + w, cy + depth]
        d.pieslice(box, 0, 180, fill=MOUTH_DARK, outline=ink, width=lw)
        if teeth:
            d.chord([box[0] + w * 0.12, box[1], box[2] - w * 0.12, cy + depth * 0.34],
                    0, 180, fill=WHITE)
        return box

    if kind == "grin_open":
        box = open_half(w * 0.95)
        d.pieslice([cx - w * 0.5, cy + w * 0.18, cx + w * 0.5, box[3] + w * 0.12], 0, 180, fill=TONGUE)
    elif kind == "open_talk":
        d.ellipse([cx - w * 0.55, cy - w * 0.42, cx + w * 0.55, cy + w * 0.66], fill=MOUTH_DARK,
                  outline=ink, width=lw)
        d.pieslice([cx - w * 0.42, cy + w * 0.1, cx + w * 0.42, cy + w * 0.86], 0, 180, fill=TONGUE)
    elif kind == "smile_small":
        d.arc([cx - w * 0.62, cy - w * 0.34, cx + w * 0.62, cy + w * 0.62], 0, 180, fill=ink, width=lw)
    elif kind == "tongue":
        d.arc([cx - w * 0.75, cy - w * 0.4, cx + w * 0.75, cy + w * 0.5], 0, 180, fill=ink, width=lw)
        d.rounded_rectangle([cx - w * 0.2, cy + w * 0.02, cx + w * 0.2, cy + w * 0.78],
                            radius=w * 0.2, fill=TONGUE, outline=ink, width=max(2, lw // 2))
    elif kind == "wavy_awkward":
        seg = w * 0.42
        for i in range(-1, 2):
            x0 = cx + i * seg - seg / 2
            up = i % 2 == 0
            box = [x0, cy - seg * 0.5, x0 + seg, cy + seg * 0.5]
            d.arc(box, 180 if up else 0, 360 if up else 180, fill=ink, width=lw)
    elif kind == "pout":
        d.ellipse([cx - w * 0.34, cy - w * 0.1, cx + w * 0.34, cy + w * 0.52], fill=TONGUE,
                  outline=ink, width=lw)
        d.line([cx - w * 0.75, cy - w * 0.12, cx - w * 0.36, cy + w * 0.05], fill=ink, width=lw)
        d.line([cx + w * 0.75, cy - w * 0.12, cx + w * 0.36, cy + w * 0.05], fill=ink, width=lw)
    elif kind == "frown":
        d.arc([cx - w * 0.7, cy + w * 0.05, cx + w * 0.7, cy + w * 0.85], 180, 360, fill=ink, width=lw)
    elif kind == "teeth_grit":
        box = [cx - w * 0.82, cy - w * 0.24, cx + w * 0.82, cy + w * 0.42]
        d.rounded_rectangle(box, radius=w * 0.18, fill=WHITE, outline=ink, width=lw)
        d.line([box[0], (box[1] + box[3]) / 2, box[2], (box[1] + box[3]) / 2], fill=ink,
               width=max(2, lw // 2))
        for i in range(-2, 3):
            x = cx + i * w * 0.32
            d.line([x, box[1] + w * 0.05, x, box[3] - w * 0.05], fill=_mix(ink, WHITE, 0.45),
                   width=max(2, lw // 3))
    elif kind == "o_shock":
        d.ellipse([cx - w * 0.34, cy - w * 0.3, cx + w * 0.34, cy + w * 0.62], fill=MOUTH_DARK,
                  outline=ink, width=lw)
    elif kind == "smirk":
        # 一侧上扬、一侧平：得意、坏笑、看穿你
        d.arc([cx - w * 0.9, cy - w * 0.2, cx + w * 0.3, cy + w * 0.6], 300, 360, fill=ink, width=lw)
        d.line([cx - w * 0.55, cy + w * 0.16, cx + w * 0.72, cy + w * 0.02], fill=ink, width=lw)
        d.line([cx + w * 0.72, cy + w * 0.02, cx + w * 0.86, cy - w * 0.22], fill=ink, width=lw)
    elif kind == "flat":
        d.line([cx - w * 0.62, cy + w * 0.06, cx + w * 0.62, cy + w * 0.06], fill=ink, width=lw)
    else:
        _warn("mouth", kind)
        _mouth(d, cx, cy, w, "smile_small", ink)


def _brow(d, cx, cy, w, kind, ink, side) -> None:
    lw = max(2, int(w * 0.14))
    if kind == "hidden":
        return
    if kind == "raised":
        cy -= w * 0.30
        kind = "normal"
    if kind == "normal":
        d.arc([cx - w, cy - w * 0.5, cx + w, cy + w * 0.5], 180, 360, fill=ink, width=lw)
    elif kind == "angry":
        inner, outer = cx - side * w, cx + side * w
        d.line([outer, cy - w * 0.42, inner, cy + w * 0.2], fill=ink, width=lw)
    elif kind == "sad_worry":
        inner, outer = cx - side * w, cx + side * w
        d.line([outer, cy + w * 0.16, inner, cy - w * 0.34], fill=ink, width=lw)
    else:
        _warn("brows", kind)
        _brow(d, cx, cy, w, "normal", ink, side)


# ------------------------------------------------------------------ 头发 / 五官之外

def _hair_cap(d, cx, cy, r, color, ink, style="short", back=None) -> None:
    """头顶的发帽；style 决定两侧的垂法。back 里的部分画在头后面。

    先按外扩一圈的形状铺墨色，再叠颜色 —— 浅色头发（白/银）在浅背景上才分得开。
    """
    lw = max(3, int(r * 0.07))
    if back in ("long", "twin", "hime", "bob"):
        side_w = r * (0.62 if back == "bob" else 0.5)
        for s in (-1, 1):
            x0 = cx + s * r * 0.86 - side_w / 2
            y0 = cy - r * 0.5
            y1 = cy + r * (1.5 if back != "bob" else 1.05)
            rad = side_w * 0.45
            d.rounded_rectangle([x0 - lw, y0 - lw, x0 + side_w + lw, y1 + lw], radius=rad + lw, fill=ink)
            d.rounded_rectangle([x0, y0, x0 + side_w, y1], radius=rad, fill=color)
    if back == "twin":
        for s in (-1, 1):
            x = cx + s * r * 1.22
            pts = [(x, cy - r * 0.35), (x + s * r * 0.62, cy + r * 0.15),
                   (x + s * r * 0.3, cy + r * 1.5), (x - s * r * 0.16, cy + r * 0.5)]
            d.polygon([(_pt(p, s, r, cx, cy, lw)) for p in pts], fill=ink)
            d.polygon(pts, fill=color)
    top = [cx - r * 1.06, cy - r * 1.12, cx + r * 1.06, cy + r * 1.02]
    d.pieslice([top[0] - lw, top[1] - lw, top[2] + lw, top[3] + lw], 150, 390, fill=ink)
    d.pieslice(top, 150, 390, fill=color)
    d.rectangle([top[0] + r * 0.06, cy - r * 0.28, top[2] - r * 0.06, cy + r * 0.05], fill=color)
    # 刘海
    if style == "hime":
        d.rectangle([cx - r * 0.98, cy - r * 0.34, cx + r * 0.98, cy - r * 0.02], fill=color)
    else:
        n = 3 if style != "bob" else 4
        for i in range(n):
            x0 = cx - r * 0.95 + i * (r * 1.9 / n)
            d.polygon([(x0, cy - r * 0.36), (x0 + r * 1.9 / n, cy - r * 0.36),
                       (x0 + r * 0.95 / n, cy + r * 0.16)], fill=color)
    if style == "twin":
        for s in (-1, 1):
            d.ellipse([cx + s * r * 1.05 - r * 0.2, cy - r * 0.5 - r * 0.2,
                       cx + s * r * 1.05 + r * 0.2, cy - r * 0.5 + r * 0.2], fill=_light(color, 0.5),
                      outline=ink, width=max(2, int(r * 0.06)))
    if style == "short":
        d.arc([cx - r * 0.5, cy - r * 1.15, cx + r * 0.62, cy - r * 0.35], 200, 320,
              fill=_light(color, 0.55), width=max(3, int(r * 0.13)))
    if style == "long":
        d.arc([cx - r * 0.72, cy - r * 1.2, cx + r * 0.72, cy - r * 0.28], 210, 330,
              fill=_light(color, 0.5), width=max(3, int(r * 0.14)))


# ------------------------------------------------------------------ 装饰件

def _extra(d, px, cx, cy, r, name, ink, pal) -> None:
    u = px / 512.0
    if name == "sparkle":
        for (x, y, s) in ((0.16, 0.2, 34), (0.84, 0.16, 26), (0.2, 0.72, 20), (0.8, 0.66, 30)):
            d.polygon(_star(px * x, px * y, s * u, 4), fill=_light(pal, 0.75), outline=ink, width=int(3 * u))
    elif name == "star":
        d.polygon(_star(px * 0.82, px * 0.2, 40 * u, 5), fill=(255, 217, 92, 255), outline=ink, width=int(4 * u))
    elif name == "hearts":
        for (x, y, s) in ((0.82, 0.22, 30), (0.18, 0.28, 22), (0.76, 0.7, 18)):
            d.polygon(_heart(px * x, px * y, s * u), fill=(244, 96, 138, 255), outline=ink, width=int(3 * u))
    elif name == "blush_lines":
        for s in (-1, 1):
            bx = cx + s * r * 0.62
            for i in range(3):
                off = (i - 1) * 11 * u
                d.line([bx - 13 * u + off, cy + r * 0.34, bx + 9 * u + off, cy + r * 0.2],
                       fill=(240, 120, 150, 220), width=int(7 * u))
    elif name == "tear_drops":
        for s in (-1, 1):
            x = cx + s * r * 0.72
            y = cy + r * 0.55
            d.polygon([(x, y - 26 * u), (x - 15 * u, y + 6 * u), (x, y + 26 * u), (x + 15 * u, y + 6 * u)],
                      fill=(150, 208, 248, 235), outline=ink, width=int(3 * u))
            d.ellipse([x - 5 * u, y - 2 * u, x + 2 * u, y + 6 * u], fill=WHITE)
    elif name == "sweat_drop":
        x, y = cx + r * 1.05, cy - r * 0.62
        d.polygon([(x, y - 30 * u), (x - 17 * u, y + 8 * u), (x, y + 30 * u), (x + 17 * u, y + 8 * u)],
                  fill=(170, 218, 250, 240), outline=ink, width=int(4 * u))
        d.ellipse([x - 6 * u, y - 4 * u, x + 2 * u, y + 8 * u], fill=WHITE)
    elif name == "shock_lines":
        for i in range(-2, 3):
            a = -math.pi / 2 + i * 0.30
            x0, y0 = cx + math.cos(a) * r * 1.18, cy + math.sin(a) * r * 1.18
            x1, y1 = cx + math.cos(a) * r * 1.58, cy + math.sin(a) * r * 1.58
            d.line([x0, y0, x1, y1], fill=ink, width=int(8 * u))
    elif name == "flame":
        for s in (-1, 1):
            x = cx + s * r * 1.16
            y = cy - r * 1.02
            d.polygon([(x, y - 34 * u), (x + 20 * u, y + 6 * u), (x, y + 30 * u), (x - 20 * u, y + 6 * u)],
                      fill=(255, 148, 60, 240), outline=ink, width=int(3 * u))
            d.polygon([(x, y - 14 * u), (x + 9 * u, y + 8 * u), (x - 9 * u, y + 8 * u)], fill=(255, 236, 150, 255))
    elif name == "smoke_puff":
        for (dx, dy, rr) in ((-0.1, 0, 30), (0.12, -0.05, 22), (0.24, 0.04, 17)):
            x, y = px * 0.78 + dx * px, px * 0.16 + dy * px
            d.ellipse([x - rr * u, y - rr * u, x + rr * u, y + rr * u], fill=(228, 228, 236, 225),
                      outline=ink, width=int(3 * u))
    elif name == "question_marks":
        for (x, y, s) in ((0.8, 0.2, 1.0), (0.17, 0.26, 0.7)):
            cxq, cyq, rr = px * x, px * y, 26 * u * s
            d.arc([cxq - rr, cyq - rr * 1.5, cxq + rr, cyq + rr * 0.3], 200, 100, fill=ink, width=int(9 * u * s))
            d.line([cxq, cyq + rr * 0.1, cxq, cyq + rr * 0.72], fill=ink, width=int(9 * u * s))
            d.ellipse([cxq - rr * 0.2, cyq + rr * 0.9, cxq + rr * 0.2, cyq + rr * 1.3], fill=ink)
    elif name == "zzz_bubble":
        for i, (x, y, s) in enumerate(((0.78, 0.22, 1.0), (0.88, 0.14, 0.72), (0.68, 0.12, 0.52))):
            w = 30 * u * s
            h = 34 * u * s
            x0, y0 = px * x, px * y
            d.line([x0 - w / 2, y0 - h / 2, x0 + w / 2, y0 - h / 2], fill=ink, width=int(8 * u * s))
            d.line([x0 + w / 2, y0 - h / 2, x0 - w / 2, y0 + h / 2], fill=ink, width=int(8 * u * s))
            d.line([x0 - w / 2, y0 + h / 2, x0 + w / 2, y0 + h / 2], fill=ink, width=int(8 * u * s))
    elif name == "music_note":
        for (x, y, s) in ((0.19, 0.24, 1.0), (0.83, 0.3, 0.75)):
            nx, ny = px * x, px * y
            d.ellipse([nx - 15 * u * s, ny, nx + 9 * u * s, ny + 20 * u * s], fill=ink)
            d.line([nx + 9 * u * s, ny + 10 * u * s, nx + 9 * u * s, ny - 34 * u * s], fill=ink, width=int(6 * u * s))
            d.polygon([(nx + 9 * u * s, ny - 34 * u * s), (nx + 30 * u * s, ny - 24 * u * s),
                       (nx + 9 * u * s, ny - 16 * u * s)], fill=ink)
    elif name == "petal":
        for (x, y, rot) in ((0.18, 0.22, 0.5), (0.84, 0.26, -0.7), (0.76, 0.72, 1.2)):
            pts = _ellipse_box(px * x, px * y, 26 * u, 13 * u, rot)
            d.polygon(pts, fill=(255, 176, 200, 235), outline=ink, width=int(3 * u))
    elif name == "moon":
        mx, my, rr = px * 0.82, px * 0.2, 34 * u
        layer = Image.new("RGBA", (int(rr * 4), int(rr * 4)), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.ellipse([0, 0, rr * 2, rr * 2], fill=(255, 240, 176, 255))
        ld.ellipse([rr * 0.7, -rr * 0.3, rr * 0.7 + rr * 2, rr * 1.7], fill=(0, 0, 0, 0))
        d.pieslice([mx - rr, my - rr, mx + rr, my + rr], 100, 260, fill=(255, 240, 176, 255), outline=ink,
                   width=int(4 * u))
    elif name == "cool_shades":
        for s in (-1, 1):
            ex = cx + s * r * 0.62
            d.rounded_rectangle([ex - r * 0.62, cy - r * 0.5, ex + r * 0.62, cy + r * 0.5],
                                radius=r * 0.22, fill=(48, 42, 62, 235), outline=ink, width=int(5 * u))
            d.polygon([(ex - r * 0.5, cy - r * 0.3), (ex - r * 0.15, cy - r * 0.3),
                       (ex - r * 0.42, cy + r * 0.3), (ex - r * 0.66, cy + r * 0.16)],
                      fill=(255, 255, 255, 90))
        d.line([cx - r * 0.66, cy - r * 0.1, cx + r * 0.66, cy - r * 0.1], fill=ink, width=int(6 * u))
    else:
        _warn("extras", name)


# ------------------------------------------------------------------ 手势

def _gesture(d, px, cx, cy, r, name, ink, skin) -> None:
    u = px / 512.0
    w = 26 * u
    base_y = cy + r * 1.15
    if name in ("none", "", None):
        return
    lw = max(3, int(w))
    if name == "cheer_arms":
        for s in (-1, 1):
            _limb(d, (cx + s * r * 0.95, base_y), (cx + s * r * 1.5, cy - r * 0.15), lw, skin, hand=w * 1.15)
    elif name == "hug_arms":
        for s in (-1, 1):
            _limb(d, (cx + s * r * 1.05, base_y + 6 * u), (cx - s * r * 0.2, base_y - r * 0.35), lw, skin,
                  hand=w * 1.1)
    elif name == "akimbo":
        for s in (-1, 1):
            elbow = (cx + s * r * 1.55, base_y - r * 0.1)
            _limb(d, (cx + s * r * 0.9, base_y - r * 0.55), elbow, lw, skin, hand=None)
            _limb(d, elbow, (cx + s * r * 0.72, base_y + r * 0.1), lw, skin, hand=w * 1.05)
    elif name == "thumbs_up":
        _limb(d, (cx + r * 1.0, base_y), (cx + r * 1.42, cy + r * 0.15), lw, skin, hand=w * 1.4)
        d.rounded_rectangle([cx + r * 1.42 - w * 0.5, cy - r * 0.1, cx + r * 1.42 + w * 0.5, cy + r * 0.2],
                            radius=w * 0.5, fill=skin, outline=ink, width=int(4 * u))
    elif name == "facepalm":
        _limb(d, (cx - r * 1.0, base_y), (cx - r * 0.62, cy - r * 0.05), lw, skin, hand=None)
        d.ellipse([cx - r * 1.05, cy - r * 0.42, cx - r * 0.18, cy + r * 0.4],
                  fill=_mix(skin, WHITE, 0.28), outline=ink, width=int(4 * u))
    elif name == "point":
        _limb(d, (cx + r * 0.98, base_y - r * 0.2), (cx + r * 1.62, base_y - r * 0.62), lw, skin, hand=w * 1.05)
        d.line([cx + r * 1.62, base_y - r * 0.62, cx + r * 2.0, base_y - r * 0.86], fill=skin, width=int(lw * 0.7))
    elif name == "shrug":
        for s in (-1, 1):
            _limb(d, (cx + s * r * 0.95, base_y - r * 0.3), (cx + s * r * 1.6, base_y - r * 0.72), lw, skin,
                  hand=w * 1.2)
    elif name == "peace":
        _limb(d, (cx + r * 0.98, base_y), (cx + r * 1.34, cy + r * 0.05), lw, skin, hand=w * 1.1)
        for i, a in enumerate((-0.5, -1.0)):
            d.line([cx + r * 1.34, cy + r * 0.05, cx + r * 1.34 + math.cos(a) * r * 0.5,
                    cy + r * 0.05 + math.sin(a) * r * 0.5], fill=skin, width=int(lw * 0.62))
    elif name == "clap":
        for s in (-1, 1):
            _limb(d, (cx + s * r * 1.0, base_y - r * 0.1), (cx + s * r * 0.22, base_y - r * 0.62), lw, skin,
                  hand=w * 1.25)
    else:
        _warn("gesture", name)
        _gesture(d, px, cx, cy, r, "none", ink, skin)


# ------------------------------------------------------------------ 表情主图

def draw_sticker(spec, palettes, size: int = 512, radius: float = 0.1875) -> Image.Image:
    palettes = palettes or {}
    px = size * SUPER
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pal = _rgba(palettes.get(spec.get("palette")), (255, 233, 240, 255))
    face = spec.get("face") or {}
    extras = list(face.get("extras") or [])

    _plate(img, d, px, pal, px * radius)
    d = ImageDraw.Draw(img)

    cx, cy, r = px * 0.5, px * 0.455, px * 0.245
    hair = _hair_of(spec.get("id"))
    skin = SKIN
    emo = spec.get("emotion") or ""

    style = "twin" if emo in ("love", "happy") else ("short" if emo in ("angry", "shock") else "bob")
    _hair_cap(d, cx, cy, r, hair, INK, style=style, back=style if style != "short" else None)
    _gesture(d, px, cx, cy, r, face.get("gesture", "none"), INK, skin)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=skin, outline=INK, width=max(3, int(px * 0.008)))
    _hair_cap(d, cx, cy - r * 0.06, r * 0.99, hair, INK, style=style)

    gap = r * 0.55
    er = r * 0.30
    eye_y = cy + r * 0.02
    _eyes(d, cx, eye_y, er, face.get("eyes", "normal"), INK, _mix(hair, (90, 120, 170, 255), 0.35), gap)
    _brow(d, cx - gap, eye_y - er * 2.05, er * 1.05, face.get("brows", "normal"), INK, -1)
    _brow(d, cx + gap, eye_y - er * 2.05, er * 1.05, face.get("brows", "normal"), INK, 1)
    _mouth(d, cx, cy + r * 0.62, r * 0.5, face.get("mouth", "smile_small"), INK)

    if emo in ("tsundere", "love", "shy") and "blush_lines" not in extras:
        _extra(d, px, cx, cy, r, "blush_lines", INK, pal)
    for name in extras:
        _extra(d, px, cx, cy, r, name, INK, pal)

    label = str(spec.get("label") or "")
    if label:
        font = _font(px, 0.135)
        if font is not None:
            tb = d.textbbox((0, 0), label, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            pad_x, pad_y = px * 0.035, px * 0.018
            bx0 = (px - tw) / 2 - pad_x
            bx1 = (px + tw) / 2 + pad_x
            by1 = px * 0.955
            by0 = by1 - th - pad_y * 2
            d.rounded_rectangle([bx0, by0, bx1, by1], radius=(by1 - by0) * 0.5,
                                fill=(48, 36, 62, 215))
            d.text(((px - tw) / 2 - tb[0], by0 + pad_y - tb[1] * 0.2), label, font=font, fill=WHITE)
    return img.resize((size, size), Image.LANCZOS)


# ------------------------------------------------------------------ 头像

_EXPRESSIONS = {
    # eyes, mouth, brows, blush
    "happy": ("normal", "smile_small", "normal", True),
    "calm": ("half_liddroop", "flat", "normal", False),
    "smug": ("half_liddroop", "smirk", "raised", False),
    "energetic": ("sparkle", "grin_open", "raised", True),
    "shy": ("closed_up", "pout", "sad_worry", True),
}


def draw_avatar(spec, size: int = 256) -> Image.Image:
    spec = spec or {}
    px = size * SUPER
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bg = _rgba(spec.get("bg_color"), (239, 241, 246, 255))
    hair = _rgba(spec.get("hair_color"), (142, 155, 179, 255))
    hair2 = _rgba(spec.get("hair_color2"), _light(hair, 0.45))
    iris = _rgba(spec.get("eye_color"), (126, 140, 168, 255))
    style = spec.get("style") or "short"
    expr = _EXPRESSIONS.get(spec.get("expression") or "happy", _EXPRESSIONS["happy"])

    d.rounded_rectangle([px * 0.02, px * 0.02, px * 0.98, px * 0.98], radius=px * 0.19, fill=bg)
    d.ellipse([px * 0.05, px * 0.05, px * 0.95, px * 0.95], fill=_light(bg, 0.45))
    cx, cy, r = px * 0.5, px * 0.56, px * 0.29

    # 脖子 + 肩，头像更有身份感
    d.rectangle([cx - r * 0.3, cy + r * 0.7, cx + r * 0.3, cy + r * 1.5], fill=SKIN)
    d.polygon([(cx - r * 1.7, px), (cx - r * 0.55, cy + r * 1.25), (cx + r * 0.55, cy + r * 1.25),
               (cx + r * 1.7, px)], fill=_mix(hair, WHITE, 0.72))
    d.line([(cx - r * 0.55, cy + r * 1.28), (cx, cy + r * 1.62), (cx + r * 0.55, cy + r * 1.28)],
           fill=_dark(hair, 0.25), width=max(3, int(px * 0.012)))

    _hair_cap(d, cx, cy, r, hair, INK, style=style, back=style)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SKIN, outline=INK, width=max(3, int(px * 0.011)))
    _hair_cap(d, cx, cy - r * 0.05, r * 0.98, hair, INK, style=style)
    d.arc([cx - r * 0.8, cy - r * 1.05, cx + r * 0.55, cy - r * 0.15], 210, 320,
          fill=hair2, width=max(4, int(r * 0.14)))

    gap, er = r * 0.52, r * 0.26
    eye_y = cy + r * 0.05
    _eyes(d, cx, eye_y, er, expr[0], INK, iris, gap)
    _brow(d, cx - gap, eye_y - er * 2.1, er * 1.0, expr[2], INK, -1)
    _brow(d, cx + gap, eye_y - er * 2.1, er * 1.0, expr[2], INK, 1)
    _mouth(d, cx, cy + r * 0.62, r * 0.42, expr[1], INK)
    if expr[3]:
        for s in (-1, 1):
            bx = cx + s * r * 0.66
            d.ellipse([bx - r * 0.17, cy + r * 0.3, bx + r * 0.17, cy + r * 0.46],
                      fill=(248, 158, 178, 150))

    acc = spec.get("accessory") or "none"
    u = px / 256.0
    if acc == "glasses":
        for s in (-1, 1):
            ex = cx + s * gap
            box = [ex - er * 1.5, eye_y - er * 1.7, ex + er * 1.5, eye_y + er * 1.5]
            d.rounded_rectangle(box, radius=er * 0.6, outline=INK, width=int(6 * u))
            d.line([box[0] + er * 0.35, box[3] - er * 0.3, box[0] + er * 1.05, box[1] + er * 0.25],
                   fill=(255, 255, 255, 210), width=int(5 * u))
        d.line([cx - gap + er * 1.5, eye_y - er * 0.3, cx + gap - er * 1.5, eye_y - er * 0.3],
               fill=INK, width=int(6 * u))
        for s in (-1, 1):
            d.line([cx + s * (gap + er * 1.5), eye_y - er * 0.5, cx + s * r * 1.0, eye_y - er * 0.1],
                   fill=INK, width=int(6 * u))
    elif acc == "eyepatch":
        ex = cx - gap
        d.rounded_rectangle([ex - er * 1.6, eye_y - er * 1.6, ex + er * 1.6, eye_y + er * 1.6],
                            radius=er * 0.5, fill=(56, 48, 72, 245), outline=INK, width=int(4 * u))
        d.line([ex - er * 1.6, eye_y - er * 0.6, cx - r * 1.05, eye_y - r * 0.5], fill=(56, 48, 72, 255),
               width=int(9 * u))
        d.line([ex + er * 1.4, eye_y, cx + r * 1.02, eye_y - r * 0.25], fill=(56, 48, 72, 255), width=int(9 * u))
    elif acc == "hairpin":
        d.polygon(_star(cx + r * 0.78, cy - r * 0.72, r * 0.24, 5), fill=(255, 214, 96, 255), outline=INK,
                  width=int(4 * u))
        d.ellipse([cx + r * 0.55, cy - r * 0.52, cx + r * 0.72, cy - r * 0.36], fill=(244, 96, 138, 255))
    elif acc == "cat_ear":
        for s in (-1, 1):
            ex = cx + s * r * 0.78
            base = cy - r * 0.92
            d.polygon([(ex - r * 0.26, base), (ex + r * 0.24, base - r * 0.42), (ex + r * 0.1, base + r * 0.26)],
                      fill=hair, outline=INK, width=int(5 * u))
            d.polygon([(ex - r * 0.11, base - r * 0.02), (ex + r * 0.13, base - r * 0.26),
                       (ex + r * 0.05, base + r * 0.12)], fill=(250, 176, 196, 255))
    return img.resize((size, size), Image.LANCZOS)


def load_manifest(path) -> dict:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))
