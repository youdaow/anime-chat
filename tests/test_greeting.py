"""开场白里的标记要变成真表情，不能把方括号漏给用户。"""

from fastapi.testclient import TestClient

from animechat.server import create_app
from animechat.stickers import library


def test_greeting_markers_become_stickers():
    if not library().all():
        import pytest

        pytest.skip("内置素材还没生成")
    happy = next((s for s in library().all() if s.emotion == "happy"), None)
    assert happy is not None
    with TestClient(create_app()) as client:
        made = client.post("/api/characters", json={
            "name": "开场白角色", "sticker_style": "light",
            "greeting": "哟[sticker:" + happy.tags[0] + "]，来啦[emotion:happy]",
        }).json()["character"]
        conv = client.post("/api/conversations",
                           json={"character_id": made["id"]}).json()["conversation"]["id"]
        msg = client.get("/api/conversations/" + str(conv)).json()["messages"][0]
        assert msg["content"] == "哟，来啦"
        assert msg["stickers"] == [happy.id]
        assert msg["emotion"] == "happy"
