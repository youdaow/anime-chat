"""人设卡一键补全：只补空格、只吃 JSON、越界内容必须被裁掉。"""

import json

import pytest
from fastapi.testclient import TestClient

from animechat import llm, persona
from animechat.server import create_app

FULL = {"name": "x", "description": "d", "personality": "p", "speaking_style": "s",
        "catchphrases": ["c"], "likes": ["l"], "dislikes": ["q"], "boundaries": "b",
        "greeting": "g", "example_dialogs": [{"user": "u", "char": "c"}], "sticker_style": "rich"}


def test_empty_lists_and_whitespace_count_as_missing():
    m = persona.missing({"name": "x", "personality": "   ", "catchphrases": [],
                         "likes": ["  "], "greeting": "hi", "example_dialogs": [{"user": "u", "char": ""}]})
    assert {"personality", "catchphrases", "likes"} <= set(m)
    assert "greeting" not in m and "example_dialogs" not in m, "有内容就不该再让模型补"


def test_prompt_asks_only_for_the_gaps_and_respects_what_is_fixed():
    msgs = persona.build_messages({"name": "小雪", "title": "傲娇大小姐", "personality": "已定，别改"})
    assert len(msgs) == 2
    want = msgs[1]["content"].split("请补全这些空缺字段：")[1].split("\n")[0]
    assert "personality" not in want, "用户写过的字段不能出现在待补清单里"
    assert "（已定，别改）personality" in msgs[1]["content"]
    assert persona.build_messages(FULL) == [], "全填完了就不该再白花一次调用"


def test_parse_strips_code_fences_and_caps_every_field():
    payload = {"personality": "长" * 600, "speaking_style": "句" * 600, "boundaries": "边" * 600,
               "greeting": "开" * 600, "description": "描" * 600, "sticker_style": "rich",
               "catchphrases": ["短" * 40, "", "  ", "哼"], "likes": "a, b、c，d, e, f, g, h",
               "example_dialogs": [{"user": "U" * 300, "char": "A" * 300}] * 9}
    fence = chr(96) * 3   # 模型很爱把 JSON 包在 ```json 里，得先剥掉
    got = persona.parse_fill(fence + "json\n" + json.dumps(payload, ensure_ascii=False) + "\n" + fence)
    assert len(got["personality"]) <= 140 and len(got["speaking_style"]) <= 200
    assert len(got["greeting"]) <= 140 and len(got["description"]) <= 160
    assert all(len(x) <= 12 for x in got["catchphrases"]) and "" not in got["catchphrases"]
    assert got["catchphrases"] == ["短" * 12, "哼"], "字符串也要能拆成列表，且去空去重"
    assert len(got["likes"]) == 6, "喜欢/讨厌最多收 6 条，防止模型刷屏"
    assert len(got["example_dialogs"]) == 3, "对话最多 3 轮"
    assert len(got["example_dialogs"][0]["user"]) <= 120


def test_parse_rejects_garbage_rather_than_returning_something():
    for bad in ("我很乐意帮你设计这个角色！", "{不是合法 json", "[1,2,3]"):
        with pytest.raises(ValueError):
            persona.parse_fill(bad)


def test_parse_returns_empty_when_json_has_no_usable_field():
    """合法 JSON 但一个认识的键都没有：交回空字典，由端点决定怎么报错，
       别在这里抛「解析失败」误导用户以为是格式问题。"""
    assert persona.parse_fill('{"a": 1}') == {}
    assert persona.parse_fill('{"personality": "   "}') == {}


def test_bad_enum_is_dropped_not_saved():
    """sticker_style 是 Literal，模型编一个 ultra 出来会让整个保存请求 400。"""
    assert "sticker_style" not in persona.parse_fill('{"sticker_style":"ultra"}')
    assert persona.parse_fill('{"sticker_style":"light"}')["sticker_style"] == "light"


def test_endpoint_refuses_in_mock_mode():
    with TestClient(create_app()) as client:
        r = client.post("/api/characters/ai-fill", json={"name": "小雪"})
        assert r.status_code == 400
        assert "演示模式" in r.json()["detail"]


def test_endpoint_requires_a_name():
    with TestClient(create_app()) as client:
        client.patch("/api/settings", json={"llm_api_key": "sk-x", "llm_base_url": "https://example.test/v1"})
        r = client.post("/api/characters/ai-fill", json={"name": "  "})
        assert r.status_code == 400 and "名字" in r.json()["detail"]


def test_endpoint_returns_fields_without_touching_the_database(monkeypatch):
    seen = {}

    async def fake(settings, messages, *, temperature=None, max_tokens=None):
        seen["messages"] = messages
        return '{"personality":"嘴硬心软","greeting":"哼。[sticker:傲娇]","sticker_style":"rich"}'

    monkeypatch.setattr(llm, "complete_chat", fake)
    with TestClient(create_app()) as client:
        client.patch("/api/settings", json={"llm_api_key": "sk-x", "llm_base_url": "https://example.test/v1"})
        before = client.get("/api/characters").json()
        r = client.post("/api/characters/ai-fill", json={"name": "小雪", "title": "傲娇大小姐"})
        assert r.status_code == 200, r.text
        assert r.json()["fields"]["personality"] == "嘴硬心软"
        assert sorted(r.json()["filled"]) == sorted(["personality", "greeting", "sticker_style"])
        assert client.get("/api/characters").json() == before, "补全只返回字段，绝不能顺手建角色"


def test_endpoint_says_so_when_model_gives_back_nothing(monkeypatch):
    async def fake(settings, messages, *, temperature=None, max_tokens=None):
        return "抱歉，我需要更多信息。"

    monkeypatch.setattr(llm, "complete_chat", fake)
    with TestClient(create_app()) as client:
        client.patch("/api/settings", json={"llm_api_key": "sk-x", "llm_base_url": "https://example.test/v1"})
        r = client.post("/api/characters/ai-fill", json={"name": "小雪"})
        assert r.status_code == 400 and "没法解析" in r.json()["detail"]


def test_ai_create_generates_card_and_saves_it(monkeypatch):
    """一键生成：一句话 → 模型出 JSON → 直接落库并画头像。"""
    import json

    async def fake(settings, messages, *, temperature=None, max_tokens=None):
        return json.dumps({"name": "测试酱", "title": "来自某游戏的少女",
            "description": "活泼的测试角色", "personality": "嘴硬心软",
            "speaking_style": "语速快，爱用反问句", "catchphrases": ["真的假的"],
            "likes": ["唱歌"], "dislikes": ["迟到"], "greeting": "哼，你来啦。[sticker:傲娇]",
            "scenario": "放学后的教室", "example_dialogs": [{"user": "在干嘛", "char": "关你什么事"}],
            "boundaries": "不聊脏话", "sticker_style": "light",
            "appearance": {"style": "twin", "hair_color": "#FF6699", "hair_color2": "#FFC0CB",
                "eye_color": "#7E8CA8", "accessory": "hairpin", "expression": "shy", "bg_color": "#EFF1F6"}})

    monkeypatch.setattr(llm, "complete_chat", fake)
    with TestClient(create_app()) as client:
        client.patch("/api/settings", json={"llm_api_key": "sk-x", "llm_base_url": "https://example.test/v1"})
        r = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["character"]["name"] == "测试酱"
        assert body["character"]["personality"] == "嘴硬心软"
        assert body["avatar"] is True, "一键生成必须带上头像"
        assert body["character"]["avatar"].startswith("/media/avatars/")
        # 同名不覆盖，保护用户自己改过的卡
        r2 = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"})
        assert r2.status_code == 409 and "已经有叫" in r2.json()["detail"]
