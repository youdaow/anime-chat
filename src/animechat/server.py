"""FastAPI 应用：角色、会话、聊天（SSE 流式 + 表情标记）、表情库、设置。"""

from __future__ import annotations

import asyncio
import json
import math
import re
import time
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import Body, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from . import cardio, ghpack, llm, media, me, mock as mock_mod, persona, prompt, providers
from . import stickerdef, websearch
from .characters import book
from .config import DATA_DIR, PKG_DIR, Settings, ensure_dirs, load_settings, save_settings, settings_for_client
from .emotion import EMOTION_KEYS, detect, label as emotion_label, norm as norm_emotion
from .models import Appearance, Character, now
from .stickers import MarkerStream, library, strip_markers, extract_markers
from .store import store

WEB_DIR = next((p for p in (PKG_DIR / "web", PKG_DIR.parent.parent / "web") if (p / "index.html").is_file()),
               PKG_DIR / "web")
UPLOAD_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
MAX_UPLOAD = 12 * 1024 * 1024


# --------------------------------------------------------------------- 请求模型
class ChatReq(BaseModel):
    conversation_id: int
    content: str = ""
    stickers: list[str] = Field(default_factory=list)
    regenerate: bool = False
    # 群聊用：本轮指定谁开口，留空则按轮转自动挑。auto=True 表示这轮没有用户发言，
    # 让 AI 直接接上一句继续聊。
    speaker: str = ""
    auto: bool = False
    # 主动开口：这一轮不是回用户，而是角色自己找用户说话（飞书桥接的「沉默后主动」用）。
    # 和 auto 的区别：auto 是群聊里接上一句（必须群聊，否则 400）；proactive 单聊也允许，
    # 并且会往系统提示里注入「是你主动开口」的引导，避免角色写出质问式的开头。
    proactive: bool = False
    idle_note: str = ""         # 给模型看的沉默说明，例如「对方已经 3 小时没消息了」


class ConvReq(BaseModel):
    character_id: str
    title: str = ""
    # 传两个以上就是群聊；character_id 自动算作第一个成员。
    participants: list[str] = Field(default_factory=list)


class ConvPatch(BaseModel):
    title: str | None = None
    pinned: bool | None = None


class MsgPatch(BaseModel):
    content: str | None = None
    stickers: list[str] | None = None


class StickerAddReq(BaseModel):
    url: str
    referer: str = ""
    query: str = ""    # 用户搜这张图时打的词：远程文件名多半是哈希，靠它才有定义
    tags: list[str] = Field(default_factory=list)
    label: str = ""
    emotion: str = ""
    note: str = ""      # 出处说明（源站/作者），留空则记图片 URL


class StickerPatch(BaseModel):
    tags: list[str] | None = None
    label: str | None = None
    emotion: str | None = None
    favorite: bool | None = None


def _split_list(value: Any) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else re.split(r"[,，、\s]+", str(value))
    return [str(i).strip() for i in items if str(i).strip()][:20]


def merge_character(base: Character, patch: dict) -> Character:
    """把前端传来的局部字段并进现有角色，未提供的字段保持不变。"""
    data = base.model_dump()
    for key, value in patch.items():
        if key == "context_chars":
            data[key] = value
            continue
        if value is None or key in {"id", "builtin", "created_at"}:
            continue
        if key == "appearance" and isinstance(value, dict):
            merged = dict(data.get("appearance") or {})
            merged.update({k: v for k, v in value.items() if isinstance(v, str)})
            data["appearance"] = merged
        elif key in {"tags", "catchphrases", "likes", "dislikes", "sticker_prefs", "alternate_greetings"}:
            data[key] = [str(x).strip() for x in (value or []) if str(x).strip()]
        elif key == "example_dialogs":
            data[key] = [{"user": str(t.get("user", "")), "char": str(t.get("char", ""))}
                         for t in (value or []) if isinstance(t, dict)][:12]
        elif key in data:
            data[key] = value
    data["updated_at"] = now()
    return Character.model_validate(data)


def effective_sticker_policy(char: Character, settings: Settings) -> dict:
    """角色说不用，就真的不用；全局开关再收一道。"""
    allowed = char.sticker_style != "off" and settings.sticker_mode != "off"
    auto = "off"
    if allowed:
        auto = "rich" if (settings.sticker_mode == "rich" and char.sticker_style == "rich") else "light"
    return {"model": allowed, "auto": auto, "max": max(1, settings.max_stickers_per_reply)}


def portrait_query(char: Character) -> str:
    """联网找头像用的关键词。

    光搜角色名会捞回一堆同名角色和同人图，所以把「出自哪部作品」一起带上；
    「立绘 portrait」这种混合写法在 Bing 上收敛得最好——中文站给中文立绘，
    英文站给 wikia / MAL 那批带尺寸信息的官方图，两边都能吃到。
    """
    series = " ".join([char.title or "", (char.tags or [""])[0] if char.tags else ""])
    series = re.sub(r"\s+", " ", series).strip()
    bits = [char.name, series[:40], "立绘", "portrait"]
    return " ".join(b for b in bits if b).strip()


# 一次 AI 生成里最多试下几张：每张都要联网下载（超时 30s），
# 批量生成十个角色时不能卡在一张防盗链的图上。
AVATAR_TRIES = 3


async def attach_avatar_from_web(char: Character, s: Settings,
                                 limit: int = 8) -> tuple[Character, str]:
    """给角色联网找一张头像，返回 (角色, 来源)。

    来源："web" 拿到并落盘了 / "none" 试了但没成 / "off" 开关关着，压根没问。
    找不到就交回调用方去程序画 Q 版——离线不该把整个生成流程带崩。
    原图顺手暂存一份，这样生成完还能回编辑器微调头像框的位置。
    """
    if not s.avatar_from_web:
        return char, "off"
    q = portrait_query(char)
    try:
        cands, _provider, _note = await websearch.search_portrait(q, s, limit=limit)
    except websearch.SearchError:
        return char, "none"
    for cand in cands[:AVATAR_TRIES]:
        try:
            data, ext = await websearch.download(cand.image_url, cand.page)
        except (websearch.SearchError, ValueError, KeyError):
            continue    # 这张下不动，换下一张
        try:
            book().stage_avatar_source(char, data, ext)
        except ValueError:
            pass        # 暂存失败不该挡住「先有个头像」
        try:
            return book().set_avatar_from_web(char, data, ext), "web"
        except (ValueError, KeyError):
            continue    # 太小 / 打不开，换下一张
    return char, "none"


def build_messages(char: Character, settings: Settings, conv_id: int, summary: str = "",
                 speaker: str = "", roster: list[dict] | None = None,
                 proactive: bool = False, idle_note: str = "",
                 now: float | None = None) -> tuple[list[dict], int]:
    lib = library()

    def word(sid: str) -> str:
        st = lib.get(sid)
        if st is None:
            # 表情已被删（如清掉联网搜来的那批）。绝不能回退成裸 id 喂给模型——
            # 它会照着历史吐一个解析不到的 [sticker:web-...]，整串漏进气泡。
            return ""
        w = st.label or sid
        return w if not re.search(r"[\[\]［］:：\n]", w) else sid

    history = store().history_for_prompt(conv_id, label_of=word)
    silence = ""
    if prompt.silence_enabled(char):
        silence = prompt.silence_note(prompt.silence_seconds(history, now=now))
    msgs = [{"role": "system", "content": prompt.system_prompt(char, settings, lib, roster=roster,
                                                              proactive=proactive,
                                                              idle_note=idle_note,
                                                              silence_note=silence)}]
    msgs.extend(prompt.history_messages(char, history, settings, summary=summary,
                                      speaker=speaker, roster=roster))
    return msgs, sum(len(str(m.get("content", ""))) for m in msgs)


def auto_attach(char: Character, settings: Settings, text: str, have: list[str], mode: str,
                seed: int) -> str | None:
    """模型没主动发图时，按文本情绪补一张；把握不够就不硬塞。"""
    if mode == "off" or len(have) >= settings.max_stickers_per_reply:
        return None
    emo, conf, _ = detect(text)
    if emo == "neutral":
        return None
    if conf < (0.35 if mode == "rich" else 0.55):
        return None
    st = library().pick_for_emotion(emo, prefs=char.sticker_prefs, exclude=set(have), seed=seed)
    return st.id if st else None


def resolve_markers(text: str, limit: int = 3) -> list[str]:
    out: list[str] = []
    for raw in extract_markers(text):
        st = library().resolve(raw)
        if st and st.id not in out:
            out.append(st.id)
        if len(out) >= limit:
            break
    return out


def declared_emotion(text: str) -> str | None:
    """开场白里显式写了 [emotion:x] 就用它，别让关键词检测去猜六个字的句子。"""
    for raw in re.findall(r"\[\s*(?:emotion|情绪|心情)\s*[:：]\s*([^\[\]［］\n]{1,24}?)\s*\]", text, re.I):
        key = norm_emotion(raw)
        if key:
            return key
    return None


def create_app(settings_override: Settings | None = None) -> Any:
    ensure_dirs()
    from fastapi import FastAPI

    app = FastAPI(title="animechat", version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")

    @app.middleware("http")
    async def _web_never_goes_stale(request: Request, call_next):
        """前端是裸文件、没有指纹版本号，所以让它每次回源校验。

        不然改了 style.css / app.js 之后刷新还可能拿到旧缓存——修好的弹窗照样卡人。
        """
        response = await call_next(request)
        if request.url.path.startswith("/web/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def settings() -> Settings:
        return settings_override or load_settings()

    # ----------------------------------------------------------- 页面与素材
    @app.get("/", include_in_schema=False)
    def index() -> Response:
        target = WEB_DIR / "index.html"
        if not target.is_file():
            return JSONResponse(status_code=500, content={
                "error": {"code": "web_missing", "message": "找不到前端文件：" + str(target)}})
        return FileResponse(str(target), headers={"Cache-Control": "no-store"})

    if WEB_DIR.is_dir():
        app.mount("/web", StaticFiles(directory=str(WEB_DIR), html=False), name="web")

    @app.get("/media/{rest:path}", include_in_schema=False)
    def media_route(rest: str) -> Response:
        path = media.resolve("/media/" + rest)
        if path is None:
            raise HTTPException(404, "素材不存在：" + rest.split("/")[-1])
        return FileResponse(str(path), headers={"Cache-Control": "public, max-age=3600"})

    # ----------------------------------------------------------- 元信息
    @app.get("/api/health")
    def health() -> dict:
        s = settings()
        return {"ok": True, "version": app.version, "mock": s.mock_mode, "data_dir": str(DATA_DIR),
                "web_dir": str(WEB_DIR), "stickers": library().stats()}

    @app.get("/api/bootstrap")
    def bootstrap() -> dict:
        s = settings()
        db = store()
        return {
            "version": app.version,
            "settings": _settings_view(s),
            "characters": _char_views(db),
            "stickers": [_sticker_view(x) for x in library().all()],
            "emotions": [{"key": k, "label": emotion_label(k)} for k in EMOTION_KEYS],
            "conversations": [c.model_dump() for c in db.list_conversations()],
            "prefs": {"last_character": db.get_pref("last_character", "")},
            "stats": db.stats(),
            "sticker_dir": str(DATA_DIR / "stickers"),
        }

    # ----------------------------------------------------------- 角色
    @app.get("/api/characters")
    def characters(include_hidden: bool = False) -> dict:
        return {"characters": _char_views(store(), include_hidden=include_hidden)}

    @app.get("/api/characters/{cid}")
    def character_one(cid: str) -> dict:
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        return {"character": char.model_dump(),
                "conversations": [c.model_dump() for c in store().list_conversations(cid)]}

    @app.post("/api/characters")
    def character_create(payload: dict = Body(...)) -> dict:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "角色名字不能为空")
        body = {**payload, "id": "", "builtin": False, "source": "manual", "created_at": now(), "updated_at": now()}
        try:
            char = Character.model_validate(body)
            char.name = name
        except ValidationError as exc:
            raise HTTPException(400, "字段不合法：" + str(exc.errors()[:1])) from exc
        return {"character": book().save(char).model_dump()}

    @app.put("/api/characters/{cid}")
    def character_update(cid: str, payload: dict = Body(...)) -> dict:
        base = book().get(cid, include_hidden=True)
        if base is None:
            raise HTTPException(404, "角色不存在")
        try:
            merged = merge_character(base, payload)
        except ValidationError as exc:
            raise HTTPException(400, "字段不合法：" + str(exc.errors()[:1])) from exc
        merged.id = cid
        return {"character": book().save(merged).model_dump()}

    @app.delete("/api/characters/{cid}")
    def character_delete(cid: str) -> dict:
        ok, how = book().delete(cid)
        if not ok:
            raise HTTPException(404, "角色不存在")
        return {"ok": True, "how": how, "note": "内置角色是隐藏（可恢复），自定义角色是删除文件"}

    @app.post("/api/characters/{cid}/duplicate")
    def character_duplicate(cid: str) -> dict:
        clone = book().duplicate(cid)
        if clone is None:
            raise HTTPException(404, "角色不存在")
        return {"character": clone.model_dump()}

    @app.post("/api/characters/{cid}/restore")
    def character_restore(cid: str) -> dict:
        char = book().restore(cid)
        if char is None:
            raise HTTPException(400, "只有内置角色可以恢复出厂设置")
        return {"character": char.model_dump()}

    @app.post("/api/characters/{cid}/avatar")
    def character_avatar(cid: str, appearance: dict = Body(...)) -> dict:
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        try:
            char.appearance = Appearance.model_validate(appearance)
        except ValidationError as exc:
            raise HTTPException(400, "外观字段不合法：" + str(exc.errors()[:1])) from exc
        previous = char.avatar
        char.avatar = None
        saved = book().ensure_avatar(char) or book().save(char)
        if previous and previous != saved.avatar and previous.startswith(media.AVATAR_PREFIX) and "gen_" in previous:
            old = media.resolve(previous)
            if old is not None:
                old.unlink(missing_ok=True)
        return {"character": saved.model_dump(), "generated": saved.avatar is not None}

    @app.post("/api/characters/ai-fill")
    async def character_ai_fill(body: dict = Body(...)) -> dict:
        """把人设卡上还没填的空格交给模型补。只返回字段、不落库：
        前端只往「用户留空的框」里填，改过的绝不动，所以连确认弹窗都不需要。"""
        s = settings()
        have = dict(body or {})
        if not str(have.get("name") or "").strip():
            raise HTTPException(400, "先给角色起个名字，不然模型无从下手")
        msgs = persona.build_messages(have)
        if not msgs:
            return {"fields": {}, "filled": [], "note": "该填的都填好了"}
        if s.mock_mode:
            raise HTTPException(400, "演示模式没有真人模型，到设置里关掉「演示模式」再试")
        try:
            text = await llm.complete_chat(s, msgs, temperature=0.9, max_tokens=700)
        except llm.LLMError as exc:
            raise HTTPException(400, exc.hint or str(exc)) from exc
        try:
            fields = persona.parse_fill(text)
        except ValueError as exc:
            raise HTTPException(400, "模型返回的内容没法解析：" + str(exc)) from exc
        if not fields:
            raise HTTPException(400, "模型这次没给出可用内容，再点一次试试")
        return {"fields": fields, "filled": sorted(fields)}

    @app.post("/api/characters/{cid}/avatar-image")
    async def character_avatar_image(cid: str, request: Request, file: UploadFile = File(...)) -> dict:
        """用本地上传的图片当头像：覆盖程序生成的头像，替代旧图。"""
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        payload, ext = await _read_upload(file)
        try:
            char = book().set_avatar_from_image(char, payload, ext)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"character": char.model_dump()}

    @app.post("/api/characters/{cid}/avatar-search")
    async def character_avatar_search(cid: str, body: dict = Body(...)) -> dict:
        """联网给角色找头像，只回候选、不落盘。

        为什么不自动取第一张：同名角色和同人图太多，自动挑经常张冠李戴，
        而头像恰恰是最容易被认出来「这不是她」的东西。所以让人扫一眼再点。
        """
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        q = str((body or {}).get("q") or "").strip() or portrait_query(char)
        try:
            res, provider, note = await websearch.search_portrait(q, settings(), limit=18)
        except websearch.SearchError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"results": [r.as_dict() for r in res], "query": q, "provider": provider, "note": note}

    @app.post("/api/characters/{cid}/avatar-web")
    async def character_avatar_web(cid: str, body: dict = Body(...)) -> dict:
        """把挑中那张下载下来当头像，并暂存一份原图。SSRF 防护和防盗链重试都在
           websearch.download 里；落地前裁成正方形缩到 256（见 CharacterBook）。

           先按居中存一版保证「一定有个头像」，再把原图交回前端弹取景框让人微调：
           搜回来的图大多不是正方形，居中经常把头顶裁掉，而头像恰恰是要认脸的。"""
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        url = str((body or {}).get("image_url") or "").strip()
        if not url:
            raise HTTPException(400, "没给图片地址")
        page = str((body or {}).get("page") or "").strip()
        try:
            data, ext = await websearch.download(url, page)
        except (websearch.SearchError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        src_url, src_w, src_h = "", 0, 0
        try:
            src_url, src_w, src_h = book().stage_avatar_source(char, data, ext)
        except ValueError:
            pass        # 暂存失败顶多是回头没得微调，别把已经下到的图浪费掉
        try:
            saved = book().set_avatar_from_web(char, data, ext)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"character": saved.model_dump(), "source": url, "bytes": len(data),
                "avatar_source": src_url or None, "src_w": src_w, "src_h": src_h}

    @app.post("/api/characters/{cid}/avatar-crop")
    def character_avatar_crop(cid: str, body: dict = Body(...)) -> dict:
        """对着暂存的原图重新框一次头像位置：不联网，纯本机重裁。

           x / y / side 用的是「原图像素」坐标，由前端那个方形取景框换算过来。
           越界交给 set_avatar_from_web 钳回来，这里只挡非数字和 NaN。"""
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        try:
            saved = book().crop_avatar_from_source(char, _crop3(body))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"character": saved.model_dump(),
                "avatar_source": book().avatar_source_url(saved)}


    # ------------------------------------------------ 「我」的资料：自己的头像
    @app.post("/api/me/avatar")
    async def me_avatar(file: UploadFile = File(...)) -> dict:
        """自己传一张当头像：和角色那套一样裁成正方形、缩到 256 存本机，
        同时留一份原图，回头还能重框位置。"""
        payload, ext = await _read_upload(file)
        try:                                    # 先暂存原图：这一步失败也不该挡住头像
            me.stage_source(payload, ext)
        except ValueError:
            pass        # 原图暂存失败只是没得回头微调，别把要用的图挡掉
        try:
            patch = me.set_from_image(payload, ext)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        save_settings(patch)
        return {"settings": _settings_view(load_settings()), "avatar_source": me.source_url()}

    @app.post("/api/me/avatar-search")
    async def me_avatar_search(body: dict = Body(...)) -> dict:
        """联网给「我」找头像，只回候选不落盘：这张是要天天看着的，得自己挑。"""
        q = str((body or {}).get("q") or "").strip()
        if not q:
            name = (settings().user_name or "").strip()
            q = name if name and name != "你" else ""
        if not q:
            raise HTTPException(400, "先想一个要搜的词（例如「白发少女 头像」），或在「我是谁」里填个名字")
        try:
            res, provider, note = await websearch.search_portrait(q, settings(), limit=18)
        except websearch.SearchError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"results": [r.as_dict() for r in res], "query": q,
                "provider": provider, "note": note}

    @app.post("/api/me/avatar-web")
    async def me_avatar_web(body: dict = Body(...)) -> dict:
        """把挑中那张下载下来当自己的头像，并暂存原图供微调（SSRF 防护在 download 里）。"""
        url = str((body or {}).get("image_url") or "").strip()
        if not url:
            raise HTTPException(400, "没给图片地址")
        page = str((body or {}).get("page") or "").strip()
        try:
            data, ext = await websearch.download(url, page)
        except (websearch.SearchError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        src_url, src_w, src_h = "", 0, 0
        try:
            src_url, src_w, src_h = me.stage_source(data, ext)
        except ValueError:
            pass        # 暂存失败顶多是回头没得微调，别把已经下到的图浪费掉
        try:
            patch = me.set_from_image(data, ext)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        save_settings(patch)
        return {"settings": _settings_view(load_settings()), "source": url,
                "bytes": len(data), "avatar_source": src_url or None,
                "src_w": src_w, "src_h": src_h}

    @app.post("/api/me/avatar-crop")
    async def me_avatar_crop(body: dict = Body(...)) -> dict:
        """对着暂存的原图重新框一次自己的头像：不联网，纯本机重裁。"""
        try:
            patch = me.crop_from_source(_crop3(body))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        save_settings(patch)
        return {"settings": _settings_view(load_settings()), "avatar_source": me.source_url()}

    @app.post("/api/me/avatar-clear")
    async def me_avatar_clear() -> dict:
        """撤掉头像：没图就退回用名字首字当头像，不硬塞一张默认的给你。"""
        save_settings(me.clear())
        return {"settings": _settings_view(load_settings())}


    @app.post("/api/characters/ai-create")
    async def character_ai_create(body: dict = Body(...)) -> dict:
        """一句话生成角色卡并直接添加：输入"角色名 + 出自哪部作品/哪个游戏"。

        和 ai-fill 的区别是这里连名字都可能是模型给的，所以不落表单、直接入库；
        同名一律不覆盖，免得一句话把用户自己改过的卡冲掉。
        """
        query = str((body or {}).get("query") or "").strip()
        if len(query) < 2:
            raise HTTPException(400, "先告诉我是谁、出自哪里，例如「千早爱音，出自《BanG Dream! It's MyGO!!!!!》」")
        s = settings()
        if s.mock_mode:
            raise HTTPException(400, "演示模式没有真人模型，到设置里关掉「演示模式」再试")
        try:
            text = await llm.complete_chat(s, persona.build_create_messages(query),
                                          temperature=0.8, max_tokens=1200)
        except llm.LLMError as exc:
            raise HTTPException(400, exc.hint or str(exc)) from exc
        try:
            fields = persona.parse_create(text)
        except ValueError as exc:
            raise HTTPException(400, "模型返回的内容没法解析：" + str(exc)) from exc
        name = str(fields.get("name") or "").strip()
        if not name:
            raise HTTPException(400, "模型这次没给出名字，再点一次试试")
        if any(c.name == name for c in book().list(include_hidden=True)):
            raise HTTPException(409, "已经有叫「" + name + "」的角色了，到侧栏搜这个名字就能找到")
        draft = {**fields, "id": "", "builtin": False, "source": "ai",
                 "created_at": now(), "updated_at": now()}
        try:
            char = Character.model_validate(draft)
        except ValidationError as exc:
            raise HTTPException(400, "字段不合法：" + str(exc.errors()[:1])) from exc
        saved = book().save(char)
        # 头像先联网找官方立绘，实在找不到（离线、防盗链、尺寸太小）才退回程序画的 Q 版。
        # 以前只有 Q 版：AI 生成的角色一律是个圆脸纸片人，用户认不出是谁，
        # 而表情库早就有联网搜图这条链路，头像没接上而已。
        saved, src = await attach_avatar_from_web(saved, s)
        if src != "web":
            saved = book().ensure_avatar(saved) or saved
            src = "drawn" if saved.avatar else "none"
        return {"character": saved.model_dump(), "query": query, "avatar": bool(saved.avatar),
                "avatar_from": src}


    @app.post("/api/characters/import")
    async def character_import(file: UploadFile = File(...)) -> dict:
        payload = await file.read()
        if len(payload) > 20 * 1024 * 1024:
            raise HTTPException(400, "文件太大（限制 20MB）")
        try:
            char = book().import_payload(payload, file.filename or "card.json")
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"character": char.model_dump()}

    @app.get("/api/characters/{cid}/export.json")
    def character_export_json(cid: str) -> Response:
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        card = cardio.to_card_v2(char)
        return Response(json.dumps(card, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": _attachment(_filename(char.name) + ".json")})

    @app.get("/api/characters/{cid}/export.png")
    async def character_export_png(cid: str) -> Response:
        char = book().get(cid, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        try:
            blob = await asyncio.to_thread(book().export_png, cid)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return Response(blob, media_type="image/png",
                        headers={"Content-Disposition": _attachment(_filename(char.name) + ".png")})

    # ----------------------------------------------------------- 会话
    @app.post("/api/conversations")
    def conversation_create(payload: ConvReq) -> dict:
        s = settings()
        # 去重保序；character_id 恒为第一个成员，群聊就是它后面再排人
        ids = [p for p in dict.fromkeys(payload.participants or []) if p]
        if payload.character_id and payload.character_id not in ids:
            ids = [payload.character_id] + ids
        if not ids:
            raise HTTPException(400, "至少得有一个聊天对象")
        for pid in ids:
            if book().get(pid, include_hidden=True) is None:
                raise HTTPException(404, "角色不存在：" + pid)
        db = store()
        conv = db.create_conversation(ids[0], payload.title, participants=ids)
        db.set_pref("last_character", ids[0])
        out: dict[str, Any] = {"conversation": conv.model_dump()}
        # 单聊只有它自己；群聊让每个成员各自打一次招呼，一进来就有群的样子（不花 token）
        for pid in ids:
            char = book().get(pid, include_hidden=True)
            greeting = (char.greeting or "").strip()
            if not greeting:
                continue
            picked = resolve_markers(greeting, s.max_stickers_per_reply) if char.sticker_style != "off" else []
            clean = strip_markers(greeting)
            emo = declared_emotion(greeting) or (detect(clean)[0] if picked else None)
            msg = db.add_message(conv.id, "assistant", clean, stickers=picked, emotion=emo,
                                 meta={"greeting": True}, speaker=pid)
            for sid in picked:
                library().use(sid)
            out.setdefault("greeting", msg.model_dump())
        return out

    @app.get("/api/conversations")
    def conversation_list(character_id: str | None = None) -> dict:
        return {"conversations": [c.model_dump() for c in store().list_conversations(character_id)]}

    @app.get("/api/conversations/{cid}")
    def conversation_get(cid: int, limit: int = 400) -> dict:
        db = store()
        conv = db.get_conversation(cid)
        if conv is None:
            raise HTTPException(404, "会话不存在")
        char = book().get(conv.character_id, include_hidden=True)
        lib = library()
        messages = []
        for m in db.messages(cid, limit=limit):
            view = m.model_dump()
            view["sticker_objects"] = [_sticker_view(s) for s in (lib.get(x) for x in m.stickers) if s]
            messages.append(view)
        return {"conversation": conv.model_dump(), "messages": messages,
                "character": char.model_dump() if char else None}

    @app.post("/api/conversations/{cid}/read")
    def conversation_read(cid: int) -> dict:
        """用户真的看到这个会话了：把未读水位推到最新一条，红点该消失。

        单独开一个接口、由前端在「会话画到屏幕上」时调，而不是 GET 详情时顺手清零：
        流式回复收尾也会 GET 一次详情，但那会儿用户可能已经切去看别人了，或者标签页
        整个切到后台——这两种情况这条回复就该留着红点。
        """
        db = store()
        if db.get_conversation(cid) is None:
            raise HTTPException(404, "会话不存在")
        cleared = db.mark_read(cid)
        return {"ok": True, "cleared": cleared, "conversation": db.get_conversation(cid).model_dump()}

    @app.patch("/api/conversations/{cid}")
    def conversation_patch(cid: int, payload: ConvPatch) -> dict:
        db = store()
        if db.get_conversation(cid) is None:
            raise HTTPException(404, "会话不存在")
        if payload.title is not None:
            db.rename(cid, payload.title)
        if payload.pinned is not None:
            db.set_pinned(cid, payload.pinned)
        return {"conversation": db.get_conversation(cid).model_dump()}

    @app.delete("/api/conversations/{cid}")
    def conversation_delete(cid: int) -> dict:
        if not store().delete_conversation(cid):
            raise HTTPException(404, "会话不存在")
        return {"ok": True}

    @app.get("/api/conversations/{cid}/context")
    def conversation_context(cid: int) -> dict:
        """把真正要发给模型的 messages 原样展示，方便调人设。"""
        s = settings()
        db = store()
        conv = db.get_conversation(cid)
        if conv is None:
            raise HTTPException(404, "会话不存在")
        char = book().get(conv.character_id, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        msgs, total = build_messages(char, s, cid, conv.summary)
        return {"messages": msgs, "chars": total, "system_chars": len(msgs[0]["content"]),
                "model": "mock" if s.mock_mode else s.llm_model, "summary": conv.summary,
                "policy": effective_sticker_policy(char, s)}

    @app.post("/api/conversations/{cid}/compact")
    async def conversation_compact(cid: int) -> dict:
        s = settings()
        db = store()
        conv = db.get_conversation(cid)
        if conv is None:
            raise HTTPException(404, "会话不存在")
        char = book().get(conv.character_id, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在")
        msgs = db.tail_transcript(cid, limit=200)
        if len(msgs) < 6:
            return {"summary": conv.summary, "note": "消息太少，还不需要总结"}
        transcript = "\n".join(
            ((s.user_name or "对方") + "：" + strip_markers(m.content) + ("（配了一张表情包）" if m.stickers else ""))
            if m.role == "user" else (char.name + "：" + strip_markers(m.content))
            for m in msgs[-40:])
        if s.mock_mode:
            summary = _mock_summary_lines(msgs)
        else:
            try:
                summary = await llm.complete_chat(
                    s, prompt.summarize_prompt(char, transcript, conv.summary, s),
                    temperature=0.3, max_tokens=400)
            except llm.LLMError as exc:
                raise HTTPException(400, exc.hint or str(exc)) from exc
        summary = (summary or "").strip()[:2000]
        db.set_summary(cid, summary, msgs[-1].id)
        return {"summary": summary, "covered": len(msgs)}

    # ----------------------------------------------------------- 消息
    @app.put("/api/messages/{mid}")
    def message_patch(mid: int, payload: MsgPatch) -> dict:
        db = store()
        msg = db.get_message(mid)
        if msg is None:
            raise HTTPException(404, "消息不存在")
        if payload.content is not None:
            picked = resolve_markers(payload.content, limit=3)
            db.update_message(mid, strip_markers(payload.content))
            db.update_stickers(mid, picked or list(msg.stickers))
        if payload.stickers is not None:
            db.update_stickers(mid, [s.id for s in (library().get(x) for x in payload.stickers) if s])
        return {"message": db.get_message(mid).model_dump()}

    @app.delete("/api/messages/{mid}")
    def message_delete(mid: int) -> dict:
        msg = store().get_message(mid)
        if msg is None:
            raise HTTPException(404, "消息不存在")
        store().delete_from(msg.conversation_id, mid)
        return {"ok": True, "note": "从这条往后的记录一起删掉"}

    @app.post("/api/messages/{mid}/use-sticker")
    def message_sticker(mid: int, sticker_id: str = Body(..., embed=True)) -> dict:
        db = store()
        msg = db.get_message(mid)
        if msg is None:
            raise HTTPException(404, "消息不存在")
        st = library().get(sticker_id)
        if st is None:
            raise HTTPException(404, "表情不存在")
        db.update_stickers(mid, list(dict.fromkeys(list(msg.stickers) + [st.id]))[:3])
        library().use(st.id)
        return {"message": db.get_message(mid).model_dump()}

    # ----------------------------------------------------------- 聊天（核心）
    @app.post("/api/chat")
    async def chat(payload: ChatReq) -> StreamingResponse:
        s = settings()
        db = store()
        lib = library()
        conv = db.get_conversation(payload.conversation_id)
        if conv is None:
            raise HTTPException(404, "会话不存在")
        participants = [p for p in (conv.participants or []) if p] or [conv.character_id]
        group = len(participants) > 1
        prior = db.messages(conv.id)

        # 本轮谁开口：显式指定 > 重新生成沿用原来那位 > 群聊按轮转 > 单聊就是那个角色。
        # 重新生成必须跟着原说话人，否则点一下「重新生成」就换人了。
        speaker_id = (payload.speaker or "").strip()
        if speaker_id and speaker_id not in participants:
            raise HTTPException(400, "这个人不在当前会话里")
        if not speaker_id and payload.regenerate:
            last_asst = [m for m in prior if m.role == "assistant"]
            if last_asst and last_asst[-1].speaker in participants:
                speaker_id = last_asst[-1].speaker
        if not speaker_id:
            speaker_id = next_speaker(participants, prior) if group else conv.character_id
        char = book().get(speaker_id, include_hidden=True)
        if char is None:
            raise HTTPException(404, "角色不存在（可能已被删除）")
        roster = roster_of(book(), participants, exclude=speaker_id, include_hidden=True) if group else None
        policy = effective_sticker_policy(char, s)

        attached: list[str] = []
        for raw in payload.stickers:
            st = lib.get(raw) or lib.resolve(raw)
            if st and st.id not in attached:
                attached.append(st.id)
                lib.use(st.id)

        if payload.regenerate:
            tail = [m for m in prior if m.role == "assistant"]
            if tail:
                db.delete_from(conv.id, tail[-1].id)
        elif payload.proactive:
            # 角色主动开口：不写用户消息，单聊群聊都允许（跟 auto 的关键差别）。
            pass
        elif payload.auto:
            # AI 互聊：这一轮没有用户发言，让下一个角色直接接上一句
            if not group:
                raise HTTPException(400, "只有群聊才能让 AI 不经过你直接接话")
        else:
            if not payload.content.strip() and not attached:
                raise HTTPException(400, "说点什么，或者挑一张表情包发出去")
            db.add_message(conv.id, "user", payload.content.strip(), stickers=attached,
                           meta={"user_stickers": attached})

        fresh = db.get_conversation(conv.id)
        msgs, prompt_chars = build_messages(char, s, conv.id, fresh.summary if fresh else "",
                                            speaker=speaker_id, roster=roster,
                                            proactive=payload.proactive,
                                            idle_note=payload.idle_note,
                                            now=time.time())
        seed = (conv.id * 7919 + int(time.time())) % 999983

        async def stream() -> AsyncIterator[str]:
            collected: list[str] = []
            reasoning: list[str] = []
            finish_reason = ""
            stream_error: dict | None = None
            picked: list[str] = []
            seen_emotion: list[str] = []
            attempts: list[dict] = []
            usage: dict = {}
            stopped = False
            started = time.time()
            mid = db.add_message(conv.id, "assistant", "", meta={
                "model": "mock" if s.mock_mode else s.llm_model, "mock": s.mock_mode,
                "prompt_chars": prompt_chars, "user_stickers": attached,
                "regenerate": payload.regenerate}, speaker=speaker_id).id
            yield _sse("start", {"message_id": mid, "conversation_id": conv.id, "character_id": char.id,
                                 "speaker": speaker_id, "group": group,
                                 "mock": s.mock_mode, "prompt_chars": prompt_chars})

            def resolve_marker(raw: str):
                if not policy["model"] or len(picked) >= policy["max"]:
                    return None
                st = lib.resolve(raw)
                if st is None or st.id in picked:
                    return None
                picked.append(st.id)
                lib.use(st.id)
                return st

            marker = MarkerStream(resolve_marker)

            def handle(events: list[tuple]) -> list[str]:
                out: list[str] = []
                for kind, body in events:
                    if kind == "text":
                        collected.append(str(body))
                        out.append(_sse("text", {"t": body}))
                    elif kind == "sticker":
                        out.append(_sse("sticker", _sticker_view(body)))
                    elif kind == "emotion":
                        key = norm_emotion(str(body))
                        if key and key != "neutral" and key not in seen_emotion:
                            seen_emotion.append(key)
                            out.append(_sse("emotion", {"key": key, "label": emotion_label(key)}))
                return out

            try:
                if s.mock_mode:
                    text = mock_mod.reply(char, _last_user_text(db, conv.id, speaker_id), char.sticker_prefs)
                    for chunk in _chunks(text, 7):
                        for line in handle(marker.feed(chunk)):
                            yield line
                        await asyncio.sleep(0.018)
                else:
                    async for ev in llm.stream_chat(s, msgs):
                        if ev["kind"] == "text":
                            for line in handle(marker.feed(ev["text"])):
                                yield line
                        elif ev["kind"] == "meta":
                            attempts = ev.get("attempts") or []
                            yield _sse("meta", {"attempts": attempts})
                        elif ev["kind"] == "usage":
                            usage = ev.get("usage") or {}
                        elif ev["kind"] == "reasoning":
                            reasoning.append(str(ev.get("text") or ""))
                            yield _sse("reasoning", {"t": ev["text"]})
                        elif ev["kind"] == "finish":
                            finish_reason = str(ev.get("reason") or "")
                for line in handle(marker.flush()):
                    yield line
            except asyncio.CancelledError:
                stopped = True
            except llm.LLMError as exc:
                stream_error = exc.as_dict()
                yield _sse("error", stream_error)
            except Exception as exc:  # 上游形状千奇百怪，不能把连接炸掉
                stream_error = {"code": exc.__class__.__name__, "message": str(exc)[:280]}
                yield _sse("error", stream_error)

            body_text = "".join(collected).strip()
            if group and roster:
                body_text = trim_other_voices(
                    body_text, char.name, [m["name"] for m in roster])
            think_text = "".join(reasoning).strip()
            err_detail = ""
            if stream_error:
                # 连接/鉴权/上游 4xx 也算失败原因，落库后刷新页面还能看到
                err_detail = str(stream_error.get("message") or stream_error.get("code") or "请求失败")
                hint = str(stream_error.get("hint") or "")
                if hint and hint not in err_detail:
                    err_detail += "\n" + hint
            elif not body_text and not stopped:
                # 上游回了 200 却没正文，以前会留一个空气泡，用户只能猜。
                if think_text and finish_reason == "length":
                    err_detail = ("模型把 token 全花在思考上，还没开口就被截断了。"
                                  "reasoning 模型的思考过程也计入「最大回复 token」，请到设置里把它调到 2000 以上。")
                elif think_text:
                    err_detail = "模型只输出了思考过程、没有正文回复。可以重试一次，或换一个非 reasoning 模型。"
                else:
                    err_detail = ("上游返回了 200 但没有内容。多半是模型名不对或该模型此刻不可用；"
                                  "用设置里的「测试连接」拉一次模型清单确认 id。")
                yield _sse("error", {"code": "empty_completion", "message": err_detail, "hint": err_detail,
                                     "reasoning_chars": len(think_text),
                                     "finish_reason": finish_reason or None})
            if policy["auto"] != "off" and not picked and body_text:
                extra = auto_attach(char, s, body_text, picked, policy["auto"], seed)
                if extra:
                    st = lib.get(extra)
                    if st is not None:
                        picked.append(extra)
                        lib.use(extra)
                        yield _sse("sticker", _sticker_view(st, auto=True))
            emotion = seen_emotion[0] if seen_emotion else None
            if not emotion:
                emo, conf, _ = detect(body_text)
                emotion = emo if conf >= 0.4 and emo != "neutral" else None
            if stopped and not body_text and not think_text:
                # 客户端中途断开（关页面、切会话、点停止）时一个字都没收到，
                # 这条占位消息会永远空着，看起来就像"发不出去、没法聊天"。
                # 既然什么都没说出来，就整条撤掉，别留空气泡。
                db.delete_from(conv.id, mid)
                return
            ended = time.time()
            db.update_message(mid, body_text)
            db.update_stickers(mid, picked)
            db.set_message_emotion(mid, emotion)
            db.patch_message_meta(mid, {"stickers": picked, "emotion": emotion, "attempts": attempts,
                                        "usage": usage, "elapsed_ms": int((ended - started) * 1000),
                                        "stopped": stopped, "finish_reason": finish_reason or None,
                                        "error": err_detail or None,
                                        "reasoning": think_text[-4000:] if think_text else ""})
            yield _sse("done", {"message_id": mid, "content": body_text, "stickers": picked,
                                "emotion": emotion, "model": "mock" if s.mock_mode else s.llm_model,
                                "prompt_chars": prompt_chars, "attempts": attempts, "speaker": speaker_id,
                                "reasoning_chars": len(think_text),
                                "elapsed_ms": int((ended - started) * 1000), "stopped": stopped})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ----------------------------------------------------------- 表情库
    @app.get("/api/stickers")
    def sticker_list(q: str = "", emotion: str = "", limit: int = 80) -> dict:
        lib = library()
        return {"stickers": [_sticker_view(s) for s in lib.search(q, limit=limit, emotion=emotion or None)],
                "stats": lib.stats()}

    @app.get("/api/stickers/{sid}")
    def sticker_one(sid: str) -> dict:
        st = library().get(sid)
        if st is None:
            raise HTTPException(404, "表情不存在")
        return {"sticker": _sticker_view(st)}

    @app.post("/api/stickers/upload")
    async def sticker_upload(request: Request, file: UploadFile = File(...)) -> dict:
        form = await request.form()
        payload = await file.read()
        suffix = Path(file.filename or "sticker.png").suffix.lower()
        if suffix not in UPLOAD_EXTS:
            raise HTTPException(400, "只支持 png / jpg / gif / webp / bmp")
        if len(payload) > MAX_UPLOAD:
            raise HTTPException(400, "单张表情限制 12MB")
        stem = Path(file.filename or "sticker").stem
        tags = _split_list(form.get("tags")) or _split_list(stem)
        label = str(form.get("label") or "").strip()
        emotion = str(form.get("emotion") or "").strip()
        tmp = DATA_DIR / ("upload-" + str(int(time.time() * 1000)) + suffix)
        tmp.write_bytes(payload)
        try:
            st = library().add_file(tmp, tags=tags, label=label or stem,
                                    emotion=emotion if emotion in EMOTION_KEYS else "", origin="upload")
        finally:
            tmp.unlink(missing_ok=True)
        return {"sticker": _sticker_view(st)}

    @app.post("/api/stickers/import")
    async def sticker_import(payload: StickerAddReq) -> dict:
        try:
            data, ext = await websearch.download(payload.url, payload.referer)
        except (websearch.SearchError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        tmp = DATA_DIR / ("web-" + str(int(time.time() * 1000)) + ext)
        tmp.write_bytes(data)
        try:
            # 远程文件名多半是一串 CDN 哈希，直接当名字入库等于「没有定义」：情绪判不出来
            # （neutral）、提示词里是一串乱码，角色按情绪挑图时永远绕开这张。
            d = stickerdef.define(query=payload.query, title=payload.label,
                                  filename=Path(payload.url.split("?")[0]).name,
                                  label=payload.label, tags=payload.tags, emotion=payload.emotion)
            st = library().add_file(tmp, tags=d["tags"], label=d["label"], emotion=d["emotion"],
                                    note=(payload.note or payload.url)[:200], origin="url",
                                    dedupe_bytes=data)
        finally:
            tmp.unlink(missing_ok=True)
        return {"sticker": _sticker_view(st)}

    @app.post("/api/stickers/search-web")
    async def sticker_search_web(body: dict = Body(...)) -> dict:
        q = str(body.get("q") or "").strip()
        limit = int(body.get("limit") or 24)
        try:
            res, provider, note = await websearch.search(q, settings(), limit=max(1, min(50, limit)))
        except websearch.SearchError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"results": [r.as_dict() for r in res], "provider": provider, "note": note, "query": q}

    # ------------------------------------------------ GitHub 表情仓库

    async def _pack_guard(coro):
        """仓库相关的错误统一翻成 400，文案里带上是哪一步失败的。"""
        try:
            return await coro
        except ghpack.PackError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/stickers/pack/preview")
    async def sticker_pack_preview() -> dict:
        """先看仓库里有什么：数量 + 前几张，确认不是空包/错仓库再下载。"""
        async def go():
            repo, entries = await ghpack.fetch_manifest(settings())
            return {"repo": repo, "total": len(entries),
                    "sample": [{"name": e["name"], "label": e["label"],
                                "emotion": e["emotion"], "tags": e["tags"]} for e in entries[:12]]}
        return await _pack_guard(go())

    @app.post("/api/stickers/pack/import")
    async def sticker_pack_import(body: dict = Body(...)) -> dict:
        """把仓库里的表情包拉进本地库；重复的按内容合并标签，不会越同步越多。"""
        limit = int(body.get("limit") or 0)
        async def go():
            out = await ghpack.import_pack(settings(), library(), limit=limit)
            library().refresh()
            return out
        return await _pack_guard(go())

    @app.post("/api/stickers/pack/publish")
    async def sticker_pack_publish() -> dict:
        """把本地表情库推上仓库（内置素材不进包），内容没变的文件自动跳过。"""
        async def go():
            return await ghpack.publish(settings(), library())
        return await _pack_guard(go())

    @app.patch("/api/stickers/{sid}")
    def sticker_patch(sid: str, payload: StickerPatch) -> dict:
        st = library().patch(sid, tags=payload.tags, label=payload.label,
                             emotion=payload.emotion, favorite=payload.favorite)
        if st is None:
            raise HTTPException(404, "表情不存在")
        return {"sticker": _sticker_view(st)}

    @app.delete("/api/stickers/{sid}")
    def sticker_delete(sid: str) -> dict:
        st = library().get(sid)
        if st is None:
            raise HTTPException(404, "表情不存在")
        builtin = st.origin == "builtin"
        if not library().delete(sid):
            raise HTTPException(400, "这张删不掉")
        # 内置那张是"隐藏"（素材留在包里，可恢复），用户自己的图是真删文件
        return {"ok": True, "hidden": builtin}

    # ----------------------------------------------------------- 设置
    @app.get("/api/settings")
    def settings_get() -> dict:
        return {"settings": _settings_view(settings())}

    @app.get("/api/providers")
    def providers_list() -> dict:
        """接入方式由后端给，界面只负责渲染：以后加一种（Anthropic 原生 / 本地 ollama）
        只需在 providers.py 添一条，这里和前端都不用动。"""
        return {"providers": providers.options(), "default": providers.DEFAULT_KEY}

    @app.patch("/api/settings")
    async def settings_patch(payload: dict = Body(...)) -> dict:
        # user_avatar / _at 由 /api/me/avatar* 那几条接口写（它们同时落文件），
        # 从设置接口塞一个路径进来只会得到一个指向没东西的地址；_source 是算出来的
        owned = {"data_dir", "mock_mode", "env_overridden",
                 "user_avatar", "user_avatar_at", "user_avatar_source"}
        patch = {k: v for k, v in (payload or {}).items() if k not in owned}
        try:
            await asyncio.to_thread(save_settings, patch)
        except ValidationError as exc:
            raise HTTPException(400, "设置不合法：" + str(exc.errors()[:1])) from exc
        return {"settings": _settings_view(load_settings())}

    @app.post("/api/settings/test")
    async def settings_test(payload: dict = Body(...)) -> dict:
        """测试连接：允许带着未保存的表单值试；掩码值不会被当成新密钥。"""
        merged = load_settings().model_dump()
        for key, value in (payload or {}).items():
            if key not in merged:
                continue
            if isinstance(value, str) and "…" in value:
                continue  # 前端把掩码原样传回来了
            if value is not None and value != "":
                merged[key] = value
        try:
            trial = Settings.model_validate(merged)
        except ValidationError as exc:
            raise HTTPException(400, "配置不合法：" + str(exc.errors()[:1])) from exc
        return await llm.probe(trial)

    @app.get("/api/models")
    async def models() -> dict:
        s = settings()
        if s.mock_mode:
            return {"models": [], "note": "内置 Mock 模式没有模型清单：填上自己的 Base URL 和 Key 后才能拉"}
        try:
            return {"models": await llm.list_models(s)}
        except llm.LLMError as exc:
            raise HTTPException(400, exc.hint or str(exc)) from exc

    @app.get("/api/stats")
    def stats() -> dict:
        return {"store": store().stats(), "stickers": library().stats()}

    @app.exception_handler(llm.LLMError)
    async def llm_error_handler(_req: Request, exc: llm.LLMError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"error": exc.as_dict()})

    return app


# --------------------------------------------------------------------- 小工具
def _settings_view(s: Settings) -> dict:
    """给前端的设置快照 = 配置本身 + 一点磁盘状态。

    多出来的 user_avatar_source 是「我的头像有没有留下可重框的原图」：界面拿它决定
    「微调位置」能不能按。这种事不该等用户点下去才报错。"""
    data = settings_for_client(s)
    data["user_avatar_source"] = me.source_url()
    return data


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    """读一张上传的图 + 那两道每个上传入口都要做的检查。

    抽出来是因为这几行在角色头像和我的头像两处抄了两遍：改一处忘另一处，就会
    出现「角色的图传得上、我的传不上」这种说不清的事。返回 (字节, 小写扩展名)。"""
    payload = await file.read()
    if not payload:
        raise HTTPException(400, "图片内容为空")
    if len(payload) > MAX_UPLOAD:
        raise HTTPException(400, "图片太大（限制 12MB）")
    ext = Path(file.filename or "avatar.png").suffix.lower()
    return payload, ext


def _crop3(body: dict) -> tuple[float, float, float]:
    """取景框换算出来的三个数：x、y、side（原图像素）。

    这里只挡非数字和 NaN；越界交给裁剪那一步钳回来 —— 手拖出来的数差一两个像素太正常
    了，为这个弹个错误只会烦人。角色头像和「我」的头像共用这一套校验。"""
    b = body or {}
    try:
        nums = [float(b.get("x")), float(b.get("y")), float(b.get("side"))]
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "裁剪位置要三个数字：x、y、side（原图像素）") from exc
    if any(math.isnan(v) for v in nums) or nums[2] <= 0:
        raise HTTPException(400, "这个裁剪框不像话，重新拖一下")
    return (nums[0], nums[1], nums[2])


def _sse(event: str, data: dict) -> str:
    return "event: " + event + "\ndata: " + json.dumps(data, ensure_ascii=False, default=str) + "\n\n"


def _chunks(text: str, size: int) -> list[str]:
    return [text[i:i + size] for i in range(0, len(text), size)] or [""]


def _sticker_view(st, auto: bool = False) -> dict:
    data = st.model_dump()
    data["emotion_label"] = emotion_label(st.emotion)
    if auto:
        data["auto"] = True
    return data


def _char_views(db, include_hidden: bool = False) -> list[dict]:
    counts: dict[str, int] = {}
    unread: dict[str, int] = {}
    # pid -> (会话更新时间, 最后一条消息预览)。列表副标题显示「上次聊了什么」，
    # 不再显示角色介绍。取 updated_at 最大的那条会话，不受置顶排序影响。
    last_seen: dict[str, tuple[float, str]] = {}
    for conv in db.list_conversations():
        counts[conv.character_id] = counts.get(conv.character_id, 0) + 1
        # 未读要摊到每个成员头上：群聊里祥子回了话，从睦的列表项也该看得见红点，
        # 不然窄屏（只有角色列表、会话列表收成头像条）就永远提示不到。
        for pid in (conv.participants or [conv.character_id]):
            unread[pid] = unread.get(pid, 0) + conv.unread_count
            if conv.preview:
                cur = last_seen.get(pid)
                if cur is None or conv.updated_at > cur[0]:
                    last_seen[pid] = (conv.updated_at, conv.preview)
    out: list[dict] = []
    for char in book().list(include_hidden=include_hidden):
        view = char.model_dump()
        if char.avatar and media.resolve(char.avatar) is None:
            view["avatar"] = None
        # 有没有留原图 = 编辑器里「微调位置」能不能按下去，前端据此置灰
        view["avatar_source"] = book().avatar_source_url(char)
        view["conversation_count"] = counts.get(char.id, 0)
        view["unread_count"] = unread.get(char.id, 0)
        view["last_preview"] = last_seen.get(char.id, (0, ""))[1]
        out.append(view)
    return out


def _last_user_text(db, conv_id: int, speaker: str = "") -> str:
    """群聊自动接话时，最近一条可能不是用户发的，退一步取另一个角色刚说的那句。"""
    for m in reversed(db.messages(conv_id, limit=12)):
        if m.role == "user":
            return m.content
        if m.role == "assistant" and m.content.strip() and m.speaker != speaker:
            return m.content
    return ""


def roster_of(book, ids: list[str], exclude: str = "", include_hidden: bool = False) -> list[dict]:
    """群聊提示词里的「其他人」名单。已被删掉的角色自动跳过，不会留个空位在提示词里。"""
    out: list[dict] = []
    for pid in ids:
        if pid == exclude:
            continue
        c = book.get(pid, include_hidden=include_hidden)
        if c is not None:
            out.append({"id": pid, "name": c.name, "title": c.title})
    return out


def trim_other_voices(text: str, own_name: str, others: list[str]) -> str:
    """群聊里弱模型常会「一个人演完全场」，吐出「甲：…乙：…」的剧本。
    从第一个别人的「名字：」处截断，再去掉开头多余的自己名字前缀。
    前端以 done 事件的 content 为准，所以界面上看不到漏出来的那部分。"""
    out = text
    for nm in others:
        for pat in (nm + "：", nm + ":"):
            i = out.find(pat)
            if i > 0:  # 从 0 开始不算越界：那只是在称呼对方，如「祥子：你怎么才来」
                out = out[:i]
    for pat in ((own_name + "："), (own_name + ":")):
        if out.startswith(pat):
            out = out[len(pat):]
    out = out.strip()
    # 削到什么都不如原样留着：宁可看到一条不完美的回复，也不能让消息凭空消失
    return out or text.strip()


def next_speaker(participants: list[str], msgs: list) -> str:
    """轮流发言：看上一条助手消息是谁说的，下一个顺位开口；还没人说过就从第一个开始。"""
    last = ""
    for m in reversed(msgs):
        if m.role == "assistant" and m.speaker:
            last = m.speaker
            break
    if not last or last not in participants:
        return participants[0]
    return participants[(participants.index(last) + 1) % len(participants)]


def _mock_summary_lines(msgs: list) -> str:
    lines: list[str] = []
    for m in msgs[-30:]:
        body = strip_markers(m.content)[:36]
        if body:
            lines.append(("对方" if m.role == "user" else "我") + "：" + body)
    return "\n".join(lines[-6:])


def _filename(name: str) -> str:
    return re.sub(r"[^\w\-]+", "-", name or "character").strip("-") or "character"


def _attachment(name: str) -> str:
    from urllib.parse import quote

    return 'attachment; filename="' + quote(name) + '"; filename*=UTF-8\'' + quote(name)


app = create_app()
