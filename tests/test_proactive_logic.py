"""主动发言：随机间隔 + 到期判定（纯逻辑，不依赖网络）"""
import random

from src.animechat.feishu import (
    proactive_interval, proactive_due, next_due_key, last_key, quota_key, day_key,
    read_float, read_int, idle_note,
    proactive_enabled, proactive_target, proactive_schedule_valid,
)


def test_interval_is_random_within_half_to_double():
    """基准 120 分钟 → 每次落在 60~240 分钟，而且不能每次都一样。"""
    vals = [proactive_interval(120) for _ in range(200)]
    assert all(120 * 60 * 0.5 - 1 <= v <= 120 * 60 * 2.0 + 1 for v in vals), vals[:5]
    assert len(set(vals)) > 50, "200 次抽样几乎全相同，骰子根本没摇"
    # 分布应大致铺开：落在区间下半段和上半段的都得有相当数量
    lo = sum(1 for v in vals if v < 120 * 60)
    assert 0.2 * len(vals) < lo < 0.8 * len(vals), f"分布偏了，低段占比 {lo / len(vals):.2f}"


def test_interval_with_seeded_rng_is_reproducible():
    a = proactive_interval(60, random.Random(42))
    b = proactive_interval(60, random.Random(42))
    assert a == b

    # 注入的 rng 必须真的被用到（而不是内部又开了一个）
    class Upper:
        def uniform(self, lo, hi):
            return hi
    assert proactive_interval(10, Upper()) == 10 * 60 * 2.0
    assert proactive_interval(10, _Lower()) == 10 * 60 * 0.5


class _Lower:
    def uniform(self, lo, hi):
        return lo


def test_interval_floors_at_one_minute():
    """idle_min 配成 0 / 负数也不能算出 0 秒间隔，否则每个 tick 都会主动。"""
    for bad in (0, -5):
        assert proactive_interval(bad, _Lower()) >= 60 * 0.5


def test_due_needs_a_recorded_deadline():
    """next_at<=0：新绑定或从没聊过的会话，绝不主动去骚扰。"""
    assert not proactive_due(1000, 0, 0, 10)
    assert not proactive_due(1000, -1, 0, 10)


def test_due_respects_deadline_and_quota():
    assert proactive_due(1000, 900, 0, 10)         # 到期点已过
    assert proactive_due(1000, 1000, 0, 10)        # 正好到点
    assert not proactive_due(1000, 1001, 0, 10)    # 还没到
    assert not proactive_due(1000, 900, 10, 10)    # 超每日上限
    assert not proactive_due(1000, 900, 99, 0)     # 上限配 0 = 不主动
    assert proactive_due(1000, 900, 9, 10)         # 还差一条就到顶


def test_key_functions():
    assert last_key("chat123") == "feishu.last.chat123"
    assert next_due_key("chat123") == "feishu.next.chat123"
    # 2024-10-04 00:00:00 UTC
    assert quota_key("chat123", 1728000000) == "feishu.pq.chat123.20241004"
    assert day_key(1728000000) == "20241004"


def test_read_float_int():
    assert read_float("123.45") == 123.45
    assert read_float("123") == 123.0
    assert read_float("abc", 10.0) == 10.0
    assert read_float("", 5.0) == 5.0
    assert read_int("456") == 456
    assert read_int("456.7") == 456
    assert read_int("abc", 20) == 20


def test_read_float_rejects_nan_inf():
    assert read_float("nan") == 0.0
    assert read_float("inf") == 0.0
    assert read_float("-inf") == 0.0
    assert read_int("nan") == 0
    assert read_int("inf") == 0


def test_schedule_requires_trustworthy_activity():
    # A stale positive deadline is accepted only while both the deadline and
    # the last activity timestamp are close to now.
    assert not proactive_schedule_valid(1000, 900, 100, 1)
    assert not proactive_schedule_valid(1000, 100, 100, 1)
    assert not proactive_schedule_valid(1000, 900, 0, 1)
    assert not proactive_schedule_valid(1000, 900, float("nan"), 1)
    assert not proactive_schedule_valid(1000, 900, float("inf"), 1)
    assert not proactive_schedule_valid(1000, 0, 100, 1)
    assert proactive_schedule_valid(1000, 900, 800, 1)
    assert proactive_schedule_valid(1000, 999, 999, 1)
    assert proactive_schedule_valid(1000, 1000, 1000, 1)


def test_idle_note():
    assert "分钟" in idle_note(60)
    assert "小时" in idle_note(3600)
    assert "天" in idle_note(86400)


def test_proactive_enabled():
    from src.animechat.store import store
    from src.animechat.config import Settings
    db = store()
    s = Settings()
    assert not proactive_enabled(db, "c1", s)       # 全局关 + 会话级未设
    s.feishu_proactive = True
    assert proactive_enabled(db, "c1", s)           # 全局开 + 会话级未设
    s.feishu_proactive = False
    db.set_pref("feishu.pmode.c1", "on")
    assert proactive_enabled(db, "c1", s)           # 会话级 on 覆盖全局
    s.feishu_proactive = True
    db.set_pref("feishu.pmode.c1", "off")
    assert not proactive_enabled(db, "c1", s)       # 会话级 off 也覆盖全局
    assert proactive_enabled(db, "other", s)        # 别的会话不受影响


def test_proactive_target():
    from src.animechat.store import store
    db = store()
    assert proactive_target(db, "brand_new") == ""          # 没记录过 → 不是目标
    db.set_pref("feishu.ctype.t_p2p", "p2p")
    assert proactive_target(db, "t_p2p") == "p2p"           # 私聊 → 目标
    db.set_pref("feishu.ctype.t_grp", "group")
    assert proactive_target(db, "t_grp") == ""               # 群聊 → 不主动
