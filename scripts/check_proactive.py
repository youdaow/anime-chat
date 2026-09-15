"""诊断主动发言：遍历所有绑定的飞书会话，打印每个会话的主动发言状态。

跑法：
    python scripts/check_proactive.py          # 看所有会话
    python scripts/check_proactive.py 全部      # 同上（占位参数，保持命令行可用）

不写死任何 chat_id：直接从库里已绑定的会话列表取。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animechat.config import load_settings
from animechat.feishu import (
    list_bindings, proactive_enabled, proactive_target,
    next_due_key, last_key, quota_key,
)
from animechat.store import store


def main() -> int:
    s = load_settings()
    db = store()
    rows = list_bindings(db)
    if not rows:
        print("还没有任何飞书会话绑过角色 —— 先去飞书私聊里发一句，或在网页设置默认角色。")
        return 0
    now = time.time()
    idle = int(getattr(s, "feishu_idle_min", 120) or 120)
    cap = int(getattr(s, "feishu_daily_max", 10) or 0)
    print(f"全局主动开关 feishu_proactive = {getattr(s, 'feishu_proactive', False)}")
    print(f"基准沉默 = {idle} 分钟   每日上限 = {cap} 条\n")
    print("-" * 64)
    for chat_id, cid in rows.items():
        target = proactive_target(db, chat_id)
        on = proactive_enabled(db, chat_id, s)
        nxt = float(db.get_pref(next_due_key(chat_id), "0") or 0)
        last = float(db.get_pref(last_key(chat_id), "0") or 0)
        sent = int(db.get_pref(quota_key(chat_id, now), "0") or 0)
        if target != "p2p":
            status = "不主动（非私聊或没聊过）"
        elif not on:
            status = "已关（/主动 off 或全局未开）"
        elif sent >= cap:
            status = f"今天已满 {sent}/{cap}，明天再说"
        elif nxt <= 0:
            status = "等对方下次说完话后再计时"
        elif now >= nxt:
            status = "已到点，下个扫描周期(≤60秒)就该发"
        else:
            mins = int((nxt - now) // 60)
            when = time.strftime("%H:%M", time.localtime(nxt))
            status = f"约 {mins} 分钟后({when})主动"
        gap = int((now - last) // 60) if last else -1
        print(f"  {chat_id[:16]}…  角色={cid[:10]}  沉默{gap}分钟  已发{sent}/{cap}")
        print(f"      -> {status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
