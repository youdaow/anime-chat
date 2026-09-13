"""错误提示：只给能落到动作上的建议，而且不再假设用户有一台本机 AI Hub 网关。

这个文件以前专门测「同一个错误码指向本机 Hub 和直连公网要给两种说法」。Hub 已经停用，
那套分叉反而坑人：自建中转站常常就跑在 127.0.0.1 上，按老逻辑会被提示「去 Hub 建
sk-hub-… Token」。现在只剩一张表，顺便锁住 Hub 的字样不许再漏回来。
"""

import pytest

from animechat import llm
from animechat.config import Settings
from animechat.llm import HINTS, LLMError, hint_for


def test_no_hub_left_over_in_the_error_layer():
    """报错提示是给用户看的：不能再出现「去 Hub 建 sk-hub-… Token」「填 auto 让网关选路」
    这类只有那个已停用网关才成立的指引。（模块顶上的历史说明写明了它停了，那是给人读源码的。）"""
    for text in HINTS.values():
        assert "Hub" not in text and "sk-hub" not in text and "入口 Token" not in text, text
    probe = "http://127.0.0.1:8789/v1"      # 拿已停的那个地址当样本问一遍
    for code in HINTS:
        got = hint_for(code, probe)
        assert "Hub" not in got and "sk-hub" not in got, code
        assert "8789" not in got, "把停用的端口号回读给用户看没有意义：" + code


def test_every_hint_is_actionable():
    for code, text in HINTS.items():
        assert len(text) > 8, code
        assert "：" in text, "提示得是「现象：该怎么做」这个形状：" + code


def test_host_is_substituted_and_never_leaks_a_template():
    text = hint_for("invalid_api_key", "https://api.deepseek.com/v1")
    assert "api.deepseek.com" in text
    assert "{host}" not in hint_for("timeout", "http://47.254.207.200:18787/v1")


def test_local_relay_gets_the_same_advice_as_public_one():
    """自建中转站常跑在 127.0.0.1：不能因为地址是本机就换一套说法让人去找网关界面。"""
    a = hint_for("invalid_api_key", "http://127.0.0.1:18787/v1")
    assert "127.0.0.1" in a and "Hub" not in a and "Token" not in a
    b = hint_for("model_not_found", "http://127.0.0.1:18787/v1")
    assert "auto" not in b, "auto 是那个网关的路由功能，现在已经没有会认它的东西了"


def test_missing_config_says_what_to_fill():
    assert "Base URL" in hint_for("no_base_url", "")
    assert "模型" in hint_for("no_model", "")


def test_unknown_code_has_no_fake_advice():
    assert hint_for("totally_unknown_code", "https://api.deepseek.com/v1") == ""


def test_status_codes_map_to_meaning():
    assert LLMError.from_response(401, '{"error":{"message":"bad"}}', 'https://a.t/v1').code == 'invalid_api_key'
    assert LLMError.from_response(404, '{}', 'https://a.t/v1').code == 'model_not_found'
    assert LLMError.from_response(429, '{}', 'https://a.t/v1').code == 'rate_limit_exceeded'


def test_empty_error_body_gets_a_readable_message():
    """中转站回 503 且响应体为空时，别让界面显示一个裸的 upstream_error。"""
    err = LLMError.from_response(503, "", "http://47.254.207.200:18787/v1")
    assert "503" in str(err) and "空" in str(err)
    assert err.as_dict()["message"] == str(err)
    got = LLMError.from_response(502, '{"error":{"message":"bad gateway"}}', 'http://47.254.207.200:18787/v1')
    assert str(got) == "bad gateway"


def test_gateway_routing_info_is_still_read():
    """对面要真是会选路的网关，attempts 还得显示得出来 —— 摘 Hub 不该把这个能力一起砍掉。"""
    assert llm._extract_attempts({"hub": {"attempts": [{"model": "a", "status": 200}]}})
    assert llm._extract_attempts({"choices": []}) == []


def test_empty_base_url_and_model_fail_loudly():
    """默认值现在是空的（用户自带地址），空着必须当场说清缺什么，不能兜底成 auto。"""
    with pytest.raises(LLMError) as e1:
        llm._url(Settings(llm_base_url=""), "/models")
    assert e1.value.code == "no_base_url"
    with pytest.raises(LLMError) as e2:
        llm._payload(Settings(llm_base_url="https://a.t/v1", llm_api_key="k", llm_model="  "),
                     [{"role": "user", "content": "hi"}], stream=False)
    assert e2.value.code == "no_model"
    ok = llm._payload(Settings(llm_base_url="https://a.t/v1", llm_api_key="k", llm_model="m1"),
                      [{"role": "user", "content": "hi"}], stream=False)
    assert ok["model"] == "m1"
