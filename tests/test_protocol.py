"""流式标记协议：token 被切碎也要正确出图。"""

from animechat.stickers import MarkerStream, strip_markers, extract_markers


class FakeSticker:
    def __init__(self, sid):
        self.id = sid


def resolver(raw):
    table = {"傲娇": FakeSticker("tsun"), "加油": FakeSticker("jiayou"), "开心": FakeSticker("dagao")}
    return table.get(raw.strip())


def flatten(events):
    text = "".join(p for kind, p in events if kind == "text")
    stickers = [p.id for kind, p in events if kind == "sticker"]
    return text, stickers


def test_marker_inside_text_is_stripped_and_emitted():
    stream = MarkerStream(resolver)
    events = stream.feed("哼，才没有喜欢你") + stream.feed("[sticker:傲娇]") + stream.feed("！别误会") + stream.flush()
    text, stickers = flatten(events)
    assert text == "哼，才没有喜欢你！别误会"
    assert stickers == ["tsun"]


def test_marker_split_across_many_chunks():
    stream = MarkerStream(resolver)
    raw = "冲呀[sticker:加油]一定行的"
    events = []
    for ch in raw:
        events += stream.feed(ch)
    events += stream.flush()
    text, stickers = flatten(events)
    assert text == "冲呀一定行的"
    assert stickers == ["jiayou"]


def test_unknown_label_is_dropped_not_leaked():
    stream = MarkerStream(resolver)
    text, stickers = flatten(stream.feed("[sticker:完全没这个表情]") + stream.flush())
    assert "[" not in text and "sticker" not in text
    assert stickers == []


def test_fullwidth_and_chinese_keywords():
    stream = MarkerStream(resolver)
    _text, stickers = flatten(stream.feed("［表情：开心］") + stream.flush())
    assert stickers == ["dagao"]


def test_emotion_marker():
    stream = MarkerStream(resolver)
    events = stream.feed("好的[emotion:happy]") + stream.flush()
    assert ("emotion", "happy") in events
    assert "".join(p for k, p in events if k == "text") == "好的"


def test_stray_bracket_streams_out():
    stream = MarkerStream(resolver)
    events = stream.feed("这个 [ 只是方括号")
    events += stream.feed("，继续说下去")
    events += stream.flush()
    text, _ = flatten(events)
    assert text == "这个 [ 只是方括号，继续说下去"


def test_long_unterminated_bracket_releases():
    stream = MarkerStream(resolver)
    events = stream.feed("[" + "a" * 200) + stream.flush()
    text, _ = flatten(events)
    assert text.count("a") == 200


def test_strip_markers_helper():
    assert strip_markers("你好[sticker:开心][emotion:happy]") == "你好"

def test_narrated_marker_is_parsed_not_leaked():
    """模型会模仿历史写法，输出 [刚刚给对方发了表情包：xxx] 这类叙述体；
    必须吃掉并出图，不能以文字形式漏进气泡（用户报的 bug）。"""
    stream = MarkerStream(resolver)
    events = stream.feed("哼！[刚刚给对方发了表情包：傲娇]别误会") + stream.flush()
    text, stickers = flatten(events)
    assert text == "哼！别误会"
    assert stickers == ["tsun"]


def test_bare_narration_is_stripped():
    """没有可解析值的纯叙述：[发了表情包] / [刚刚给对方发了表情包] 整段吃掉。"""
    stream = MarkerStream(resolver)
    text, stickers = flatten(stream.feed("好呀[发了表情包]") + stream.flush())
    assert text == "好呀" and stickers == []
    assert strip_markers("好呀[刚刚给对方发了表情包]") == "好呀"
    assert strip_markers("好呀[刚刚给对方发了表情包：web-111-aaa]") == "好呀"


def test_extract_markers_accepts_narrated_form():
    assert extract_markers("[刚刚给对方发了表情包：傲娇]") == ["傲娇"]
    assert extract_markers("a[sticker:开心]b[表情：加油]") == ["开心", "加油"]


def test_history_marker_uses_protocol_format():
    """历史里的表情占位必须用 [sticker:...] 协议原形，模型才会照着学。"""
    from animechat.store import Store
    db = Store()
    cid = db.create_conversation("c1", "聊").id
    db.add_message(cid, "assistant", "诶嘿新表情！", stickers=["web-111-aaa"])
    rows = db.history_for_prompt(cid)
    assert len(rows) == 1
    assert "[sticker:web-111-aaa]" in rows[0]["content"]
    assert "刚刚给对方发了表情包" not in rows[0]["content"]


def test_history_strips_leftover_narrated_garbage():
    """修复前留在库里的叙述体脏数据，取给模型前要被 strip 掉。"""
    from animechat.store import Store
    db = Store()
    cid = db.create_conversation("c1", "聊").id
    db.add_message(cid, "user", "看这张[刚刚给对方发了表情包：web-111-aaa]", stickers=[])
    rows = db.history_for_prompt(cid)
    assert "刚刚给对方发了表情包" not in rows[0]["content"]

def test_flush_drops_unterminated_marker():
    """模型 finish 得比右括号还早时，流里只剩半个 [sticker:web-1789：
       绝不能当文字漏进气泡（用户报的就是这个残缺串）。"""
    stream = MarkerStream(resolver)
    text, stickers = flatten(stream.feed("哼！\n[sticker:web-178910592") + stream.flush())
    assert text == "哼！\n", "残缺标记必须以文字漏出"
    assert stickers == []
    # 合法方括号不能被误删
    t2, s2 = flatten(MarkerStream(resolver).feed("[笑] 挺好") + MarkerStream(resolver).flush())
    assert t2 == "[笑] 挺好" and s2 == []


def test_history_does_not_reinject_deleted_sticker_ids():
    """表情被删（清掉联网搜来的那批）后，历史绝不能把裸 id 重新喂回模型：
       它会照着吐一个解析不到的 [sticker:web-...]，整串漏进气泡。"""
    from animechat.store import Store
    db = Store()
    cid = db.create_conversation("c1", "聊").id
    db.add_message(cid, "assistant", "给你看张图", stickers=["web-gone-123", "kept-456"])
    rows = db.history_for_prompt(cid, label_of=lambda sid: "" if sid == "web-gone-123" else "加油")
    content = rows[0]["content"]
    assert "web-gone-123" not in content, "已删表情的裸 id 不得回喂"
    assert content.count("[sticker:") == 1, "只应保留还在库里那张"
    assert "[sticker:加油]" in content


