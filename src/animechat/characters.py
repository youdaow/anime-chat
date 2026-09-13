"""角色库：内置角色 + 用户自定义 + 角色卡导入导出。

存储约定：
- 内置角色：包内 assets/characters.json（只读事实源）
- 用户角色 / 对内置角色的编辑：data/characters/<id>.json
- 隐藏某个内置角色：data/characters/_state.json 记 disabled
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import time
from pathlib import Path

from . import cardio, images, media
from .config import ASSET_DIR, character_dir, ensure_dirs
from .models import Appearance, Character


def _slugify(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^0-9a-zA-Z]+", "-", name or "").strip("-").lower()[:28]
    if not base or base == "-":
        base = "c" + hashlib.sha1((name or "?").encode("utf-8")).hexdigest()[:8]
    cand, i = base, 2
    while cand in taken:
        cand = base + "-" + str(i)
        i += 1
    return cand


def _load(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


class CharacterBook:
    def __init__(self) -> None:
        ensure_dirs()
        self.dir = character_dir()
        self._builtin_cache: dict[str, Character] | None = None

    # ---------------------------------------------------------- 内置
    def _builtins(self) -> dict[str, Character]:
        if self._builtin_cache is not None:
            return self._builtin_cache
        out: dict[str, Character] = {}
        manifest = ASSET_DIR / "characters.json"
        raw = _load(manifest)
        for item in raw.get("characters", []):
            if not isinstance(item, dict) or not item.get("name"):
                continue
            item = dict(item)
            item["builtin"] = True
            item["source"] = "builtin"
            av = item.get("avatar")
            if isinstance(av, str) and av and not av.startswith("/media/"):
                item["avatar"] = media.BUILTIN_PREFIX + av
            try:
                char = Character.model_validate(item)
            except Exception as exc:  # 人设写坏了也不能让整个应用起不来
                print("[warn] 内置角色解析失败", item.get("id"), exc)
                continue
            out[char.id] = char
        self._builtin_cache = out
        return out

    # ---------------------------------------------------------- 查询
    def _user_chars(self) -> dict[str, Character]:
        out: dict[str, Character] = {}
        if not self.dir.is_dir():
            return out
        for path in sorted(self.dir.glob("*.json")):
            if path.name == "_state.json":
                continue
            item = _load(path)
            if not item.get("name"):
                continue
            try:
                char = Character.model_validate(item)
            except Exception as exc:
                print("[warn] 角色文件解析失败", path.name, exc)
                continue
            out[char.id] = char
        return out

    def disabled_ids(self) -> set[str]:
        st = _load(self.dir / "_state.json")
        return {str(i) for i in st.get("disabled", [])}

    def list(self, include_hidden: bool = False) -> list[Character]:
        merged = dict(self._builtins())
        merged.update(self._user_chars())  # 用户可覆盖内置
        hidden = self.disabled_ids()
        chars = [c for cid, c in merged.items() if include_hidden or cid not in hidden]
        chars.sort(key=lambda c: (not c.builtin, c.created_at))
        return chars

    def get(self, cid: str) -> Character | None:
        merged = dict(self._builtins())
        merged.update(self._user_chars())
        return merged.get(cid)

    def exists(self, cid: str) -> bool:
        return self.get(cid) is not None

    # ---------------------------------------------------------- 写入
    def save(self, char: Character) -> Character:
        ensure_dirs()
        taken = {c.id for c in self.list(include_hidden=True)} - {char.id}
        if not char.id or not re.fullmatch(r"[0-9a-zA-Z][0-9a-zA-Z_-]{0,47}", char.id):
            char.id = _slugify(char.name, taken)
        elif char.id in taken:
            char.id = _slugify(char.name, taken)
        char.name = (char.name or "未命名角色").strip()[:40]
        char.updated_at = time.time()
        if not char.builtin:
            char.created_at = char.created_at or char.updated_at
        (self.dir / (char.id + ".json")).write_text(
            char.model_dump_json(indent=2), encoding="utf-8"
        )
        # 取消隐藏
        st_path = self.dir / "_state.json"
        st = _load(st_path)
        if char.id in self.disabled_ids():
            st["disabled"] = [i for i in st.get("disabled", []) if i != char.id]
            st_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        self._builtin_cache = None if char.builtin else self._builtin_cache
        return char

    def delete(self, cid: str) -> tuple[bool, str]:
        """内置角色只能隐藏，不删源文件。"""
        char = self.get(cid)
        if char is None:
            return False, "not_found"
        builtin = cid in self._builtins()
        path = self.dir / (cid + ".json")
        if path.is_file():
            path.unlink()
        if builtin:
            st_path = self.dir / "_state.json"
            st = _load(st_path)
            st["disabled"] = sorted(set(st.get("disabled", []) or []) | {cid})
            st_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
            return True, "hidden"
        # 用户角色的头像是我们自己的文件，删角色时得一起收走：导入的角色卡存成
        # card_<id>.png，只清 gen_ 会让它永远留在盘上。内置头像走 /media/builtin/，
        # 前缀不是 AVATAR_PREFIX，天然不会误删。
        doomed = {media.avatar_path("gen_" + cid + ".png"), media.avatar_path("card_" + cid + ".png")}
        if char.avatar and char.avatar.startswith(media.AVATAR_PREFIX):
            p = media.resolve(char.avatar)
            if p is not None:
                doomed.add(p)
        for path in doomed:
            if path.is_file():
                path.unlink()
        return True, "deleted"

    def restore(self, cid: str) -> Character | None:
        """恢复内置角色到出厂状态（删掉覆盖文件并取消隐藏）。"""
        if cid not in self._builtins():
            return None
        path = self.dir / (cid + ".json")
        if path.is_file():
            path.unlink()
        st_path = self.dir / "_state.json"
        st = _load(st_path)
        st["disabled"] = [i for i in (st.get("disabled", []) or []) if i != cid]
        st_path.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.get(cid)

    def duplicate(self, cid: str) -> Character | None:
        src = self.get(cid)
        if src is None:
            return None
        clone = src.model_copy(deep=True)
        clone.id = ""
        clone.builtin = False
        clone.name = (src.name + " 的副本")[:40]
        clone.source = "manual"
        clone.created_at = time.time()
        clone.id = _slugify(src.name + "-copy", {c.id for c in self.list(True)})
        return self.save(clone)

    # ---------------------------------------------------------- 角色卡
    def import_payload(self, payload: bytes, filename: str) -> Character:
        card, image = cardio.parse_card_payload(payload, filename)
        taken = {c.id for c in self.list(include_hidden=True)}
        char = cardio.from_card(
            card,
            make_id=lambda name: _slugify(name, taken),
            source="png" if image is not None else "json",
            image=image,
        )
        if image is not None:
            path = media.avatar_path("card_" + char.id + ".png")
            path.write_bytes(image)
            char.avatar = media.AVATAR_PREFIX + path.name
        elif char.avatar == "__IMAGE__":
            char.avatar = None
        return self.save(char)

    def export_card(self, cid: str) -> dict:
        char = self.get(cid)
        if char is None:
            raise KeyError(cid)
        return cardio.to_card_v2(char)

    def export_png(self, cid: str) -> bytes:
        """导出成「PNG 角色卡」：有头像就写进头像，没有就画一张。"""
        char = self.get(cid)
        if char is None:
            raise KeyError(cid)
        base: bytes | None = None
        if char.avatar:
            p = media.resolve(char.avatar)
            if p is not None and p.suffix.lower() == ".png":
                base = p.read_bytes()
        if base is None:
            png = self.render_avatar_png(char)
            if png is None:
                raise RuntimeError("没有可写入角色卡的 PNG 底图（且无法程序化生成头像）")
            base = png
        return cardio.embed_card_png(base, cardio.to_card_v2(char))

    # ---------------------------------------------------------- 头像
    def set_avatar_from_image(self, char: Character, image: bytes, ext: str = ".png",
                             slot: str = "user") -> Character:
        """用一张外部图片当头像：写到 /media/avatars/<id>-<slot><ext>，
        替换掉旧的程序生成头像。图会原样落盘，不做缩放。"""
        if not image:
            raise ValueError("图片内容为空")
        ext = (ext or ".png").lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
            raise ValueError("不支持的图片格式：" + ext)
        old = char.avatar
        path = media.avatar_path(char.id + "-" + slot + ext)
        path.write_bytes(image)
        char.avatar = media.AVATAR_PREFIX + path.name
        if old and old != char.avatar and old.startswith(media.AVATAR_PREFIX):
            p = media.resolve(old)
            if p is not None and p.name != path.name:
                p.unlink(missing_ok=True)
        return self.save(char)

    # 搜索结果里混着一堆站点头像和缩略图，比这还小的直接不收
    MIN_AVATAR_PX = 140
    # 暂存的原图压到长边 1024：够来回框 256 的头像，又不会为了一张侧栏 36px 的圆
    # 把用户的硬盘占了（官方立绘动辄 2000×4000、好几 MB）
    SRC_MAX_EDGE = 1024
    SRC_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    # -------------------------------------------------- 联网头像：原图与裁剪

    def avatar_source_path(self, char: Character) -> Path | None:
        """上次挑中那张原图存在哪（给「回头再框一次位置」用），没有就 None。"""
        for ext in self.SRC_EXTS:
            path = media.avatar_path(char.id + "-src" + ext)
            if path.is_file():
                return path
        return None

    def avatar_source_url(self, char: Character) -> str | None:
        path = self.avatar_source_path(char)
        return (media.AVATAR_PREFIX + path.name) if path else None

    def stage_avatar_source(self, char: Character, image: bytes, ext: str = ".png") -> tuple[str, int, int]:
        """把挑中的原图存成 <id>-src.png，返回 (URL, 宽, 高)。

        为什么要留原图：位置往往要对着侧栏那个小圆看一眼才决定得下来，而网上那张
        链接随时会换或 404 —— 存在本机才谈得上「过两天回来再调一次」。
        """
        got = images.normalized_source(image, self.SRC_MAX_EDGE)
        if got is None:                        # 没 Pillow：原样留着，尺寸报 0 让前端自己量
            path = media.avatar_path(char.id + "-src" + (ext or ".png").lower())
            path.write_bytes(image)
            return media.AVATAR_PREFIX + path.name, 0, 0
        blob, w, h = got
        old = self.avatar_source_path(char)
        path = media.avatar_path(char.id + "-src.png")
        path.write_bytes(blob)
        if old is not None and old != path:    # 换图时把上一个别的格式的暂存清掉
            old.unlink(missing_ok=True)
        return media.AVATAR_PREFIX + path.name, w, h

    def crop_avatar_from_source(self, char: Character, crop: tuple[float, float, float],
                                side: int = 256) -> Character:
        """用暂存的原图重新框一次位置（不联网）。"""
        path = self.avatar_source_path(char)
        if path is None:
            raise ValueError("这张没留下原图，重新找一张吧")
        return self.set_avatar_from_web(char, path.read_bytes(), ".png", side=side, crop=crop)

    def set_avatar_from_web(self, char: Character, image: bytes, ext: str = ".png",
                            side: int = 256, crop: tuple[float, float, float] | None = None) -> Character:
        """把网上找来的一张图变成头像：裁成正方形、缩到 256，统一存 PNG。

        必须裁：搜回来的尺寸千奇百怪，横长条立绘直接放进侧栏就是个被压扁的脸。
        必须缩：原图常常是 2MB 的官方大图，本机用不着，侧栏只有 36px。
        crop=(x, y, 边长) 指定「在原图像素里取哪一块」，不给就居中。越界钳回来而不是
        报错：手拖出来的数差一两个像素太正常了，为这个弹个错误只会烦人。
        Pillow 不在或图打不开时原样落盘——至少别挡住用户想用的那张图。
        """
        blob = images.square(image, ext=ext, side=side, crop=crop, min_px=self.MIN_AVATAR_PX)
        if blob is None:                     # 没 Pillow：原样收下，别挡住用户想用的那张图
            return self.set_avatar_from_image(char, image, ext, slot="web")
        return self.set_avatar_from_image(char, blob, ".png", slot="web")


    def render_avatar_png(self, char: Character) -> bytes | None:
        try:
            from .chibi import draw_avatar
        except Exception:
            return None
        spec = char.appearance.model_dump()
        spec["name"] = ""
        try:
            img = draw_avatar(spec, size=256)
        except Exception as exc:
            print("[warn] 头像生成失败", char.id, exc)
            return None
        import io

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def ensure_avatar(self, char: Character) -> Character | None:
        """头像缺失时程序化补一张，返回（可能已改过的）角色。"""
        if char.avatar and media.resolve(char.avatar) is not None:
            return char
        png = self.render_avatar_png(char)
        if png is None:
            return None
        path = media.avatar_path("gen_" + char.id + ".png")
        path.write_bytes(png)
        char.avatar = media.AVATAR_PREFIX + path.name
        return self.save(char)


_book: CharacterBook | None = None


def book() -> CharacterBook:
    global _book
    if _book is None:
        _book = CharacterBook()
    return _book
