import base64
import io
import json

import pytest
from PIL import Image

from animechat import cardio
from animechat.models import Character, DialogTurn


def sample() -> Character:
    return Character(
        id="demo", name="测试角色", title="会写诗的机器人", tags=["冷幽默"],
        description="外表冷淡，内心吐槽役", personality="克制、精准",
        speaking_style="短句，结论先行", catchphrases=["不坏", "两分钟"],
        likes=["冷萃"], dislikes=["拖延"], scenario="深夜办公室",
        greeting="说重点。", example_dialogs=[DialogTurn(user="在吗", char="在，两分钟说完。")],
        sticker_style="light", sticker_prefs=["wuyu", "嗯哼"], boundaries="不做人身攻击",
    )


def test_card_roundtrip_keeps_animechat_fields():
    card = cardio.to_card_v2(sample())
    assert card["spec"] == "chara_card_v2"
    assert card["data"]["first_mes"] == "说重点。"
    back = cardio.from_card(card, make_id=lambda name: "restored")
    assert back.name == "测试角色"
    assert back.speaking_style == "短句，结论先行"
    assert back.sticker_prefs == ["wuyu", "嗯哼"]
    assert back.example_dialogs[0].user == "在吗"
    assert "两分钟说完" in back.example_dialogs[0].char
    assert back.boundaries == "不做人身攻击"


def test_card_roundtrip_keeps_context_budget():
    c = sample()
    c.context_chars = 12000
    card = cardio.to_card_v2(c)
    assert card["data"]["extensions"]["animechat"]["context_chars"] == 12000
    back = cardio.from_card(card, make_id=lambda name: "restored")
    assert back.context_chars == 12000

    card["data"]["extensions"]["animechat"]["context_chars"] = "6000"
    assert cardio.from_card(card, make_id=lambda name: "restored").context_chars == 6000


def test_v1_card_without_spec_is_accepted():
    v1 = {"name": "老卡", "description": "desc", "first_mes": "hi", "personality": "p"}
    char = cardio.from_card(v1, make_id=lambda name: "old")
    assert char.name == "老卡" and char.description == "desc" and char.greeting == "hi"


def test_mes_example_format_is_sillytavern_shaped():
    text = cardio.build_mes_example(sample().example_dialogs)
    assert "<START>" in text and "{{user}}:" in text and "{{char}}:" in text
    turns = cardio.parse_mes_example(text)
    assert len(turns) == 1 and turns[0].user == "在吗"


def test_mes_example_handles_multiline_and_bare_prefixes():
    raw = "<START>\n{{user}}: 第一句\n还有第二行\nchar: 回复A\n回复B\n"
    turns = cardio.parse_mes_example(raw)
    assert turns[0].user.startswith("第一句") and "第二行" in turns[0].user
    assert "回复B" in turns[0].char


def test_png_embed_and_extract_roundtrip():
    buf = io.BytesIO()
    Image.new("RGBA", (32, 32), (255, 255, 255, 255)).save(buf, format="PNG")
    png = buf.getvalue()
    assert cardio.extract_card_png(png) is None
    card = cardio.to_card_v2(sample())
    embedded = cardio.embed_card_png(png, card)
    got = cardio.extract_card_png(embedded)
    assert got["data"]["name"] == "测试角色"
    # 只能有一份卡：重复嵌入要覆盖而不是堆叠
    twice = cardio.embed_card_png(embedded, card)
    chunks = [c for c in cardio.iter_png_chunks(twice) if c[0] == "tEXt"]
    assert len(chunks) == 1
    assert Image.open(io.BytesIO(twice)).size == (32, 32)


def test_parse_payload_detects_png_and_json():
    buf = io.BytesIO()
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(buf, format="PNG")
    png = cardio.embed_card_png(buf.getvalue(), cardio.to_card_v2(sample()))
    card, image = cardio.parse_card_payload(png, "card.png")
    assert image == png and card["data"]["name"] == "测试角色"
    raw = json.dumps(card).encode("utf-8")
    card2, image2 = cardio.parse_card_payload(raw, "card.json")
    assert image2 is None and card2["data"]["name"] == "测试角色"


def test_bad_payload_raises():
    with pytest.raises(ValueError):
        cardio.parse_card_payload(b"not json at all", "x.json")
