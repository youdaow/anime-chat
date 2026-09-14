"""运行期配置：默认值 < .env / 环境变量 < data/settings.json。

界面里改的设置写进 data/settings.json（本机文件）。密钥只在本机保存，
对外接口一律返回掩码，绝不回显明文，也不写进日志。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from . import providers

ENV_PREFIX = "ANIMECHAT_"

# 这些字段可能来自环境变量；环境变量优先于 settings.json（便于部署时注入密钥）。
ENV_KEYS = {
    "host",
    "port",
    "llm_provider",
    "llm_base_url",
    "llm_api_key",
    "llm_model",
    "llm_temperature",
    "llm_max_tokens",
    "llm_timeout",
    "llm_thinking",
    "context_chars",
    "sticker_mode",
    "max_stickers_per_reply",
    "sticker_search_provider",
    "avatar_from_web",
    "tenor_api_key",
    "giphy_api_key",
    "user_name",
    "user_notes",
    "github_repo",
    "github_pack_path",
    "github_token",
    "feishu_app_id",
    "feishu_app_secret",
    "feishu_character",
    "feishu_api_base",
    "feishu_stickers",
    "feishu_proactive",
    "feishu_idle_min",
    "feishu_daily_max",
}

SECRET_FIELDS = ("llm_api_key", "tenor_api_key", "giphy_api_key", "github_token", "feishu_app_secret")


def _default_data_dir() -> Path:
    env = os.environ.get(ENV_PREFIX + "DATA_DIR")
    if env:
        return Path(env).expanduser().resolve()
    here = Path(__file__).resolve()
    for parent in here.parents:
        pyproj = parent / "pyproject.toml"
        if pyproj.is_file() and "animechat" in pyproj.read_text(encoding="utf-8", errors="ignore"):
            return parent / "data"
    return Path.cwd() / "data"


DATA_DIR: Path = _default_data_dir()
PKG_DIR: Path = Path(__file__).resolve().parent
ASSET_DIR: Path = PKG_DIR / "assets"
BUILTIN_STICKER_DIR: Path = ASSET_DIR / "stickers"
AVATAR_DIR: Path = ASSET_DIR / "avatars"
STICKER_MANIFEST: Path = BUILTIN_STICKER_DIR / "manifest.json"


def user_sticker_dir() -> Path:
    return DATA_DIR / "stickers"


def character_dir() -> Path:
    return DATA_DIR / "characters"


def settings_path() -> Path:
    return DATA_DIR / "settings.json"


def db_path() -> Path:
    return DATA_DIR / "animechat.db"


def ensure_dirs() -> None:
    for p in (DATA_DIR, user_sticker_dir(), character_dir(), AVATAR_DIR):
        p.mkdir(parents=True, exist_ok=True)


class Settings(BaseModel):
    """全部可调项。字段名即 settings.json 里的键。"""

    host: str = "127.0.0.1"
    port: int = 8899

    # 模型接入。以前这里的默认值是同机 AI API Hub 网关（127.0.0.1:8789 + 模型 auto，
    # 由网关选路）—— 那个网关已经停用，而且将来给别人用一定是自带 Key，所以默认改成
    # 「OpenAI 兼容、地址和 Key 自己填」。什么都不填就是内置 Mock：不联网、不花钱。
    llm_provider: str = providers.DEFAULT_KEY
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_temperature: float = 0.9
    llm_max_tokens: int = 800
    llm_timeout: float = 120.0
    # 混合推理模型（qwen3 / deepseek-r1）默认先思考一大段，实测聊天里 84% 的生成量
    # 烧在没人看的 reasoning_content 上、平均 7.5s 一条。off=直接关，low=只让它少想点。
    llm_thinking: Literal["off", "low", "on"] = "off"
    context_chars: int = 6000

    # 表情包
    sticker_mode: Literal["off", "light", "rich"] = "rich"
    max_stickers_per_reply: int = 1
    sticker_search_provider: Literal["auto", "tenor", "giphy", "bing", "duckduckgo"] = "auto"
    tenor_api_key: str = ""
    giphy_api_key: str = ""

    # 角色：AI 生成角色卡时，头像先联网找官方立绘，找不到（离线 / 防盗链 / 图太小）
    # 再退回程序画的 Q 版。关掉它就永远只用 Q 版，不想沾别人的图就关。
    avatar_from_web: bool = True

    # 用户自我设定（进入系统提示词）
    user_name: str = "你"
    user_notes: str = ""
    # 「我」这一侧的头像。空 = 没有，前端就用名字首字当头像。
    # _at 是版本号：换头像必须让浏览器去要新图，否则同名文件会被缓存住，
    # 用户只觉得「点了没反应」。角色的对应物是它的 updated_at。
    user_avatar: str = ""
    user_avatar_at: float = 0.0

    # GitHub 表情仓库：owner/name[:分支]，仓库里用 stickers.json + 图片的平铺结构。
    # 拉取公开仓库不需要 Token；发布（写）才需要。
    github_repo: str = ""
    github_pack_path: str = "stickers"
    github_token: str = ""

    # 飞书机器人。桥接是单独进程（animechat feishu），用长连接收事件，所以本机不用
    # 暴露公网地址。这里只存凭据和目标角色；开关就是「那个进程跑没跑」。
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    # 新来的私聊默认绑哪个角色。空 = 用角色库第一个，并让用户在飞书里用 /角色 换。
    feishu_character: str = ""
    # 桥接进程回调本机 API 的地址。空 = 按 host/port 拼。跨机部署才需要填。
    feishu_api_base: str = ""
    # 角色的表情包要不要真的发成飞书图片。关掉就只在文字里描述「[表情：xx]」。
    feishu_stickers: bool = True
    # 沉默后主动发言：飞书私聊里对方超过 idle_min 分钟没消息，角色就自己找 ta 说话。
    # 只对私聊生效（群里主动发言容易吵到别人），且总开关关了那个后台循环就空转。
    feishu_proactive: bool = False
    # 沉默多久算「该主动了」（分钟）。太小会变成夺命连环催。
    feishu_idle_min: int = 120
    # 每个会话每天最多主动发几条（防刷屏 + 省额度）。跨天自动清零。
    feishu_daily_max: int = 10

    @field_validator("llm_provider")
    @classmethod
    def _canon_provider(cls, v: str) -> str:
        """认不出的一律落到默认那条：接入方式是选出来的，不该存进脏值让后端猜。
        老的 settings.json 没有这个字段，取默认值也走的是同一条路。"""
        return v if providers.is_known(v) else providers.DEFAULT_KEY

    @field_validator("llm_base_url")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.strip().rstrip("/")

    @model_validator(mode="after")
    def _fill_preset_base_url(self) -> "Settings":
        """选了预置平台却没填地址，就补上那家的官方地址。

        界面上选中平台会顺手把地址填进格子，所以这条主要服务另外两条来路：
        环境变量 / CLI 只给了 ANIMECHAT_LLM_PROVIDER=deepseek，以及手改 settings.json
        只改了 provider。反过来，地址已经填了（哪怕是同一家、或故意写成的代理地址）
        就一个字都不动 —— 那是用户明确写下的，不该被注册表悄悄改回去。"""
        preset = providers.get(self.llm_provider).base_url
        if preset and not (self.llm_base_url or "").strip():
            self.llm_base_url = preset.rstrip("/")
        return self

    @field_validator("llm_temperature")
    @classmethod
    def _clamp_temp(cls, v: float) -> float:
        return max(0.0, min(2.0, float(v)))

    @field_validator("github_repo")
    @classmethod
    def _canon_repo(cls, v: str) -> str:
        """用户会从网页「Code ▸ HTTPS」整条复制，存成 owner/名字[:分支] 这种最短形式，
        界面上才看得清自己配的是哪个仓库。非法值原样留着，报错交给用到它的时候说清楚。"""
        raw = (v or "").strip()
        if not raw:
            return ""
        try:
            from .ghpack import parse_repo
            owner, name, ref = parse_repo(raw)
        except Exception:
            return raw
        base = owner + "/" + name
        return base if ref == "main" else base + ":" + ref

    @property
    def mock_mode(self) -> bool:
        """三种情况一律走本机假模型：明选了内置 Mock、没填 Key、模型名写着 mock。"""
        if providers.family_of(self.llm_provider) == "mock":
            return True
        return not self.llm_api_key.strip() or self.llm_model.strip().lower() == "mock"


def _env_overrides() -> dict[str, object]:
    out: dict[str, object] = {}
    for key in ENV_KEYS:
        raw = os.environ.get(ENV_PREFIX + key.upper())
        if raw is None or raw == "":
            continue
        out[key] = raw
    return out


def _read_file_settings() -> dict[str, object]:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def load_settings() -> Settings:
    """构造当前配置：默认值 ← settings.json ← 环境变量。"""
    merged: dict[str, object] = {}
    merged.update(_read_file_settings())
    merged.update(_env_overrides())
    known = {k: v for k, v in merged.items() if k in Settings.model_fields}
    return Settings.model_validate(known)


def save_settings(patch: dict) -> Settings:
    """把 patch 合并写入 settings.json（保留未提供的键），返回新配置。"""
    ensure_dirs()
    current = _read_file_settings()
    valid = Settings.model_fields
    clean: dict[str, object] = {}
    for key, value in patch.items():
        if key not in valid:
            continue
        if value is None:
            continue
        if isinstance(value, str) and key in SECRET_FIELDS:
            value = value.strip()
        clean[key] = value
    # 数值/枚举先过一遍模型校验，非法值直接抛错，不落盘
    model = Settings.model_validate({**{k: v for k, v in current.items() if k in valid}, **clean})
    # 校验器会改写一些值（仓库名整条 URL 复制过来会规范成 owner/名字、Base URL 去掉尾斜杠）。
    # 落盘必须用改写后的，否则界面重启后又看到原始输入，像是没保存成功。
    for key in clean:
        clean[key] = getattr(model, key)
    current.update(clean)
    settings_path().write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    return load_settings()


def mask(secret: str) -> str:
    """把密钥变成可安全展示的样子：sk-hu…xxxx。"""
    s = (secret or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "…" + "*" * 4 + s[-4:]


def settings_for_client(s: Settings) -> dict:
    """给前端的设置快照：密钥换成掩码 + 是否已配置。"""
    data = s.model_dump()
    for key in SECRET_FIELDS:
        data[key] = mask(str(data.get(key, "")))
    data["configured_secrets"] = {key: bool(str(getattr(s, key)).strip()) for key in SECRET_FIELDS}
    data["env_overridden"] = sorted(k for k in _env_overrides() if k in Settings.model_fields)
    data["data_dir"] = str(DATA_DIR)
    data["mock_mode"] = s.mock_mode
    return data
