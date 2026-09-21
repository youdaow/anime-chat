"""数据结构：角色、表情、消息、会话。"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field, field_validator

from .emotion import match_words


def now() -> float:
    return time.time()


class Appearance(BaseModel):
    """没有头像时，用来程序化生成 Q 版头像的外观参数。"""

    style: Literal["long", "twin", "short", "bob", "hime"] = "short"
    hair_color: str = "#8E9BB3"
    hair_color2: str = "#C9D4E8"
    eye_color: str = "#7E8CA8"
    accessory: Literal["glasses", "eyepatch", "hairpin", "cat_ear", "none"] = "none"
    expression: Literal["happy", "calm", "smug", "energetic", "shy"] = "happy"
    bg_color: str = "#EFF1F6"


class DialogTurn(BaseModel):
    user: str = ""
    char: str = ""


class Character(BaseModel):
    id: str
    name: str
    title: str = ""
    tags: list[str] = Field(default_factory=list)
    avatar: str | None = None
    appearance: Appearance = Field(default_factory=Appearance)

    greeting: str = ""
    alternate_greetings: list[str] = Field(default_factory=list)
    description: str = ""
    personality: str = ""
    speaking_style: str = ""
    catchphrases: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    scenario: str = ""
    example_dialogs: list[DialogTurn] = Field(default_factory=list)

    # 表情包偏好
    sticker_style: Literal["off", "light", "rich"] = "light"
    sticker_prefs: list[str] = Field(default_factory=list)

    boundaries: str = ""
    system_extra: str = ""  # 对应角色卡 system_prompt 的自定义补充
    post_history: str = ""  # 对应 post_history_instructions
    # 角色单独需要更长记忆时覆盖全局 context_chars；None 表示沿用设置。
    context_chars: int | None = None

    @field_validator("context_chars")
    @classmethod
    def _valid_context_chars(cls, value: int | None) -> int | None:
        if value is None:
            return None
        value = int(value)
        if value < 600:
            raise ValueError("角色上下文至少 600 字")
        return value

    builtin: bool = False
    source: str = "manual"  # builtin | json | png | manual
    creator: str = ""
    card_version: str = ""
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)

    def short_desc(self, limit: int = 90) -> str:
        base = " ".join(p for p in (self.description, self.personality) if p)
        base = base.replace("\n", " ").strip()
        return base[: limit - 1] + "…" if len(base) > limit else base


class Sticker(BaseModel):
    id: str
    label: str = ""
    tags: list[str] = Field(default_factory=list)
    emotion: str = "neutral"
    url: str  # /media/stickers/<id> 或远程 URL
    origin: str = "builtin"  # builtin | upload | url | generated
    width: int | None = None
    height: int | None = None
    bytes: int | None = None
    favorite: bool = False
    uses: int = 0
    note: str = ""
    created_at: float = Field(default_factory=now)

    @property
    def searchable(self) -> set[str]:
        words = {t.strip() for t in self.tags if t.strip()}
        if self.label:
            words.add(self.label)
        words.add(self.emotion)
        words.add(self.id)
        return {w for w in words if w}

    @property
    def emotions(self) -> set[str]:
        """这张图能演哪些情绪：显式情绪 + 从标签/名字里认出来的。
        联网抓回来的表情常常 emotion=neutral 而标签写着「傲娇」——
        只认 emotion 字段的话角色永远不会发它。"""
        out = {self.emotion} if self.emotion else set()
        out |= set(match_words(" ".join([self.label] + list(self.tags))))
        return out


class Message(BaseModel):
    id: int
    conversation_id: int
    role: Literal["user", "assistant", "system"]
    content: str
    stickers: list[str] = Field(default_factory=list)
    emotion: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    # 群聊里这条是谁说的（角色 id）。单聊和用户消息留空，老数据也留空。
    speaker: str = ""
    created_at: float | None = Field(default_factory=now)


class Conversation(BaseModel):
    id: int
    character_id: str
    title: str
    pinned: bool = False
    # 参与聊天的角色 id。单聊就是 [character_id]，群聊两个以上。
    participants: list[str] = Field(default_factory=list)
    created_at: float = Field(default_factory=now)
    updated_at: float = Field(default_factory=now)
    message_count: int = 0
    preview: str = ""
    summary: str = ""
    # 「读到哪条」的水位线（消息 id）。未读只数角色的回复：用户自己发的那条不该
    # 给自己点个红点。两个值都由 store 查询算出来，前端只负责画。
    last_read_id: int = 0
    unread_count: int = 0
    # 认证开起来之后这条会话归谁（访客 id）。空 = 本机主人本人的，包括所有历史行。
    owner: str = ""

    @computed_field  # 跟着 model_dump 走，前端直接拿得到
    @property
    def group(self) -> bool:
        return len(self.participants) > 1

