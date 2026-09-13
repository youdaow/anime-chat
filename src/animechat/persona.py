"""一键补全人设卡：把「填 20 个框」变成「写一行，再改两处」。

只填用户留空的字段，已经动过的绝不覆盖——这样就不需要「会不会冲掉我写的东西」这种确认，
也不用担心模型把人设改跑偏。
"""

import json
import re

# 字段 -> 上限字数。模型爱写小作文，卡太长会把提示词挤爆。
_LIMITS = {
    "personality": 140, "speaking_style": 200, "boundaries": 120, "greeting": 140,
    "description": 160, "sticker_style": 8,
}
_LISTS = {"catchphrases": (5, 12), "likes": (6, 16), "dislikes": (6, 16)}
_STICKER = {"off", "light", "rich"}
_FIELDS = ("description", "personality", "speaking_style", "catchphrases",
           "likes", "dislikes", "boundaries", "greeting", "example_dialogs", "sticker_style")
_FENCE = "```"



def build_create_messages(query: str) -> list[dict]:
    """根据"角色名 + 出处（游戏/动画/作品）"生成一张完整角色卡。
    输出严格 JSON，前端拿到后直接落库并程序化画头像。"""
    shape = ('{"name":"","title":"","description":"","personality":"","speaking_style":"",'
             '"catchphrases":[],"likes":[],"dislikes":[],"greeting":"","scenario":"",'
             '"example_dialogs":[{"user":"","char":""}],"boundaries":"","sticker_style":"light",'
             '"appearance":{"style":"short","hair_color":"#8E9BB3","hair_color2":"#C9D4E8",'
             '"eye_color":"#7E8CA8","accessory":"none","expression":"happy","bg_color":"#EFF1F6"}}')
    sys = ("你在为二次元聊天应用生成一张完整的角色卡。只输出一个 JSON 对象，不要解释、不要代码围栏，"
           "键只能是下面要求的这些，全部用中文。\n"
           "输入是「角色名 + 出处（游戏/动画/作品）」，也可能是只有角色名。请基于公开设定还原性格与说话方式：\n"
           "- name：角色名（中文）；title：一句话身份（作品 + 身份，例如「《BanG Dream! It's MyGO!!!!!》的吉他手」）；\n"
           "- description 不超 45 字；personality 写性格内核与矛盾点，不超 90 字；\n"
           "- speaking_style 具体到句长、口癖、什么时候破功，不超 80 字；\n"
           "- catchphrases 给 3-5 个真会重复说的短词，每条不超 8 字；\n"
           "- likes / dislikes 各 4-8 条，每条不超 10 字；\n"
           "- greeting 是它主动开口的第一句，可以带一个 [sticker:标签]，不超 50 字；\n"
           "- scenario 写当前聊天场景（默认就是和玩家日常聊天）；\n"
           "- example_dialogs 给 2 轮 U/A 对话，示范语气而不是介绍设定，每句不超 25 字；\n"
           "- boundaries 写聊天底线，不超 40 字；sticker_style 用 light 或 rich；\n"
           "- appearance：按角色外观填 style(long|twin|short|bob|hime)、hair_color、hair_color2、eye_color"
           "（都用十六进制颜色，如 #FF6699）、accessory(glasses|eyepatch|hairpin|cat_ear|none)、"
           "expression(happy|calm|smug|energetic|shy)、bg_color（十六进制）。不确定就填默认值。\n"
           "- 不确定或出处有歧义的角色，不要编造设定，用模糊写法并在 description 里注明出处。\n"
           "JSON 形状：" + shape)
    usr = "请为这个角色生成完整角色卡：\n" + str(query or "").strip() + "\n\nJSON："
    return [{"role": "system", "content": sys}, {"role": "user", "content": usr}]


def parse_create(text: str) -> dict:
    """解析一键生成接口返回的 JSON，收成 Character 能直接 model_validate 的字段。"""
    body = re.sub(r"^\s*" + _FENCE + r"[a-zA-Z]*\s*|\s*" + _FENCE + r"\s*$", "", str(text or "").strip())
    lo, hi = body.find("{"), body.rfind("}")
    if lo < 0 or hi <= lo:
        raise ValueError("模型没有返回 JSON")
    try:
        obj = json.loads(body[lo:hi + 1])
    except ValueError as exc:
        raise ValueError("JSON 解析失败：" + str(exc)[:80]) from exc
    if not isinstance(obj, dict):
        raise ValueError("返回的不是对象")
    out: dict = {}
    for key in ("name", "title", "description", "personality", "speaking_style", "greeting", "scenario", "boundaries"):
        val = _clean_text(obj.get(key), 160)
        if val:
            out[key] = val
    for key, n, each in (("catchphrases", 6, 8), ("likes", 8, 10), ("dislikes", 8, 10)):
        vals = _clean_list(obj.get(key), n, each)
        if vals:
            out[key] = vals
    st = _clean_text(obj.get("sticker_style"), 8)
    if st in {"off", "light", "rich"}:
        out["sticker_style"] = st
    turns = obj.get("example_dialogs") or obj.get("dialogs") or []
    dialogs = []
    if isinstance(turns, list):
        for t in turns[:3]:
            if isinstance(t, dict):
                u = _clean_text(t.get("user"), 120)
                c = _clean_text(t.get("char"), 160)
                if u or c:
                    dialogs.append({"user": u, "char": c})
            elif isinstance(t, str) and t.strip():
                dialogs.append({"user": "", "char": _clean_text(t, 160)})
    if dialogs:
        out["example_dialogs"] = dialogs
    ap = obj.get("appearance") if isinstance(obj.get("appearance"), dict) else {}
    look = {}
    for key in ("style", "hair_color", "hair_color2", "eye_color", "accessory", "expression", "bg_color"):
        val = _clean_text(ap.get(key), 24)
        if val:
            look[key] = val
    if look:
        out["appearance"] = look
    return out



def missing(have: dict) -> list[str]:
    """用户还没填的字段。列表类要看长度，不能只看真假。"""
    out = []
    for key in _FIELDS:
        val = have.get(key)
        if isinstance(val, (list, tuple)):
            if not [v for v in val if str(v or "").strip()]:
                out.append(key)
        elif isinstance(val, str):
            if not val.strip():
                out.append(key)
        elif not val:
            out.append(key)
    return out


def build_messages(have: dict) -> list[dict]:
    """have 是用户当前填了什么；只让模型补 missing 里那些。全填完就返回空，不白花一次调用。"""
    want = missing(have)
    if not want:
        return []
    known = []
    for key in ("name", "title", "scenario"):
        text = str(have.get(key) or "").strip()
        if text:
            known.append(key + "：" + text)
    for key in _FIELDS:
        if key in want:
            continue
        val = have.get(key)
        if isinstance(val, (list, tuple)):
            text = "、".join(str(v) for v in val if str(v or "").strip())
        else:
            text = str(val or "").strip()
        if text:
            known.append("（已定，别改）" + key + "：" + text[:120])
    sys = ("你在为二次元聊天角色填一张人设卡。只输出一个 JSON 对象，不要解释、不要代码围栏，"
           "键只能是下面要求的这些。全部用中文。\n"
           "写作要求：\n"
           "- personality 写性格内核与矛盾点，别写履历表；\n"
           "- speaking_style 必须具体到句长、口癖、什么时候破功，这是像不像的关键；\n"
           "- catchphrases 给 3-5 个真会重复说的短词，别给完整句子；\n"
           "- greeting 是它主动开口的第一句，带一个 [sticker:标签]，标签用两字中文；\n"
           "- example_dialogs 给 2 轮 U/A 对话，示范语气而不是介绍设定；\n"
           "- 不要客套话，直接写成能演的台词。\n"
           "长度是硬要求，超了会被直接裁掉：personality 不超 45 字、speaking_style 不超 70 字、"
           "boundaries 不超 40 字、greeting 不超 50 字、description 不超 45 字、"
           "catchphrases 每条不超 8 字、likes/dislikes 每条不超 10 字、对话每句不超 25 字。"
           "一次写满全部字段，不要分点解释。")
    usr = ("角色已知信息：\n" + ("\n".join(known) or "（只有名字）") + "\n\n"
           + "请补全这些空缺字段：" + "、".join(want) + "\n\n"
           + "JSON 形状：{"
           + '"description":"...", "personality":"...", "speaking_style":"...", '
           + '"catchphrases":["..."], "likes":["..."], "dislikes":["..."], '
           + '"boundaries":"...", "greeting":"...", "sticker_style":"off|light|rich", '
           + '"example_dialogs":[{"user":"...", "char":"..."}]}')
    return [{"role": "system", "content": sys}, {"role": "user", "content": usr}]


def _clean_text(val, limit: int) -> str:
    text = re.sub(r"[\r\t]+", "", str(val if val is not None else "")).strip()
    text = text.strip('"“”').strip()
    return text[:limit].strip()


def _clean_list(val, n: int, each: int) -> list[str]:
    if isinstance(val, str):
        items = re.split(r"[,，、\n]+", val)
    elif isinstance(val, (list, tuple)):
        items = list(val)
    else:
        items = []
    out: list[str] = []
    for it in items:
        s = _clean_text(it, each)
        if s and s not in out:
            out.append(s)
        if len(out) >= n:
            break
    return out


def parse_fill(text: str) -> dict:
    """把模型输出收成能直接写进表单的字段；解析不出 JSON 就抛 ValueError。"""
    body = re.sub(r"^\s*" + _FENCE + r"[a-zA-Z]*\s*|\s*" + _FENCE + r"\s*$", "", str(text or "").strip())
    lo, hi = body.find("{"), body.rfind("}")
    if lo < 0 or hi <= lo:
        raise ValueError("模型没有返回 JSON")
    try:
        obj = json.loads(body[lo:hi + 1])
    except ValueError as exc:
        raise ValueError("JSON 解析失败：" + str(exc)[:80]) from exc
    if not isinstance(obj, dict):
        raise ValueError("返回的不是对象")
    out: dict = {}
    for key, limit in _LIMITS.items():
        val = _clean_text(obj.get(key), limit)
        if val:
            out[key] = val
    if out.get("sticker_style") not in _STICKER:
        out.pop("sticker_style", None)
    for key, (n, each) in _LISTS.items():
        vals = _clean_list(obj.get(key), n, each)
        if vals:
            out[key] = vals
    turns = obj.get("example_dialogs") or obj.get("dialogs") or []
    dialogs = []
    if isinstance(turns, list):
        for t in turns[:3]:
            if isinstance(t, dict):
                u = _clean_text(t.get("user"), 120)
                c = _clean_text(t.get("char"), 160)
                if u or c:
                    dialogs.append({"user": u, "char": c})
            elif isinstance(t, str) and t.strip():
                dialogs.append({"user": "", "char": _clean_text(t, 160)})
    if dialogs:
        out["example_dialogs"] = dialogs
    return out
