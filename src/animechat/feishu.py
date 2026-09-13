"""飞书机器人桥接：把 animechat 的角色接到飞书里聊天。

    用户在飞书发消息 → 长连接推给桥接进程 → 调本机 /api/chat → 回复发回飞书

三条设计约束，都是查证过才定下来的：

1. **收事件走长连接（WebSocket），不做 HTTP 回调。** 回调模式要求一个飞书能访问到的
   公网 HTTPS 地址（备案域名 + 证书）；长连接是本机主动连出去，本机继续只监听
   127.0.0.1 就行，也不需要处理验签/解密/challenge（SDK 内部走 _do_without_validation，
   鉴权只在建连时做一次）。代价：桥接得是个常驻进程。
2. **回复一律走本机 /api/chat，不在这里重算角色和表情。** 那端已经把系统提示词、
   表情标记解析、群聊轮转、上下文压缩、落库全做完了一遍；在这里再实现一次只会长出
   第二套会走偏的逻辑。走 HTTP 还有个好处的：飞书里聊的会话，回网页接着能看到。
3. **事件回调必须 3 秒内返回。** 飞书对长连接推送的处理超时是 3s，而模型一条回复
   动辄 30s，所以 handler 只做「收下 + 丢给后台线程」，绝不在里面等模型。
   超时的后果不是丢消息，是飞书**重推同一条**——所以我们还得自己去重。

lark_oapi 只在真要连飞书时才需要，所以它是**延迟导入**的：纯逻辑（正文抽取、长文
分段、命令解析、SSE 归并）保持零依赖、可直接单测；网页端不装 SDK 也照常工作。
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import threading
import time
from typing import Any, Callable, Iterable, Optional

# 飞书文本消息请求体上限 150KB，远用不完；这里按可读性收：聊天窗口里一坨几千字没法看。
MAX_CHUNK = 1000

# 同一目标发消息的频控是 5 QPS，留点余量。
SEND_INTERVAL = 0.25

# 长连接超时重推的抑制窗口。飞书重推的是同一个 event_id，但跨重连后
# message_id 更稳，所以两个都记。
DEDUPE_TTL = 600.0

FEISHU_BASE = "https://open.feishu.cn/open-apis"

BIND_PREFIX = "feishu.bind."          # prefs: feishu.bind.<chat_id> -> character_id
MODE_PREFIX = "feishu.mode."          # prefs: feishu.mode.<chat_id> -> on / off
CONV_PREFIX = "feishu.conv."          # prefs: feishu.conv.<chat_id> -> conversation id


# --------------------------------------------------------------- 入站：抽正文
def strip_mentions(text: str, keys: Iterable[str] = ()) -> str:
    """去掉 @ 占位，留下真正想说的话。

    飞书正文里的 @ 是 `@_user_1` 这种占位符，模型看见只会困惑。群里 @机器人是送达
    条件、不是内容，整个吃掉；连着几个占位算一个空格。
    """
    out = text or ""
    for k in keys:
        if k:
            out = out.replace(str(k), "")
    out = re.sub(r"@_user_\d+", "", out)
    return re.sub(r"[ \t]{2,}", " ", out).strip()


def _flatten_post(content: str) -> str:
    """富文本（post）拼回纯文本。

    结构 {"title":..., "content":[[{"tag":"text","text":"..."}, ...], ...]}，
    外层一项一段。注意发送格式带语言层（zh_cn），接收格式不带 —— 两处别搞混。
    """
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return ""
    if not isinstance(data, dict):
        return ""
    lines: list[str] = []
    title = str(data.get("title") or "").strip()
    if title:
        lines.append(title)
    for row in data.get("content") or []:
        parts: list[str] = []
        for node in row or []:
            if not isinstance(node, dict):
                continue
            tag = node.get("tag")
            if tag in ("img", "media"):
                parts.append("[图片]" if tag == "img" else "[视频]")
            elif tag == "file":
                parts.append("[文件]")
            elif tag == "link":
                parts.append(str(node.get("text") or node.get("href") or ""))
            else:
                parts.append(str(node.get("text") or ""))
        line = "".join(parts).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()


def extract_text(message_type: str, content: str) -> tuple[str, str]:
    """从飞书消息里取出能喂给模型的文本。返回 (文本, 给用户的提示)。

    两者都空表示这条处理不了。语音特意留着但先如实说不支持：接进来得再调一次
    语音识别，硬塞给模型只会让它对着文件名瞎编 —— 那是把失败伪装成成功。
    """
    if message_type == "text":
        try:
            return str(json.loads(content).get("text") or ""), ""
        except (ValueError, TypeError):
            return (content or ""), ""
    if message_type == "post":
        return _flatten_post(content), ""
    if message_type == "image":
        return "", "图片我还看不了，用文字说说是什么？"
    if message_type == "audio":
        return "", "语音我还听不了，打字说吧。"
    if message_type == "file":
        return "", "文件我还读不了。"
    if message_type == "merge_forward":
        return "", "合并转发的聊天记录我还拆不开。"
    return "", f"{message_type or '这种'}类型的消息我还处理不了。"


def _clean_quote(text: str) -> str:
    """引用消息的正文前，飞书会带一段 `>引用：(原文)`。去掉，别混进对话历史。"""
    return re.sub(r"^>?\s*引用：\s*\([^\n]*\)\s*", "", text or "").strip()


def mention_keys(mentions: list[Any]) -> list[str]:
    """mentions 里的占位符（@_user_1），用来从正文中剔除。"""
    out = []
    for m in mentions:
        k = _get(m, "key")
        if k:
            out.append(str(k))
    return out


def bot_mentioned(mentions: list[Any]) -> bool:
    """这条消息有没有 @ 到机器人本身。

    mentioned_type == "bot" 就够了 —— 不需要先调 bot/v3/info 拿自己的 open_id。
    那个接口连官方文档页都搜不到，少一处依赖就少一处不确定。
    """
    for m in mentions:
        if str(_get(m, "mentioned_type") or "").lower() == "bot":
            return True
    return False


def is_from_bot(sender: Any) -> bool:
    """发送者是不是机器人。飞书这个字段取值是 "bot"（不是 "app"）。

    挡掉自己回自己是硬要求：不挡的话机器人 reply 一条又触发一次事件，
    无限循环，几分钟就把额度烧光。
    """
    return str(_get(sender, "sender_type") or "").lower() in ("bot", "app")


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """SDK 模型和普通 dict 都能取字段。单测直接传 dict，省得造一堆假对象。"""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _pick(resp: Any, name: str) -> Any:
    """从飞书接口响应里取值：先看 data 子对象，再退到顶层。

    lark_oapi 的所有 *Response 都是 {code, msg, error, data}，业务字段全在
    **data 里面**（CreateImageResponseBody.image_key、CreateMessageResponseBody
    .message_id）。直接 getattr(resp, name) 永远拿到 None —— 表现是「权限全开了
    还是不发表情包」，而且不报错。单测里习惯传裸 dict，所以两种形状都得吃。
    """
    data = _get(resp, "data")
    if data is not None:
        val = _get(data, name)
        if val not in (None, ""):
            return val
    return _get(resp, name)


# --------------------------------------------------------------- 出站：分段
def _take_chunk(text: str, limit: int) -> str:
    """切出不超过 limit 的前缀，尽量在段落/句子边界收口。

    优先级：段落 > 换行 > 句末标点 > 逗号 > 空格 > 硬切。硬切按码点切，
    免得劈开 emoji 的代理对变成乱码。
    """
    if len(text) <= limit:
        return text
    head = text[:limit]
    for pat in ("\n\n", "\n", "。", "！", "？", "…", ". ", "! ", "? ", "；", "; ", "，", ", ", " "):
        cut = head.rfind(pat)
        # 太靠后＝这刀几乎没切掉东西；太靠前＝白浪费额度。只在中间这段收口。
        if cut >= limit * 0.35:
            return head[:cut + len(pat)].strip()
    return "".join(list(text)[:limit]).strip()


def split_message(text: str, limit: int = MAX_CHUNK) -> list[str]:
    """长回复拆成多条。空输入回空列表，调用方据此决定发不发。"""
    body = (text or "").strip()
    if not body:
        return []
    out: list[str] = []
    while body:
        if len(body) <= limit:
            out.append(body)
            break
        piece = _take_chunk(body, limit)
        if not piece:                        # 整段都是空白/分隔符，防止死循环
            piece = body[:limit]
        out.append(piece)
        body = body[len(piece):].strip()
    return out


def text_payload(chunk: str) -> tuple[str, str]:
    """飞书 text 消息的 (msg_type, content JSON)。换行就是 \\n，飞书会照原样折行。"""
    return "text", json.dumps({"text": chunk}, ensure_ascii=False)


def image_payload(image_key: str) -> tuple[str, str]:
    return "image", json.dumps({"image_key": image_key}, ensure_ascii=False)


# --------------------------------------------------------------- 会话内命令
HELP_TEXT = (
    "这里是本机的 animechat，用法：\n"
    "· 直接说话，我用当前角色回你\n"
    "· /角色 看有哪些人；/角色 名字 换人\n"
    "· /表情 on|off 要不要真发图\n"
    "· /清空 重开一段对话（网页记录不动）\n"
    "· /状态 看当前配置\n"
    "· /帮助 再看一遍这段"
)

COMMAND_ALIASES = {
    "角色": "character", "char": "character", "character": "character",
    "换": "character", "人设": "character",
    "表情": "sticker", "sticker": "sticker", "stickers": "sticker",
    "清空": "reset", "reset": "reset", "new": "reset", "重开": "reset",
    "帮助": "help", "help": "help", "？": "help", "?": "help",
    "状态": "status", "status": "status",
}


def parse_command(text: str) -> tuple[str, str]:
    """/命令 解析。不是命令返回 ("", "")。中英文冒号都容忍：手机上打 `/角色： 名字` 很自然。"""
    body = (text or "").strip()
    if not body.startswith("/"):
        return "", ""
    head, _, rest = body[1:].partition(" ")
    return head.strip().lower().rstrip("：:"), rest.strip()


def canonical_command(name: str) -> str:
    return COMMAND_ALIASES.get((name or "").strip().lower(), "")


# 角色扮演动作，不是命令：这些开头的斜杠要原样送给模型，角色会当小动作演。
# 二次元聊天里 `/me 摸摸对方的头` 是标准写法，拦下来等于把玩法砍了。
ROLEPLAY_WORDS = frozenset({"me", "action", "do", "act", "n", "note", "ooc"})


def match_character(arg: str, chars: Iterable[Any]) -> tuple[Any | None, bool]:
    """按 id 或名字找角色。返回 (角色, 是否有歧义)。

    精确 > 唯一包含。多个包含时报歧义让用户说清楚 —— 猜一个送出去，用户只会觉得
    「怎么换成别人了」，比让他重打一次更糟。
    """
    want = (arg or "").strip()
    if not want:
        return None, False
    pool = list(chars)
    for c in pool:
        if getattr(c, "id", "") == want or getattr(c, "name", "") == want:
            return c, False
    hits = [c for c in pool if want in (getattr(c, "name", "") or "")
            or want in (getattr(c, "title", "") or "")]
    if len(hits) == 1:
        return hits[0], False
    return None, bool(len(hits) > 1)


def sticker_mode_text(arg: str) -> Optional[bool]:
    v = (arg or "").strip().lower()
    if v in ("on", "开", "开启", "要", "true", "1"):
        return True
    if v in ("off", "关", "关闭", "不要", "false", "0"):
        return False
    return None


# --------------------------------------------------------------- SSE 归并
class ChatResult:
    """一次 /api/chat 的结果。"""

    __slots__ = ("text", "stickers", "emotion", "error", "stopped", "mock", "message_id")

    def __init__(self) -> None:
        self.text = ""
        self.stickers: list[dict] = []
        self.emotion: str = ""
        self.error = ""
        self.stopped = False
        self.mock = False
        self.message_id = 0

    @property
    def ok(self) -> bool:
        return bool(self.text or self.stickers) and not self.error


def feed_sse(event: str, data: dict, out: ChatResult) -> None:
    """把一条 SSE 事件并进 out。拆出来是为了能不起服务器直接单测。

    事件名与字段以 server.py 为准：start / text / reasoning / sticker / emotion /
    meta / error / done。reasoning 和 meta 这里故意不接 —— 飞书气泡里不该出现思考过程。
    """
    if event == "start":
        out.message_id = int(data.get("message_id") or 0)
        out.mock = bool(data.get("mock"))
    elif event == "text":
        out.text += str(data.get("t") or "")
    elif event == "sticker":
        out.stickers.append(data)
    elif event == "emotion":
        out.emotion = str(data.get("key") or "")
    elif event == "error":
        out.error = str(data.get("message") or data.get("code") or "生成失败")
    elif event == "done":
        # done 里带最终正文，以它为准：中途可能被 trim_other_voices 删过别人的声音，
        # 只累加 text 会拿到加工前的版本。
        final = str(data.get("content") or "")
        if final:
            out.text = final
        out.stopped = bool(data.get("stopped"))
        out.emotion = str(data.get("emotion") or "") or out.emotion
        err = str(data.get("error") or "")
        if err:
            out.error = err
        # done 里的 stickers 只是一串 id（["p08"]），不是完整 dict —— 真正的图片信息
        # 走 sticker 事件那条已经收过了（out.stickers），这里不该重复拼接，
        # 否则 _upload_image 收到的就不是 dict 而是裸字符串，直接报错。


class SseAccumulator:
    """把「一行一行来」的 SSE 攒成事件。

    /api/chat 是边生成边推的，必须逐行喂。单独一个类是为了能直接单测
    「没有空行收尾的最后一段」这种情况（httpx aiter_lines 到流结束不一定给空行）。
    """

    def __init__(self) -> None:
        self.result = ChatResult()
        self._event = ""
        self._data: list[str] = []

    def feed(self, raw: str) -> bool:
        """喂一行；吐出一个完整事件返回 True。"""
        line = (raw or "").rstrip("\r\n")
        if not line:
            return self._flush()
        if line.startswith(":"):
            return False                                  # 心跳注释
        if line.startswith("event:"):
            self._event = line[6:].strip()
        elif line.startswith("data:"):
            self._data.append(line[5:].lstrip())
        return False

    def close(self) -> bool:
        return self._flush()

    def _flush(self) -> bool:
        if not self._data:
            self._event = ""
            return False
        payload = "\n".join(self._data)
        event, self._data, self._event = (self._event or "message"), [], ""
        try:
            obj = json.loads(payload)
        except (ValueError, TypeError):
            return False
        feed_sse(event, obj if isinstance(obj, dict) else {}, self.result)
        return True


def collect_chat(lines: Iterable[str]) -> ChatResult:
    """同步版，给单测直接传 list 用。"""
    acc = SseAccumulator()
    for line in lines:
        acc.feed(line)
    acc.close()
    return acc.result


# --------------------------------------------------------------- 绑定存储
def bind_key(chat_id: str) -> str:
    return BIND_PREFIX + str(chat_id or "")


def mode_key(chat_id: str) -> str:
    return MODE_PREFIX + str(chat_id or "")


def conv_key(chat_id: str) -> str:
    return CONV_PREFIX + str(chat_id or "")


def bound_character(db: Any, chat_id: str, default_id: str) -> str:
    """这个飞书会话该用哪个角色。没绑过就用配置的默认，再没有就用最近聊过的。

    注意不能直接退到「角色库第一个」：网页里刚建的空角色被随机派给飞书用户，
    他会以为软件坏了。last_character 至少是真人聊过的。
    """
    cid = db.get_pref(bind_key(chat_id), "")
    if cid:
        return cid
    return (default_id or "").strip() or db.get_pref("last_character", "")


def list_bindings(db: Any) -> dict[str, str]:
    return {k[len(BIND_PREFIX):]: v for k, v in db.list_prefs(BIND_PREFIX).items() if v}


def unbind_character(db: Any, character_id: str) -> int:
    """角色被删时清掉指向它的绑定，否则飞书那边会一直报「角色不存在」。"""
    n = 0
    for chat_id, cid in list_bindings(db).items():
        if cid == character_id:
            db.set_pref(bind_key(chat_id), "")
            db.set_pref(conv_key(chat_id), "")
            n += 1
    return n


def unbind_all(db: Any) -> int:
    """全清。换了一批角色、或者想从零开始时用。"""
    chat_ids = list(list_bindings(db))
    for chat_id in chat_ids:
        db.set_pref(bind_key(chat_id), "")
        db.set_pref(conv_key(chat_id), "")
    return len(chat_ids)


def wants_stickers(db: Any, chat_id: str, s: Any) -> bool:
    v = db.get_pref(mode_key(chat_id), "")
    if v == "on":
        return True
    if v == "off":
        return False
    return bool(getattr(s, "feishu_stickers", True))


# --------------------------------------------------------------- 去重
class Dedupe:
    """飞书处理超时（3s）后会重推同一条事件，不去重就回两遍、还多烧一次 token。"""

    def __init__(self, ttl: float = DEDUPE_TTL) -> None:
        self.ttl = ttl
        self._seen: dict[str, float] = {}

    def seen(self, *keys: str) -> bool:
        """任一 key 见过就算重复；同时记录所有 key。"""
        live = [k for k in keys if k]
        if not live:
            return False
        now = time.time()
        if len(self._seen) > 2048:                 # 顺手回收，跑几天也不会漏
            self._seen = {k: t for k, t in self._seen.items() if now - t < self.ttl}
        hit = any(k in self._seen and now - self._seen[k] < self.ttl for k in live)
        for k in live:
            self._seen[k] = now
        return hit


# --------------------------------------------------------------- 后台工作线程
class Worker:
    """一个自带事件循环的后台线程。

    为什么非要另起线程：SDK 的 `_handle_data_frame` 是 async 的，它**直接 await
    我们的回调**（ws/client.py:341），也就是回调跑在飞书那条 WebSocket 的循环上。
    在里面阻塞 30s，同一条连接的心跳和后续消息全停住，飞书判定超时就开始重推。
    所以回调只做「收下」，耗时的活丢到这个线程。
    """

    def __init__(self) -> None:
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="feishu-bridge", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=10):
            raise RuntimeError("飞书桥接的工作线程起不来")

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def submit(self, coro_factory: Callable[[], Any]) -> None:
        """收工厂而不是协程本身：协程对象在错误的 loop 上创建会直接 RuntimeError。"""
        if self.loop is None:
            raise RuntimeError("Worker 还没 start")
        asyncio.run_coroutine_threadsafe(coro_factory(), self.loop)

    def stop(self) -> None:
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.loop.stop)


class BotState:
    """一个桥接进程的可变量。"""

    def __init__(self, settings: Any) -> None:
        self.s = settings
        self.dedupe = Dedupe()
        self.started_at = time.time()
        self.processed = 0
        self.failures = 0

    @property
    def api_base(self) -> str:
        """桥接回调本机 API 的地址。跨机部署时要在设置里显式填。"""
        base = (getattr(self.s, "feishu_api_base", "") or "").strip()
        if not base:
            host = self.s.host or "127.0.0.1"
            if host in ("0.0.0.0", "::", "*"):
                host = "127.0.0.1"
            base = f"http://{host}:{self.s.port}"
        return base.rstrip("/")


# --------------------------------------------------------------- 运行时
def _import_lark():
    try:
        import lark_oapi as lark
        return lark
    except ImportError as exc:
        raise SystemExit(
            "缺少飞书官方 SDK。装一下再跑：\n"
            "    uv pip install --python .venv\\Scripts\\python.exe lark-oapi\n"
            "（或 pip install lark-oapi）\n"
            "它只在跑飞书桥接时才需要，网页端不装也照常工作。"
        ) from exc


def check_config(s: Any) -> list[tuple[str, bool, str]]:
    """(项目, 就绪, 说明)。doctor 和 CLI 共用一份判断，别两处各写一遍。"""
    out = [("App ID", bool((s.feishu_app_id or "").strip()),
            (s.feishu_app_id or "未填").strip() or "未填"),
           ("App Secret", bool((s.feishu_app_secret or "").strip()),
            "已填" if (s.feishu_app_secret or "").strip() else "未填")]
    try:
        import lark_oapi  # noqa: F401
        out.append(("SDK lark-oapi", True, "已安装"))
    except ImportError:
        out.append(("SDK lark-oapi", False,
                    "未安装：uv pip install --python .venv\\Scripts\\python.exe \"lark-oapi>=1.7\""
                    "　（或 pip install -e \".[feishu]\"）"))
    return out


def acquire_singleton_lock(echo: Callable[[str], None] = print):
    """挡住同一台机器上开两个桥接；拿到锁返回文件句柄，拿不到返回 False。

    飞书长连接是**集群模式派发**：同一个应用最多 50 个连接，一条消息只随机推给
    其中一个。所以开两个桥接不会报错，只会「有时回、有时不回」——比任何真 bug
    都难查。锁在进程退出时由系统自动释放，不会有残留。

    句柄必须一直攥着（调用方存到局部变量、finally 再关），一关锁就没了。
    非 Windows 走 fcntl；两个都没有就跳过这层保护。

    PID 单独写一个文件：Windows 的 msvcrt.locking 是强制锁，别人占着那段字节时
    连**读**都会 PermissionError，所以锁文件的内容绝对不能去读。
    """
    from .config import DATA_DIR

    try:
        import msvcrt
    except ImportError:
        msvcrt = None
        try:
            import fcntl
        except ImportError:
            return True                      # 没有可用的文件锁：放行，别拦住启动
    else:
        fcntl = None

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(DATA_DIR / "feishu.lock", "a+b")
    try:
        fh.seek(0)
        if msvcrt is not None:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        echo("[feishu] 已经有一个桥接在跑了（进程 " + _read_holder_pid(DATA_DIR) + "）。")
        echo("[feishu] 两个桥接会抢消息：飞书随机推给其中一个，表现成「有时回、有时不回」。")
        echo("[feishu] 先去那个窗口按 Ctrl+C 关掉，再重跑这条命令。")
        return False
    try:                                   # 占锁成功才写 PID；写失败不影响持锁
        (DATA_DIR / "feishu.pid").write_text(str(os.getpid()), encoding="ascii")
    except OSError:
        pass
    return fh


def _read_holder_pid(data_dir) -> str:
    """data_dir 是 config.DATA_DIR（已经是 Path），别再包一层。"""
    try:
        pid = (data_dir / "feishu.pid").read_text(encoding="ascii").strip()
        return pid or "?"
    except OSError:
        return "?"


def run_bot(settings: Any, *, echo: Callable[[str], None] = print) -> int:
    """起长连接，阻塞到进程结束。返回退出码。"""
    lark = _import_lark()
    from .config import load_settings

    s = settings
    if not (s.feishu_app_id or "").strip() or not (s.feishu_app_secret or "").strip():
        echo("还没有飞书凭据：去 open.feishu.cn/app 建一个「企业自建应用」，添加机器人能力，\n"
             "拿到 App ID / App Secret 填到网页的 设置 → 飞书 里保存，然后重跑这条命令。")
        return 2

    lock = acquire_singleton_lock(echo)
    if lock is False:
        return 3
    if lock is True:                         # 平台不支持文件锁，没有句柄要关
        lock = None

    state = BotState(s)
    client = (lark.Client.builder()
              .app_id(s.feishu_app_id.strip())
              .app_secret(s.feishu_app_secret.strip())
              .log_level(lark.LogLevel.WARNING)
              .build())
    worker = Worker()
    worker.start()
    bridge = Bridge(state, client, lark, worker)

    def handler(data: Any) -> None:
        try:
            bridge.handle_event(data)
        except Exception as exc:            # 单个事件炸不能带走整条连接
            state.failures += 1
            echo("[feishu] 事件处理出错：" + str(exc)[:300])

    # 长连接模式下 encrypt_key / verification_token 必须传空串：鉴权只在建连时做过，
    # 推过来的都是明文，填了反而会去验一个不存在的签名。
    dispatcher = (lark.EventDispatcherHandler.builder("", "")
                  .register_p2_im_message_receive_v1(handler)
                  .build())
    ws = lark.ws.Client(s.feishu_app_id.strip(), s.feishu_app_secret.strip(),
                        event_handler=dispatcher, log_level=lark.LogLevel.INFO)

    echo("本机地址：" + state.api_base + "（animechat 得先跑着）")
    echo("飞书桥接已启动，长连接中。Ctrl+C 退出。")
    try:
        ws.start()                          # SDK 内部阻塞并自管重连
    except KeyboardInterrupt:
        echo("")
    finally:
        worker.stop()
        if lock is not None:
            lock.close()
    return 0


class Bridge:
    """一次事件 → 一条飞书回复。

    settings_loader 可注入：默认每条消息重读一次 settings.json，这样你在网页里
    换了模型、改了角色，不用重启桥接就生效。但测试必须能钉住一份配置 —— 之前
    没留这个口子，测试里注入的 api_base 被重读覆盖，桥接就悄悄打到本机 8899 上
    那个真实开发服务去了（会话 id 对不上，只是没写脏数据而已）。
    """

    def __init__(self, state: BotState, client: Any, lark: Any, worker: Worker,
                 settings_loader: Callable[[], Any] | None = None) -> None:
        self.state = state
        self.client = client
        self.lark = lark
        self.worker = worker
        self._load_settings = settings_loader
        # 会话级锁只在同一个 loop 里用（全部 handler 都 submit 到 worker 的 loop），
        # 所以这里用普通 dict 存 asyncio.Lock 是安全的。
        self._locks: dict[str, asyncio.Lock] = {}

    # -- SDK 回调入口：必须立刻返回
    def handle_event(self, data: Any) -> None:
        event = _get(data, "event")
        message = _get(event, "message")
        sender = _get(event, "sender")
        if message is None:
            return
        chat_id = str(_get(message, "chat_id") or "")
        mid = str(_get(message, "message_id") or "")
        header = _get(data, "header") or {}
        eid = str(_get(header, "event_id") or "")
        if not chat_id:
            return
        # 机器人自己的消息挡掉，否则自己回自己无限循环（飞书这个字段取值是 "bot"）
        if is_from_bot(sender):
            return
        if self.state.dedupe.seen(eid, mid):
            return

        def factory():
            return self._process(message, sender, chat_id, mid)
        self.worker.submit(factory)

    async def _process(self, message: Any, sender: Any, chat_id: str, mid: str) -> None:
        from .characters import book
        from .config import load_settings
        from .store import store

        try:
            s = (self._load_settings or load_settings)()
            self.state.s = s
            mtype = str(_get(message, "message_type") or "")
            content = str(_get(message, "content") or "")
            chat_type = str(_get(message, "chat_type") or "")
            mentions = list(_get(message, "mentions") or [])

            raw, notice = extract_text(mtype, content)
            text = strip_mentions(_clean_quote(raw), mention_keys(mentions))

            # 群聊没 @ 到就一个字都不回（@ 了群里别人也会推事件过来，所以得自己判）
            if chat_type == "group" and not bot_mentioned(mentions):
                return

            db = store()
            chars = book().list()

            kind, arg = parse_command(text)
            cmd = canonical_command(kind)
            if cmd:
                await self._command(cmd, arg, chat_id, mid, db, chars, s)
                return
            # 打了 /xxx 但不是已知命令：直说，别丢给模型。
            # 不拦的话「/helo」会被角色当台词接，回一大段莫名其妙的话，
            # 用户只会觉得这机器人听不懂命令。/me 这类是角色扮演动作，放行。
            if kind and kind.lower() not in ROLEPLAY_WORDS:
                await self._send("没这个命令。发 `/帮助` 看用法。", chat_id, mid)
                return
            if notice:
                await self._send(notice, chat_id, mid)
                return
            if not text:
                return

            cid = bound_character(db, chat_id, s.feishu_character)
            char = book().get(cid) if cid else None
            if char is None:
                if chars:
                    await self._send(
                        "还没给我指定角色。`/角色` 看清单，`/角色 名字` 换人。", chat_id, mid)
                else:
                    await self._send("本机还没有角色。去 animechat 网页建一个角色，再来飞书找我。",
                                     chat_id, mid)
                return

            lock = self._locks.setdefault(chat_id, asyncio.Lock())
            if lock.locked():
                # 不排队的话两条并发请求会往同一个会话里交错写，历史就乱了
                await self._send("（上一条还在想，这条稍等）", chat_id, "")
            async with lock:
                result = await self._chat(s, char, text, chat_id, chat_type, db)

            self.state.processed += 1
            await self._deliver(result, chat_id, mid, s, db)
        except Exception as exc:
            self.state.failures += 1
            print("[feishu] 处理失败：" + str(exc)[:300])
            try:
                await self._send("这条处理时出错了：" + str(exc)[:120], chat_id, mid)
            except Exception:
                pass

    # --------------------------------------------------------- 调本机聊天接口
    async def _chat(self, s: Any, char: Any, text: str, chat_id: str,
                    chat_type: str, db: Any):
        import httpx

        conv_id = self._conversation(db, char, chat_id, chat_type)
        payload = {"conversation_id": conv_id, "content": text}
        out = ChatResult()
        try:
            timeout = httpx.Timeout(max(30.0, float(s.llm_timeout) + 30), connect=10)
            async with httpx.AsyncClient(timeout=timeout) as c:
                async with c.stream("POST", self.state.api_base + "/api/chat", json=payload) as r:
                    if r.status_code != 200:
                        body = (await r.aread()).decode("utf-8", "ignore")[:300]
                        out.error = self._unreachable_note(
                            f"返回 {r.status_code}" + (f"：{body}" if body else "（无内容）"))
                        return out
                    acc = SseAccumulator()
                    async for line in r.aiter_lines():
                        acc.feed(line)
                    acc.close()
                    return acc.result
        except httpx.HTTPError as exc:
            out.error = self._unreachable_note(str(exc)[:160])
        return out

    def _unreachable_note(self, detail: str) -> str:
        """非 200 和连不上归到同一句话，并且一定带上「怎么修」。

        光报状态码不够：Windows 上 http.sys 会在 1 号端口直接回一个空的 503，
        看着像「animechat 坏了」，其实是地址写错、应答的根本不是它。
        """
        return (f"连不上本机的 animechat（{self.state.api_base}）{detail}。"
                "先确认那个地址上跑的确实是 animechat；没开着就跑 `animechat run`，"
                "跨机部署要在设置里填「桥接回调地址」。")

    def _conversation(self, db: Any, char: Any, chat_id: str, chat_type: str) -> int:
        """一个飞书会话 ↔ 一个本地会话。私聊按 chat_id，群聊整个群共用一条。

        缓存的会话可能已被人从网页删掉，也可能角色对不上（换了绑定），所以每次
        都验证一遍再复用，不信任存进去的那个 id。
        """
        key = conv_key(chat_id)
        cached = db.get_pref(key, "")
        if cached.isdigit():
            conv = db.get_conversation(int(cached))
            if conv is not None and char.id in (conv.participants or [conv.character_id]):
                return conv.id
        title = (("飞书群 · " if chat_type == "group" else "飞书 · ") + char.name)[:60]
        conv = db.create_conversation(char.id, title, participants=[char.id])
        db.set_pref(key, str(conv.id))
        return conv.id

    # --------------------------------------------------------- 命令
    async def _command(self, cmd: str, arg: str, chat_id: str, mid: str,
                       db: Any, chars: Any, s: Any) -> None:
        if cmd == "help":
            await self._send(HELP_TEXT, chat_id, mid)
            return
        if cmd == "status":
            cid = bound_character(db, chat_id, s.feishu_character)
            char = next((c for c in chars if getattr(c, "id", "") == cid), None)
            up = int(time.time() - self.state.started_at)
            await self._send(
                "当前角色：" + (char.name if char else "（还没选）") + "\n"
                + "表情包：" + ("开着" if wants_stickers(db, chat_id, s) else "关着") + "\n"
                + "模型：" + ("Mock（没填 Key）" if s.mock_mode else s.llm_model) + "\n"
                + f"桥接已运行 {up // 60} 分 {up % 60} 秒，处理 {self.state.processed} 条",
                chat_id, mid)
            return
        if cmd == "character":
            if not arg:
                names = [getattr(c, "name", "") for c in chars[:20]]
                await self._send("可聊的角色：" + "、".join(names) + "\n\n`/角色 名字` 换人。",
                                 chat_id, mid)
                return
            char, ambiguous = match_character(arg, chars)
            if char is not None:
                db.set_pref(bind_key(chat_id), getattr(char, "id", ""))
                # 换人必须换会话：同一条会话混两个角色，模型会串戏
                db.set_pref(conv_key(chat_id), "")
                await self._send("好，接下来我是" + str(getattr(char, "name", "")) + "。",
                                 chat_id, mid)
            elif ambiguous:
                hits = [str(getattr(c, "name", "")) for c in chars
                        if arg in (getattr(c, "name", "") or "")
                        or arg in (getattr(c, "title", "") or "")][:6]
                await self._send("「" + arg + "」像是说好几个人：" + "、".join(hits) + "。说全一点？",
                                 chat_id, mid)
            else:
                await self._send("本机没有叫「" + arg + "」的角色。`/角色` 看清单。", chat_id, mid)
            return
        if cmd == "sticker":
            want = sticker_mode_text(arg)
            if want is None:
                await self._send("表情包现在是"
                                 + ("开着" if wants_stickers(db, chat_id, s) else "关着")
                                 + "。`/表情 on` 或 `/表情 off`。", chat_id, mid)
                return
            db.set_pref(mode_key(chat_id), "on" if want else "off")
            await self._send("好，" + ("回复会带上表情包图。" if want else "只发文字。"),
                             chat_id, mid)
            return
        if cmd == "reset":
            db.set_pref(conv_key(chat_id), "")
            await self._send("重开了，刚才那段翻篇（网页里的记录还在）。", chat_id, mid)
            return
        await self._send("没这个命令。`/帮助` 看用法。", chat_id, mid)

    # --------------------------------------------------------- 投递
    async def _deliver(self, result: ChatResult, chat_id: str, mid: str,
                       s: Any, db: Any) -> None:
        if result.error and not result.text:
            await self._send("生成失败了：" + result.error, chat_id, mid)
            return
        if not result.text and not result.stickers:
            await self._send("这条我没接住话，再说一次试试？", chat_id, mid)
            return

        chunks = split_message(result.text)
        images: list[str] = []
        if wants_stickers(db, chat_id, s):
            for st in result.stickers:
                key = await self._upload_image(st)
                if key:
                    images.append(key)

        first = True
        for chunk in chunks:
            # 第一条挂在用户那条消息下（回复），后面的直接发 —— 全 reply 会套成一串
            await self._send(chunk, chat_id, mid if first else "")
            first = False
        for key in images:
            await self._send_image(key, chat_id, mid if first else "")
            first = False

    async def _upload_image(self, sticker: dict) -> str:
        """表情库里的图传到飞书换 image_key。失败只丢图，不连带丢整条回复。

        只发本地文件：远程 URL 得先下载，而防盗链 / 超大 / GIF 转码都是坑，
        宁可少发一张图也别让整条回复卡在那儿。

        每条跳过都要打一行日志。少了这个，「不发表情包」就只能靠猜 ——
        最常见的其实是没开 im:resource 权限（上传图片和发消息是两套权限）。
        """
        from .media import resolve as resolve_media

        if not isinstance(sticker, dict):
            print(f"[feishu] 表情数据不是字典，跳过：{sticker!r}")
            return ""
        sid = str(sticker.get("id") or "?")
        url = str(sticker.get("url") or "")
        if not url.startswith("/media/"):
            print(f"[feishu] 表情 {sid} 是远程图（{url[:60]}），当前只发本机图")
            return ""
        path = resolve_media(url)
        if path is None:
            print(f"[feishu] 表情 {sid} 在本机找不到文件：{url}")
            return ""
        try:
            data = path.read_bytes()
        except OSError as exc:
            print(f"[feishu] 表情 {sid} 读不出来：{exc}")
            return ""
        from lark_oapi.api.im.v1 import CreateImageRequest, CreateImageRequestBody

        req = (CreateImageRequest.builder()
               .request_body(CreateImageRequestBody.builder()
                             .image_type("message")
                             .image(io.BytesIO(data))
                             .build())
               .build())
        key = await self._call(lambda: self.client.im.v1.image.create(req),
                               lambda r: str(_pick(r, "image_key") or ""))
        if not key:
            print(f"[feishu] 表情 {sid} 上传失败：多半是没开 im:resource 权限"
                  "（获取与上传图片或文件资源）。见上面那行 code/msg。")
            return ""
        return key

    async def _send(self, text: str, chat_id: str, mid: str) -> bool:
        msg_type, content = text_payload(text)
        return await self._dispatch(msg_type, content, chat_id, mid)

    async def _send_image(self, image_key: str, chat_id: str, mid: str) -> bool:
        msg_type, content = image_payload(image_key)
        return await self._dispatch(msg_type, content, chat_id, mid)

    async def _dispatch(self, msg_type: str, content: str, chat_id: str, mid: str) -> bool:
        from lark_oapi.api.im.v1 import (CreateMessageRequest, CreateMessageRequestBody,
                                         ReplyMessageRequest, ReplyMessageRequestBody)

        def build():
            if mid:
                req = (ReplyMessageRequest.builder().message_id(mid)
                       .request_body(ReplyMessageRequestBody.builder()
                                     .msg_type(msg_type).content(content).build()).build())
                return lambda: self.client.im.v1.message.reply(req)
            req = (CreateMessageRequest.builder().receive_id_type("chat_id")
                   .request_body(CreateMessageRequestBody.builder()
                                 .receive_id(chat_id).msg_type(msg_type)
                                 .content(content).build()).build())
            return lambda: self.client.im.v1.message.create(req)

        call = build()
        # message_id 在 resp.data 里（见 _pick）。以前用顶层 getattr 永远取到 None，
        # 只是文字那边没人检查这个返回值，才没暴露成「消息发不出去」。
        ok = bool(await self._call(call, lambda r: _pick(r, "message_id")))
        await asyncio.sleep(SEND_INTERVAL)          # 同一目标 5 QPS，逐条发要留间隔
        return ok

    async def _call(self, do: Callable[[], Any], pick: Callable[[Any], Any]) -> Any:
        """SDK 底层是 requests（同步阻塞），放线程里跑，别占住 worker 的 loop。

        不这么做的话：A 会话正在发消息，B 会话的事件就排在那儿干等，
        等过 3 秒飞书又开始重推。
        """
        def once():
            resp = do()
            code = _get(resp, "code")
            if code not in (0, None):
                print(f"[feishu] 接口 code={code} msg={_get(resp, 'msg', '')}")
                return None
            return pick(resp)
        try:
            return await asyncio.to_thread(once)
        except Exception as exc:
            print("[feishu] 调用失败：" + str(exc)[:200])
            return None
