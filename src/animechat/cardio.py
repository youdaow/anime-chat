"""角色卡（Character Card v2 / SillyTavern）互转，含 PNG 内嵌读写。

规则：
- JSON 卡：spec == chara_card_v2 时数据在 data 里；v1 卡字段直接在根层。两种都吃。
- PNG 卡：tEXt 分块，keyword 常见 chara（v1/v2）、ccv2、ccv3，值是 base64(JSON)。
- 导入时不认识的字段收进 extensions.animechat 原样带出，导出不丢信息。
"""

from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from typing import Iterator

from .models import Appearance, Character, DialogTurn

CARD_TEXT_KEYWORDS = ("chara", "ccv2", "ccv3")
EXT_NS = "animechat"
_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_OURS = {
    "title", "sticker_style", "sticker_prefs", "speaking_style", "catchphrases",
    "likes", "dislikes", "boundaries", "appearance", "source", "id", "builtin",
}


# ---------------------------------------------------------------- PNG 分块底层

def iter_png_chunks(data: bytes) -> Iterator[tuple[str, bytes]]:
    if not data.startswith(_PNG_SIG):
        raise ValueError("不是 PNG 文件")
    pos = len(_PNG_SIG)
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8].decode("latin-1")
        if length > 64 * 1024 * 1024:
            raise ValueError("PNG 分块异常巨大，拒绝解析")
        body = data[pos + 8:pos + 8 + length]
        if len(body) != length:
            raise ValueError("PNG 截断")
        yield ctype, body
        pos += 12 + length
        if ctype == "IEND":
            return


def _make_chunk(ctype: str, body: bytes) -> bytes:
    raw_type = ctype.encode("latin-1")
    return struct.pack(">I", len(body)) + raw_type + body + struct.pack(">I", zlib.crc32(raw_type + body) & 0xFFFFFFFF)


def extract_card_png(data: bytes) -> dict | None:
    """从 PNG 里取内嵌角色卡，取不到返回 None。"""
    for ctype, body in iter_png_chunks(data):
        if ctype != "tEXt":
            continue
        kw, _, text = body.partition(b"\x00")
        keyword = kw.decode("latin-1", "ignore").strip().lower()
        if keyword not in CARD_TEXT_KEYWORDS:
            continue
        try:
            payload = base64.b64decode(text, validate=True)
            card = json.loads(payload.decode("utf-8"))
        except Exception:
            continue
        if isinstance(card, dict):
            return card
    return None


def embed_card_png(data: bytes, card: dict) -> bytes:
    """把角色卡写回 PNG（放在 IEND 之前），同名 keyword 会先清掉。"""
    if not data.startswith(_PNG_SIG):
        raise ValueError("不是 PNG 文件")
    blob = base64.b64encode(json.dumps(card, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    chunk = _make_chunk("tEXt", b"chara\x00" + blob)
    out = bytearray(_PNG_SIG)
    pos = len(_PNG_SIG)
    inserted = False
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8].decode("latin-1")
        total = 12 + length
        raw = data[pos:pos + total]
        if ctype == "tEXt":
            kw, _, _rest = raw[8:-4].partition(b"\x00")
            if kw.decode("latin-1", "ignore").strip().lower() in CARD_TEXT_KEYWORDS:
                pos += total
                continue  # 丢掉旧卡
        if ctype == "IEND" and not inserted:
            out += chunk
            inserted = True
        out += raw
        pos += total
        if ctype == "IEND":
            break
    if not inserted:
        out += chunk
    return bytes(out)


# ---------------------------------------------------------------- mes_example

def build_mes_example(turns: list[DialogTurn]) -> str:
    blocks: list[str] = []
    for t in turns:
        if not (t.user or t.char):
            continue
        lines = ["<START>", "{{user}}: " + (t.user or "…"), "{{char}}: " + (t.char or "…")]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


_SPEAKER = re.compile(
    r"^\{\{\s*(user|char(?:acter)?)\s*\}\}\s*[:：]\s*(.*)$"
    r"|^(user|you|人类|用户)\s*[:：]\s*(.*)$"
    r"|^(char|character|assistant|助手|角色)\s*[:：]\s*(.*)$",
    re.I,
)


def parse_mes_example(text: str) -> list[DialogTurn]:
    """解析示例对话；兼容 {{user}}/user:/角色: 等写法与多行续写。"""
    turns: list[DialogTurn] = []
    cur = DialogTurn()
    last: str | None = None
    for line in (text or "").splitlines():
        s = line.strip()
        if s.lower().startswith("<start>"):
            if cur.user or cur.char:
                turns.append(cur)
            cur, last = DialogTurn(), None
            continue
        m = _SPEAKER.match(s)
        if m:
            who = next((g for g in (m.group(1), m.group(3), m.group(5)) if g), "")
            rest = next((g for g in (m.group(2), m.group(4), m.group(6)) if g is not None), "")
            if who.lower() in {"user", "you", "人类", "用户"}:
                if cur.user or cur.char:
                    turns.append(cur)
                    cur = DialogTurn()
                cur.user = rest
                last = "user"
            else:
                cur.char = (cur.char + "\n" + rest) if cur.char else rest
                last = "char"
            continue
        if s and last:  # 多行续写挂到上一个说话人
            if last == "user":
                cur.user = (cur.user + "\n" + line).strip("\n")
            else:
                cur.char = (cur.char + "\n" + line).strip("\n")
    if cur.user or cur.char:
        turns.append(cur)
    return [t for t in turns if t.user or t.char]


# ---------------------------------------------------------------- 互转

def to_card_v2(c: Character) -> dict:
    ext = {key: value for key, value in (
        ("title", c.title),
        ("speaking_style", c.speaking_style),
        ("catchphrases", c.catchphrases),
        ("likes", c.likes),
        ("dislikes", c.dislikes),
        ("sticker_style", c.sticker_style),
        ("sticker_prefs", c.sticker_prefs),
        ("boundaries", c.boundaries),
        ("appearance", c.appearance.model_dump()),
        ("avatar", c.avatar),
        ("local_id", c.id),
    ) if value}
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": c.name,
            "description": c.description,
            "personality": c.personality,
            "scenario": c.scenario,
            "first_mes": c.greeting,
            "mes_example": build_mes_example(c.example_dialogs),
            "creator_notes": c.boundaries,
            "system_prompt": c.system_extra,
            "post_history_instructions": c.post_history,
            "alternate_greetings": list(c.alternate_greetings),
            "tags": list(c.tags),
            "creator": c.creator or "animechat",
            "character_version": c.card_version or "1",
            "extensions": {EXT_NS: ext},
        },
    }


def _appearance_from(raw: dict) -> Appearance:
    known = set(Appearance.model_fields)
    clean = {k: v for k, v in raw.items() if k in known and isinstance(v, str)}
    return Appearance.model_validate(clean) if clean else Appearance()


def from_card(card: dict, *, make_id, source: str = "json", image: bytes | None = None) -> Character:
    """吃 v2 / v1 / 以及animechat 自己的导出格式。make_id(name) 负责分配本地 id。"""
    if not isinstance(card, dict):
        raise ValueError("角色卡不是 JSON 对象")
    body = card.get("data") if isinstance(card.get("data"), dict) else card
    name = str(body.get("name") or body.get("title") or "未命名角色").strip() or "未命名角色"
    ext = {}
    raw_ext = body.get("extensions")
    if isinstance(raw_ext, dict):
        ns = raw_ext.get(EXT_NS)
        if isinstance(ns, dict):
            ext = ns
    avatar = ext.get("avatar") if isinstance(ext.get("avatar"), str) else None

    dialogs = parse_mes_example(str(body.get("mes_example") or ""))
    alts = body.get("alternate_greetings")
    alt_list = [str(x) for x in alts if str(x).strip()][:6] if isinstance(alts, list) else []
    tags = body.get("tags")
    tag_list = [str(t) for t in tags if str(t).strip()][:12] if isinstance(tags, list) else []
    appearance = ext.get("appearance")
    if image is not None:
        avatar = "__IMAGE__"  # 由调用方落盘后替换

    def lst(key: str) -> list[str]:
        v = ext.get(key)
        return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []

    return Character(
        id=make_id(name),
        name=name,
        title=str(ext.get("title") or ""),
        tags=tag_list,
        avatar=avatar,
        appearance=_appearance_from(appearance) if isinstance(appearance, dict) else Appearance(),
        greeting=str(body.get("first_mes") or ""),
        alternate_greetings=alt_list,
        description=str(body.get("description") or ""),
        personality=str(body.get("personality") or ""),
        speaking_style=str(ext.get("speaking_style") or ""),
        catchphrases=lst("catchphrases"),
        likes=lst("likes"),
        dislikes=lst("dislikes"),
        scenario=str(body.get("scenario") or ""),
        example_dialogs=dialogs,
        sticker_style=str(ext.get("sticker_style") or "light"),  # type: ignore[arg-type]
        sticker_prefs=lst("sticker_prefs"),
        boundaries=str(ext.get("boundaries") or body.get("creator_notes") or ""),
        system_extra=str(body.get("system_prompt") or ""),
        post_history=str(body.get("post_history_instructions") or ""),
        builtin=False,
        source=source,  # type: ignore[arg-type]
        creator=str(body.get("creator") or ""),
        card_version=str(body.get("character_version") or ""),
    )


def parse_card_payload(payload: bytes, filename: str) -> tuple[dict, bytes | None]:
    """从上传的文件里解析出（角色卡 dict, 头像图片 bytes）。"""
    name = (filename or "").lower()
    if name.endswith(".png") or payload[:8] == _PNG_SIG:
        card = extract_card_png(payload)
        if card is None:
            card = {"name": re.sub(r"\.png$", "", filename.rsplit("/", 1)[-1], flags=re.I), "description": ""}
        return card, payload
    text = payload.decode("utf-8-sig", errors="replace")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ValueError("不是可解析的 JSON 或 PNG 角色卡：" + str(exc)) from exc
    if isinstance(data, list):  # 批量卡
        data = data[0] if data else {}
    if isinstance(data, dict) and "character" in data and isinstance(data["character"], dict):
        data = data["character"]
    return data, None
