from animechat import prompt
from animechat.config import Settings
from animechat.models import Character, DialogTurn


class FakeLib:
    def all(self):
        class S:
            label = "傲娇"
            tags = ["才不是", "哼"]
            emotion = "tsundere"
        return [S()]


def char(**kw):
    base = dict(id="c", name="小爱", title="元气同桌", description="永远满电",
                personality="直率", speaking_style="感叹号很多",
                example_dialogs=[DialogTurn(user="早", char="早呀！")])
    base.update(kw)
    return Character(**base)


def test_system_prompt_carries_persona_and_protocol():
    text = prompt.system_prompt(char(), Settings(), FakeLib())
    assert "小爱" in text and "永远满电" in text and "感叹号很多" in text
    assert "[sticker:标签]" in text and "[emotion:" in text
    assert "傲娇" in text  # 表情标签词表进来了
    assert "傲娇/才不是/哼(傲娇)" in text, \
        "每张都要带上情绪名：联网抓来的表情名字起得随便，模型只认得出「它在演什么」"
    assert "12356" in text  # 危机求助写进硬规则


def test_off_style_forbids_markers():
    text = prompt.system_prompt(char(sticker_style="off"), Settings(), FakeLib())
    assert "绝对不要输出" in text


def test_default_caps_one_sticker_per_reply():
    """用户反馈「一次回两个」太多：默认上限收成 1 张，提示词也不许鼓动发两张。"""
    assert Settings().max_stickers_per_reply == 1
    text = prompt.system_prompt(char(sticker_style="rich"), Settings(), FakeLib())
    assert "一条回复最多 1 张" in text
    assert "配 2 张" not in text


def test_rich_style_pushes_frequency():
    text = prompt.system_prompt(char(sticker_style="rich"), Settings(), FakeLib())
    assert "几乎每条回复都配" in text


def test_history_respects_char_budget():
    settings = Settings(context_chars=800)
    history = [{"role": "user", "content": "x" * 300}, {"role": "assistant", "content": "y" * 300},
               {"role": "user", "content": "最新的一条"}]
    msgs = prompt.history_messages(char(), history, settings)
    assert msgs[-1]["content"] == "最新的一条"
    total = sum(len(m["content"]) for m in msgs)
    assert total <= 800 + 300  # 至少留一条，不会把预算当成硬砍到 0


def test_summary_and_post_history_are_injected():
    settings = Settings()
    msgs = prompt.history_messages(char(post_history="保持简短"),
                                   [{"role": "user", "content": "hi"}], settings, summary="他叫小明")
    assert any("之前聊过的" in m["content"] for m in msgs)
    assert msgs[-1]["content"] == "保持简短"


def test_empty_history_only_system_injects_summary():
    msgs = prompt.history_messages(char(), [], Settings(), summary="只有摘要")
    assert len(msgs) == 1 and "只有摘要" in msgs[0]["content"]
