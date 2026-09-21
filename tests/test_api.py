"""端到端：Mock 模式下，界面要拿到的东西都得有。"""

import io
import json
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from animechat import media
from animechat.config import user_sticker_dir
from animechat.server import create_app, merge_character
from animechat.stickers import library
from animechat.models import Character


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def sse_events(text: str) -> list[tuple[str, dict]]:
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


def test_bootstrap_shape(client):
    data = client.get("/api/bootstrap").json()
    assert data["version"]
    assert len(data["characters"]) >= 5
    assert data["settings"]["mock_mode"] is True
    assert data["emotions"]


def test_conversation_starts_with_greeting(client):
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    created = client.post("/api/conversations", json={"character_id": cid}).json()
    detail = client.get("/api/conversations/" + str(created["conversation"]["id"])).json()
    assert detail["messages"][0]["role"] == "assistant"
    assert detail["messages"][0]["content"]
    assert "[sticker" not in detail["messages"][0]["content"]  # 开场白里的标记要变成真表情
    assert created["conversation"]["character_id"] == cid


def test_chat_streams_text_and_finishes(client):
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    resp = client.post("/api/chat", json={"conversation_id": conv, "content": "你好呀"})
    assert resp.status_code == 200
    events = sse_events(resp.text)
    names = [n for n, _ in events]
    assert names[0] == "start" and names[-1] == "done"
    text = "".join(d["t"] for n, d in events if n == "text")
    assert text.strip()
    assert "[" not in text and "sticker:" not in text
    done = events[-1][1]
    assert done["message_id"]
    assert re.search(r"\d+", str(done["elapsed_ms"]))
    stored = client.get("/api/conversations/" + str(conv)).json()["messages"]
    # 落库内容是收完的正文去掉首尾空白；done 事件与库里必须一致（前端靠它对账）
    assert stored[-1]["content"] == text.strip()
    assert done["content"] == stored[-1]["content"]


def test_user_sticker_and_model_marker_both_reach_client(client):
    sticker_dir = user_sticker_dir()
    sticker_dir.mkdir(parents=True, exist_ok=True)
    path = sticker_dir / "加油+冲+打气.png"
    Image.new("RGBA", (32, 32), (255, 180, 60, 255)).save(path)
    library().refresh()
    st = library().resolve("加油")
    assert st is not None

    created = client.post("/api/characters", json={
        "name": "测试元气", "sticker_style": "rich", "greeting": "冲！",
        "sticker_prefs": ["加油"], "description": "爱发图",
    }).json()["character"]
    conv = client.post("/api/conversations", json={"character_id": created["id"]}).json()["conversation"]["id"]
    resp = client.post("/api/chat", json={"conversation_id": conv, "content": "我要考试了，给我打打气",
                                          "stickers": [st.id]})
    events = sse_events(resp.text)
    stickers = [d for n, d in events if n == "sticker"]
    assert stickers, "角色一句话都没甩表情，说明标记/兜底链路断了"
    detail = client.get("/api/conversations/" + str(conv)).json()["messages"]
    user_msg = [m for m in detail if m["role"] == "user"][0]
    assert user_msg["stickers"] == [st.id]


def test_regenerate_replaces_last_reply(client):
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    client.post("/api/chat", json={"conversation_id": conv, "content": "讲句话"})
    before = client.get("/api/conversations/" + str(conv)).json()["messages"]
    resp = client.post("/api/chat", json={"conversation_id": conv, "regenerate": True})
    assert resp.status_code == 200
    after = client.get("/api/conversations/" + str(conv)).json()["messages"]
    assert len(after) == len(before)
    assert after[-1]["id"] != before[-1]["id"]


def test_character_context_can_be_reset_and_validated():
    base = Character(id="c", name="测试", context_chars=12000)
    assert merge_character(base, {"context_chars": None}).context_chars is None
    with pytest.raises(ValueError):
        Character(id="bad", name="测试", context_chars=599)


def test_character_crud_and_card_import(client):
    made = client.post("/api/characters", json={"name": "卡皮巴拉", "personality": "淡定"}).json()["character"]
    assert made["builtin"] is False
    updated = client.put("/api/characters/" + made["id"], json={"personality": "更淡定", "nothing": 1}).json()["character"]
    assert updated["personality"] == "更淡定"
    card = client.get("/api/characters/" + made["id"] + "/export.json").json()
    assert card["spec"] == "chara_card_v2" and card["data"]["name"] == "卡皮巴拉"
    assert client.post("/api/characters", json={"name": "  "}).status_code == 400

    png = client.get("/api/characters/" + made["id"] + "/export.png")
    if png.status_code == 200:  # 需要 Pillow + chibi 生成底图
        assert png.content[:8] == b"\x89PNG\r\n\x1a\n"

    imported = client.post("/api/characters/import",
                           files={"file": ("card.json", json.dumps(card), "application/json")})
    assert imported.status_code == 200
    assert imported.json()["character"]["name"] == "卡皮巴拉"
    assert imported.json()["character"]["personality"] == "更淡定"

    assert client.delete("/api/characters/" + made["id"]).json()["how"] == "deleted"
    assert client.get("/api/characters/" + made["id"]).status_code == 404


def test_deleting_character_takes_its_avatar_with_it(client):
    """导入的角色卡把头像存成 card_<id>.png；以前 delete 只清 gen_，那文件会永远
       留在盘上。内置头像走 /media/builtin/，前缀不同，不会被顺手删掉。"""
    from animechat.media import resolve

    # resolve() 对不存在的文件返回 None，所以先把头像真的落盘
    p = media.avatar_path("card_leak-test.png")
    p.write_bytes(b"\x89PNG\r\n\x1a\n fake")
    made = client.post("/api/characters", json={
        "name": "测试头像残留", "avatar": media.AVATAR_PREFIX + p.name})
    cid = made.json()["character"]["id"]
    assert resolve(made.json()["character"]["avatar"]) is not None, "前置条件：头像文件得真的存在"

    assert client.delete("/api/characters/" + cid).status_code == 200
    assert not p.is_file(), "删了角色却留下头像文件，是磁盘泄漏"

    builtins = [c for c in client.get("/api/characters").json()["characters"] if c["builtin"]]
    assert builtins, "没有内置角色，测不到误删"
    for c in builtins:
        assert resolve(c["avatar"]) is not None, c["name"] + " 的内置头像被波及了"

def test_builtin_character_is_hidden_not_deleted(client):
    cid = "xingye-liuli"
    out = client.delete("/api/characters/" + cid).json()
    assert out["how"] == "hidden"
    assert cid not in [c["id"] for c in client.get("/api/characters").json()["characters"]]
    assert cid in [c["id"] for c in client.get("/api/characters?include_hidden=true").json()["characters"]]
    client.post("/api/characters/" + cid + "/restore")
    assert cid in [c["id"] for c in client.get("/api/characters").json()["characters"]]


def test_hidden_character_can_join_group_and_speak(client):
    hidden = "xingye-liuli"
    visible = "baihe-qianxue"
    assert client.delete("/api/characters/" + hidden).json()["how"] == "hidden"

    created = client.post("/api/conversations", json={
        "character_id": visible,
        "participants": [hidden],
        "title": "隐藏角色群聊",
    })
    assert created.status_code == 200, created.text
    conv_id = created.json()["conversation"]["id"]
    assert set(created.json()["conversation"]["participants"]) == {visible, hidden}

    resp = client.post("/api/chat", json={
        "conversation_id": conv_id,
        "content": "你们都在吗？",
        "speaker": hidden,
    })
    assert resp.status_code == 200, resp.text
    assert "done" in [name for name, _ in sse_events(resp.text)]


def test_conversation_detail_carries_hidden_member_cards(client):
    """会话详情要把成员的角色卡一起带回来：隐藏掉的角色不在公开角色列表里，
       前端没有这份快照就画不出它的气泡名字和头像。
       这里锁接口这一端，前端接线由 test_web_assets.py 的源码断言把住。"""
    from animechat.store import store

    hidden, visible = "xingye-liuli", "baihe-qianxue"
    assert client.delete("/api/characters/" + hidden).json()["how"] == "hidden"
    assert hidden not in [c["id"] for c in client.get("/api/characters").json()["characters"]]

    conv_id = client.post("/api/conversations", json={
        "character_id": visible, "participants": [hidden],
    }).json()["conversation"]["id"]
    detail = client.get("/api/conversations/" + str(conv_id)).json()

    got = {c["id"]: c for c in detail["participant_characters"]}
    assert set(got) == {visible, hidden}, "两个成员都得在，隐藏的那个不能被过滤掉：" + str(sorted(got))
    assert got[hidden]["name"] and got[hidden]["avatar"]
    assert store().get_conversation(conv_id).participants == [visible, hidden]


def test_deleting_a_character_clears_its_feishu_bindings(client):
    """删角色必须把飞书那边的绑定一起清掉。留着的话那个私聊下一次发消息会解析到
       一个已经不存在的角色，桥接只会回一句「角色不存在」，而 /角色 列表里也换不回来。"""
    from animechat import feishu
    from animechat.store import store

    cid = client.post("/api/characters", json={"name": "要删掉的角色"}).json()["character"]["id"]
    db = store()
    db.set_pref(feishu.bind_key("oc_demo"), cid)
    db.set_pref(feishu.conv_key("oc_demo"), "42")
    db.set_pref(feishu.mode_key("oc_demo"), "off")
    db.set_pref(feishu.group_key("oc_demo"), '["a", "b"]')
    assert feishu.list_bindings(db) == {"oc_demo": cid}

    assert client.delete("/api/characters/" + cid).json()["how"] == "deleted"
    assert feishu.list_bindings(db) == {}
    for key in (feishu.conv_key("oc_demo"), feishu.mode_key("oc_demo"), feishu.group_key("oc_demo")):
        assert db.get_pref(key) == "", "绑定键清了但 " + key + " 还留着"


def test_sticker_endpoints(client):
    listing = client.get("/api/stickers").json()["stickers"]
    if listing:
        sid = listing[0]["id"]
        patched = client.patch("/api/stickers/" + sid, json={"favorite": True}).json()["sticker"]
        assert patched["favorite"] is True
        client.patch("/api/stickers/" + sid, json={"favorite": False})
    buf = io.BytesIO()
    Image.new("RGBA", (20, 20)).save(buf, format="PNG")
    up = client.post("/api/stickers/upload",
                     files={"file": ("啦啦+开心.png", buf.getvalue(), "image/png")},
                     data={"tags": "啦啦,开心", "emotion": "happy"})
    assert up.status_code == 200
    made = up.json()["sticker"]
    assert "开心" in client.get("/api/stickers?q=啦啦").json()["stickers"][0]["tags"]
    assert client.delete("/api/stickers/" + made["id"]).status_code == 200


def test_client_sticker_record_has_no_dead_weight(client):
    """手机端一开页面就要下整份表情索引：一条里前端不读的字段 × 1690 张，就是白烧的
    下载和 JSON.parse（实测瘦身前 572KB）。note 必须留着 —— 管理面板要显示备注；
    删字段那一步曾经把它一起裁掉，被 test_scenario_note_reaches_the_client 拦住了。"""
    buf = io.BytesIO()
    Image.new("RGBA", (20, 20)).save(buf, format="PNG")
    up = client.post("/api/stickers/upload",
                     files={"file": ("字段形状+开心.png", buf.getvalue(), "image/png")},
                     data={"tags": "字段形状", "emotion": "happy"}).json()["sticker"]
    try:
        assert "note" in up, "note 不能从前端契约里掉出去（管理面板读它显示备注）"
        assert {"id", "label", "url", "tags", "emotion", "emotion_label", "origin", "favorite"} <= set(up)
        for dead in ("width", "height", "bytes", "created_at"):
            assert dead not in up, dead + " 前端没地方读"
    finally:
        client.delete("/api/stickers/" + up["id"])


def test_settings_roundtrip_masks_key(client):
    out = client.patch("/api/settings", json={"llm_temperature": 0.35, "sticker_mode": "off"}).json()["settings"]
    assert out["llm_temperature"] == 0.35
    assert out["sticker_mode"] == "off"
    assert "…" not in out["llm_api_key"] or len(out["llm_api_key"]) < 14  # 绝不回显明文
    assert client.patch("/api/settings", json={"sticker_mode": "不像话的值"}).status_code == 400


def test_media_route_blocks_traversal(client):
    assert client.get("/media/stickers/..%2f..%2fpyproject.toml").status_code in (400, 404)
    assert client.get("/media/nope/x.png").status_code == 404


def test_context_endpoint_shows_system_prompt(client):
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    client.post("/api/chat", json={"conversation_id": conv, "content": "在吗"})
    data = client.get("/api/conversations/" + str(conv) + "/context").json()
    assert data["messages"][0]["role"] == "system"
    assert "[sticker:标签]" in data["messages"][0]["content"]
    assert data["chars"] > 100

# ---------------------------------------------------------------- 未读红点

def _unread_of(client, conv_id):
    rows = client.get("/api/conversations").json()["conversations"]
    return [c for c in rows if c["id"] == conv_id][0]["unread_count"]


def test_unread_counts_character_replies_only(client):
    """侧栏那个红色气泡的数字来自这里：只数角色的回复，用户自己发的那条不算。"""
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    assert _unread_of(client, conv) == 1, "开场白是角色发的，没读过就该亮 1"

    client.post("/api/chat", json={"conversation_id": conv, "content": "在吗"})
    assert _unread_of(client, conv) == 2, "用户那句不该算未读，角色回的这句才算"

    chars = {c["id"]: c for c in client.get("/api/bootstrap").json()["characters"]}
    assert chars[cid]["unread_count"] == 2, "角色列表项上的未读总数要跟着会话走"

    out = client.post("/api/conversations/" + str(conv) + "/read").json()
    assert out["cleared"] == 2 and out["conversation"]["unread_count"] == 0
    assert _unread_of(client, conv) == 0, "读过之后必须归零，否则红点消不掉"

    client.post("/api/chat", json={"conversation_id": conv, "content": "再说一句"})
    assert _unread_of(client, conv) == 1, "新回复要重新点亮"


def test_character_view_carries_chat_list_row_data(client):
    """「对话」那一页是一行一个角色（微信式会话列表）：最后一句、什么时候、几条没看。
       这三样都得后端给 —— 前端只拿得到角色卡，翻不动消息。"""
    fresh = client.get("/api/characters").json()["characters"][0]
    assert fresh["last_at"] == 0 and fresh["last_preview"] == "", "没聊过的人不该有时间戳和预览"

    cid = fresh["id"]
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    client.post("/api/chat", json={"conversation_id": conv, "content": "讲句话"})

    row = {c["id"]: c for c in client.get("/api/characters").json()["characters"]}[cid]
    assert row["last_preview"], "聊过之后要有最后一句"
    assert "[sticker" not in row["last_preview"], "发给模型的标记不能漏进列表预览"
    assert row["last_at"] > 0, "要有时间戳，否则列表只能按创建顺序排，最近聊过的沉在下面"
    assert row["unread_count"] >= 1, "未读条数跟着走"


def test_mark_read_watermark_never_goes_backwards(client):
    """「删到这儿」会把最大的消息 id 删小。水位线要是跟着往回拽，已读的消息会集体
       重新亮起红点——用户看到的就是「我明明看过了」。"""
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    client.post("/api/chat", json={"conversation_id": conv, "content": "讲句话"})
    client.post("/api/conversations/" + str(conv) + "/read")
    assert _unread_of(client, conv) == 0

    msgs = client.get("/api/conversations/" + str(conv)).json()["messages"]
    client.delete("/api/messages/" + str(msgs[1]["id"]))   # 从第二条往后全删
    assert _unread_of(client, conv) == 0, "截断之后水位线不能被往回拽"


def test_read_endpoint_is_idempotent_and_404s(client):
    cid = client.get("/api/characters").json()["characters"][0]["id"]
    conv = client.post("/api/conversations", json={"character_id": cid}).json()["conversation"]["id"]
    assert client.post("/api/conversations/" + str(conv) + "/read").json()["cleared"] == 1
    assert client.post("/api/conversations/" + str(conv) + "/read").json()["cleared"] == 0
    assert client.post("/api/conversations/999999/read").status_code == 404

def test_upgrade_does_not_light_up_old_history(tmp_path):
    """老库升到带未读水位的这一版时，库里的历史必须一律算读过。少这步回填
       （默认 0 = 「一条都没读」），用户升级完看到的是满屏红点，会以为软件坏了。"""
    import sqlite3

    from animechat.store import Store

    path = tmp_path / "old_unread.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE conversations (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " character_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',"
        " pinned INTEGER NOT NULL DEFAULT 0, participants TEXT NOT NULL DEFAULT '[]',"
        " summary TEXT NOT NULL DEFAULT '', summary_upto INTEGER NOT NULL DEFAULT 0,"
        " created_at REAL NOT NULL, updated_at REAL NOT NULL);"
        "CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " conversation_id INTEGER NOT NULL, role TEXT NOT NULL,"
        " content TEXT NOT NULL DEFAULT '', stickers TEXT NOT NULL DEFAULT '[]', emotion TEXT,"
        " meta TEXT NOT NULL DEFAULT '{}', speaker TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);")
    conn.execute("INSERT INTO conversations(character_id,created_at,updated_at) VALUES('a',1.0,1.0)")
    for i in range(3):
        conn.execute("INSERT INTO messages(conversation_id,role,content,created_at)"
                     " VALUES(1,'assistant',?,1.0)", ("看过的老消息" + str(i),))
    conn.commit()
    conn.close()

    db = Store(path)
    assert db.get_conversation(1).unread_count == 0, "升级完不该把老回复算成未读"
    assert db.list_conversations("a")[0].unread_count == 0
    db.add_message(1, "assistant", "升级之后刚到的一句")
    assert db.get_conversation(1).unread_count == 1, "升级后新到的那句该亮红点"
    assert db.mark_read(1) == 1
    assert db.get_conversation(1).unread_count == 0


def test_group_unread_shows_on_every_member(client):
    """群里有人回了话，从任何一个成员的列表项都该看得到红点：平板 / 手机的窄栏里
       只剩角色头像条，会话那一列根本看不见字。"""
    ids = [client.post("/api/characters", json={"name": n, "greeting": n + "的招呼"}).json()["character"]["id"]
           for n in ("甲", "乙")]
    client.post("/api/conversations", json={"character_id": ids[0], "participants": ids, "title": "群"})
    chars = {c["id"]: c for c in client.get("/api/bootstrap").json()["characters"]}
    assert chars[ids[0]]["unread_count"] == 2 and chars[ids[1]]["unread_count"] == 2

    conv = client.get("/api/conversations").json()["conversations"][0]
    client.post("/api/conversations/" + str(conv["id"]) + "/read")
    chars = {c["id"]: c for c in client.get("/api/bootstrap").json()["characters"]}
    assert chars[ids[0]]["unread_count"] == 0 and chars[ids[1]]["unread_count"] == 0
