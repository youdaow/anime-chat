"""接入方式：默认值不再指向那台已经停用的本机网关，接什么全靠用户自己填。

产品以前把 AI API Hub（127.0.0.1:8789 + 模型 auto + sk-hub-… Token）当成理所当然的
默认接法写散在各处。它停用之后，这些文案不只是没用，还会把人往错的方向带 —— 尤其
是「地址是本机就劝人去 Hub 界面建 Token」那条：自建中转站常常就跑在 127.0.0.1 上。
所以这里锁三件事：① 注册表自洽，② 默认值干净，③ 界面上再也找不到那个网关的字样。
"""

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from animechat import providers
from animechat.config import ENV_KEYS, Settings
from animechat.server import create_app

SRC = Path(__file__).resolve().parent.parent / "src" / "animechat"
WEB = SRC / "web"
README = SRC.parent.parent / "README.md"
ENV_EXAMPLE = SRC.parent.parent / ".env.example"

# 那些只有已停用的网关才成立的说法。出现一次就是给用户指错路。
BANNED = ("sk-hub", "8789", "AI Hub", "Hub 网关", "只有 Hub", "填 auto", "去 Hub")


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


# ------------------------------------------------------------------ 注册表本身

def test_registry_is_self_consistent():
    keys = providers.keys()
    assert keys and len(keys) == len(set(keys)), "key 撞了就会有一条永远选不到"
    assert providers.DEFAULT_KEY in keys
    for p in providers.PROVIDERS:
        assert p.name and p.hint, p.key
        assert p.family in ("openai", "mock"), "llm.py 只认这两个家族，添一条就要接一段实现"
        if p.family == "mock":
            assert not p.needs_base_url and not p.needs_key, p.key


def test_unknown_provider_falls_back_instead_of_crashing():
    """老 settings.json 里根本没这个字段；手改出一个错值也不该把程序带崩。"""
    assert providers.get("").key == providers.DEFAULT_KEY
    assert providers.get("hub").key == providers.DEFAULT_KEY
    assert providers.is_known("mock") and not providers.is_known("hub")
    assert providers.family_of("mock") == "mock"


def test_registry_carries_no_dead_hub_wording():
    for p in providers.PROVIDERS:
        blob = " ".join([p.key, p.name, p.hint, p.base_placeholder, p.key_placeholder,
                         p.model_placeholder])
        for word in BANNED:
            assert word not in blob, p.key + " 的文案里还有：" + word


def test_options_shape_is_what_the_ui_renders():
    opts = providers.options()
    assert json.loads(json.dumps(opts)) == opts, "必须是能直接 JSON 化的"
    first = opts[0]
    for field in ("key", "name", "hint", "family", "needs_base_url", "needs_key",
                  "base_url", "base_placeholder", "key_placeholder", "model_placeholder"):
        assert field in first, field


# ------------------------------------------------------------------ 预置平台

def test_mainstream_platforms_are_preset_and_custom_is_last_of_the_real_ones():
    """接入方式直接列主流平台（地址预置，只填 Key），「自定义 / 中转站」排在平台之后、
    内置 Mock 永远垫底 —— 用户最常做的动作是"选一家 + 贴 Key"，不该先让他填地址。"""
    keys = providers.keys()
    for want in ("deepseek", "qwen", "glm", "kimi", "openai_official"):
        assert want in keys, want
    # 自定义和 Mock 是最后两条，且 Mock 在最末
    assert keys[-2] == "openai" and keys[-1] == "mock", keys
    assert keys.index("openai") > keys.index("deepseek"), "自定义不能排在平台前面"
    for p in providers.PROVIDERS:
        if p.key in ("deepseek", "qwen", "glm", "kimi", "openai_official"):
            assert p.base_url.startswith("https://"), p.key
            assert p.family == "openai", p.key
            assert p.model_placeholder, p.key + " 要给出该家常用模型名当提示"
    # 自定义没有预置地址（前端据此决定 Base URL 那格给不给改）
    assert providers.get("openai").base_url == ""
    assert providers.get("mock").base_url == ""


def test_preset_platform_urls_are_the_ones_the_docs_give():
    """地址写错不是「连不上」而是 404，而且智谱是 /api/paas/v4 不是 /v1 —— 锁死这几条。"""
    urls = {p.key: p.base_url for p in providers.PROVIDERS}
    assert urls["deepseek"] == "https://api.deepseek.com/v1"
    assert urls["qwen"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
    assert urls["glm"] == "https://open.bigmodel.cn/api/paas/v4"
    assert urls["kimi"] == "https://api.moonshot.cn/v1"
    assert urls["openai_official"] == "https://api.openai.com/v1"


def test_choosing_a_preset_platform_fills_its_url_without_being_asked():
    """只设 provider（环境变量 / CLI / 手改 settings.json 的来路）就自动补上官方地址。"""
    s = Settings(llm_provider="deepseek", llm_api_key="sk-abc", llm_model="deepseek-chat")
    assert s.llm_base_url == "https://api.deepseek.com/v1"
    assert not s.mock_mode


def test_an_explicit_url_always_wins_over_the_preset():
    """走代理 / 中转站的人会把 provider 选成某家平台、地址填自己的。
    注册表绝不能把他写的地址悄悄改回官方地址 —— 那就是把请求发到别处去了。"""
    s = Settings(llm_provider="deepseek", llm_base_url="https://my-proxy.example/v1",
                 llm_api_key="sk-abc", llm_model="m")
    assert s.llm_base_url == "https://my-proxy.example/v1"
    # 自定义一条没有预置地址：不填就是空（空 + 没 Key = Mock，老行为）
    assert Settings(llm_provider="openai").llm_base_url == ""


def test_custom_provider_stays_the_default_for_old_configs():
    """老的 settings.json 存的是 llm_provider="openai"（那时它 = 唯一的 OpenAI 兼容），
    现在它变成「自定义 / 中转站」，默认值也仍是它：老配置一个字节都不用改。"""
    assert providers.DEFAULT_KEY == "openai"
    assert providers.get("hub").key == "openai", "认不出的值退回自定义，不许猜成某家平台"
    assert Settings(llm_provider="hub").llm_base_url == ""


# ------------------------------------------------------------------ 配置默认值

def test_defaults_are_bring_your_own_key():
    s = Settings()
    assert s.llm_provider == providers.DEFAULT_KEY
    assert s.llm_base_url == "", "默认不能再写死一个地址，那是别人机器上的东西"
    assert s.llm_model == "", "auto 是网关的路由功能，没有网关了在默认值里写它就是骗人"


def test_bogus_provider_is_normalized_on_load():
    assert Settings(llm_provider="hub").llm_provider == providers.DEFAULT_KEY
    assert Settings(llm_provider="mock").llm_provider == "mock"


def test_mock_provider_alone_is_enough_for_mock_mode():
    """选了内置 Mock 就别再偷偷往外发请求，哪怕 Key 还留在文件里。"""
    assert Settings(llm_provider="mock", llm_api_key="sk-abc", llm_base_url="https://x/v1",
                    llm_model="m").mock_mode
    assert not Settings(llm_provider="openai", llm_api_key="sk-abc", llm_model="m").mock_mode
    assert Settings(llm_provider="openai", llm_api_key="").mock_mode, "没 Key 也走 Mock（老行为）"


# ------------------------------------------------------------------ 接口与文案

def test_providers_endpoint_feeds_the_dropdown(client):
    d = client.get("/api/providers").json()
    assert [p["key"] for p in d["providers"]] == providers.keys()
    assert d["default"] == providers.DEFAULT_KEY
    body = client.get("/api/providers").text
    for word in BANNED:
        assert word not in body, word


def test_mock_mode_does_not_offer_auto_as_a_model(client):
    """老版本在 Mock 下把模型清单写成 ["auto"]，等于劝人选一个没有网关会认的名字。"""
    client.patch("/api/settings", json={"llm_provider": "mock"})
    d = client.get("/api/models").json()
    assert d["models"] == [], d
    assert d["note"] and "Mock" in d["note"]


def test_switching_provider_can_be_saved_and_read_back(client):
    got = client.patch("/api/settings", json={"llm_provider": "mock"}).json()["settings"]
    assert got["llm_provider"] == "mock" and got["mock_mode"]
    back = client.patch("/api/settings", json={"llm_provider": "不存在的"}).json()["settings"]
    assert back["llm_provider"] == providers.DEFAULT_KEY


def test_dropdown_is_rendered_from_the_endpoint_not_hardcoded():
    """接入方式必须是从 /api/providers 画出来的：以后在 providers.py 添一条，
    前端一格都不用改。这条测试就是那个「预留接口」的合同。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    assert 'api("/api/providers")' in panels, "下拉没有从后端取"
    assert "provs.map(p => [p.key, p.name])" in panels, "选项要由注册表映射出来"
    assert "llm_provider: f.access.value" in panels, "选的接入方式要真的存进设置"
    assert "p.needs_base_url === false" in panels and "p.needs_key === false" in panels, \
        "格子得跟着 needs_* 收放，否则内置 Mock 也摆着一排空地址"
    assert "applyProvider();" in panels, "开框时按当前接入方式收一次，不能等用户去点下拉"
    # 预置平台的地址由后端给（前端不抄一份，抄了就会漂）
    assert "p.base_url" in panels, "前端要按注册表的 base_url 决定地址能不能改"


def test_preset_url_is_locked_and_custom_url_is_editable():
    """选中平台 → 地址填好并 readOnly（官方地址手打只会打错）；
    选「自定义 / 中转站」→ 放开可填，并且单独记住用户自己填的地址，
    在平台和自定义之间来回切一次不该把中转站地址弄丢。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    body = panels[panels.index("const applyProvider = ()"):]
    body = body[:body.index("f.access.addEventListener")]
    assert "f.base_url.readOnly = true" in body, "预置平台的地址要锁成只读"
    assert "f.base_url.readOnly = false" in body, "自定义要能自己填地址"
    assert "customBase" in panels, "要记住用户自己填的中转站地址"
    assert 'f.base_url.addEventListener("input"' in panels, "自定义地址要在用户改动时记下来"


def test_providers_endpoint_carries_the_preset_urls(client):
    d = client.get("/api/providers").json()
    by_key = {p["key"]: p for p in d["providers"]}
    assert by_key["deepseek"]["base_url"] == "https://api.deepseek.com/v1"
    assert by_key["openai"]["base_url"] == "", "自定义没有预置地址"
    assert by_key["mock"]["base_url"] == ""


def test_ui_files_do_not_mention_the_dead_gateway():
    """界面文案是用户唯一会读的东西：那里不该再有已停用网关的名字和地址。"""
    for name in ("app.js", "panels.js", "ui.js", "index.html"):
        body = (WEB / name).read_text(encoding="utf-8")
        hits = [ln.strip() for ln in body.splitlines() if any(w in ln for w in BANNED)]
        assert not hits, name + " 里还有：" + "; ".join(hits[:3])


def test_cli_epilogue_does_not_assume_a_local_gateway():
    body = (SRC / "cli.py").read_text(encoding="utf-8")
    hits = [ln.strip() for ln in body.splitlines() if any(w in ln for w in BANNED)]
    assert not hits, "cli 的提示里还有：" + "; ".join(hits[:3])


def test_readme_is_written_for_a_user_with_their_own_key():
    body = README.read_text(encoding="utf-8")
    hits = [ln.strip() for ln in body.splitlines() if any(w in ln for w in BANNED)]
    assert not hits, "README 还在讲那个停用的网关：" + "; ".join(hits[:3])


def test_env_example_teaches_the_current_way_to_connect():
    """.env.example 以前教的是「Base URL 填本机 8789、模型名填 auto 让网关选路、想要好图
    就去注册 Tenor Key」—— 那台网关已经停用，Tenor/Giphy 也从界面拆了（README 明说填了
    也用不上）。上面那些扫描没有一个读过它，所以代码改了三圈它还停在原地。
    这里锁三件事：① 没有已停用网关的字样，② 不劝人注册用不上的 Key，③ 列出的变量名和
    默认接入方式必须真的是程序认的那些 —— 第三条才是防下次再漂的。"""
    body = ENV_EXAMPLE.read_text(encoding="utf-8")
    for word in BANNED:
        assert word not in body, ".env.example 还在讲那个停用的网关：" + word
    for word in ("Tenor", "Giphy"):
        assert word not in body, ".env.example 别劝人为用不上的来源注册：" + word

    named = {n[len("ANIMECHAT_"):].lower() for n in re.findall(r"^#?(ANIMECHAT_[A-Z_0-9]+)=", body, re.M)}
    assert named, ".env.example 一个变量都没列，这个测试就白写了"
    # DATA_DIR 走 config._default_data_dir 里的 os.environ，不在 ENV_KEYS 里，但确实能设
    unknown = sorted(named - set(ENV_KEYS) - {"data_dir"})
    assert not unknown, ".env.example 教了程序不认的变量：" + "、".join(unknown)
    assert "llm_provider" in named and "llm_api_key" in named and "llm_model" in named, \
        "接入方式是这三格，示例里少了谁用户就配不上"
    assert "=" + providers.DEFAULT_KEY in body, "默认接入方式要跟 providers.DEFAULT_KEY 对上"

