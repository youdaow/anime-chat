"""飞书桥接：纯逻辑单测。

这些用例全部不需要 lark_oapi、也不需要连飞书 —— 模块里所有要 SDK 的东西都在
函数体内延迟 import，所以这一层测的是「消息怎么解析、怎么分段、怎么决定回不回」。
"""

import asyncio
import json

import pytest

from animechat import feishu
from animechat.models import Character
from animechat.store import Store


# ---------------------------------------------------------------- 入站解析
def test_text_content_is_a_json_string_and_mentions_get_stripped():
    # 飞书 text 的 content 是 JSON 字符串，不是裸文本
    text, notice = feishu.extract_text("text", json.dumps({"text": "@_user_1 你好"}))
    assert text == "@_user_1 你好"
    assert notice == ""
    assert feishu.strip_mentions(text, ["@_user_1"]) == "你好"


def test_extract_text_handles_post_and_says_no_for_unsupported():
    post = json.dumps({"title": "标题", "content": [[{"tag": "text", "text": "第一段"}],
                                                    [{"tag": "text", "text": "第二段"}]]})
    text, _ = feishu.extract_text("post", post)
    assert text == "标题\n第一段\n第二段"

    # 不支持的类型必须给一句人话，而不是回空字符串让人对着已读不发火
    for mt in ("image", "audio", "file", "merge_forward"):
        text, notice = feishu.extract_text(mt, "{}")
        assert text == "" and notice, mt


def test_garbage_content_does_not_raise():
    assert feishu.extract_text("text", "not json")[0] == "not json"
    assert feishu.extract_text("post", "[1,2]")[0] == ""


def test_quote_prefix_is_removed():
    assert feishu._clean_quote(">引用：(昨天的事)\n今天继续") == "今天继续"


# ---------------------------------------------------------------- @ 判断
def test_bot_mentioned_uses_mentioned_type_not_open_id():
    """用 mentioned_type == "bot" 判断，不依赖 bot/v3/info（那接口连文档都找不到）。"""
    assert feishu.bot_mentioned([{"key": "@_user_1", "mentioned_type": "bot"}])
    assert not feishu.bot_mentioned([{"key": "@_user_1", "mentioned_type": "user"}])
    assert not feishu.bot_mentioned([])


def test_is_from_bot_accepts_both_spellings():
    # 文档写 "bot"，也有地方见过 "app"；两个都挡，别赌
    assert feishu.is_from_bot({"sender_type": "bot"})
    assert feishu.is_from_bot({"sender_type": "app"})
    assert not feishu.is_from_bot({"sender_type": "user"})


def test_get_reads_both_dict_and_objects():
    class O:
        chat_id = "oc_1"

    assert feishu._get(O(), "chat_id") == "oc_1"
    assert feishu._get({"chat_id": "oc_2"}, "chat_id") == "oc_2"
    assert feishu._get(None, "chat_id", "兜底") == "兜底"


def test_pick_reads_business_fields_from_data_subobject():
    """真 SDK 的响应是 {code,msg,error,data}，image_key/message_id 全在 data 里。

    这是「权限全开了还是不发表情包」那个线上 bug 的防回归：代码当初读顶层
    getattr(resp,"image_key") 永远拿到 None，每张图都被判定上传失败静默跳过；
    而测试替身恰好把字段摊在顶层，于是全绿。两边形状必须同时吃得下。
    """
    class Body:
        image_key = "img_v3_abc"

    class Resp:
        code = 0
        msg = "ok"
        data = Body()

    assert feishu._pick(Resp(), "image_key") == "img_v3_abc"
    # 裸 dict（单测习惯）也照收
    assert feishu._pick({"data": {"message_id": "om_1"}}, "message_id") == "om_1"
    assert feishu._pick({"message_id": "om_2"}, "message_id") == "om_2"
    assert feishu._pick(Resp(), "不存在") is None
    # data 存在但字段为空时退回顶层，而不是拿空值冒充成功
    class Empty:
        code = 0
        data = type("B", (), {"image_key": ""})()
        image_key = "img_top"

    assert feishu._pick(Empty(), "image_key") == "img_top"


# ---------------------------------------------------------------- 分段
def test_split_keeps_short_text_as_one_message():
    assert feishu.split_message("短句") == ["短句"]
    assert feishu.split_message("") == []
    assert feishu.split_message("   ") == []


def test_split_breaks_at_sentence_boundary_not_midword():
    body = "第一句话。" * 200          # 1000 字
    parts = feishu.split_message(body, 100)
    assert len(parts) > 5
    # 每段都应该是完整句子结尾，不该把「第一句话」劈开
    for p in parts:
        assert p.endswith("。"), p[-10:]
    assert "".join(parts) == body


def test_split_falls_back_to_hard_cut_without_punctuation():
    parts = feishu.split_message("a" * 250, 100)
    assert [len(x) for x in parts] == [100, 100, 50]
    assert "".join(parts) == "a" * 250


def test_split_terminates_on_degenerate_input():
    """全是分隔符时不能死循环 —— 这种 bug 一上线就是 CPU 打满、桥接假死。"""
    for body in ("\n\n\n\n", "。，。，。，", "  \n  \n  ", "，"):
        parts = feishu.split_message(body, 2)
        assert len(parts) <= len(body) + 1      # 每轮至少吃掉一个字，切不出无限条
        assert all(p for p in parts), parts     # 也不该发出空气泡



def test_text_payload_encodes_newline_as_text():
    mt, content = feishu.text_payload("第一行\n第二行")
    assert mt == "text"
    assert json.loads(content)["text"] == "第一行\n第二行"


# ---------------------------------------------------------------- 命令
@pytest.mark.parametrize("raw,want", [
    ("/角色", ("character", "")),
    ("/角色： 亚丝娜", ("character", "亚丝娜")),
    ("/char 亚丝娜", ("character", "亚丝娜")),
    ("/Help", ("help", "")),
    ("/表情 off", ("sticker", "off")),
    ("？", ("", "")),                       # 不是命令
])
def test_parse_and_canonical(raw, want):
    kind, arg = feishu.parse_command(raw)
    assert (feishu.canonical_command(kind), arg) == want


def test_unknown_command_is_not_silent():
    kind, _ = feishu.parse_command("/不存在的命令")
    assert feishu.canonical_command(kind) == ""


def test_roleplay_slash_is_not_treated_as_a_command():
    """/me 摸摸头 是角色扮演动作，不能拦下来报「没这个命令」。"""
    assert "me" in feishu.ROLEPLAY_WORDS
    kind, _ = feishu.parse_command("/me 摸摸对方的头")
    assert feishu.canonical_command(kind) == ""
    assert kind in feishu.ROLEPLAY_WORDS


def test_match_character_prefers_exact_then_unique_contains():
    chars = [Character(id="a", name="亚丝娜"), Character(id="b", name="桐人"),
             Character(id="c", name="西妮卡")]
    assert feishu.match_character("桐人", chars)[0].id == "b"
    assert feishu.match_character("b", chars)[0].id == "b"        # id 也算精确命中
    got, ambiguous = feishu.match_character("亚丝", chars)
    assert got.id == "a" and not ambiguous                        # 唯一包含，直接用

    # 完全同名优先于猜：输入正好等于某个名字时直接给那个，不算歧义
    two = [Character(id="x", name="小林"), Character(id="y", name="小林同学")]
    assert feishu.match_character("小林", two)[0].id == "x"

    # 只有「都不是精确名、又命中多个」才报歧义，别猜一个送出去
    three = [Character(id="p", name="小林一"), Character(id="q", name="小林二")]
    assert feishu.match_character("小林", three) == (None, True)
    assert feishu.match_character("查无此人", chars) == (None, False)



def test_sticker_mode_text():
    assert feishu.sticker_mode_text("on") is True
    assert feishu.sticker_mode_text("关") is False
    assert feishu.sticker_mode_text("随便") is None


# ---------------------------------------------------------------- SSE 归并
def _sse(event, data):
    return "event: " + event, "data: " + json.dumps(data, ensure_ascii=False)


def test_feed_sse_uses_done_as_final_text():
    """done 带最终正文，以它为准 —— 累加的 text 可能是被 trim 之前的版本。"""
    out = feishu.ChatResult()
    feishu.feed_sse("text", {"t": "旧"}, out)
    feishu.feed_sse("done", {"content": "新", "stopped": False}, out)
    assert out.text == "新"


def test_feed_sse_records_sticker_and_error():
    out = feishu.ChatResult()
    feishu.feed_sse("start", {"message_id": 9, "mock": True}, out)
    feishu.feed_sse("sticker", {"id": "s1"}, out)
    feishu.feed_sse("emotion", {"key": "happy"}, out)
    assert (out.message_id, out.mock, out.emotion) == (9, True, "happy")
    assert out.stickers == [{"id": "s1"}]
    assert out.ok

    bad = feishu.ChatResult()
    feishu.feed_sse("error", {"code": "no_key", "message": "没填 Key"}, bad)
    assert bad.error == "没填 Key"
    assert not bad.ok


def test_reasoning_is_not_forwarded_to_feishu():
    """思考过程不该出现在飞书气泡里。"""
    out = feishu.ChatResult()
    feishu.feed_sse("reasoning", {"t": "让我想想"}, out)
    assert out.text == ""


def test_sse_accumulator_flushes_tail_without_blank_line():
    """httpx 读流结束时最后一段不一定有空行收尾，close() 必须把它吐出来。"""
    acc = feishu.SseAccumulator()
    acc.feed("event: done")
    acc.feed('data: {"content":"收尾"}')
    assert acc.result.text == ""
    acc.close()
    assert acc.result.text == "收尾"


def test_sse_accumulator_ignores_heartbeats_and_bad_json():
    acc = feishu.SseAccumulator()
    acc.feed(": ping")
    acc.feed("")
    acc.feed("event: text")
    acc.feed("data: {坏 json")
    acc.feed("")
    assert acc.result.text == ""


def test_collect_chat_round_trips_real_server_format():
    lines = ["event: start", 'data: {"message_id":3,"mock":true}', "",
             "event: text", 'data: {"t":"你好"}', "",
             "event: done", 'data: {"content":"你好","emotion":"happy"}', ""]
    out = feishu.collect_chat(lines)
    assert out.text == "你好" and out.emotion == "happy" and out.message_id == 3


# ---------------------------------------------------------------- 绑定
def test_bound_character_falls_back_to_last_character_not_first_in_library():
    """不能退到「角色库第一个」：刚建的空角色被派给飞书用户，他会以为软件坏了。"""
    db = Store()
    assert feishu.bound_character(db, "oc_1", "") == ""
    db.set_pref("last_character", "hero")
    assert feishu.bound_character(db, "oc_1", "") == "hero"
    db.set_pref(feishu.bind_key("oc_1"), "bound")
    assert feishu.bound_character(db, "oc_1", "") == "bound"
    assert feishu.bound_character(db, "oc_1", "cfg") == "bound"     # 绑定优先于配置


def test_unbind_character_only_touches_that_character(tmp=None):
    db = Store()
    db.set_pref(feishu.bind_key("oc_a"), "x")
    db.set_pref(feishu.bind_key("oc_b"), "y")
    assert feishu.unbind_character(db, "x") == 1
    assert feishu.list_bindings(db) == {"oc_b": "y"}
    assert feishu.unbind_all(db) == 1
    assert feishu.list_bindings(db) == {}


def test_wants_stickers_prefers_session_over_global():
    class S:
        feishu_stickers = True

    db = Store()
    assert feishu.wants_stickers(db, "oc_1", S()) is True
    db.set_pref(feishu.mode_key("oc_1"), "off")
    assert feishu.wants_stickers(db, "oc_1", S()) is False       # 会话级盖过全局
    assert feishu.wants_stickers(db, "oc_2", S()) is True


def test_prefixes_do_not_collide():
    """三张表都挂在 prefs 上，前缀撞了就全乱。"""
    assert feishu.BIND_PREFIX != feishu.CONV_PREFIX != feishu.MODE_PREFIX
    assert not feishu.BIND_PREFIX.startswith(feishu.CONV_PREFIX)
    assert feishu.conv_key("oc_1") not in feishu.bind_key("oc_1")


# ---------------------------------------------------------------- 去重
def test_dedupe_catches_repush_within_window():
    d = feishu.Dedupe(ttl=60)
    assert d.seen("evt_1", "om_1") is False
    assert d.seen("evt_1", "om_1") is True      # 飞书超时重推
    assert d.seen("evt_2", "om_1") is True      # 换 event_id 但同一条消息也算重复
    assert d.seen("evt_3", "om_9") is False


def test_dedupe_expires_and_ignores_empty_keys():
    assert feishu.Dedupe().seen("", "") is False
    d = feishu.Dedupe(ttl=-1)                   # 立刻过期
    d.seen("evt_a")
    assert d.seen("evt_a") is False


def test_dedupe_reclaims_memory_past_threshold():
    d = feishu.Dedupe(ttl=-1)
    for i in range(2100):
        d.seen(f"e{i}")
    assert len(d._seen) < 2100


# ---------------------------------------------------------------- 状态与配置
def test_api_base_normalizes_wildcard_host():
    class S:
        feishu_api_base = ""
        host = "0.0.0.0"
        port = 8899

    assert feishu.BotState(S()).api_base == "http://127.0.0.1:8899"

    class S2:
        feishu_api_base = "http://192.168.1.20:8899/"
        host = "127.0.0.1"
        port = 8899

    assert feishu.BotState(S2()).api_base == "http://192.168.1.20:8899"


def test_settings_round_trip_masks_app_secret():
    from animechat import config

    saved = config.save_settings({"feishu_app_id": "cli_test", "feishu_app_secret": "sec_1234567890"})
    assert saved.feishu_app_id == "cli_test"
    client = config.settings_for_client(saved)
    # mask() 的形状是 前4 …**** 后4：能认出是哪家、但拼不出完整 Key
    assert client["feishu_app_secret"] == "sec_…****7890"
    assert "sec_1234567890" not in json.dumps(client)
    assert client["configured_secrets"]["feishu_app_secret"] is True
    # App ID 不是密钥，得原样回显，否则用户打开设置看到空的会以为没保存
    assert client["feishu_app_id"] == "cli_test"


def test_singleton_lock_blocks_a_second_bridge(tmp_path):
    """两个桥接同时跑 = 有时回有时不回，必须当场拒绝第二个。

    飞书长连接是集群模式派发，一条消息只随机推给一个连接，所以第二个进程不会
    报错，只会安静地分走一半消息 —— 排查起来比任何真 bug 都费劲。
    跨进程才测得出文件锁，所以起一个真的子进程去持锁。
    """
    import os
    import subprocess
    import sys
    import time

    holder = tmp_path / "hold.py"
    holder.write_text(
        "import sys, time\n"
        "sys.path.insert(0, %r)\n"
        "from animechat import feishu\n"
        "lock = feishu.acquire_singleton_lock()\n"
        "print('HELD', flush=True)\n"
        "time.sleep(20)\n" % str(os.path.dirname(os.path.dirname(feishu.__file__))),
        encoding="utf-8",
    )
    env = dict(os.environ, ANIMECHAT_DATA_DIR=str(tmp_path))
    proc = subprocess.Popen([sys.executable, str(holder)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8")
    try:
        assert proc.stdout.readline().strip() == "HELD", "持锁子进程没起来"
        time.sleep(0.3)
        saved = os.environ.get("ANIMECHAT_DATA_DIR")
        os.environ["ANIMECHAT_DATA_DIR"] = str(tmp_path)
        try:
            import animechat.config as cfg

            old = cfg.DATA_DIR
            cfg.DATA_DIR = tmp_path
            try:
                msgs: list[str] = []
                got = feishu.acquire_singleton_lock(echo=msgs.append)
                assert got is False, "第二个桥接没被挡住，会抢消息"
                assert any("已经有一个桥接在跑" in m for m in msgs), msgs
            finally:
                cfg.DATA_DIR = old
        finally:
            if saved is None:
                os.environ.pop("ANIMECHAT_DATA_DIR", None)
            else:
                os.environ["ANIMECHAT_DATA_DIR"] = saved
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_check_config_reports_missing_credentials():
    from animechat.config import Settings

    rows = dict((n, g) for n, g, _ in feishu.check_config(Settings()))
    assert rows["App ID"] is False and rows["App Secret"] is False


# ---------------------------------------------------------------- 接口往返
def test_settings_api_round_trips_feishu_fields():
    """网页那三个输入框靠这几个键名存取，改名必须在这里先红。"""
    from fastapi.testclient import TestClient

    from animechat.server import create_app

    with TestClient(create_app()) as client:
        r = client.patch("/api/settings", json={
            "feishu_app_id": "cli_abc123",
            "feishu_app_secret": "sek_realmustnotleak",
            "feishu_character": "hero",
            "feishu_stickers": False,
        })
        assert r.status_code == 200, r.text
        got = r.json()["settings"]
        assert got["feishu_app_id"] == "cli_abc123"
        assert got["feishu_character"] == "hero"
        assert got["feishu_stickers"] is False
        assert "sek_realmustnotleak" not in r.text, "App Secret 明文回显了"
        assert got["configured_secrets"]["feishu_app_secret"] is True

        back = client.get("/api/settings").json()["settings"]
        assert back["feishu_app_id"] == "cli_abc123"
        assert "sek_realmustnotleak" not in client.get("/api/settings").text


# ---------------------------------------------------------------- 关键安全约束
def test_long_connection_mode_passes_empty_encrypt_and_token():
    """长连接模式下这两个参数必须是空串。

    填了 SDK 就会去验一个根本不存在的签名（长连接只在建连时鉴权，推过来的是明文），
    结果是每条消息都被丢掉 —— 表现成「机器人已读不回」，极难查，所以钉住。
    """
    import inspect
    import pathlib

    src = pathlib.Path(feishu.__file__).read_text(encoding="utf-8")
    assert 'EventDispatcherHandler.builder("", "")' in src
    fn = inspect.getsource(feishu.Bridge.handle_event)
    # 飞书 3 秒超时：在回调里等模型就会触发重推 + 心跳停摆
    assert "await" not in fn, "handle_event 必须同步返回"
    assert "self.worker.submit(factory)" in fn


def test_bot_replies_are_filtered_to_stop_self_loop():
    """不挡自己的消息就是无限自问自答，几分钟烧光额度。"""
    import pathlib

    src = pathlib.Path(feishu.__file__).read_text(encoding="utf-8")
    assert "if is_from_bot(sender):" in src


def test_group_chat_requires_mention():
    """群里没 @ 到就不说话 —— 飞书会把 @ 了别人的消息也推过来。"""
    import inspect

    src = inspect.getsource(feishu.Bridge._process)
    assert 'chat_type == "group"' in src and "bot_mentioned" in src


def test_module_does_not_import_lark_at_top_level():
    """网页端和测试环境不该因为没装 lark_oapi 就 import 失败。"""
    import pathlib
    import re

    src = pathlib.Path(feishu.__file__).read_text(encoding="utf-8")
    lines = [ln for ln in src.splitlines()
             if re.match(r"^(import|from)\s", ln) and not ln.startswith("from .")]
    assert not any("lark" in ln for ln in lines), lines


# ---------------------------------------------------------------- 群聊（纯逻辑）
def test_group_members_roundtrip_and_dedupe():
    db = Store()
    assert feishu.group_members(db, "oc_x") == []          # 没开群 = 空
    feishu.set_group(db, "oc_x", ["a", "b", "a"])          # 重复自动去重、保序
    assert feishu.group_members(db, "oc_x") == ["a", "b"]
    feishu.clear_group(db, "oc_x")
    assert feishu.group_members(db, "oc_x") == []


def test_match_characters_splits_names_and_reports_missing():
    from animechat.characters import book

    for cid, name in [("a", "丛雨"), ("b", "丰川祥子"), ("c", "千早爱音")]:
        book().save(Character(id=cid, name=name, greeting="嗨"))
    chars = book().list()
    found, missing = feishu.match_characters("丛雨 祥子 不存在的人", chars)
    assert [c.name for c in found] == ["丛雨", "丰川祥子"], found
    assert missing == ["不存在的人"], missing          # 没认出的原样回给用户，别默默丢
    # 顿号 / 逗号分隔也认，重复只算一个
    assert len(feishu.match_characters("丛雨、爱音", chars)[0]) == 2
    assert len(feishu.match_characters("丛雨 丛雨", chars)[0]) == 1


def test_group_command_aliases():
    assert feishu.canonical_command("群聊") == "group"
    assert feishu.canonical_command("群") == "group"
    assert feishu.canonical_command("group") == "group"


def test_group_expired_pure():
    now = 1_000_000.0
    t = feishu.GROUP_IDLE_TIMEOUT
    assert feishu.group_expired(now, now - t - 1, t) is True     # 刚好超阈值：过期
    assert feishu.group_expired(now, now - t, t) is True         # 边界等于阈值：过期
    assert feishu.group_expired(now, now - t + 60, t) is False   # 还差一分钟：没过期
    assert feishu.group_expired(now, 0, t) is False              # 没记过活动：别乱踢
    assert feishu.group_expired(now, -1, t) is False


def test_group_idle_timeout_is_one_hour():
    assert feishu.GROUP_IDLE_TIMEOUT == 3600.0


