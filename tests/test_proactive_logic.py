"""主动发言资格判定（纯逻辑，不依赖网络）"""
import time

from src.animechat.feishu import (
    proactive_qualified, last_key, quota_key, day_key,
    read_float, read_int, idle_note,
    proactive_enabled, proactive_target,
)


def test_proactive_qualified():
    # 从没聊过 → 不主动
    assert not proactive_qualified(1000, 0, 0, 10, 120)[0]
    # 超过每日上限 → 不主动
    assert not proactive_qualified(1000, 100, 10, 10, 120)[0]
    # 沉默不够久 → 不主动
    result = proactive_qualified(1000, 900, 0, 10, 120)
    print(f"Debug: now=1000, last_active=900, idle=100, idle_min=120, threshold={120*60}, result={result}")
    assert not result[0]  # 100s < 120*60=72000, so should return False
    # 合格 → 主动
    ok, idle = proactive_qualified(1000, 100, 0, 120, 10)  # 900s > 10*60
    assert ok
    assert idle == 900.0
    # 边界：刚好够
    ok, idle = proactive_qualified(1000, 100, 0, 10, 1)  # 900s > 1*60
    assert ok
    # 边界：刚好不够
    ok, idle = proactive_qualified(1000, 100, 0, 10, 16)  # 900s < 16*60=960
    assert not ok


def test_read_float_int():
    assert read_float("123.45") == 123.45
    assert read_float("123") == 123.0
    assert read_float("abc", 10.0) == 10.0
    assert read_int("456") == 456
    assert read_int("456.7") == 456
    assert read_int("abc", 20) == 20


def test_idle_note():
    assert "分钟" in idle_note(60)
    assert "小时" in idle_note(3600)
    assert "天" in idle_note(86400)


def test_key_functions():
    assert last_key("chat123") == "feishu.last.chat123"
    # 2024-10-04 00:00:00 UTC
    assert quota_key("chat123", 1728000000) == "feishu.pq.chat123.20241004"
    assert day_key(1728000000) == "20241004"


def test_proactive_enabled():
    from src.animechat.store import store
    from src.animechat.config import Settings
    db = store()
    s = Settings()
    # 全局关 + 会话级未设置 → 关
    assert not proactive_enabled(db, "chat123", s)
    # 全局开 + 会话级未设置 → 开
    s.feishu_proactive = True
    assert proactive_enabled(db, "chat123", s)
    # 全局关 + 会话级 on → 开
    s.feishu_proactive = False
    db.set_pref("feishu.pmode.chat123", "on")
    assert proactive_enabled(db, "chat123", s)
    # 全局开 + 会话级 off → 关
    s.feishu_proactive = True
    db.set_pref("feishu.pmode.chat123", "off")
    assert not proactive_enabled(db, "chat123", s)


def test_proactive_target():
    from src.animechat.store import store
    db = store()
    # 没记录过 → 不算目标（避免骚扰刚绑定却没聊过的会话）
    assert proactive_target(db, "new") == ""
    # 记录为 p2p → 算目标
    db.set_pref("feishu.ctype.p2p123", "p2p")
    assert proactive_target(db, "p2p123") == "p2p"
    # 记录为 group → 不算目标（群聊不主动）
    db.set_pref("feishu.ctype.group123", "group")
    assert proactive_target(db, "group123") == ""