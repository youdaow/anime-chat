"""SQLite 会话存储。

每次操作一个短连接（应用跑在线程池里，这样最省心也不会跨线程共用连接）。
WAL + 外键级联删除，删会话不会留下孤儿消息。
"""

from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .config import db_path, ensure_dirs
from .models import Conversation, Message
from .stickers import strip_markers

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    character_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    pinned INTEGER NOT NULL DEFAULT 0,
    participants TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    summary_upto INTEGER NOT NULL DEFAULT 0,
    last_read_id INTEGER NOT NULL DEFAULT 0,
    owner TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content TEXT NOT NULL DEFAULT '',
    stickers TEXT NOT NULL DEFAULT '[]',
    emotion TEXT,
    meta TEXT NOT NULL DEFAULT '{}',
    speaker TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_conv_char ON conversations(character_id, updated_at);
CREATE TABLE IF NOT EXISTS prefs (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- 访客（认证开着时才用）。库里没有明文口令：只存 salted scrypt 摘要，
-- bucket 是口令摘要的一个短前缀，用来把「逐条跑 scrypt」变成查一行 ——
-- 全表扫的话每个访客一次登录都要几十毫秒的哈希开销，既慢又是个计时侧信道。
CREATE TABLE IF NOT EXISTS visitors (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    bucket TEXT NOT NULL,
    code_hash TEXT NOT NULL,
    admin INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_seen REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_visitor_bucket ON visitors(bucket);
"""

# 会话列表和会话详情共用的一段 SQL：未读 = 水位线之后的角色回复条数。只数 assistant，
# 用户自己发出去的那条不该给自己点红点；按消息 id 比大小而不是比时间，
# 免得同一秒内落库的两条消息谁先谁后不确定。
_UNREAD_SQL = (" (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id"
               " AND m.role='assistant' AND m.id>c.last_read_id) AS unread")


def _migrate(conn) -> None:
    """给已经建好的老库补列。CREATE TABLE IF NOT EXISTS 不会动已存在的表，
    所以群聊新加的 participants / speaker 必须显式 ALTER，否则老库一查就 OperationalError。"""
    def cols(t):
        return {r[1] for r in conn.execute('PRAGMA table_info(' + t + ')')}
    if 'participants' not in cols('conversations'):
        conn.execute("ALTER TABLE conversations ADD COLUMN participants TEXT NOT NULL DEFAULT '[]'")
    if 'speaker' not in cols('messages'):
        conn.execute("ALTER TABLE messages ADD COLUMN speaker TEXT NOT NULL DEFAULT ''")
    if 'last_read_id' not in cols('conversations'):
        conn.execute("ALTER TABLE conversations ADD COLUMN last_read_id INTEGER NOT NULL DEFAULT 0")
        # 老库里的历史一律当成读过。少这一步，升级完满屏都是红点（默认水位 0 =
        # 「一条都没读」），用户会以为软件坏了，而不是以为自己真有几十条没看。
        conn.execute(
            "UPDATE conversations SET last_read_id ="
            " COALESCE((SELECT MAX(id) FROM messages WHERE messages.conversation_id = conversations.id), 0)")
    if 'owner' not in cols('conversations'):
        # 认证没开时没人写 owner，空串就是「本机主人本人的会话」。老库整片留空即可：
        # 那些历史本来就是他自己的聊天记录，不该被某个后注册的访客看见。
        conn.execute("ALTER TABLE conversations ADD COLUMN owner TEXT NOT NULL DEFAULT ''")


class Store:
    def __init__(self, path: Path | None = None) -> None:
        ensure_dirs()
        self.path = path or db_path()
        self._lock = threading.Lock()
        with self._raw() as conn:
            conn.executescript(SCHEMA)
            _migrate(conn)

    @contextmanager
    def _raw(self):
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=8000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------ 会话
    def create_conversation(self, character_id: str, title: str = "",
                            participants: list[str] | None = None, owner: str = "") -> Conversation:
        """participants 给两个以上就是群聊。character_id 恒为第一个成员，
        这样只认 character_id 的老代码（列表过滤、侧栏归属）不会瞎。
        owner 是认证开起来之后「这条会话是谁的」；空 = 本机主人本人。"""
        parts = [p for p in dict.fromkeys(participants or []) if p]
        if character_id not in parts:
            parts = [character_id] + parts
        now = time.time()
        with self._raw() as conn:
            cur = conn.execute(
                "INSERT INTO conversations(character_id,title,pinned,participants,owner,created_at,updated_at)"
                " VALUES(?,?,0,?,?,?,?)",
                (character_id, title[:60], json.dumps(parts, ensure_ascii=False), owner, now, now),
            )
            cid = int(cur.lastrowid)
        return Conversation(id=cid, character_id=character_id, title=title, participants=parts,
                            owner=owner, created_at=now, updated_at=now)

    def get_conversation(self, cid: int) -> Conversation | None:
        with self._raw() as conn:
            row = conn.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS n,"
                " (SELECT content FROM messages m WHERE m.conversation_id=c.id ORDER BY id DESC LIMIT 1) AS last,"
                + _UNREAD_SQL +
                " FROM conversations c WHERE c.id=?", (cid,)).fetchone()
        if row is None:
            return None
        return _conv(row)

    def conversation_owner(self, cid: int) -> str | None:
        """这条会话归谁；会话不存在返回 None。门口做归属检查用的单条查询。"""
        with self._raw() as conn:
            row = conn.execute("SELECT owner FROM conversations WHERE id=?", (cid,)).fetchone()
        return None if row is None else (row["owner"] or "")

    def message_owner(self, mid: int) -> str | None:
        """这条消息所在会话归谁；消息不存在返回 None。"""
        with self._raw() as conn:
            row = conn.execute(
                "SELECT c.owner FROM messages m JOIN conversations c ON c.id=m.conversation_id"
                " WHERE m.id=?", (mid,)).fetchone()
        return None if row is None else (row["owner"] or "")

    def list_conversations(self, character_id: str | None = None,
                           owner: str | None = None) -> list[Conversation]:
        """owner 给了就只列这个人的会话（认证开着、来的是访客时）；
        不给 = 全列，那是本机主人本人和没开认证时的行为。"""
        sql = ("SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS n,"
               " (SELECT content FROM messages m WHERE m.conversation_id=c.id ORDER BY id DESC LIMIT 1) AS last,"
               + _UNREAD_SQL +
               " FROM conversations c ")
        where: list[str] = []
        args: list[object] = []
        if character_id:
            where.append("c.character_id=?")
            args.append(character_id)
        if owner is not None:
            where.append("c.owner=?")
            args.append(owner)
        if where:
            sql += "WHERE " + " AND ".join(where)
        sql += " ORDER BY c.pinned DESC, c.updated_at DESC LIMIT 300"
        with self._raw() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_conv(r) for r in rows]

    def rename(self, cid: int, title: str) -> None:
        with self._raw() as conn:
            conn.execute("UPDATE conversations SET title=?, updated_at=updated_at WHERE id=?", (title[:60], cid))

    def set_pinned(self, cid: int, pinned: bool) -> None:
        with self._raw() as conn:
            conn.execute("UPDATE conversations SET pinned=? WHERE id=?", (1 if pinned else 0, cid))

    def touch(self, cid: int) -> None:
        with self._raw() as conn:
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), cid))

    def mark_read(self, cid: int) -> int:
        """把「读到哪」的水位线推到本会话最后一条消息，返回这次清掉的未读条数。

        只前进不后退：「删到这儿」会把最大的消息 id 删没，直接写 MAX(id) 就可能把水位线
        往回拽、让已读的消息重新亮起红点。不动 updated_at，否则读一下消息就把会话顶到
        列表最上面，排序会乱跳。
        """
        with self._raw() as conn:
            row = conn.execute("SELECT last_read_id FROM conversations WHERE id=?", (cid,)).fetchone()
            if row is None:
                return 0
            top = conn.execute(
                "SELECT COALESCE(MAX(id), 0) m FROM messages WHERE conversation_id=?", (cid,)).fetchone()["m"]
            unread = conn.execute(
                "SELECT COUNT(*) n FROM messages WHERE conversation_id=? AND role='assistant' AND id>?",
                (cid, row["last_read_id"])).fetchone()["n"]
            conn.execute("UPDATE conversations SET last_read_id=? WHERE id=?",
                         (max(int(top), int(row["last_read_id"])), cid))
            return int(unread)

    def delete_conversation(self, cid: int) -> bool:
        with self._raw() as conn:
            cur = conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
            return cur.rowcount > 0

    def set_summary(self, cid: int, summary: str, upto: int) -> None:
        with self._raw() as conn:
            conn.execute("UPDATE conversations SET summary=?, summary_upto=? WHERE id=?",
                         (summary.strip()[:2000], upto, cid))

    # ------------------------------------------------------------ 访客（认证开着才用）
    def add_visitor(self, name: str, code_hash: str, bucket: str, admin: bool = False) -> str:
        vid = "v" + uuid.uuid4().hex[:8]
        with self._raw() as conn:
            conn.execute("INSERT INTO visitors(id,name,bucket,code_hash,admin,created_at,last_seen)"
                         " VALUES(?,?,?,?,?,?,0)",
                         (vid, (name or "").strip()[:24], bucket, code_hash, 1 if admin else 0, time.time()))
        return vid

    def visitor_candidates(self, bucket: str) -> list[dict]:
        with self._raw() as conn:
            rows = conn.execute("SELECT id,name,code_hash,admin FROM visitors WHERE bucket=?",
                                (bucket,)).fetchall()
        return [dict(r) for r in rows]

    def get_visitor(self, vid: str) -> dict | None:
        with self._raw() as conn:
            row = conn.execute("SELECT id,name,admin,last_seen FROM visitors WHERE id=?", (vid,)).fetchone()
        return dict(row) if row else None

    def list_visitors(self) -> list[dict]:
        with self._raw() as conn:
            rows = conn.execute("SELECT id,name,admin,created_at,last_seen FROM visitors"
                                " ORDER BY admin DESC, created_at ASC").fetchall()
        return [dict(r) for r in rows]

    def count_visitors(self) -> int:
        with self._raw() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM visitors").fetchone()[0])

    def touch_visitor(self, vid: str) -> None:
        with self._raw() as conn:
            conn.execute("UPDATE visitors SET last_seen=? WHERE id=?", (time.time(), vid))

    def revoke_visitor(self, vid: str) -> bool:
        """删行即可 —— cookie 里只有访客 id，行没了签名再对也换不到任何权限。"""
        with self._raw() as conn:
            cur = conn.execute("DELETE FROM visitors WHERE id=?", (vid,))
            conn.execute("UPDATE conversations SET owner='' WHERE owner=?", (vid,))
            return cur.rowcount > 0

    def set_visitor_code(self, vid: str, code_hash: str, bucket: str) -> bool:
        """换口令，不动别的：访客 id 不变，所以**已经登录的设备不会被踢下线**
        （cookie 认的是签名里的 id，跟口令无关）。bucket 也要一起换，
        否则新口令查不到候选行，登录会一直报「口令不对」。"""
        with self._raw() as conn:
            cur = conn.execute("UPDATE visitors SET code_hash=?, bucket=? WHERE id=?",
                               (code_hash, bucket, vid))
            return cur.rowcount > 0

    # ------------------------------------------------------------ 消息
    def add_message(self, cid: int, role: str, content: str, stickers: list[str] | None = None,
                    emotion: str | None = None, meta: dict | None = None,
                    speaker: str = "") -> Message:
        now = time.time()
        with self._raw() as conn:
            cur = conn.execute(
                "INSERT INTO messages(conversation_id,role,content,stickers,emotion,meta,speaker,created_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (cid, role, content, json.dumps(stickers or [], ensure_ascii=False), emotion,
                 json.dumps(meta or {}, ensure_ascii=False), speaker, now),
            )
            mid = int(cur.lastrowid)
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        return Message(id=mid, conversation_id=cid, role=role, content=content,
                       stickers=stickers or [], emotion=emotion, meta=meta or {},
                       speaker=speaker, created_at=now)

    def messages(self, cid: int, limit: int | None = None, after_id: int = 0) -> list[Message]:
        sql = "SELECT * FROM messages WHERE conversation_id=? AND id>?"
        args: list[object] = [cid, after_id]
        if limit:
            sql += " ORDER BY id DESC LIMIT ?"
            args.append(limit)
            sql_wrap = True
        else:
            sql_wrap = False
        if sql_wrap:
            sql = "SELECT * FROM (" + sql + ") ORDER BY id ASC"
        else:
            sql += " ORDER BY id ASC"
        with self._raw() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [_msg(r) for r in rows]

    def history_for_prompt(self, cid: int, upto_id: int | None = None, label_of=None) -> list[dict]:
        """给模型用的 role/content 列表。表情用协议原形 [sticker:标签] 占位——模型会模仿历史里
        见到的写法；以前这里写中文叙述 [刚刚给对方发了表情包：id]，弱模型照着学，结果解析不到、
        整串以文字形式漏进气泡（用户报的 bug）。label_of 把 id 翻成更自然的标签。"""
        with self._raw() as conn:
            if upto_id:
                rows = conn.execute("SELECT * FROM messages WHERE conversation_id=? AND id<? ORDER BY id ASC",
                                    (cid, upto_id)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id ASC", (cid,)).fetchall()
        out: list[dict] = []
        for r in rows:
            if r["role"] == "system":
                continue
            body = strip_markers(r["content"])
            tags = json.loads(r["stickers"] or "[]")
            if tags:
                marks = []
                for t in tags:
                    lbl = label_of(t) if label_of else t   # 翻不出标签＝表情已删，跳过，别喂裸 id
                    if lbl:
                        marks.append("[sticker:" + lbl + "]")
                body = (body + "\n" if body else "") + "\n".join(marks)
            # created_at 理论上非空；但历史脏数据可能写成空串/非数字。这类时间戳
            # 没有可信意义，绝不能转成 0.0 —— 那会让沉默情绪把这条当成「很久没回」
            # 直接顶到最高档。解析不了就置 None，让沉默计算跳过这条消息。
            try:
                created = float(r["created_at"])
            except (TypeError, ValueError, OverflowError):
                created = None
            if created is not None and not math.isfinite(created):
                created = None
            out.append({"role": r["role"], "content": body,
                            "speaker": (r["speaker"] if "speaker" in r.keys() else "") or "",
                            "created_at": created})
        return out

    def update_message(self, mid: int, content: str) -> bool:
        with self._raw() as conn:
            cur = conn.execute("UPDATE messages SET content=? WHERE id=?", (content, mid))
            return cur.rowcount > 0

    def update_stickers(self, mid: int, stickers: list[str]) -> None:
        with self._raw() as conn:
            conn.execute("UPDATE messages SET stickers=? WHERE id=?",
                         (json.dumps(stickers, ensure_ascii=False), mid))

    def set_message_emotion(self, mid: int, emotion: str | None) -> None:
        with self._raw() as conn:
            conn.execute("UPDATE messages SET emotion=? WHERE id=?", (emotion, mid))

    def patch_message_meta(self, mid: int, patch: dict) -> None:
        with self._raw() as conn:
            row = conn.execute("SELECT meta FROM messages WHERE id=?", (mid,)).fetchone()
            if row is None:
                return
            meta = json.loads(row["meta"] or "{}")
            meta.update(patch)
            conn.execute("UPDATE messages SET meta=? WHERE id=?",
                         (json.dumps(meta, ensure_ascii=False), mid))

    def get_message(self, mid: int) -> Message | None:
        with self._raw() as conn:
            row = conn.execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()
        return _msg(row) if row else None

    def delete_from(self, cid: int, from_id: int) -> int:
        """截断：删掉该会话里 id >= from_id 的所有消息（重新生成用）。"""
        with self._raw() as conn:
            cur = conn.execute("DELETE FROM messages WHERE conversation_id=? AND id>=?", (cid, from_id))
            return cur.rowcount

    def tail_transcript(self, cid: int, limit: int = 40) -> list[Message]:
        return self.messages(cid, limit=limit)

    # ------------------------------------------------------------ 偏好
    def set_pref(self, key: str, value: str) -> None:
        with self._raw() as conn:
            conn.execute("INSERT INTO prefs(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (key, value))

    @staticmethod
    def _pref_int(row, default: int = 0) -> int:
        if not row:
            return default
        try:
            value = float(row["value"])
            if not math.isfinite(value):
                return default
            return int(value)
        except (TypeError, ValueError, OverflowError):
            return default

    @staticmethod
    def _parse_proactive_claim(value: object) -> tuple[dict | None, float, str, str]:
        """读取 lease；缺字段、非对象或非有限时间都视为不可证明。"""
        try:
            data = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None, 0.0, "reserved", ""
        if not isinstance(data, dict) or "expires" not in data:
            return None, 0.0, "reserved", ""
        try:
            expires = float(data.get("expires"))
        except (TypeError, ValueError, OverflowError):
            return None, 0.0, "reserved", ""
        if not math.isfinite(expires):
            return None, 0.0, "reserved", ""
        state = data.get("state") or "reserved"
        if state not in ("reserved", "committing"):
            return None, 0.0, "reserved", ""
        quota_key = data.get("quota_key")
        token = data.get("token")
        if not isinstance(quota_key, str) or not quota_key:
            return None, 0.0, "reserved", ""
        if not isinstance(token, str) or not token:
            return None, 0.0, "reserved", ""
        return data, expires, str(state), quota_key

    @staticmethod
    def _proactive_ttl(ttl: float) -> float:
        try:
            value = float(ttl)
        except (TypeError, ValueError, OverflowError):
            return 1.0
        return value if math.isfinite(value) and value > 0 else 1.0

    def claim_proactive(self, chat_id: str, quota_key: str, daily_max: int,
                        ttl: float = 900.0) -> str | None:
        """在同一事务里预留当日配额并取得会话 lease。

        返回 ``None`` 表示目标日已满，或另一个桥接进程仍持有有效 lease。
        lease 过期接管时，只撤销 ``reserved`` 状态下的旧预留；``committing``
        表示消息已经发出，不能再把这次成功投递扣回，避免崩溃恢复时重复发言。
        """
        if daily_max <= 0:
            return None
        ttl = self._proactive_ttl(ttl)
        claim_key = "feishu.proactive.claim." + str(chat_id or "")
        now = time.time()
        with self._raw() as conn:
            conn.execute("BEGIN IMMEDIATE")
            claim = conn.execute("SELECT value FROM prefs WHERE key=?", (claim_key,)).fetchone()
            if claim:
                data, expires, state, old_quota_key = self._parse_proactive_claim(claim["value"])
                if data is not None and expires > now:
                    return None
                # 只有可证明属于旧 reserved 预留的配额才能回收。非对象、缺字段、
                # 非有限时间等损坏记录无法证明归属，不能拿当前 quota 做回退。
                if data is not None and state == "reserved" and old_quota_key:
                    current = self._pref_int(
                        conn.execute("SELECT value FROM prefs WHERE key=?", (old_quota_key,)).fetchone())
                    if current > 0:
                        conn.execute("UPDATE prefs SET value=? WHERE key=?",
                                     (str(current - 1), old_quota_key))
                conn.execute("DELETE FROM prefs WHERE key=?", (claim_key,))

            current = self._pref_int(
                conn.execute("SELECT value FROM prefs WHERE key=?", (quota_key,)).fetchone())
            if current >= daily_max:
                return None
            conn.execute(
                "INSERT INTO prefs(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (quota_key, str(current + 1)))
            token = uuid.uuid4().hex
            payload = json.dumps({
                "token": token,
                "expires": now + ttl,
                "quota_key": quota_key,
                "state": "reserved",
            }, ensure_ascii=False)
            conn.execute(
                "INSERT INTO prefs(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (claim_key, payload))
            return token

    def commit_proactive(self, chat_id: str, token: str) -> bool:
        """成功发送后提交 lease；配额保留，lease 进入 committing 状态。

        不立即删除 committing 记录：进程若在发送完成和提交之间崩溃，其他进程
        也不能回收这次配额或再次发言。记录会在 lease 过期后被安全清理。
        """
        claim_key = "feishu.proactive.claim." + str(chat_id or "")
        with self._raw() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM prefs WHERE key=?", (claim_key,)).fetchone()
            if not row:
                return False
            data, _expires, state, _old_quota_key = self._parse_proactive_claim(row["value"])
            if data is None or state != "reserved" or data.get("token") != token:
                return False
            data["state"] = "committing"
            data["committed_at"] = time.time()
            conn.execute("UPDATE prefs SET value=? WHERE key=?",
                         (json.dumps(data, ensure_ascii=False), claim_key))
            return True

    def release_proactive(self, chat_id: str, token: str) -> bool:
        """发送前失败或取消时，仅凭 token 幂等释放 quota + lease。"""
        claim_key = "feishu.proactive.claim." + str(chat_id or "")
        with self._raw() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM prefs WHERE key=?", (claim_key,)).fetchone()
            if not row:
                return False
            data, _expires, state, quota_key = self._parse_proactive_claim(row["value"])
            if data is None or state != "reserved" or data.get("token") != token:
                return False
            current = self._pref_int(
                conn.execute("SELECT value FROM prefs WHERE key=?", (quota_key,)).fetchone())
            if current > 0:
                conn.execute("UPDATE prefs SET value=? WHERE key=?",
                             (str(current - 1), quota_key))
            conn.execute("DELETE FROM prefs WHERE key=?", (claim_key,))
            return True

    def get_pref(self, key: str, default: str = "") -> str:
        with self._raw() as conn:
            row = conn.execute("SELECT value FROM prefs WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def list_prefs(self, prefix: str) -> dict[str, str]:
        """按前缀批量取偏好。飞书用它列出「哪个聊天对应哪个角色」，
        不用为一个小映射另建表。"""
        with self._raw() as conn:
            rows = conn.execute("SELECT key, value FROM prefs WHERE key LIKE ?",
                                (prefix + "%",)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def stats(self, owner: str | None = None) -> dict:
        """不带 owner = 全局统计（主人本机、以及认证没开时的老行为）。
        带 owner 就只数这一个人自己的，并且**不再交出数据库路径** —— 那对访客没有意义，
        却把这台机器的目录结构说出去了。"""
        with self._raw() as conn:
            if owner is None:
                conv = conn.execute("SELECT COUNT(*) c FROM conversations").fetchone()["c"]
                msg = conn.execute("SELECT COUNT(*) c FROM messages").fetchone()["c"]
                sticker = conn.execute(
                    "SELECT COUNT(*) c FROM messages WHERE stickers IS NOT NULL AND stickers != '[]'").fetchone()["c"]
            else:
                mine = "JOIN conversations c ON c.id = m.conversation_id WHERE c.owner = ?"
                conv = conn.execute("SELECT COUNT(*) c FROM conversations WHERE owner=?",
                                    (owner,)).fetchone()["c"]
                msg = conn.execute("SELECT COUNT(*) c FROM messages m " + mine, (owner,)).fetchone()["c"]
                sticker = conn.execute("SELECT COUNT(*) c FROM messages m " + mine +
                                       " AND m.stickers IS NOT NULL AND m.stickers != '[]'",
                                       (owner,)).fetchone()["c"]
        out = {"conversations": conv, "messages": msg, "messages_with_sticker": sticker}
        if owner is None:
            out["db"] = str(self.path)
        return out


def _finite_timestamp(value: object, default: float | None) -> float | None:
    """读取历史时间戳；空值、NaN 和无穷值都按默认值处理。"""
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _conv(row: sqlite3.Row) -> Conversation:
    try:
        parts = json.loads(row["participants"] or "[]") if "participants" in row.keys() else []
    except (ValueError, TypeError):
        parts = []
    parts = [str(p) for p in parts if p] or [row['character_id']]
    return Conversation(
        id=int(row["id"]), character_id=row["character_id"], title=row["title"] or "",
        pinned=bool(row["pinned"]), participants=parts,
        created_at=_finite_timestamp(row["created_at"], 0.0) or 0.0,
        updated_at=_finite_timestamp(row["updated_at"], 0.0) or 0.0,
        message_count=int(row["n"] or 0), preview=(row["last"] or "")[:60], summary=row["summary"] or "",
        last_read_id=int(row["last_read_id"] or 0) if "last_read_id" in row.keys() else 0,
        # 没带 unread 子查询的调用方（极少）当作 0，别让 KeyError 冒出来
        unread_count=int(row["unread"] or 0) if "unread" in row.keys() else 0,
        owner=(row["owner"] or "") if "owner" in row.keys() else "",
    )


def _msg(row: sqlite3.Row) -> Message:
    # 老库 / 异常数据可能把 created_at 写成空串、NaN 或无穷值；展示层保留 None，
    # 不把不可信时间戳伪装成 1970 年，也不让它影响沉默情绪计算。
    created = _finite_timestamp(row["created_at"], None)
    return Message(
        id=int(row["id"]), conversation_id=int(row["conversation_id"]), role=row["role"],
        content=row["content"] or "", stickers=json.loads(row["stickers"] or "[]"),
        emotion=row["emotion"], meta=json.loads(row["meta"] or "{}"),
        speaker=(row["speaker"] if "speaker" in row.keys() else "") or "",
        created_at=created,
    )


_store: Store | None = None
_store_lock = threading.Lock()


def store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
        return _store
