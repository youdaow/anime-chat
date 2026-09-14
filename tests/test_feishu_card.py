"""角色选择卡片：构造 + 回调解析（纯逻辑，不需要 lark_oapi）。"""
import json

from animechat import feishu
from animechat.models import Character


def _chars():
    return [Character(id="kiriya", name="桐岛郁弥"),
            Character(id="makoto", name="橘真琴"),
            Character(id="haruka", name="七濑遥")]


# ---------------------------------------------------------------- 构造
def test_card_is_valid_json_and_lists_roles_by_id():
    card = json.loads(feishu.character_card(_chars(), "makoto"))
    assert card["header"]["title"]["content"] == "选一个角色"
    action = card["elements"][1]["actions"][0]
    assert action["tag"] == "select_static"
    # 选项 value 必须是角色 id —— 回调回来时靠它认人，不能是显示文案
    assert [o["value"] for o in action["options"]] == ["kiriya", "makoto", "haruka"]
    assert [o["text"]["content"] for o in action["options"]] == ["桐岛郁弥", "橘真琴", "七濑遥"]


def test_card_marks_current_character_in_text():
    """当前是谁写进正文，不靠 initial_index —— 1.0 卡片对那个支持不齐。"""
    card = json.loads(feishu.character_card(_chars(), "makoto"))
    assert "橘真琴" in card["elements"][0]["text"]["content"]
    assert "initial_index" not in json.dumps(card)


def test_card_handles_unknown_or_empty_current():
    card = json.loads(feishu.character_card(_chars(), "ghost"))
    assert "还没选" in card["elements"][0]["text"]["content"]


def test_callback_behaviour_value_must_be_a_dict():
    """behaviors[].value 写成字符串，飞书 SDK 反序列化会抛 UnmarshalException，
    回调根本进不了 handler（实测）。所以这里钉死它是个 dict。"""
    card = json.loads(feishu.character_card(_chars(), ""))
    beh = card["elements"][1]["actions"][0]["behaviors"]
    assert beh[0]["value"] == {"act": "switch_role"}
    assert isinstance(beh[0]["value"], dict)


def test_card_caps_options():
    many = [Character(id=f"c{i}", name=f"角色{i}") for i in range(150)]
    card = json.loads(feishu.character_card(many, ""))
    action = card["elements"][1]["actions"][0]
    assert len(action["options"]) == feishu.CARD_MAX_OPTIONS
    assert "100" in card["elements"][0]["text"]["content"]


# ---------------------------------------------------------------- 解析回调
def _pick_payload(option="haruka", act="switch_role", chat="oc_9"):
    """照 lark_oapi 的 P2CardActionTrigger 真实形状造：字段挂在对象属性上，
    action.value 是个 dict（behaviors 那份固定值），option 才是用户选的。"""
    return {
        "header": {"event_id": "evt_1", "event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": "ou_1"},
            "action": {"value": {"act": act}, "tag": "select_static",
                       "option": option, "name": "role_select"},
            "context": {"open_chat_id": chat, "open_message_id": "om_card"},
            "token": "c-abc",
        },
    }


def test_card_pick_returns_choice_and_chat():
    act, cid, chat = feishu.card_pick(_pick_payload(option="haruka"))
    assert (act, cid, chat) == ("switch_role", "haruka", "oc_9")


def test_card_pick_reads_option_not_value():
    """最容易搞混的一处：value 对每张换角色卡都一样，只有 option 随选择变。
    要是代码去读 value 当角色，永远会切到同一个。"""
    a1, c1, _ = feishu.card_pick(_pick_payload(option="kiriya"))
    a2, c2, _ = feishu.card_pick(_pick_payload(option="makoto"))
    assert a1 == a2 == "switch_role", "value 是固定标记，两次一样是对的"
    assert c1 != c2, "option 才是用户选的，两次必须不同"


def test_card_pick_tolerates_object_shape():
    """真回调传进来的是 SDK 对象而不是 dict，_get 两种都得吃。"""
    raw = _pick_payload(option="makoto")
    obj = type("E", (), {"event": type("D", (), {
        "action": type("A", (), raw["event"]["action"])(),
        "context": type("C", (), raw["event"]["context"])(),
        "operator": type("O", (), raw["event"]["operator"])(),
        "token": "t"})(),
        "header": type("H", (), raw["header"])()})()
    assert feishu.card_pick(obj) == ("switch_role", "makoto", "oc_9")


def test_card_pick_of_foreign_card_is_not_switch():
    p = _pick_payload(act="something_else")
    act, cid, chat = feishu.card_pick(p)
    assert act != "switch_role"


def test_card_pick_survives_missing_fields():
    act, cid, chat = feishu.card_pick({"event": {}})
    assert (act, cid, chat) == ("", "", "")
    assert feishu.card_pick(None) == ("", "", "")
