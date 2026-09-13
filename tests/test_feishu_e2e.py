"""端到端：真的起一个 animechat 服务，喂一条飞书事件，看回复发出去了没有。

test_feishu.py 全是纯函数，碰不到「桥接能不能真的调到 /api/chat、SSE 能不能真的解析、
回复有没有挂到 reply 上」这些接缝 —— 而接缝才是最容易错的地方。

需要 lark_oapi（Bridge 构造请求对象要用它的 builder），没装就跳过。
"""

import asyncio
import json
import socket
import threading
import time

import pytest

lark_oapi = pytest.importorskip("lark_oapi", reason="没装 lark_oapi，跳过端到端")

from animechat import feishu                                        # noqa: E402
from animechat.characters import book                              # noqa: E402
from animechat.config import Settings, load_settings, save_settings  # noqa: E402
from animechat.store import store                                  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server:
    """在测试进程里起一个真的 uvicorn。

    用真 HTTP 而不是 TestClient，是因为桥接走的就是 httpx + SSE 流式：
    换成本机内调用就把这条路径整个跳过了，测了等于没测。
    """

    def __enter__(self):
        import uvicorn
        from animechat.server import app

        self.port = _free_port()
        cfg = uvicorn.Config(app, host="127.0.0.1", port=self.port, log_level="error")
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.time() + 20
        import httpx

        while time.time() < deadline:
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/api/health", timeout=1.0).status_code == 200:
                    return self
            except Exception:
                time.sleep(0.15)
        raise RuntimeError("测试服务起不来")

    def __exit__(self, *exc):
        self.server.should_exit = True
        self.thread.join(timeout=10)
        return False


class _Resp:
    """照真 SDK 的形状造假响应：业务字段在 .data 里，不在顶层。

    lark_oapi 的 CreateImageResponse / CreateMessageResponse 都是
    {code, msg, error, data}，image_key / message_id 全在 data 子对象里。
    替身要是把字段摊平到顶层，就和真接口不一致 —— 「不发表情包」那个 bug
    正是这么漏过全部测试的：代码读顶层读不到，测试喂顶层却全绿。
    """

    def __init__(self, **fields):
        self.code = 0
        self.msg = "ok"
        self.error = None
        self.data = type("Body", (), dict(fields))()


class FakeResource:
    def __init__(self, sink, kind):
        self.sink, self.kind = sink, kind

    def reply(self, req):
        self.sink.append(("reply", req))
        return _Resp(message_id="om_out")

    def create(self, req):
        self.sink.append((self.kind, req))
        return _Resp(message_id="om_out")


class FakeImageResource:
    """image.create 必须返回 image_key，否则桥接会以为上传失败。"""

    def __init__(self, sink):
        self.sink = sink

    def create(self, req):
        self.sink.append(("image", req))
        return _Resp(image_key="img_fake_1")


class FakeClient:
    """只替掉 im.v1.message / im.v1.image，其余照原样（builder 还是真的）。"""

    def __init__(self, sink):
        self.sent = sink
        msg = FakeResource(sink, "create")
        msg.reply = FakeResource(sink, "reply").reply
        self.im = type("im", (), {"v1": type("v1", (), {
            "message": msg,
            "image": FakeImageResource(sink),
        })()})()


def _bridge(server, sink, settings=None):
    """settings 用闭包钉死，不能让 Bridge 每条消息去重读 settings.json —— 那样
    注入的 api_base 会被覆盖，桥接就会打到本机 8899 上那个真实开发服务。"""
    s = settings or load_settings()
    s = s.model_copy(update={"feishu_api_base": f"http://127.0.0.1:{server.port}"})
    state = feishu.BotState(s)
    worker = feishu.Worker()
    worker.start()
    return feishu.Bridge(state, FakeClient(sink), lark_oapi, worker,
                         settings_loader=lambda: s), s


def _event(text, chat_id="oc_test", chat_type="p2p", mentions=None, mid="om_in"):
    """按 SDK 的对象形状造事件（Bridge 用 getattr 取，对象和 dict 都吃得下）。"""
    message = type("M", (), {
        "chat_id": chat_id, "chat_type": chat_type, "message_id": mid,
        "message_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False),
        "mentions": mentions or [],
    })()
    sender = type("S", (), {"sender_type": "user",
                            "sender_id": type("I", (), {"open_id": "ou_1"})()})()
    return message, sender


def _seed_character():
    from animechat.models import Character

    # sticker_style=off：文字链路的用例别让表情干扰；
    # 表情图有专门的直喂 _deliver 的用例（模型挑不挑图是随机的，绕真服务会飘）。
    char = Character(id="e2e", name="端到端酱", greeting="呀，你来了。",
                     speaking_style="短句，爱用「呀」。", sticker_style="off")
    book().save(char)
    save_settings({"feishu_character": "e2e"})
    return char


def test_sticker_images_actually_get_uploaded_and_sent():
    """表情图那条链路：sticker dict → 上传 im/v1/images → 发一条 msg_type=img。

    之前所有用例都把角色设成 sticker_style=off，这条链路一次都没真跑过，
    于是「飞书里不发表情包」只能靠猜。而且它依赖 im:resource 权限 —— 和发消息
    是两套权限，漏了的话文字照发、图静默丢，最难查。
    """
    # 1x1 透明 PNG：上传只看字节，不必是真图
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
           b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
           b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")
    from animechat.config import DATA_DIR
    import animechat.stickers as stk

    sdir = DATA_DIR / "stickers"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "smoke_e2e.png").write_bytes(png)
    stk._lib = None                        # 让库重扫，认到这张 /media/stickers/ 下的图

    sink: list = []
    s = load_settings().model_copy(update={"feishu_stickers": True})
    worker = feishu.Worker()
    worker.start()
    try:
        bridge = feishu.Bridge(feishu.BotState(s), FakeClient(sink), lark_oapi, worker,
                               settings_loader=lambda: s)
        result = feishu.ChatResult()
        result.text = "这句话配一张图"
        result.stickers = [{"id": "smoke_e2e", "url": "/media/stickers/smoke_e2e.png"}]
        asyncio.run(bridge._deliver(result, "oc_img1", "om_m", s, store()))
    finally:
        worker.stop()
        stk._lib = None
        (sdir / "smoke_e2e.png").unlink(missing_ok=True)

    kinds = [k for k, _ in sink]
    assert "image" in kinds, f"根本没调用上传图片接口，只发了 {kinds}"
    # 只断言「调过上传」不够：拿到 key 却没发出去，用户那边一样是空白
    img_keys = []
    for _kind, req in sink:
        body = getattr(req, "request_body", None)
        if body is not None and getattr(body, "msg_type", "") == "image":
            img_keys.append(json.loads(body.content).get("image_key"))
    assert img_keys, f"上传成功但没发出 img 消息，收到的类型是 {kinds}"
    assert all(str(k).startswith("img_") for k in img_keys), img_keys
    assert _sent(sink), "为了发图把文字弄丢了"


def test_sticker_upload_failure_keeps_the_text_reply(capsys):
    """上传失败只能丢图，不能把整条回复一起吞掉；且要打得出一行能定位原因的话。"""
    sink: list = []
    s = load_settings().model_copy(update={"feishu_stickers": True})
    worker = feishu.Worker()
    worker.start()
    try:
        bridge = feishu.Bridge(feishu.BotState(s), FakeClient(sink), lark_oapi, worker,
                               settings_loader=lambda: s)
        result = feishu.ChatResult()
        result.text = "本机没这张图"
        result.stickers = [{"id": "ghost", "url": "/media/stickers/根本不存在.png"}]
        asyncio.run(bridge._deliver(result, "oc_img2", "om_g", s, store()))
    finally:
        worker.stop()

    kinds = [k for k, _ in sink]
    assert "image" not in kinds, "文件不存在却还去上传"
    assert _sent(sink), "图没了，文字也跟着没了"
    out = capsys.readouterr().out
    assert "找不到文件" in out, out



# ---------------------------------------------------------------- 用例
def _sent(sink, want="reply"):
    """把 FakeClient 收下的请求还原成 (kind, text)。"""
    out = []
    for kind, req in sink:
        body = getattr(req, "request_body", None)
        content = getattr(body, "content", None) if body else None
        if kind in ("reply", "create") and content:
            try:
                out.append((kind, json.loads(content).get("text", ""), getattr(req, "message_id", "")))
            except (ValueError, TypeError):
                pass
    return out


def _assert_real_reply(sink):
    """断言「真的聊出来了」，而不是「收到了一个错误提示」。

    少了这一层，桥接把 404 当成失败发回用户也能让 assert sink 通过 ——
    第一版就差点这么混过去（打到本机 8899 的真实服务，会话 id 对不上返回 404）。
    """
    texts = [t for _, t, _ in _sent(sink)]
    joined = "".join(texts)
    assert texts, "一条都没发出去"
    assert "生成失败" not in joined and "连不上" not in joined, joined[:200]
    assert "本机接口返回" not in joined, joined[:200]
    return texts


def test_full_round_trip_delivers_a_reply_via_reply_api():
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        message, sender = _event("今天好累呀")
        asyncio.run(bridge._process(message, sender, "oc_test", "om_in"))

        sent = _sent(sink)
        _assert_real_reply(sink)
        assert sent, "一条都没发出去"
        assert sent[0][0] == "reply" and sent[0][2] == "om_in", "第一条没挂在用户消息下"
        assert sent[0][1].strip(), "第一条是空气泡"


def test_reply_is_persisted_locally_and_visible_to_web():
    """飞书里聊的话要落到本机库，回到网页才接得上 —— 这是这个方案的全部意义。"""
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        message, sender = _event("记一下这句话")
        asyncio.run(bridge._process(message, sender, "oc_bind", "om_1"))

        _assert_real_reply(sink)
        conv_id = store().get_pref(feishu.conv_key("oc_bind"), "")
        assert conv_id.isdigit(), "没建会话"
        rows = store().messages(int(conv_id))
        assert any(m.role == "user" and "记一下这句话" in m.content for m in rows), "用户那句没入库"
        assert any(m.role == "assistant" and m.content for m in rows), "角色回复没入库"


def test_second_message_reuses_the_same_conversation():
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        for text in ("第一句", "第二句"):
            m, sd = _event(text)
            asyncio.run(bridge._process(m, sd, "oc_reuse", "om_x"))
        _assert_real_reply(sink)
        conv_id = store().get_pref(feishu.conv_key("oc_reuse"), "")
        rows = store().messages(int(conv_id))
        user_lines = [x.content for x in rows if x.role == "user"]
        assert user_lines == ["第一句", "第二句"], user_lines


def test_group_message_without_bot_mention_is_silent():
    """群里没 @ 到机器人就一个字都不回（飞书也会把 @ 了别人的消息推过来）。"""
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        m, sd = _event("随便聊聊", chat_type="group",
                       mentions=[type("Mn", (), {"key": "@_user_1", "mentioned_type": "user",
                                                 "id": type("I", (), {"open_id": "ou_2"})()})()])
        asyncio.run(bridge._process(m, sd, "oc_grp", "om_g"))
        assert sink == [], "没被 @ 却回话了"


def test_group_message_with_bot_mention_is_answered():
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        m, sd = _event("在吗@_user_1", chat_type="group",
                       mentions=[type("Mn", (), {"key": "@_user_1", "mentioned_type": "bot",
                                                 "id": type("I", (), {"open_id": "ou_bot"})()})()])
        asyncio.run(bridge._process(m, sd, "oc_grp2", "om_g2"))
        assert sink, "@ 了机器人却没回"


def test_handle_event_drops_bot_and_duplicates_without_touching_loop():
    """handle_event 必须同步返回（飞书 3 秒超时），且要挡掉自己和重推。"""
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)

        def make(sender_type, mid, eid):
            msg = type("M", (), {"chat_id": "oc_h", "chat_type": "p2p", "message_id": mid,
                                 "message_type": "text",
                                 "content": json.dumps({"text": "hi"}, ensure_ascii=False),
                                 "mentions": []})()
            return type("D", (), {"event": type("E", (), {"message": msg,
                                    "sender": type("S", (), {"sender_type": sender_type})()})(),
                                  "header": {"event_id": eid}})()

        bridge.handle_event(make("bot", "om_a", "ev_a"))          # 机器人自己发的
        bridge.handle_event(make("user", "om_b", "ev_b"))
        bridge.handle_event(make("user", "om_b", "ev_b"))         # 同 event 重推
        time.sleep(0.2)
        # 只有第二条该被收下（处理与否不在这里断言，避免和耗时竞态）
        assert bridge.state.dedupe.seen("ev_a") is False, "自己的消息不该进去重表"
        assert bridge.state.dedupe.seen("ev_b") is True


def test_command_lists_characters_and_binds_one():
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        m, sd = _event("/角色")
        asyncio.run(bridge._process(m, sd, "oc_cmd", "om_c"))
        first = _sent(sink)[0][1]
        assert "端到端酱" in first, first

        m2, sd2 = _event("/角色 端到端酱")
        asyncio.run(bridge._process(m2, sd2, "oc_cmd", "om_c2"))
        assert store().get_pref(feishu.bind_key("oc_cmd")) == "e2e"

        # 换角色要顺带换会话，否则两个角色混在同一条历史里会串戏
        assert store().get_pref(feishu.conv_key("oc_cmd")) == ""


def test_unknown_command_still_replies():
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        m, sd = _event("/不存在的命令")
        asyncio.run(bridge._process(m, sd, "oc_uc", "om_u"))
        assert "没这个命令" in _sent(sink)[0][1]


def test_image_message_gets_an_explanation_not_silence():
    """已读不回是最差的体验：处理不了也得说一句话。"""
    with _Server() as server:
        _seed_character()
        sink = []
        bridge, s = _bridge(server, sink)
        msg = type("M", (), {"chat_id": "oc_img", "chat_type": "p2p", "message_id": "om_i",
                             "message_type": "image", "content": json.dumps({"image_key": "k"}),
                             "mentions": []})()
        sender = type("S", (), {"sender_type": "user"})()
        asyncio.run(bridge._process(msg, sender, "oc_img", "om_i"))
        assert sink, "图片消息完全没反应"
        assert "看不了" in _sent(sink)[0][1]


def test_reports_clearly_when_server_is_unreachable():
    """网页端没开着时，飞书那边要听到一句人话，而不是石沉大海。"""
    _seed_character()
    sink = []
    s = load_settings().model_copy(update={"feishu_api_base": "http://127.0.0.1:1"})
    state = feishu.BotState(s)
    worker = feishu.Worker()
    worker.start()
    bridge = feishu.Bridge(state, FakeClient(sink), lark_oapi, worker, settings_loader=lambda: s)
    m, sd = _event("在吗")
    asyncio.run(bridge._process(m, sd, "oc_off", "om_o"))
    assert sink, "连不上服务却什么都没发"
    text = _sent(sink)[0][1]
    assert "animechat run" in text, text
    worker.stop()
