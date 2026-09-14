"""模型接入方式注册表：界面上的下拉、后端走哪条实现，都只认这里的 key。

为什么要有这个文件：这套界面以前把「本机 AI API Hub 网关」当成默认接入方式写死了
（默认地址 127.0.0.1:8789、模型填 auto 让网关选路、Key 形如 sk-hub-…）。那个网关已经
停用，而将来给别人用时一定是他们自带 Key —— 所以接入方式必须是一个能选的口子，
而不是散在界面和报错文案里的一个地址。以后要加 Anthropic 原生 / Gemini 原生 / 本地
ollama，在这里加一条、在 llm.py 按 family 接一下实现，前端不用改。

model_placeholder 只是输入框里的灰色示例，不是兜底默认值（模型名默认留空，逼用户
要么填要么点「测试连接」拉清单）。各家都填**当前在售的最新代际**，别停留在 deepseek-chat /
gpt-4o-mini 这种已经退役或落后的名字上：DeepSeek 官方已把 deepseek-chat 标记为退役、
路由到 V4.1-Flash；阿里云百炼的千问已到 3.7/3.8。示例名以官方文档为准，拿不准就点
「测试连接」从对方真实清单里选。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Provider:
    key: str                    # 存进 settings.llm_provider 的值
    name: str                   # 界面上显示的说法
    hint: str                   # 一句话解释这条接法
    family: str = "openai"      # llm.py 按它选实现；同家族的不同平台共用一套协议
    needs_base_url: bool = True
    needs_key: bool = True
    base_url: str = ""          # 预置平台的固定地址：选中即填进 Base URL 并锁定；自定义留空由用户填
    base_placeholder: str = ""
    key_placeholder: str = ""
    model_placeholder: str = ""


# 顺序就是界面上拉里的顺序：主流平台在最上面（地址已预置，只填 Key），
# 自定义 / 中转站排在平台之后（要自己填地址），内置 Mock 永远垫底。
# 自定义那条的 key 沿用历史值 "openai"，是为了让老 settings.json 里存过的值仍然有效。
PROVIDERS: tuple[Provider, ...] = (
    Provider(
        key="deepseek",
        name="DeepSeek（深度求索）",
        hint="官方地址已预置，只需填 Key。模型用 deepseek-flash（V4.1-Flash，带视觉）或更强的 deepseek-v4-pro；点「测试连接」拉清单。",
        base_url="https://api.deepseek.com/v1",
        key_placeholder="从 platform.deepseek.com 建的 Key（sk-…）",
        model_placeholder="deepseek-flash",
    ),
    Provider(
        key="qwen",
        name="通义千问 Qwen（阿里云百炼）",
        hint="兼容模式地址已预置。模型如 qwen3.7-plus（均衡）、qwen3.8-max（最强）、qwen3.8-flash（最快）；这类会先出思考过程，可在设置里关。",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        key_placeholder="百炼控制台建的 API-KEY（sk-…）",
        model_placeholder="qwen3.7-plus",
    ),
    Provider(
        key="glm",
        name="智谱 GLM",
        hint="官方地址已预置（注意智谱是 /api/paas/v4，不是 /v1）。模型如 glm-5.2；点「测试连接」拉最新清单。",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        key_placeholder="open.bigmodel.cn 建的 Key",
        model_placeholder="glm-5.2",
    ),
    Provider(
        key="kimi",
        name="Kimi（月之暗面 Moonshot）",
        hint="官方地址已预置。模型如 kimi-k3；点「测试连接」拉最新清单。",
        base_url="https://api.moonshot.cn/v1",
        key_placeholder="platform.moonshot.cn 建的 Key（sk-…）",
        model_placeholder="kimi-k3",
    ),
    Provider(
        key="openai_official",
        name="OpenAI 官方",
        hint="api.openai.com 已预置。模型如 gpt-6-astra（当前旗舰）；走代理就改用下面的「自定义」。",
        base_url="https://api.openai.com/v1",
        key_placeholder="platform.openai.com 建的 Key（sk-…）",
        model_placeholder="gpt-6-astra",
    ),
    Provider(
        key="openai",
        name="自定义 / 中转站（自己填 Base URL）",
        hint="自建中转站、或上面没列到的 OpenAI 兼容平台都选这个，自己填地址（通常以 /v1 结尾）。",
        base_placeholder="https://api.openai.com/v1（或你的中转站地址）",
        key_placeholder="平台 / 中转站给的 Key；留空 = 不联网，走内置 Mock",
        model_placeholder="必填，可点「测试连接」拉清单后选",
    ),
    Provider(
        key="mock",
        name="内置 Mock（不联网、不花钱）",
        hint="用程序里的假模型跑通全部流程：不要 Key、不出门，回复是本地模板生成的，适合先看看界面和表情逻辑。",
        family="mock",
        needs_base_url=False,
        needs_key=False,
        base_placeholder="（这条不需要地址）",
        key_placeholder="（这条不需要 Key）",
        model_placeholder="mock",
    ),
)

# 默认落在「自定义」：不替用户预设任何一家平台，也不写死地址。
# 注意别用 PROVIDERS[0]（那是 DeepSeek 了）。
DEFAULT_KEY = "openai"


def keys() -> list[str]:
    return [p.key for p in PROVIDERS]


def get(key: str) -> Provider:
    """认不了的一律退回默认那条（自定义）：老 settings.json 里根本没这个字段，不能因此报错。"""
    want = (key or "").strip()
    for p in PROVIDERS:
        if p.key == want:
            return p
    for p in PROVIDERS:
        if p.key == DEFAULT_KEY:
            return p
    return PROVIDERS[0]


def is_known(key: str) -> bool:
    return (key or "").strip() in set(keys())


def options() -> list[dict]:
    """给 /api/providers：前端照着渲染，接入方式不写在界面里。"""
    return [asdict(p) for p in PROVIDERS]


def family_of(key: str) -> str:
    return get(key).family
