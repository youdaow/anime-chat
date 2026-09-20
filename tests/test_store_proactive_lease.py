"""主动发言配额 lease 的并发、异常数据与恢复测试。"""

import json
import math
import multiprocessing
import queue
import time
from pathlib import Path

import pytest

from animechat.store import Store


def _claim_worker(path, chat_id, quota_key, barrier, results):
    db = Store(Path(path))
    barrier.wait(timeout=10)
    results.put(db.claim_proactive(chat_id, quota_key, daily_max=1, ttl=30))


def _spawn_claims(tmp_path, chat_id="race"):
    ctx = multiprocessing.get_context("spawn")
    barrier = ctx.Barrier(2)
    results = ctx.Queue()
    path = str(tmp_path / "race.db")
    # 先由父进程完成建表、迁移和 WAL 初始化；spawn 子进程随后同时进入 BEGIN IMMEDIATE，
    # 避免两个子进程争抢 journal_mode 写锁，真正测试的是 quota + lease 原子竞争。
    Store(Path(path))
    processes = [
        ctx.Process(target=_claim_worker, args=(path, chat_id, "quota-race", barrier, results)),
        ctx.Process(target=_claim_worker, args=(path, chat_id, "quota-race", barrier, results)),
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(15)
        assert not process.is_alive(), "跨进程竞争测试超时"
        assert process.exitcode == 0, f"竞争进程退出码 {process.exitcode}"
    values = []
    for _ in processes:
        try:
            values.append(results.get(timeout=2))
        except queue.Empty:
            values.append(None)
    return values


@pytest.fixture()
def db(tmp_path):
    return Store(tmp_path / "lease.db")


def _claim(chat_id, quota_key, token="token", expires=None, state="reserved"):
    return json.dumps({
        "token": token,
        "expires": time.time() + 30 if expires is None else expires,
        "quota_key": quota_key,
        "state": state,
    })


def test_claim_commit_release_are_token_scoped(db):
    token = db.claim_proactive("chat-1", "quota-20260919", daily_max=1, ttl=30)
    assert token
    assert db.get_pref("feishu.proactive.claim.chat-1")
    assert db.get_pref("quota-20260919") == "1"

    assert db.release_proactive("chat-1", "wrong-token") is False
    assert db.renew_proactive("chat-1", "wrong-token", ttl=30) is False
    assert db.commit_proactive("chat-1", "wrong-token") is False
    assert db.get_pref("quota-20260919") == "1"

    assert db.renew_proactive("chat-1", token, ttl=60) is True
    assert db.commit_proactive("chat-1", token) is True
    assert db.commit_proactive("chat-1", token) is False
    assert db.release_proactive("chat-1", token) is False
    assert db.get_pref("quota-20260919") == "1"

    db.set_pref("feishu.proactive.claim.chat-1", _claim("chat-1", "quota-20260919", token))
    assert db.release_proactive("chat-1", token) is True
    assert db.get_pref("quota-20260919") == "0"
    assert db.get_pref("feishu.proactive.claim.chat-1") == ""
    assert db.release_proactive("chat-1", token) is False


def test_expired_reserved_can_be_reclaimed_but_committing_quota_is_kept(db):
    first = db.claim_proactive("chat-expired", "quota-expired", daily_max=1, ttl=0.01)
    assert first
    time.sleep(0.03)
    second = db.claim_proactive("chat-expired", "quota-expired", daily_max=1, ttl=30)
    assert second and second != first
    assert db.get_pref("quota-expired") == "1"

    committed = db.claim_proactive("chat-committed", "quota-committed", daily_max=2, ttl=0.01)
    assert committed
    assert db.commit_proactive("chat-committed", committed) is True
    time.sleep(0.03)
    reclaimed = db.claim_proactive("chat-committed", "quota-committed", daily_max=2, ttl=30)
    assert reclaimed and reclaimed != committed
    assert db.get_pref("quota-committed") == "2"


def test_malformed_lease_records_are_deleted_without_unsafe_refund(db):
    malformed = [
        "null",
        "[]",
        '"not-an-object"',
        json.dumps({"token": "missing-expiry", "quota_key": "quota-missing"}),
        json.dumps({"token": "nan", "expires": "nan", "quota_key": "quota-nan", "state": "reserved"}),
        json.dumps({"token": "inf", "expires": "inf", "quota_key": "quota-inf", "state": "reserved"}),
        json.dumps({"token": "numeric-quota", "quota_key": 123, "state": "reserved"}),
        json.dumps({"token": "", "quota_key": "empty-token", "state": "reserved"}),
    ]
    for index, raw in enumerate(malformed):
        chat_id = f"chat-bad-{index}"
        quota_key = f"quota-bad-{index}"
        db.set_pref("feishu.proactive.claim." + chat_id, raw)
        db.set_pref(quota_key, "7")
        token = db.claim_proactive(chat_id, quota_key, daily_max=2, ttl=30)
        assert token is None, f"损坏 lease 不应拿当前 quota 冒险: {raw}"
        assert db.get_pref(quota_key) == "7", f"无法证明归属的配额被错误回退: {raw}"
        assert db.get_pref("feishu.proactive.claim." + chat_id) == ""


def test_invalid_conversation_and_message_timestamps_are_normalized(tmp_path):
    db = Store(tmp_path / "timestamps.db")
    with db._raw() as conn:
        conn.execute(
            "INSERT INTO conversations(character_id,title,participants,created_at,updated_at) "
            "VALUES(?,?,?, ?, ?)",
            ("char", "坏时间", "[\"char\"]", "", "nan"),
        )
        conn.execute(
            "INSERT INTO messages(conversation_id,role,content,speaker,created_at) "
            "VALUES(?,?,?,?,?)",
            (1, "assistant", "旧消息", "char", "inf"),
        )
        conn.execute(
            "INSERT INTO conversations(character_id,title,participants,created_at,updated_at) "
            "VALUES(?,?,?, ?, ?)",
            ("char2", "无穷时间", "[\"char2\"]", "-inf", "1.0"),
        )
        conn.execute(
            "INSERT INTO messages(conversation_id,role,content,speaker,created_at) "
            "VALUES(?,?,?,?,?)",
            (2, "user", "用户", "", ""),
        )
    conversations = db.list_conversations()
    assert [c.created_at for c in conversations] == [0.0, 0.0]
    assert [c.updated_at for c in conversations] == [0.0, 1.0]
    assert [m.created_at for m in db.messages(1)] == [None]
    assert [m.created_at for m in db.messages(2)] == [None]


def test_cross_process_claim_is_atomic_with_spawn(tmp_path):
    results = _spawn_claims(tmp_path)
    assert sum(token is not None for token in results) == 1
    assert None in results
