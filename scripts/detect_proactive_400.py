"""决定性探测：正在运行的 web(8899) 认不认 /api/chat 的 proactive 字段。

拿一个真实本地会话，POST 一个 proactive=true、content 空的请求：
  - 返回 200(开始流式)  → 运行中的 web 支持主动发言，问题在桥接发错，重启桥接
  - 返回 400「说点什么」  → 运行中的 web 是旧代码、不认 proactive，重启 web
只读诊断，不改任何东西。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from animechat.config import load_settings
from animechat.feishu import list_bindings, conv_key
from animechat.store import store

BASE = "http://127.0.0.1:8899"


def pick_conv():
    db = store()
    rows = list_bindings(db)
    for chat_id, cid in rows.items():
        cv = db.get_pref(conv_key(chat_id), "")
        if cv.isdigit():
            conv = db.get_conversation(int(cv))
            if conv is not None:
                return conv.id
    # 兜底：任意一个会话
    for conv in getattr(db, "list_conversations", lambda: [])():
        cid = getattr(conv, "id", None)
        if cid is not None:
            return cid
    return None


def main() -> int:
    conv = pick_conv()
    if conv is None:
        print("没有可用会话，换个方式验证。")
        return 1
    payload = {"conversation_id": conv, "proactive": True, "idle_note": "诊断", "content": ""}
    print(f"POST /api/chat  conv={conv}  proactive=True content=''  →")
    try:
        with httpx.Client(timeout=httpx.Timeout(60.0, connect=8)) as c:
            with c.stream("POST", BASE + "/api/chat", json=payload) as r:
                code = r.status_code
                print(f"HTTP {code}")
                if code != 200:
                    body = (r.read()).decode("utf-8", "ignore")[:400]
                    print("返回体:", body)
                    print("判定: 运行中的 web 不认 proactive → 需【重启 web 服务】")
                else:
                    # 读几行 SSE，确认它真的在生成而非报错
                    lines = []
                    for ln in r.iter_lines():
                        if ln.strip():
                            lines.append(ln)
                        if len(lines) >= 6:
                            break
                    print("前几帧:", " | ".join(lines[:6])[:300])
                    print("判定: 运行中的 web 支持 proactive，主动发言链路正常")
    except httpx.HTTPError as exc:
        print("连不上 web:", exc, "（那才是真·web 没跑）")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
