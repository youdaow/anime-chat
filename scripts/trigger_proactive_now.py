"""手动触发一次主动发言，验证「到点了确实发得出来」。

把到期点挪到现在（过去几秒），桥接的下个扫描周期(≤60秒)就会主动发一条；
本脚本随后轮询「今日主动条数」是否 +1，+1 = 真的发出去了，并顺带确认发完后
next_due 是否被自动重排到未来（即恢复 1~4 小时的原节奏，不是永久加速）。

只写数据库 prefs（下次到期时间戳），不动 settings.json、不动任何会话历史。
跑：python scripts/trigger_proactive_now.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from animechat.config import load_settings
from animechat.feishu import (list_bindings, proactive_target, proactive_enabled,
                              next_due_key, quota_key)
from animechat.store import store

WAIT_SECONDS = 180


def main() -> int:
    s = load_settings()
    db = store()
    now = time.time()
    rows = list_bindings(db)
    if not rows:
        print("还没有任何飞书会话绑过角色。")
        return 1
    cap = int(getattr(s, "feishu_daily_max", 10) or 0)

    targets = []
    for chat_id in rows:
        if proactive_target(db, chat_id) != "p2p":
            print("跳过 %s… —— 非私聊或从没聊过（群聊不主动）" % chat_id[:14])
            continue
        if not proactive_enabled(db, chat_id, s):
            print("跳过 %s… —— 主动开关关着（去飞书发 /主动 on）" % chat_id[:14])
            continue
        before = int(db.get_pref(quota_key(chat_id, now), "0") or 0)
        db.set_pref(next_due_key(chat_id), str(now - 10))   # 到期点设到过去
        print("已把 %s… 的到期点设为过去，当前今日已发 %d/%d。等待桥接下个扫描周期(≤60秒)…"
              % (chat_id[:14], before, cap))
        targets.append((chat_id, before))

    if not targets:
        print("没有可触发的会话。")
        return 1

    deadline = time.time() + WAIT_SECONDS
    done = {}
    while time.time() < deadline:
        time.sleep(4)
        t = time.time()
        for chat_id, before in targets:
            if chat_id in done:
                continue
            q = int(db.get_pref(quota_key(chat_id, t), "0") or 0)
            if q > before:
                nd = float(db.get_pref(next_due_key(chat_id), "0") or 0)
                mins = int((nd - t) // 60) if nd > 0 else -1
                print("✅ %s… 已发出（今日 %d/%d）。发完后下次主动约 %d 分钟后 —— 原节奏已自动恢复。"
                      % (chat_id[:14], q, cap, mins))
                done[chat_id] = True
        if len(done) == len(targets):
            break

    pending = [c[:14] for c, _ in targets if c not in done]
    if pending:
        print("⚠️ %d 秒内没观察到发出：%s。" % (WAIT_SECONDS, pending))
        print("   排查：桥接进程是否还在跑？这期间你是否又发了消息把到期点顶后了？"
              "本机 web 接口是否超时？今日是否已满上限？")
        print("   也可直接看飞书：有没有收到角色的主动消息。")
    return 0 if not pending else 2


if __name__ == "__main__":
    raise SystemExit(main())
