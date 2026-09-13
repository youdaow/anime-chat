"""reasoning 模型（qwen3 / r1 这类）的流式形状：思考与正文分开，思考可能吃掉整个 token 预算。"""

import json

import pytest
from fastapi.testclient import TestClient

from animechat import llm
from animechat.server import create_app

# 真实网关返回的形状（qwen3.8-flash），逐字抄下来当夹具
REASONING_CHUNK = {"model": "m", "choices": [{"index": 0, "finish_reason": None,
                                              "delta": {"content": "", "reasoning_content": "我们需要"}}]}
CONTENT_CHUNK = {"model": "m", "choices": [{"index": 0, "finish_reason": None,
                                            "delta": {"content": "欧尼", "reasoning_content": ""}}]}
FINAL_CHUNK = {"model": "m", "choices": [{"index": 0, "finish_reason": "stop", "delta": {}}]}
LENGTH_CHUNK = {"model": "m", "choices": [{"index": 0, "finish_reason": "length",
                                           "delta": {"content": "", "reasoning_content": "继续想"}}]}
NONSTREAM = {"choices": [{"message": {"role": "assistant", "content": "欧尼酱",
                                      "reasoning_content": "思考了一大段"}, "finish_reason": "stop"}]}


def test_events_split_reasoning_from_content():
    kinds = [(e["kind"], e.get("text") or e.get("reason")) for e in llm._events_from_object(REASONING_CHUNK)]
    assert kinds == [("reasoning", "我们需要")], "content 为空串时不该产出 text 事件"
    kinds = [(e["kind"], e.get("text") or e.get("reason")) for e in llm._events_from_object(CONTENT_CHUNK)]
    assert ("text", "欧尼") in kinds
    done = [e for e in llm._events_from_object(FINAL_CHUNK) if e["kind"] == "finish"]
    assert done and done[0]["reason"] == "stop"
    both = llm._events_from_object(NONSTREAM)
    assert {e["kind"] for e in both} == {"text", "reasoning", "finish"}


def test_thinking_is_off_by_default():
    """实测聊天里 84% 的生成量烧在没人看的 reasoning_content 上（平均 7.5s/条）：
    默认必须关掉，且三种模式各发各的参数。"""
    from animechat.config import Settings
    assert Settings().llm_thinking == "off"
    # 模型名默认是空的（用户自带），这里给一个才谈得上发哪些参数
    off = llm._payload(Settings(llm_model="m"), [{"role": "user", "content": "hi"}], stream=True)
    assert off["enable_thinking"] is False, "off 要发顶层 enable_thinking（实测这个网关认它）"
    low = llm._payload(Settings(llm_model="m", llm_thinking="low"), [], stream=True)
    assert low.get("reasoning_effort") == "low" and "enable_thinking" not in low
    on = llm._payload(Settings(llm_model="m", llm_thinking="on"), [], stream=True)
    assert "enable_thinking" not in on and "reasoning_effort" not in on


def test_param_rejection_detected_narrowly():
    """上游不认这个参数时摘掉重试，但真错误不能被当成参数问题吞掉。"""
    assert llm._rejects_param(llm.LLMError("Unknown parameter: enable_thinking", code="bad_request"))
    for real in (llm.LLMError("The model gpt-9 does not exist", code="model_not_found"),
                 llm.LLMError("Insufficient balance", code="rate_limit_exceeded"),
                 llm.LLMError("请求超时（120s）", code="timeout"),
                 llm.LLMError("context too long", code="context_length_exceeded")):
        assert not llm._rejects_param(real), "真错误必须原样抛给用户"
    assert llm._is_exhausted(llm.LLMError("All models failed for route qwen3.8-flash",
                                          code="free_router_exhausted", status=502))
    assert not llm._is_exhausted(llm.LLMError("余额不足", code="rate_limit_exceeded"))


def test_exhausted_route_retries_once_before_any_text(monkeypatch):
    """网关瞬时抽风（free_router_exhausted）且还没吐字时，原样再试一次。"""
    import asyncio
    from animechat.config import Settings
    calls = []
    async def fake_once(settings, messages, thinking):
        calls.append(thinking)
        if len(calls) == 1:
            raise llm.LLMError("All models failed for route qwen3.8-flash",
                               code="free_router_exhausted", status=502)
        yield {"kind": "text", "text": "好了"}
    monkeypatch.setattr(llm, "_stream_once", fake_once)
    s = Settings(llm_api_key="sk-x", llm_base_url="https://example.test/v1",
                 llm_model="qwen3.8-flash", llm_thinking="off")
    async def run():
        return [ev async for ev in llm.stream_chat(s, [{"role": "user", "content": "hi"}])]
    got = asyncio.run(run())
    assert len(calls) == 2, "应只重试一次"
    assert calls == ["off", "off"], "路由耗尽应原样重试，不切 thinking 模式"
    assert got[0]["text"] == "好了"


def _use_real_model(client: TestClient):
    client.patch("/api/settings", json={
        "llm_api_key": "sk-fake-but-nonempty", "llm_base_url": "https://example.test/v1",
        "llm_model": "reasoning-model"})
    assert client.get("/api/settings").json()["settings"]["mock_mode"] is False


def _script(monkeypatch, chunks):
    async def fake_stream(settings, messages):
        for c in chunks:
            for ev in llm._events_from_object(c):
                yield ev
    monkeypatch.setattr(llm, "stream_chat", fake_stream)


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def _chat(client, conv, text="你好"):
    raw = client.post("/api/chat", json={"conversation_id": conv, "content": text}).text
    out = []
    for block in raw.split("\n\n"):
        name, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((name, json.loads(data)))
    return out


def _new_conv(client, cid="xingye-liuli"):
    return client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]


def test_thinking_then_answer_is_not_an_error(client, monkeypatch):
    _use_real_model(client)
    _script(monkeypatch, [REASONING_CHUNK, CONTENT_CHUNK, FINAL_CHUNK])
    conv = _new_conv(client)
    events = _chat(client, conv)
    names = [n for n, _ in events]
    assert "reasoning" in names and "error" not in names
    done = [d for n, d in events if n == "done"][0]
    assert done["content"] == "欧尼"
    assert done["reasoning_chars"] == len("我们需要")
    stored = client.get("/api/conversations/" + str(conv)).json()["messages"][-1]
    assert stored["content"] == "欧尼"
    assert stored["meta"]["reasoning"] == "我们需要"
    assert stored["meta"]["error"] in (None, "")


def test_reasoning_eating_the_token_budget_reports_a_real_error(client, monkeypatch):
    _use_real_model(client)
    _script(monkeypatch, [REASONING_CHUNK, LENGTH_CHUNK])
    conv = _new_conv(client)
    events = _chat(client, conv)
    errs = [d for n, d in events if n == "error"]
    assert errs, "只出思考、没出正文时必须报错，不能留一个空气泡"
    assert errs[0]["code"] == "empty_completion"
    assert "最大回复 token" in errs[0]["hint"] and "2000" in errs[0]["hint"]
    assert errs[0]["finish_reason"] == "length"
    stored = client.get("/api/conversations/" + str(conv)).json()["messages"][-1]
    assert stored["content"] == ""
    assert stored["meta"]["error"], "错误要落库，刷新页面后还能看到原因"
    assert stored["meta"]["reasoning"]


def test_upstream_error_is_reported_once_and_stored(client, monkeypatch):
    _use_real_model(client)

    async def boom(settings, messages):
        raise llm.LLMError("连不上 https://example.test/v1：getaddrinfo failed",
                           code="connection", base_url="https://example.test/v1")
        yield {"kind": "text", "text": "不会走到这里"}   # 让它是个异步生成器

    monkeypatch.setattr(llm, "stream_chat", boom)
    conv = _new_conv(client)
    events = _chat(client, conv)
    errs = [d for n, d in events if n == "error"]
    assert len(errs) == 1, "报一次就够了，别再补一个 empty_completion"
    assert errs[0]["code"] == "connection"
    stored = client.get("/api/conversations/" + str(conv)).json()["messages"][-1]
    err = stored["meta"]["error"]
    assert "getaddrinfo" in err, "刷新页面后原因还得看得见"
    assert "\n" in err and "Base URL" in err, "第二行是分场景建议，前端按行拆成 消息 + hint"


def test_empty_reply_without_anything_still_explains(client, monkeypatch):
    _use_real_model(client)
    _script(monkeypatch, [])
    conv = _new_conv(client)
    events = _chat(client, conv)
    errs = [d for n, d in events if n == "error"]
    assert errs and "测试连接" in errs[0]["hint"]


def test_complete_chat_refuses_empty_result(monkeypatch):
    import asyncio

    s = llm.Settings(llm_api_key="sk-x", llm_base_url="https://https://example.test/v1", llm_model="m")

    async def ok(*a, **kw):
        return _Resp(NONSTREAM)

    async def empty(*a, **kw):
        return _Resp({"choices": [{"message": {"role": "assistant", "content": "",
                                                "reasoning_content": "只想不说"},
                                   "finish_reason": "length"}]})

    monkeypatch.setattr(llm.httpx.AsyncClient, "post", ok)
    assert asyncio.run(llm.complete_chat(s, [{"role": "user", "content": "hi"}])) == "欧尼酱"

    monkeypatch.setattr(llm.httpx.AsyncClient, "post", empty)
    with pytest.raises(llm.LLMError) as exc:
        asyncio.run(llm.complete_chat(s, [{"role": "user", "content": "hi"}]))
    assert exc.value.code == "empty_completion"
    assert "max token" in str(exc.value)


class _Resp:
    def __init__(self, obj):
        self._obj = obj
        self.status_code = 200
        self.text = json.dumps(obj)

    def json(self):
        return self._obj
