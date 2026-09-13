"""OpenAI 兼容协议的客户端：Base URL + Key + 模型名都来自设置，接谁家的都行。

这个文件以前默认连的是同机 AI Hub 网关（127.0.0.1:8789，模型名填 auto 由它选路）。那个网关
已经停用；现在接入方式由 providers.py 那张注册表说了算，这里只认设置里填的地址和 Key。

如果对面本身是个会做选路的网关（自建中转站常有），它可能在响应里带 hub.attempts（这一条
真上游试了谁、为什么换）；这里把它一并吐给上层，界面上就能看到「这次换了几次」，不用翻日志。
对面没带这个字段就是普通直连，一切照常。
"""

from __future__ import annotations

import json
from typing import AsyncIterator
from urllib.parse import urlsplit

import httpx

from .config import Settings

# 错误码 → 该去修什么。同一个码在谁家都可能是别的原因，所以说法一律落到具体动作上，
# 并把 {host} 换成设置里那个地址的主机名，看着才知道是哪一家在说话。
HINTS = {
    "no_base_url": "还没填 Base URL：设置 → 模型 → 填 OpenAI 兼容地址（自建中转站一般以 /v1 结尾）。",
    "no_model": "还没填模型名：点「测试连接」拉一次模型清单，选中要用的那个再存。",
    "invalid_api_key": "{host} 不认这个 Key：确认没多余空格、没过期，且 Base URL 与 Key 属于同一家。",
    "model_not_found": "{host} 上没有这个模型 id：点「测试连接」拉模型清单，填里面准确的名字。",
    "no_route_available": "{host} 说没有可用模型：换一个模型 id（自建中转站的话去看它的路由配置）。",
    "all_providers_failed": "{host} 内部的候选全部失败：展开「路由」看每次打了谁，通常是额度或 Key 失效。",
    "free_router_exhausted": "{host} 这条路由的所有上游都失败了：稍等再发，或换一个模型。",
    "rate_limit_exceeded": "{host} 限流或额度用尽：稍等再发，或换模型 / 去平台充值。",
    "context_length_exceeded": "上下文太长：在设置里调小「上下文字符预算」，或让角色先总结记忆。",
    "timeout": "{host} 响应超时：调大设置里的超时，或换个更快的模型 / 减小最大 token。",
    "bad_request": "{host} 认为请求不合法：核对模型名、max token、温度这些参数。",
    "upstream_error": "{host} 返回了错误：多半是模型名不对，或该平台临时故障。",
    "connection": "连不上 {host}：检查网络和代理，以及 Base URL 是不是写成 https:// 开头、带 /v1。",
}


def _host_of(base_url: str) -> str:
    try:
        return urlsplit(str(base_url or "")).hostname or str(base_url or "")
    except ValueError:
        return str(base_url or "")


def hint_for(code: str, base_url: str = "") -> str:
    """只说现象不给动作的错误码，返回空串：宁可少说一句，也别编一个建议出来。"""
    text = HINTS.get(code) or ""
    if not text:
        return ""
    return text.replace("{host}", _host_of(base_url) or "对面")


class LLMError(RuntimeError):
    def __init__(self, message: str, code: str = "error", status: int | None = None,
                 detail: dict | None = None, base_url: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail or {}
        self.base_url = base_url

    @property
    def hint(self) -> str:
        return hint_for(self.code, self.base_url)

    def as_dict(self) -> dict:
        out = {"message": str(self), "code": self.code, "status": self.status}
        if self.hint:
            out["hint"] = self.hint
        if self.detail:
            out["detail"] = self.detail
        return out

    @classmethod
    def from_response(cls, status: int, body: str, base_url: str = "") -> "LLMError":
        code = "upstream_error"
        message = body[:400]
        detail: dict = {}
        try:
            obj = json.loads(body)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            err = obj.get("error")
            if isinstance(err, dict):
                code = str(err.get("code") or err.get("type") or code)
                message = str(err.get("message") or message)
                detail = {k: v for k, v in err.items() if k in ("code", "type", "param", "hub")}
            elif isinstance(err, str):
                message = err
            hub = obj.get("hub") or (err.get("hub") if isinstance(err, dict) else None)
            if isinstance(hub, dict):
                detail["hub"] = hub
        if not message.strip():
            # 网关偶尔会回一个空响应体，这时候别把 "upstream_error" 这种裸码丢给用户看
            message = "上游返回了 HTTP " + str(status) + "，而且响应体是空的（服务本身可能挂了或正在重启）。"
        if status in (401, 403):
            code = "invalid_api_key"
        elif status == 404:
            code = "model_not_found"
        elif status == 429:
            code = "rate_limit_exceeded"
        return cls(message, code=code, status=status, detail=detail, base_url=base_url)


def _url(settings: Settings, path: str) -> str:
    base = (settings.llm_base_url or "").strip().rstrip("/")
    if not base:
        # 光说「连不上」没用：默认值现在是空的（用户自带地址），得告诉他去哪填
        raise LLMError("还没有填模型地址", code="no_base_url", base_url="")
    if base.endswith("/v1"):
        base = base[:-3]
    return base + path


def _headers(settings: Settings) -> dict:
    h = {"Content-Type": "application/json"}
    key = settings.llm_api_key.strip()
    if key:
        h["Authorization"] = "Bearer " + key
    return h


def _timeout(settings: Settings) -> httpx.Timeout:
    return httpx.Timeout(settings.llm_timeout, connect=15.0)


def _apply_thinking(body: dict, mode: str) -> dict:
    """关思考是这套界面最划算的一刀：qwen3 一类混合推理模型会先吐几百字 reasoning
    才开口，聊天场景纯属浪费。off 走顶层 enable_thinking（实测这个网关认它，1.4s vs 2.7s）；
    thinking_budget 这类写法会被静默忽略，别用。"""
    if mode == "off":
        body["enable_thinking"] = False
    elif mode == "low":
        body["reasoning_effort"] = "low"
    return body


def _payload(settings: Settings, messages: list[dict], *, stream: bool,
             temperature: float | None = None, max_tokens: int | None = None,
             thinking: str | None = None) -> dict:
    model = (settings.llm_model or "").strip()
    if not model:
        # 以前这里兜底成 "auto" 交给本机网关选路；网关没了，兜底只会换来一句
        # model_not_found，不如当场说清楚缺什么
        raise LLMError("还没有填模型名", code="no_model", base_url=settings.llm_base_url)
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": settings.llm_temperature if temperature is None else temperature,
        "max_tokens": settings.llm_max_tokens if max_tokens is None else max_tokens,
        "stream": stream,
    }
    if stream:
        body["stream_options"] = {"include_usage": True}
    return _apply_thinking(body, settings.llm_thinking if thinking is None else thinking)


def _extract_attempts(obj: dict) -> list[dict]:
    hub = obj.get("hub") if isinstance(obj.get("hub"), dict) else None
    if not hub:
        err = obj.get("error")
        if isinstance(err, dict) and isinstance(err.get("hub"), dict):
            hub = err["hub"]
    attempts = (hub or {}).get("attempts")
    return attempts if isinstance(attempts, list) else []


# 严格校验的 OpenAI 兼容端点会因为多出来的 enable_thinking / reasoning_effort 直接 400。
# 只认这几条明确指向「参数不认识」的措辞，别把模型名错、额度尽这类真错误也顺手重试掉。
_PARAM_REJECT = ("unknown parameter", "unrecognized", "unexpected keyword", "extra fields",
                 "unknown field", "enable_thinking", "reasoning_effort")


def _rejects_param(exc: "LLMError") -> bool:
    blob = " ".join([getattr(exc, "code", "") or "", str(exc), json.dumps(getattr(exc, "detail", {}), ensure_ascii=False)]).lower()
    return any(k in blob for k in _PARAM_REJECT)


def _is_exhausted(exc: "LLMError") -> bool:
    """网关把一条路由的所有上游都试遍了（如 qwen3.8-flash 瞬时抽风），
       未吐字时可以原样再试一次。"""
    return exc.code == "free_router_exhausted" or "all models failed for route" in str(exc).lower()


async def _stream_once(settings: Settings, messages: list[dict], thinking: str) -> AsyncIterator[dict]:
    url = _url(settings, "/v1/chat/completions")
    payload = _payload(settings, messages, stream=True, thinking=thinking)
    try:
        async with httpx.AsyncClient(timeout=_timeout(settings)) as client:
            async with client.stream("POST", url, headers=_headers(settings), json=payload) as resp:
                if resp.status_code >= 400:
                    raise LLMError.from_response(resp.status_code,
                                                  (await resp.aread()).decode("utf-8", "replace"),
                                                  settings.llm_base_url)
                ctype = (resp.headers.get("content-type") or "").lower()
                if "text/event-stream" not in ctype and "json" in ctype:
                    # 网关没流式返回，当成一次性响应处理
                    raw = (await resp.aread()).decode("utf-8", "replace")
                    obj = json.loads(raw)
                    for ev in _events_from_object(obj):
                        yield ev
                    return
                seen_meta = False
                async for line in resp.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(obj.get("error"), (dict, str)):
                        raise LLMError.from_response(200, json.dumps(obj, ensure_ascii=False),
                                                     settings.llm_base_url)
                    attempts = _extract_attempts(obj)
                    if attempts and not seen_meta:
                        seen_meta = True
                        yield {"kind": "meta", "attempts": attempts}
                    for ev in _events_from_object(obj):
                        yield ev
    except httpx.TimeoutException as exc:
        raise LLMError("请求超时（" + str(settings.llm_timeout) + "s）", code="timeout",
                       base_url=settings.llm_base_url) from exc
    except httpx.HTTPError as exc:
        raise LLMError("连不上 " + url + "：" + exc.__class__.__name__ + " " + str(exc)[:120],
                       code="connection", base_url=settings.llm_base_url) from exc


async def stream_chat(settings: Settings, messages: list[dict]) -> AsyncIterator[dict]:
    """产出事件：{"kind":"text","text":...} / {"kind":"meta",...} / {"kind":"usage",...}。

    关思考只是为提速，不该把对话打死：上游要是明确说「不认识这个参数」，摘掉它原样重试一次。
    已经吐过字就不重试，免得回复重复一遍。
    """
    want = settings.llm_thinking
    if want == "on":
        async for ev in _stream_once(settings, messages, "on"):
            yield ev
        return
    emitted = False
    try:
        async for ev in _stream_once(settings, messages, want):
            emitted = True
            yield ev
    except LLMError as exc:
        if emitted or not (_rejects_param(exc) or _is_exhausted(exc)):
            raise
        # 参数不认同时去掉 thinking 参数重试；路由耗尽时原样再试一次
        thinking = "on" if _rejects_param(exc) else want
        async for ev in _stream_once(settings, messages, thinking):
            yield ev


def _events_from_object(obj: dict) -> list[dict]:
    events: list[dict] = []
    if not isinstance(obj, dict):
        return events
    attempts = _extract_attempts(obj)
    if attempts:
        events.append({"kind": "meta", "attempts": attempts})
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        c = choices[0] or {}
        msg = c.get("message") if isinstance(c.get("message"), dict) else {}
        delta = c.get("delta") if isinstance(c.get("delta"), dict) else {}
        text = delta.get("content")
        if not text and isinstance(msg.get("content"), str):
            text = msg.get("content")
        if isinstance(text, str) and text:
            events.append({"kind": "text", "text": text})
        # reasoning 模型（qwen3 / deepseek-r1 / kimi-thinking）会先流式吐 reasoning_content，
        # 这段时间 content 一直是空串。不接住它，界面看起来就是"卡住不出字"。
        think = delta.get("reasoning_content") or msg.get("reasoning_content")
        if isinstance(think, str) and think:
            events.append({"kind": "reasoning", "text": think})
        reason = c.get("finish_reason")
        if isinstance(reason, str) and reason:
            events.append({"kind": "finish", "reason": reason})
    usage = obj.get("usage")
    if isinstance(usage, dict) and usage:
        events.append({"kind": "usage", "usage": usage})
    return events


async def _complete_once(settings: Settings, messages: list[dict], *, temperature: float | None = None,
                     max_tokens: int | None = None, thinking: str = "on") -> str:
    url = _url(settings, "/v1/chat/completions")
    payload = _payload(settings, messages, stream=False, temperature=temperature, max_tokens=max_tokens,
                       thinking=thinking)
    try:
        async with httpx.AsyncClient(timeout=_timeout(settings)) as client:
            resp = await client.post(url, headers=_headers(settings), json=payload)
    except httpx.TimeoutException as exc:
        raise LLMError("请求超时", code="timeout", base_url=settings.llm_base_url) from exc
    except httpx.HTTPError as exc:
        raise LLMError("连不上 " + url + "：" + str(exc)[:120], code="connection",
                       base_url=settings.llm_base_url) from exc
    if resp.status_code >= 400:
        raise LLMError.from_response(resp.status_code, resp.text, settings.llm_base_url)
    obj = resp.json()
    parts: list[str] = []
    think: list[str] = []
    reason = ""
    for ev in _events_from_object(obj if isinstance(obj, dict) else {}):
        if ev["kind"] == "text":
            parts.append(ev["text"])
        elif ev["kind"] == "reasoning":
            think.append(ev["text"])
        elif ev["kind"] == "finish":
            reason = ev["reason"]
    text = "".join(parts).strip()
    if not text:
        # 空回复必须说清楚，否则调用方（总结记忆等）会拿到一个空字符串当正常结果。
        if think:
            raise LLMError("模型只输出了思考内容，没有给出回复"
                           + ("（finish_reason=length）" if reason == "length" else "")
                           + "。把 max token 调大再试。",
                           code="empty_completion", base_url=settings.llm_base_url)
        raise LLMError("上游返回了空内容（HTTP 200 但没有正文），检查模型名是否可用。",
                       code="empty_completion", base_url=settings.llm_base_url)
    return text


async def complete_chat(settings: Settings, messages: list[dict], *, temperature: float | None = None,
                        max_tokens: int | None = None) -> str:
    """非流式补全（总结记忆、测试连接走这里），和 stream_chat 一样带思考参数与兜底。"""
    modes = ["on"] if settings.llm_thinking == "on" else [settings.llm_thinking, "on"]
    for i, mode in enumerate(modes):
        try:
            return await _complete_once(settings, messages, temperature=temperature,
                                        max_tokens=max_tokens, thinking=mode)
        except LLMError as exc:
            if i + 1 == len(modes) or not _rejects_param(exc):
                raise
    raise AssertionError("unreachable")


async def list_models(settings: Settings) -> list[str]:
    url = _url(settings, "/v1/models")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=8.0)) as client:
            resp = await client.get(url, headers=_headers(settings))
    except httpx.HTTPError as exc:
        raise LLMError("连不上 " + url + "：" + str(exc)[:120], code="connection",
                       base_url=settings.llm_base_url) from exc
    if resp.status_code >= 400:
        raise LLMError.from_response(resp.status_code, resp.text, settings.llm_base_url)
    try:
        data = resp.json().get("data", [])
    except ValueError:
        return []
    ids = [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]
    return sorted(set(ids))


async def probe(settings: Settings) -> dict:
    """设置页的「测试连接」：先看能不能列模型，再真发一句话。"""
    out: dict = {"base_url": settings.llm_base_url, "model": settings.llm_model, "mock": settings.mock_mode}
    if settings.mock_mode:
        out["ok"] = True
        out["note"] = "未填 Key，走内置 Mock 模型（假回复，用来试界面和表情流程）"
        return out
    try:
        ids = await list_models(settings)
        out["models"] = ids[:60]
        out["model_count"] = len(ids)
    except LLMError as exc:
        out["ok"] = False
        out["error"] = exc.as_dict()
        return out
    try:
        text = await complete_chat(settings, [{"role": "user", "content": "说「连接正常」四个字"}],
                                   max_tokens=24, temperature=0.2)
        out["ok"] = True
        out["sample"] = text[:80]
    except LLMError as exc:
        out["ok"] = False
        out["error"] = exc.as_dict()
    return out