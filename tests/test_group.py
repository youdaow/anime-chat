"""群聊：多个 AI 互相对话。重点盯住两件事——话是谁说的、别替别人发言。"""
import json, re
import pytest
from fastapi.testclient import TestClient
from animechat.prompt import history_messages, sticker_vocab, system_prompt
from animechat.server import create_app, next_speaker, trim_other_voices
from animechat.stickers import library
from animechat.config import Settings, save_settings
from animechat.models import Character
from animechat.store import Store

@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c

def sse_events(text):
    out = []
    for block in text.split("\n\n"):
        name, data = "message", ""
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            out.append((name, json.loads(data)))
    return out

def make_char(client, name):
    return client.post("/api/characters", json={
        "name": name, "personality": "测试角色", "greeting": name + "的招呼",
    }).json()["character"]["id"]

# ---------------------------------------------------------------- 数据层

def test_participants_roundtrip_and_group_flag(tmp_path):
    db = Store(tmp_path / "g.db")
    conv = db.create_conversation("a", "三人群", participants=["a", "b", "c"])
    assert conv.participants == ["a", "b", "c"]
    assert conv.model_dump()["group"] is True
    again = db.get_conversation(conv.id)
    assert again.participants == ["a", "b", "c"]
    solo = db.create_conversation("z", "单聊")
    assert solo.participants == ["z"]
    assert solo.model_dump()["group"] is False

def test_conversation_created_earlier_still_loads(tmp_path):
    """老库没有 participants / speaker 两列：迁移必须补上，不能一查就崩。"""
    import sqlite3
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " character_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',"
        " pinned INTEGER NOT NULL DEFAULT 0, summary TEXT NOT NULL DEFAULT '',"
        " summary_upto INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " conversation_id INTEGER NOT NULL, role TEXT NOT NULL,"
        " content TEXT NOT NULL DEFAULT '', stickers TEXT NOT NULL DEFAULT '[]', emotion TEXT,"
        " meta TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL);")
    conn.execute("INSERT INTO conversations(character_id,title,created_at,updated_at) VALUES('a','旧会话',1.0,1.0)")
    conn.execute("INSERT INTO messages(conversation_id,role,content,created_at) VALUES(1,'assistant','老消息',1.0)")
    conn.commit(); conn.close()
    db = Store(path)
    conv = db.get_conversation(1)
    assert conv.participants == ["a"]
    assert conv.model_dump()["group"] is False
    assert db.messages(1)[0].speaker == ""

# ---------------------------------------------------------------- 提示词

def test_group_prompt_names_the_others_and_forbids_script_format(client):
    cid = make_char(client, "小绿")
    other = client.get("/api/characters").json()["characters"][0]["id"]
    char = Character(**client.get("/api/characters/" + other).json()["character"])
    text = system_prompt(char, Settings(), library(),
                         roster=[{"id": cid, "name": "小绿", "title": ""}])
    assert "群聊" in text and "小绿" in text
    assert "不要替" in text
    assert "小绿：" not in text

def test_other_speakers_become_user_lines_for_the_model(client):
    roster = [{"id": "a", "name": "阿一", "title": ""}, {"id": "b", "name": "阿二", "title": ""}]
    history = [
        {"role": "assistant", "content": "我先说话", "speaker": "a"},
        {"role": "assistant", "content": "轮到我了", "speaker": "b"},
        {"role": "user", "content": "用户插一句", "speaker": ""},
    ]
    settings = Settings()
    char = Character(id="a", name="阿一")
    out = history_messages(char, history, settings, speaker="a", roster=roster)
    body = [(m["role"], m["content"]) for m in out if m["role"] != "system"]
    assert body[0] == ("assistant", "我先说话")
    assert body[1] == ("user", "阿二：轮到我了")
    assert body[2] == ("user", "用户插一句")

# ---------------------------------------------------------------- 端点

def test_group_conversation_greets_from_every_member(client):
    ids = [make_char(client, n) for n in ("甲", "乙", "丙")]
    conv = client.post("/api/conversations",
                       json={"character_id": ids[0], "participants": ids, "title": "群"}
                       ).json()["conversation"]
    assert conv["group"] is True
    msgs = client.get("/api/conversations/" + str(conv["id"])).json()["messages"]
    assert [m["speaker"] for m in msgs] == ids

def test_group_rejects_unknown_member(client):
    a = make_char(client, "甲")
    r = client.post("/api/conversations", json={"character_id": a, "participants": [a, "ghost"]})
    assert r.status_code == 404

def test_speaker_rotates_and_auto_turns_need_no_user(client):
    ids = [make_char(client, n) for n in ("甲", "乙")]
    conv = client.post("/api/conversations",
                       json={"character_id": ids[0], "participants": ids}).json()["conversation"]["id"]
    resp = client.post("/api/chat", json={"conversation_id": conv, "content": "再问一次"})
    done = [d for n, d in sse_events(resp.text) if n == "done"][-1]
    assert done["speaker"] == ids[0]
    resp2 = client.post("/api/chat", json={"conversation_id": conv, "auto": True})
    done2 = [d for n, d in sse_events(resp2.text) if n == "done"][-1]
    assert done2["speaker"] == ids[1]
    stored = client.get("/api/conversations/" + str(conv)).json()["messages"]
    assert [m["speaker"] for m in stored if m["role"] == "assistant"][-1] == ids[1]

def test_can_point_at_a_speaker(client):
    ids = [make_char(client, n) for n in ("甲", "乙")]
    conv = client.post("/api/conversations",
                       json={"character_id": ids[0], "participants": ids}).json()["conversation"]["id"]
    resp = client.post("/api/chat",
                       json={"conversation_id": conv, "content": "乙你先说", "speaker": ids[1]})
    done = [d for n, d in sse_events(resp.text) if n == "done"][-1]
    assert done["speaker"] == ids[1]

def test_pointing_outsider_is_rejected(client):
    a, b = make_char(client, "甲"), make_char(client, "乙")
    conv = client.post("/api/conversations",
                       json={"character_id": a, "participants": [a, b]}).json()["conversation"]["id"]
    assert client.post("/api/chat", json={"conversation_id": conv, "content": "x",
                                          "speaker": "ghost"}).status_code == 400

def test_auto_needs_a_group(client):
    cid = make_char(client, "甲")
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    assert client.post("/api/chat", json={"conversation_id": conv, "auto": True}).status_code == 400

def test_regenerate_keeps_the_same_speaker(client):
    ids = [make_char(client, n) for n in ("甲", "乙")]
    conv = client.post("/api/conversations",
                       json={"character_id": ids[0], "participants": ids}).json()["conversation"]["id"]
    client.post("/api/chat", json={"conversation_id": conv, "content": "第一句", "speaker": ids[1]})
    resp = client.post("/api/chat", json={"conversation_id": conv, "regenerate": True})
    done = [d for n, d in sse_events(resp.text) if n == "done"][-1]
    assert done["speaker"] == ids[1]

# ---------------------------------------------------------------- 越界兜底

def test_next_speaker_rotation():
    class M:
        def __init__(self, role, speaker=""):
            self.role, self.speaker = role, speaker
    assert next_speaker(["a", "b", "c"], []) == "a"
    assert next_speaker(["a", "b", "c"], [M("assistant", "b")]) == "c"
    assert next_speaker(["a", "b", "c"], [M("assistant", "c")]) == "a"
    assert next_speaker(["a", "b"], [M("assistant", "ghost")]) == "a"

def test_trim_other_voices_cuts_script_but_keeps_normal_speech():
    others = ["若叶睦", "丛雨"]
    assert trim_other_voices("安静些。 若叶睦：（低头）嗯。", "丰川祥子", others) == "安静些。"
    assert trim_other_voices("丛雨：你怎么才来。", "丰川祥子", others) == "丛雨：你怎么才来。"
    assert trim_other_voices("睦，把黄瓜放下。", "丰川祥子", others) == "睦，把黄瓜放下。"
    assert trim_other_voices("丰川祥子：失礼了。", "丰川祥子", others) == "失礼了。"
    assert trim_other_voices("丛雨：全是别人", "丰川祥子", others) == "丛雨：全是别人"


def test_scenario_note_reaches_the_client(client):
    """表情库卡片要显示「什么情况下发」，前提是 note 能一路传到前端。
       早期联网搜来的那批只有来源记录，就是缺了这个。"""
    from PIL import Image
    from animechat.config import user_sticker_dir
    from animechat.stickers import library
    d = user_sticker_dir()
    d.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (24, 24), (200, 60, 60, 255)).save(d / "傲娇+嘴硬.png")
    meta = d.parent / "stickers_meta.json"
    meta.write_text(json.dumps({"傲娇+嘴硬.png": {
        "label": "才不稀罕", "emotion": "tsundere", "tags": ["傲娇", "嘴硬"],
        "note": "明明很想要，却偏说才不稀罕"}}, ensure_ascii=False), encoding="utf-8")
    library().refresh()
    got = [s for s in client.get("/api/stickers?limit=300").json()["stickers"]
           if s["label"] == "才不稀罕"]
    assert got, "表情没进列表"
    assert got[0]["note"] == "明明很想要，却偏说才不稀罕", "note 没传到前端"


def test_aborted_stream_leaves_no_empty_bubble(client, monkeypatch):
    """客户端中途断开（关页面 / 切会话 / 点停止）时，还没说出一个字的那条占位消息
       必须整条撤掉，否则会留一个永远空白的气泡，看起来就像「发不出去、没法聊天」。"""
    import asyncio
    import animechat.llm as llm_mod
    # TestClient 不会把"客户端断开"真的传播成服务端生成器的取消，所以这里直接
    # 抛 CancelledError：走的是和断连完全相同的那条收尾分支（真实 socket 关闭
    # 已在手工验证里确认过：断开后只剩用户那句，没有空气泡）。
    async def aborted(_settings, _messages, **_kw):
        raise asyncio.CancelledError()
        yield  # pragma: no cover - 让它成为异步生成器
    monkeypatch.setattr(llm_mod, "stream_chat", aborted)
    # mock_mode 是计算属性（没有 api_key 就是 mock），会直接走 mock_mod.reply，
    # 碰不到 llm.stream_chat。给个假 key 逼它进真实流式分支，monkeypatch 才生效。
    save_settings({"llm_api_key": "sk-test-fake", "llm_model": "test-model"})
    cid = make_char(client, "甲")
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]
    cid_id = conv["id"]
    before = len(client.get("/api/conversations/" + str(cid_id)).json()["messages"])
    with client.stream("POST", "/api/chat", json={"conversation_id": cid_id, "content": "写长一点"}) as resp:
        for _ in resp.iter_bytes(64):   # 只读一个块就断开，模拟关页面
            break
    msgs = client.get("/api/conversations/" + str(cid_id)).json()["messages"]
    msgs = client.get("/api/conversations/" + str(cid_id)).json()["messages"]
    empty = [m for m in msgs if m["role"] == "assistant" and not (m["content"] or "").strip()]
    assert not empty, "留了空气泡"
    # before 只有招呼语；断连后应留下招呼语 + 用户那句，不该多出 assistant 占位
    assert len(msgs) == before + 1, "断连应撤掉 assistant 占位、只留用户那句；现在 %d 条" % len(msgs)
    assert msgs[-1]["role"] == "user", "最后应是用户那句，不该有半截回复"


def test_prompt_allows_longer_replies_instead_of_four_lines():
    """用户要 AI 多说点，所以「一次别超过 4 行」的限制得去掉，换成「回得充实些」。"""
    char = Character(id="a", name="测试")
    text = system_prompt(char, Settings(), library())
    assert "三到五句" in text, "应该鼓励写充实一点"
    assert "别超过 4 行" not in text, "旧的长度上限还在压制字数"


def test_sticker_vocab_lists_all_when_library_is_large():
    """库里已经上百张，默认 120 的截断会让后面的表情从提示词里消失。
       改到 260 以后，上百张应该完整出现在词表里。"""
    from animechat.stickers import StickerLibrary
    from animechat.models import Sticker
    lib = StickerLibrary()
    items = {}
    for i in range(130):
        items["s%03d" % i] = Sticker(id="s%03d" % i, label="表情%d" % i, tags=["x%d" % i],
                                  emotion="happy", url="u", origin="upload")
    lib.items = items
    vocab = sticker_vocab(lib, limit=260)
    listed = [w for w in vocab.split("、") if w]
    assert len(listed) == 130, "130 张应该全进词表，现在是 %d 张" % len(listed)
    assert "表情0" in vocab and "表情129" in vocab
