"""主动发言 e2e 测试（需要真实桥接运行）"""
import asyncio
import time

from src.animechat.feishu import (
    Bridge, BotState, Worker, acquire_singleton_lock, _read_holder_pid,
    last_key, ctype_key, quota_key, proactive_qualified,
    proactive_enabled, proactive_target,
    read_float, read_int,
)
from src.animechat.config import load_settings
from src.animechat.store import store


def test_proactive_e2e():
    """模拟桥接行为：记录活跃时间 + 触发主动发言资格判定"""
    db = store()
    s = load_settings()
    now = time.time()

    # 模拟一个私聊会话，绑定角色
    chat_id = "test_p2p_123"
    char_id = "test_char_001"
    db.set_pref("feishu.bind." + chat_id, char_id)
    db.set_pref("feishu.ctype." + chat_id, "p2p")  # 标记为私聊
    db.set_pref("feishu.last." + chat_id, str(now - 7200))  # 2小时前活跃过

    # 检查资格：沉默2小时，每日上限10，当前已发0条
    qualified, idle = proactive_qualified(now, now - 7200, 0, 10, 120)
    assert qualified, "应该主动（沉默2小时，未超限）"
    assert idle == 7200.0, "沉默时长应该是2小时"

    # 检查主动开关：全局关，会话级未设置 → 关
    assert not proactive_enabled(db, chat_id, s), "默认全局关，不应该主动"

    # 开启全局主动
    s.feishu_proactive = True
    assert proactive_enabled(db, chat_id, s), "全局开了，应该主动"

    # 关闭会话级主动
    db.set_pref("feishu.pmode." + chat_id, "off")
    assert not proactive_enabled(db, chat_id, s), "会话级关了，不应该主动"

    # 重新开启会话级主动
    db.set_pref("feishu.pmode." + chat_id, "on")
    assert proactive_enabled(db, chat_id, s), "会话级开了，应该主动"

    # 检查目标：私聊算目标，群聊不算
    assert proactive_target(db, chat_id) == "p2p", "私聊应该算目标"
    assert proactive_target(db, "group_123") == "", "群聊不算目标"

    # 模拟已经发了5条，还在限额内
    db.set_pref(quota_key(chat_id, now), "5")
    qualified, _ = proactive_qualified(now, now - 7200, 5, 10, 120)
    assert qualified, "发了5条还在限额内，应该主动"

    # 模拟已经发了10条，超限了
    db.set_pref(quota_key(chat_id, now), "10")
    qualified, _ = proactive_qualified(now, now - 7200, 10, 10, 120)
    assert not qualified, "已发10条，超限了，不应该主动"

    # 模拟从没聊过
    db.set_pref(last_key("new_chat"), "0")
    qualified, _ = proactive_qualified(now, 0, 0, 10, 120)
    assert not qualified, "从没聊过，不应该主动"

    print("✅ 主动发言 e2e 测试通过")


if __name__ == "__main__":
    test_proactive_e2e()