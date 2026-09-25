"""表情包库 + 匹配 + 流式标记解析。

模型侧协议（写进系统提示词，界面上也能看懂）：
    [sticker:标签]   —— 让角色发一张表情包，标签可以是表情 id、标签或中文情绪
    [emotion:开心]   —— 声明本轮情绪，用于徽章和兜底选图
标签写错、库里没有时，退化成按情绪挑图，绝不把方括号原文甩给用户看。
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import time
import unicodedata
from pathlib import Path

from . import media
from .config import BUILTIN_STICKER_DIR, STICKER_MANIFEST, ensure_dirs, user_sticker_dir
from .models import Sticker
from . import stickerdef
from .emotion import EMOTION_KEYS, detect
from .emotion import label as emotion_label
from .emotion import match_words, norm as emo_norm

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
META_NAME = "stickers_meta.json"
# 本机才有的那部分：QQ 导入的表情（图被 .gitignore 的 data/stickers/qq* 排除，永不进仓库）
# 的定义，以及每张图被用过几次。它们和「表情定义」不是一类东西 —— 换台机器根本
# 没有那些图，把 1613 条指向不存在文件的条目提交进仓库，只会让工作区永远脏着。
LOCAL_META_NAME = "stickers_meta.local.json"
# 前缀表和 .gitignore 里那条 data/stickers/qq* 一一对应，改一边记得改另一边
LOCAL_ONLY_PREFIXES = ("qq",)


def is_local_only(filename: str) -> bool:
    """这张图的定义该不该只留在本机。"""
    return str(filename or "").startswith(LOCAL_ONLY_PREFIXES)

# ------------------------------------------------------------------ 标记解析
# 协议标记：[sticker:标签] / [表情：开心] / ［贴纸：xxx］ 等。
# 第一组容差放宽：允许 [sticker: <标签>] 这种带前导空格的写法，并吃掉模型学着历史叙述出来的
# 变体（「刚刚给对方发了表情包：xxx」）——否则这串会以文字漏进气泡（用户报的 bug）。
_MARKER = re.compile(
    r"[\[［]\s*([^[\]［］\n]{0,16}?(?:sticker|贴纸|表情包|表情|emoji))"
    r"[^:：\[\]［］\n]{0,10}[：:]\s*([^\[\]［］\n]{1,48}?)\s*[\]］]", re.I)
# 纯叙述、没有可解析值：[发了表情包] / [刚刚给对方发了表情包] —— 整段吃掉，别外漏。
_BARE_NOTE = re.compile(
    r"[\[［]\s*[^[\]［］\n]{0,14}?(?:发了|发了张|发张|给你发|刚发)(?:一张|一个|张|个)?"
    r"(?:表情包|表情|贴纸)\s*[\]］]", re.I)
_EMOTION = re.compile(r"[\[［]\s*(emotion|情绪|心情)\s*[:：]\s*([^\[\]［］\n]{1,16}?)\s*[\]］]", re.I)
# 模型 finish 得比右括号还早时，流里会剩一个没闭合的半个标记（如 [sticker:web-1789）。
# 这种绝不能当文字吐进气泡，识别关键词后整段丢掉。
_PARTIAL_KW = re.compile(r"(?:sticker|贴纸|表情包|表情|emoji|emotion|情绪|心情)", re.I)
_HOLD_LIMIT = 120


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = s.lower().strip()
    return re.sub(r"[\s_\-·、,，。.!！?？~〜:：;；]+", "", s)


def strip_markers(text: str) -> str:
    text = _EMOTION.sub("", text or "")
    text = _MARKER.sub("", text)
    text = _BARE_NOTE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_markers(text: str) -> list[str]:
    """按出现顺序取出所有表情标记里的值；含模型自由发挥的叙述体。"""
    out: list[str] = []
    for m in _MARKER.finditer(text or ""):
        v = m.group(2).strip()
        if v and v not in out:
            out.append(v)
    return out


class MarkerStream:
    """把逐 token 的文本流切成 (text|sticker|emotion) 事件，跨块的半个标记会被扣住。"""

    def __init__(self, resolve) -> None:
        self._resolve = resolve
        self._buf = ""

    def _drain(self) -> list[tuple[str, object]]:
        """把 buf 里所有完整标记取干净，标记之前的文字作为 raw 事件吐出去。"""
        events: list[tuple[str, object]] = []
        while True:
            found = [(m.start(), m.end(), m, "") for m in _MARKER.finditer(self._buf)]
            found += [(m.start(), m.end(), m, "") for m in _EMOTION.finditer(self._buf)]
            found += [(m.start(), m.end(), m, "bare") for m in _BARE_NOTE.finditer(self._buf)]
            if not found:
                return events
            start, end, m, bare = min(found, key=lambda x: (x[0], x[1]))
            if start:
                events.append(("raw", self._buf[:start]))
            if bare:
                pass  # 模型自己叙述的 [发了表情包]：没有可解析值，整段吃掉别外漏
            elif m.group(1).lower() in ("emotion", "情绪", "心情"):
                events.append(("emotion", m.group(2).strip()))
            else:
                events.append(("sticker_mark", m.group(2).strip()))
            self._buf = self._buf[end:]

    def feed(self, chunk: str) -> list[tuple[str, object]]:
        self._buf += chunk or ""
        out: list[tuple[str, object]] = []
        out.extend(self._drain())
        # 扣住可能被 token 截断的尾部标记：从最后一个未闭合的左括号开始
        hold_at = -1
        for opener, closers in (("[", "]]"), ("［", "］]")):
            idx = self._buf.rfind(opener)
            if idx >= 0 and not any(c in self._buf[idx:] for c in closers):
                hold_at = idx if hold_at < 0 else min(hold_at, idx)
        if hold_at < 0:
            out.append(("raw", self._buf))
            self._buf = ""
        elif len(self._buf) - hold_at > _HOLD_LIMIT:
            out.append(("raw", self._buf[:hold_at + 1]))
            self._buf = self._buf[hold_at + 1:]
        return self._merge(out)

    def flush(self) -> list[tuple[str, object]]:
        out = self._drain()
        tail = self._buf
        self._buf = ""
        # 从最后一个「含关键词且没闭合」的左括号处截断：那是模型没写完的半个标记，
        # 直接丢掉，否则会以 [sticker:web-xxx 这种残缺文字漏进气泡（用户报的 bug）。
        for i, ch in enumerate(tail):
            if ch in "[［":
                seg = tail[i:]
                if "]" not in seg and "］" not in seg and _PARTIAL_KW.search(seg):
                    tail = tail[:i]
                    break
        if tail:
            out.append(("raw", tail))
        return self._merge(out)

    def _merge(self, events: list[tuple[str, object]]) -> list[tuple[str, object]]:
        merged: list[tuple[str, object]] = []
        for kind, payload in events:
            if kind == "raw":
                if not payload:
                    continue
                if merged and merged[-1][0] == "text":
                    merged[-1] = ("text", merged[-1][1] + payload)
                else:
                    merged.append(("text", payload))
            elif kind == "sticker_mark":
                st = self._resolve(str(payload))
                merged.append(("sticker", st) if st else ("text", ""))
            else:
                merged.append((kind, payload))
        return [e for e in merged if not (e[0] == "text" and not e[1])]


# ------------------------------------------------------------------ 表情库
def _safe_id(stem: str) -> str:
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z_-]{0,31}", stem):
        return stem
    return "s" + hashlib.sha1(stem.encode("utf-8")).hexdigest()[:10]


def _tags_from_name(stem: str) -> list[str]:
    parts = [p for p in re.split(r"[+、,，_\- ]+", stem) if p]
    tags: list[str] = []
    for p in parts:
        if p.lower().startswith("emo:") or p.lower().startswith("emotion:"):
            continue
        tags.append(p)
    return tags[:12]


class StickerLibrary:
    def __init__(self) -> None:
        ensure_dirs()
        self.items: dict[str, Sticker] = {}
        self.files: dict[str, Path] = {}
        self._mtime: float = 0.0
        self.migrate_meta_split()
        self.refresh()

    # ---------------------------------------------------------- 载入
    @property
    def meta_path(self) -> Path:
        """跟着仓库走的那半：表情定义 + 内置图的覆盖。"""
        return user_sticker_dir().parent / META_NAME

    @property
    def local_meta_path(self) -> Path:
        """只留在本机的那半：qq* 那批图（不进仓库）的定义 + 使用次数。"""
        return user_sticker_dir().parent / LOCAL_META_NAME

    @staticmethod
    def _read(path: Path) -> dict:
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _split(meta: dict) -> tuple[dict, dict]:
        """一份内存里的 meta → （进仓库的那半, 只留本机的那半）。

        三条规则：`__uses__` 是本机统计，永远本地；每个文件条目里的 `uses` 字段也剥进
        `__uses__`（不然每发一张图就把仓库那份改一次）；文件名撞上 qq* 前缀的（图本来
        就不进仓库）留在本地，其余进仓库。`__builtin_overrides__` 属于内置图，内置图在
        包里，所以它必然走仓库那半。"""
        shared: dict = {}
        local: dict = {}
        raw_uses = meta.get("__uses__")
        uses = dict(raw_uses) if isinstance(raw_uses, dict) else {}
        for key, value in meta.items():
            if key == "__uses__":
                continue
            if isinstance(value, dict) and ("uses" in value or not value.get("favorite")):
                value = dict(value)
                count = value.pop("uses", 0)
                if count:
                    uses[key] = count
                # 收藏取消后别把 "favorite": false 留在文件里：那行本来不存在，
                # 写回去之后这条 entry 就永久算「已修改」，收藏一次再取消也回不到原样。
                if not value.get("favorite"):
                    value.pop("favorite", None)
            if key == "__builtin_overrides__" or not is_local_only(key):
                shared[key] = value
            else:
                local[key] = value
        if uses:
            local["__uses__"] = uses
        return shared, local

    def _meta(self) -> dict:
        merged = dict(self._read(self.meta_path))
        # 本机那半盖过去：拆开之后两边本该互不重叠，万一重叠（拆到一半被中断）
        # 以 local 为准 —— 它是更新的那次写入。
        merged.update(self._read(self.local_meta_path))
        # 写入时从文件条目里剥出去的 uses 放回原处，让 refresh()/patch() 看到的
        # 形状和拆分以前一样（内置表情的条目不在这里，它按 sid 直接读 __uses__）。
        uses = merged.get("__uses__")
        if isinstance(uses, dict):
            for name, count in uses.items():
                entry = merged.get(name)
                if isinstance(entry, dict) and not entry.get("uses"):
                    entry["uses"] = count
        return merged

    def _write_meta(self, meta: dict) -> None:
        shared, local = self._split(meta)
        # newline="\n" 不是讲究：Windows 上 write_text 默认把 \n 翻成 \r\n，而这个文件
        # 是 git 跟踪的、.gitattributes 里写死了 eol=lf —— 于是每动一次表情库，仓库那份
        # 就整篇换行符不一致，git status 永远报「已修改」，谁也不知道改没改（踩过）。
        self.meta_path.write_text(json.dumps(shared, ensure_ascii=False, indent=2),
                                  encoding="utf-8", newline="\n")
        lp = self.local_meta_path
        if local or lp.is_file():        # 没有本机专属条目时不造空文件
            lp.write_text(json.dumps(local, ensure_ascii=False, indent=2),
                          encoding="utf-8", newline="\n")

    # -------------------------------------------- 脚本入口（别自己 json.load 单个文件）
    def read_meta(self) -> dict:
        """两份文件合并后的完整视图。

        批量脚本必须走这里：qq* 那批定义现在住在 local 文件里，直接 json.load
        `stickers_meta.json` 会读到一份缺了那批的 meta，然后整个覆盖写回 ——
        打好的视觉标签就这么没了。"""
        return self._meta()

    def write_meta(self, meta: dict) -> None:
        """按归属拆成两份落盘（进仓库的那半 / 只留本机的那半）。"""
        self._write_meta(meta)

    def backup_meta(self) -> list[Path]:
        """两份文件各存一份带时间戳的备份，返回备份路径。调用方自己 print 出来。"""
        stamp = time.strftime("%Y%m%d%H%M%S")
        done: list[Path] = []
        for p in (self.meta_path, self.local_meta_path):
            if p.is_file():
                dest = p.with_name(p.name + ".bak-" + stamp)
                shutil.copy2(p, dest)
                done.append(dest)
        return done

    def migrate_meta_split(self) -> bool:
        """老仓库只有一个 stickers_meta.json，里面混着 qq 那批的定义和 __uses__。
           第一次加载就把它拆开，否则那些条目要等到下次有人改标签才走 —— 而在那之前
           工作区一直是「已修改」，谁也不知道该不该提交。"""
        old = self._read(self.meta_path)
        if not old or self.local_meta_path.is_file():
            return False
        _shared, local = self._split(old)
        if not local:
            return False
        self._write_meta(old)
        return True

    def refresh(self) -> None:
        items: dict[str, Sticker] = {}
        files: dict[str, Path] = {}
        manifest = {}
        if STICKER_MANIFEST.is_file():
            try:
                manifest = json.loads(STICKER_MANIFEST.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifest = {}
        meta = self._meta()
        dirty = False   # 有 meta 条目被我们补算过字段，结束时写回去
        overrides = meta.get("__builtin_overrides__")
        overrides = overrides if isinstance(overrides, dict) else {}
        uses = meta.get("__uses__")
        uses = uses if isinstance(uses, dict) else {}
        for entry in manifest.get("stickers", []):
            sid = str(entry.get("id") or "").strip()
            if not sid:
                continue
            path = BUILTIN_STICKER_DIR / (sid + ".png")
            if not path.is_file():
                continue  # 素材还没生成（首次 build-assets 之前），跳过而不是报错
            stat = path.stat()
            ov = overrides.get(sid)
            ov = ov if isinstance(ov, dict) else {}
            if ov.get("hidden"):
                continue  # 被用户"删掉"的内置表情：素材还在包里，库里不出现（animechat unhide-stickers 恢复）
            raw_tags = ov.get("tags") if isinstance(ov.get("tags"), list) else entry.get("tags", [])
            items[sid] = Sticker(
                id=sid,
                label=str(ov.get("label") or entry.get("label") or sid),
                tags=[str(t) for t in raw_tags],
                emotion=str(ov.get("emotion") or entry.get("emotion") or "neutral"),
                url=media.BUILTIN_PREFIX + "stickers/" + sid + ".png",
                origin="builtin",
                bytes=stat.st_size,
                favorite=bool(ov.get("favorite")),
                uses=int(uses.get(sid) or 0),
                created_at=stat.st_mtime,
            )
            files[sid] = path
        sdir = user_sticker_dir()
        if sdir.is_dir():
            for path in sorted(sdir.iterdir()):
                if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
                    continue
                stem = path.stem
                sid = _safe_id(stem)
                if sid in items:
                    sid = _safe_id(stem + "-" + hashlib.sha1(path.name.encode()).hexdigest()[:4])
                info = meta.get(path.name)
                if not isinstance(info, dict):
                    info = {}
                    meta[path.name] = info
                    dirty = True
                if not info.get("sha1"):
                    # 手写或脚本导入的 meta 往往没有 sha1，去重就看不见这张图：
                    # 从仓库拉回同一张会白多一份副本（踩过）。这里补算一次，之后不再读文件。
                    try:
                        info["sha1"] = hashlib.sha1(path.read_bytes()).hexdigest()[:10]
                        dirty = True
                    except OSError:
                        pass
                # 联网抓回来的图，meta 里常常是「label = 一串 CDN 哈希 + emotion = neutral」。
                # 那种定义模型看不懂，按情绪挑图也永远挑不中，等于这张图加了等于没加。
                # 扫库时顺手补一次：只补这两样坏掉的，用户手动改过的（defined=user）不碰。
                if str(info.get("defined") or "") != "user":
                    fix = stickerdef.backfill(info)
                    if fix:
                        info.update(fix)
                        dirty = True
                emo = str(info.get("emotion") or "")
                if emo not in EMOTION_KEYS:
                    guessed, _c, _s = detect(" ".join(_tags_from_name(stem)))
                    emo = guessed
                stat = path.stat()
                items[sid] = Sticker(
                    id=sid,
                    label=str(info.get("label") or stem),
                    tags=[str(t) for t in info.get("tags", [])] or _tags_from_name(stem),
                    emotion=emo or "neutral",
                    url=media.STICKER_PREFIX + path.name,
                    origin=str(info.get("origin") or "upload"),
                    bytes=stat.st_size,
                    favorite=bool(info.get("favorite")),
                    uses=int(info.get("uses") or 0),
                    note=str(info.get("note") or ""),
                    created_at=float(info.get("created_at") or stat.st_mtime),
                )
                files[sid] = path
        if dirty:
            try:
                self._write_meta(meta)
            except OSError:
                pass   # 补算写回失败不影响本次扫描，下次再试
        self.items, self.files = items, files
        self._mtime = time.time()

    def maybe_refresh(self) -> bool:
        """库里文件被外部改动时重扫：用户往 data/stickers 丢图、或 build-assets 新长出内置图。"""
        newest = 0.0
        for p in (self.meta_path, self.local_meta_path, STICKER_MANIFEST):
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                pass
        for d in (user_sticker_dir(), BUILTIN_STICKER_DIR):
            # 目录 mtime 随增删文件变化，内置素材靠它发现（不必逐个 stat 30 张）
            try:
                if d.is_dir():
                    newest = max(newest, d.stat().st_mtime)
            except OSError:
                pass
        d = user_sticker_dir()
        if d.is_dir():
            for p in d.iterdir():
                if p.suffix.lower() in IMAGE_EXTS:
                    try:
                        newest = max(newest, p.stat().st_mtime)  # 覆盖同名文件不改目录 mtime，得看文件
                    except OSError:
                        pass
        if newest > self._mtime:
            self.refresh()
            return True
        return False

    # ---------------------------------------------------------- 查询
    def all(self) -> list[Sticker]:
        return sorted(self.items.values(), key=lambda s: (s.origin != "builtin", -s.uses, s.id))

    def get(self, sid: str) -> Sticker | None:
        self.maybe_refresh()
        return self.items.get(sid)

    def search(self, q: str, limit: int = 60, emotion: str | None = None) -> list[Sticker]:
        self.maybe_refresh()
        items = list(self.items.values())
        if emotion:
            items = [s for s in items if s.emotion == emotion]
        needle = _norm(q)
        if not needle:
            return sorted(items, key=lambda s: (not s.favorite, -s.uses, s.id))[:limit]
        # 查询词本身可能就是在问一种情绪（「傲娇」「杂鱼」「tsundere」），不是在点某张图的名字
        wanted = {emo_norm(q)} & set(EMOTION_KEYS)
        wanted |= set(match_words(q))
        scored: list[tuple[float, Sticker]] = []
        for st in items:
            score = 0.0
            for token in st.searchable:
                t = _norm(str(token))
                if not t:
                    continue
                if t == needle:
                    score = max(score, 3.0)
                elif t.startswith(needle) or needle.startswith(t):
                    score = max(score, 2.0 + min(len(needle), len(t)) / 10)
                elif needle in t or t in needle:
                    score = max(score, 1.6)
                else:
                    overlap = len(set(needle) & set(t)) / max(1, len(set(needle) | set(t)))
                    score = max(score, overlap * 1.2)
            if st.emotion and _norm(emotion_label(st.emotion)) and needle == _norm(emotion_label(st.emotion)):
                score = max(score, 1.8)
            if wanted & st.emotions:
                score = max(score, 1.9)
            if score:
                score += 0.35 if st.favorite else 0.0
                score += min(1.0, st.uses / 10.0) * 0.25
                if st.origin != "builtin":
                    score += 0.4  # 平手时优先用户自己搞进来的图（联网抓的或手动丢的）
                scored.append((score, st))
        scored.sort(key=lambda x: (-x[0], -x[1].uses, x[1].id))
        return [s for score, s in scored if score >= 0.55][:limit]

    def resolve(self, raw: str) -> Sticker | None:
        """[sticker:xxx] 的解析：id 精确 → 标签搜索 → 情绪兜底。"""
        key = (raw or "").strip()
        if not key:
            return None
        if key in self.items:
            return self.items[key]
        hits = self.search(key, limit=1)
        if hits:
            return hits[0]
        emo, conf, _scores = detect(key)
        if conf > 0.35 and emo != "neutral":
            return self.pick_for_emotion(emo)
        return None

    def pick_for_emotion(self, emotion: str, prefs: list[str] | None = None,
                         exclude: set[str] | None = None, seed: int | None = None) -> Sticker | None:
        exclude = exclude or set()
        # 认 emotions 而不是死认 emotion 字段：联网抓的那批标签写着「傲娇」而字段是
        # neutral，只比字段的话角色按情绪兜底挑图时永远绕开它们。
        pool = [s for s in self.items.values() if s.id not in exclude and emotion in s.emotions]
        if not pool:
            pool = [s for s in self.items.values() if s.id not in exclude]
        if not pool:
            return None
        prefs = prefs or []
        pref_keys = {_norm(p) for p in prefs}

        def rank(s: Sticker) -> tuple[float, float]:
            bonus = 0.0
            if _norm(s.id) in pref_keys or any(_norm(t) in pref_keys for t in s.tags):
                bonus += 2.0
            if s.favorite:
                bonus += 0.5
            if s.origin != "builtin":
                bonus += 1.0   # 联网搜来 / 自己丢进来的，优先于内置手绘那批兜底图
            # 不用 hash()：CPython 对 str 每进程加盐，重启后同一句话会换一张图。
            stable = sum((i + 7) * ord(ch) for i, ch in enumerate(s.id))
            rnd = random.Random((seed or 0) * 131 + stable % 9973).random()
            return (-bonus, rnd)

        ranked = sorted(pool, key=rank)
        # 取前几名里随机选一个（增加多样性），权重随排名下降
        top_n = min(5, len(ranked))
        weights = [1.0 / (i + 1) for i in range(top_n)]
        total = sum(weights)
        weights = [w / total for w in weights]
        idx = random.choices(range(top_n), weights=weights)[0]
        return ranked[idx]

    # ---------------------------------------------------------- 写入
    def _update_meta(self, filename: str, patch: dict) -> dict:
        meta = self._meta()
        cur = meta.get(filename) if isinstance(meta.get(filename), dict) else {}
        cur.update(patch)
        meta[filename] = cur
        self._write_meta(meta)
        return cur

    def find_by_digest(self, digest: str) -> Sticker | None:
        """按内容 sha1 找已入库的同一张图（重跑抓取脚本、同一文件传两次时用）。"""
        meta = self._meta()
        for sid, st in self.items.items():
            path = self.files.get(sid)
            if path is None:
                continue
            info = meta.get(path.name)
            if isinstance(info, dict) and str(info.get("sha1") or "") == digest:
                return st
        return None

    def add_file(self, src: Path, tags: list[str] | None = None, label: str = "",
                 emotion: str = "", note: str = "", origin: str = "upload",
                 dedupe_bytes: bytes | None = None) -> Sticker:
        target_dir = user_sticker_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        payload = dedupe_bytes if dedupe_bytes is not None else src.read_bytes()
        digest = hashlib.sha1(payload).hexdigest()[:10]
        # 文件名保留中文（Windows 允许），只去掉文件系统忌口的字符；
        # 标签从**原始**文件名切出来，别用消毒后的基名，否则中文全被哈希吃掉。
        stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", src.stem).strip() or "sticker"
        base = stem[:48]
        dup = self.find_by_digest(digest)
        if dup is not None:
            # 同一张图重复入库：把标签并过去就返回，不再落一份新文件
            # （否则每跑一次抓取脚本，data/stickers 就多一份带新时间戳的副本）
            name = str(self.files.get(dup.id)) and self.files[dup.id].name
            cur = self._meta().get(name) if isinstance(self._meta().get(name), dict) else {}
            keep = [str(t).strip() for t in (cur.get("tags") or []) if str(t).strip()]
            extra = [str(t).strip() for t in (tags or []) if str(t).strip()]
            merged = list(dict.fromkeys(keep + extra))[:16]
            patch: dict = {}
            if merged:
                patch["tags"] = merged
            if label and not str(cur.get("label") or "").strip():
                patch["label"] = label
            if emotion in EMOTION_KEYS:
                patch["emotion"] = emotion
            if note:
                patch["note"] = note[:200]
            if patch:
                self._update_meta(name, patch)
            self.refresh()
            return dup
        target = target_dir / (base + "-" + digest + src.suffix.lower())
        target.write_bytes(payload)
        name = target.name
        derived = tags or _tags_from_name(stem)
        emo = emotion if emotion in EMOTION_KEYS else detect(" ".join(derived))[0]
        self._update_meta(name, {
            "tags": [t for t in derived if t.strip()][:16] or [base],
            "label": label or stem,
            "emotion": emo,
            "origin": origin,
            "note": note,
            "created_at": time.time(),
            "sha1": digest,
        })
        self.refresh()
        sid = next((k for k, p in self.files.items() if p.name == name), None)
        assert sid is not None
        return self.items[sid]

    def delete(self, sid: str) -> bool:
        st = self.items.get(sid)
        if st is None:
            return False
        if st.origin == "builtin":
            # 内置素材在包目录里，真删了下次 build-assets 又长回来，还会让别人升级后少素材。
            # 所以这里记成 hidden 写在 data 侧：库里立刻消失、重启也不回来，随时可恢复。
            meta = self._meta()
            overrides = meta.get("__builtin_overrides__")
            overrides = dict(overrides) if isinstance(overrides, dict) else {}
            entry = dict(overrides.get(sid)) if isinstance(overrides.get(sid), dict) else {}
            entry["hidden"] = True
            overrides[sid] = entry
            meta["__builtin_overrides__"] = overrides
            self._write_meta(meta)
            self.refresh()
            return True
        path = self.files.get(sid)
        if path and path.is_file():
            path.unlink()
            meta = self._meta()
            meta.pop(path.name, None)
            self._write_meta(meta)
        self.refresh()
        return True

    def unhide_builtins(self) -> int:
        """把被隐藏的内置表情全部放回来，返回恢复张数。"""
        meta = self._meta()
        overrides = meta.get("__builtin_overrides__")
        if not isinstance(overrides, dict):
            return 0
        n = 0
        for sid, entry in list(overrides.items()):
            if isinstance(entry, dict) and entry.get("hidden"):
                kept = dict(entry)
                kept.pop("hidden", None)
                overrides[sid] = kept
                n += 1
        if n:
            self._write_meta(meta)
            self.refresh()
        return n

    def patch(self, sid: str, *, tags: list[str] | None = None, label: str | None = None,
              emotion: str | None = None, favorite: bool | None = None) -> Sticker | None:
        st = self.items.get(sid)
        if st is None:
            return None
        path = self.files.get(sid)
        patch: dict = {}
        if tags is not None:
            patch["tags"] = [t.strip() for t in tags if t.strip()][:16]
        if label is not None:
            patch["label"] = label.strip()[:24]
        if emotion is not None and emotion in EMOTION_KEYS:
            patch["emotion"] = emotion
        if favorite is not None:
            patch["favorite"] = bool(favorite)
        if label is not None or tags is not None or emotion is not None:
            # 用户亲口起的名字 / 定的情绪：以后自动补定义要绕开这张
            patch["defined"] = "user"
        if st.origin == "builtin":
            # 内置表情的自定义写进 data 侧 override，不改包内文件
            meta = self._meta()
            overrides = meta.get("__builtin_overrides__")
            overrides = dict(overrides) if isinstance(overrides, dict) else {}
            entry = dict(overrides.get(sid)) if isinstance(overrides.get(sid), dict) else {}
            entry.update(patch)
            overrides[sid] = entry
            meta["__builtin_overrides__"] = overrides
            self._write_meta(meta)
        elif path:
            self._update_meta(path.name, patch)
        self.refresh()
        return self.items.get(sid)

    def use(self, sid: str) -> None:
        st = self.items.get(sid)
        if st is None:
            return
        st.uses += 1
        path = self.files.get(sid)
        if st.origin != "builtin" and path:
            self._update_meta(path.name, {"uses": st.uses})
            return
        meta = self._meta()  # 内置表情不改包内文件，用量记在 data 侧
        uses = meta.get("__uses__") if isinstance(meta.get("__uses__"), dict) else {}
        uses[sid] = st.uses
        meta["__uses__"] = uses
        self._write_meta(meta)

    def stats(self) -> dict:
        return {
            "total": len(self.items),
            "builtin": sum(1 for s in self.items.values() if s.origin == "builtin"),
            "user": sum(1 for s in self.items.values() if s.origin != "builtin"),
            "favorite": sum(1 for s in self.items.values() if s.favorite),
            "dir": str(user_sticker_dir()),
        }


_lib: StickerLibrary | None = None


def library() -> StickerLibrary:
    global _lib
    if _lib is None:
        _lib = StickerLibrary()
    return _lib
