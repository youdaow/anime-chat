from animechat import prompt
from animechat.config import Settings
import pytest
from animechat.models import Character, DialogTurn


class FakeLib:
    def all(self):
        class S:
            label = "傲娇"
            tags = ["才不是", "哼"]
            emotion = "tsundere"
            created_at = 0.0
            favorite = 0
            uses = 0
            id = "s0"
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


@pytest.mark.parametrize("seconds, tier", [
    (1799, "黏人试探"),
    (1800, "转冷"),
    (7199, "转冷"),
    (7200, "轻微失控"),
    (21599, "轻微失控"),
    (21600, "偏执"),
])
def test_silence_tier_boundaries(seconds, tier):
    assert prompt.silence_state(seconds)[0] == tier


def test_character_context_budget_overrides_global_budget():
    settings = Settings(context_chars=800)
    c = char(context_chars=12000)
    history = [{"role": "user", "content": "早" * 1000},
               {"role": "assistant", "content": "早" * 1000},
               {"role": "user", "content": "最新"}]
    msgs = prompt.history_messages(c, history, settings)
    assert len("".join(m["content"] for m in msgs)) > 2000


def test_normal_reply_softens_long_silence_from_real_timestamps():
    now = 1_700_000_000.0
    history = [{"role": "user", "content": "旧消息", "created_at": now - 7200},
               {"role": "assistant", "content": "旧回复", "created_at": now - 7199},
               {"role": "user", "content": "刚回", "created_at": now}]
    note = prompt.silence_note(prompt.silence_seconds(history, now=now))
    assert "黏人试探" in note and "转冷" not in note
    assert "刚回过你消息" in note


def test_proactive_silence_escalates_from_real_timestamps():
    now = 1_700_000_000.0
    history = [{"role": "user", "content": "最后一条", "created_at": now - 7200}]
    note = prompt.silence_note(prompt.silence_seconds(history, now=now))
    assert "轻微失控" in note
    assert "7200" not in note and "2.0 小时" in note


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf", "", None, "not-a-time"])
def test_silence_timestamps_ignore_non_finite_user_messages(bad):
    now = 1_700_000_000.0
    history = [
        {"role": "user", "content": "坏时间", "created_at": bad},
        {"role": "user", "content": "可信时间", "created_at": now - 60},
    ]
    assert prompt.last_user_message_at(history) == now - 60
    assert prompt.silence_seconds(history, now=now) == 60
    assert "黏人试探" in prompt.silence_note(60)


def test_silence_timestamps_select_latest_finite_user_message():
    now = 1_700_000_000.0
    history = [
        {"role": "user", "content": "旧消息", "created_at": now - 7200},
        {"role": "assistant", "content": "旧回复", "created_at": now - 1},
        {"role": "user", "content": "新消息", "created_at": now - 30},
    ]
    assert prompt.last_user_message_at(history) == now - 30
    assert prompt.silence_seconds(history, now=now) == 30
