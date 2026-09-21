"""认证：默认关、口令、cookie、访客隔离、桥接令牌、限速。

跑法：`.venv\\Scripts\\python.exe -m pytest tests/test_auth.py -q`
"""
import re
import time

import pytest
from fastapi.testclient import TestClient

from animechat import auth
from animechat.cli import main as cli_main
from animechat.config import Settings
from animechat.server import create_app
from animechat.store import store

SECRET = "unit-test-session-secret"
BRIDGE = "unit-test-bridge-token"


def on(**over) -> TestClient:
    """开认证的客户端。用 settings_override 而不是写 settings.json：这几个测试要的是
    「门的行为」，不是配置的持久化。"""
    base = dict(auth_enabled=True, auth_session_secret=SECRET, auth_bridge_token=BRIDGE)
    base.update(over)
    return TestClient(create_app(settings_override=Settings(**base)))


def make_visitor(name="小明", admin=False):
    code = auth.new_code()
    vid = store().add_visitor(name, auth.hash_code(code), auth.bucket_of(code), admin=admin)
    return vid, code


def login(client, code):
    return client.post("/api/login", json={"code": code})


# ------------------------------------------------------------------ 纯函数
def test_code_hash_roundtrip_and_shape():
    code = auth.new_code()
    stored = auth.hash_code(code)
    assert stored.startswith("scrypt$") and stored.count("$") == 2
    assert code not in stored
    assert auth.verify_code(code, stored)
    assert auth.verify_code(code.upper() + " ", stored), "人敲口令会有大小写和空格"
    assert not auth.verify_code(auth.new_code(), stored)
    for junk in ("", "scrypt$", "scrypt$zz$zz", "plain$aa$bb", "scrypt$ab$"):
        assert not auth.verify_code(code, junk), junk


def test_cookie_signature_rejects_forgery_and_expiry():
    tok = auth.sign(SECRET, "v1", time.time() + 600)
    assert auth.unsign(SECRET, tok) is not None
    assert auth.unsign("另一个密钥", tok) is None, "换了密钥就不能继续认旧 cookie"
    assert auth.unsign(SECRET, tok, now=time.time() + 700) is None, "过期要拒"
    a, b, c = tok.split(".")
    assert auth.unsign(SECRET, f"{a}.9999999999.{c}") is None, "改过期时间必须把签名打坏"
    assert auth.unsign("", tok) is None, "没配密钥时一律拒绝，不能退化成无签名放行"
    assert auth.unsign(SECRET, "不是一(cookie)") is None
    assert auth.sign(SECRET, "v1", time.time() + 60).split(".")[0] == "v1"


def test_limiter_blocks_after_repeated_failures():
    lim = auth.LoginLimiter()
    assert lim.wait_seconds("ip") == 0
    for _ in range(auth.LoginLimiter.MAX_FAILS):
        lim.fail("ip")
    assert lim.wait_seconds("ip") > 0
    assert lim.wait_seconds("别的ip") == 0, "一个访客被限不该牵连别人"
    lim.clear("ip")
    assert lim.wait_seconds("ip") == 0


# ------------------------------------------------------------------ 门
def test_auth_off_by_default_leaves_everything_open():
    client = TestClient(create_app(settings_override=Settings()))
    assert client.get("/api/bootstrap").status_code == 200
    assert client.get("/api/conversations").status_code == 200


def test_unauthenticated_requests_are_refused_or_redirected():
    client = on()
    assert client.get("/api/bootstrap", follow_redirects=False).status_code == 401
    assert client.get("/api/conversations").status_code == 401
    assert client.post("/api/chat", json={"conversation_id": 1, "content": "hi"}).status_code == 401
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/api/health").status_code == 200, "探针不该被挡（它只报版本和计数）"


def test_login_sets_a_working_cookie_and_wrong_code_does_not():
    client = on()
    vid, code = make_visitor("阿强")
    assert login(client, code + "错").status_code == 401
    assert login(client, code.upper()).status_code == 200, "口令大小写不敏感"
    cookie = client.cookies.get(auth.COOKIE_NAME)
    assert cookie and code not in cookie, "cookie 里放的是签名，不是口令本身"
    assert client.get("/api/bootstrap").status_code == 200
    assert login(client, code).status_code == 200
    assert client.get("/api/bootstrap").status_code == 200


def test_plaintext_code_never_hits_the_database():
    client = on()
    _, code = make_visitor("小美")
    raw = store().path.read_bytes() if store().path.exists() else b""
    assert code.encode() not in raw
    assert auth.hash_code(code).encode() not in raw, "摘要本身也不该被当口令存第二份"


def test_brute_force_lockout_per_source():
    client = on()
    make_visitor("小莫")
    for _ in range(auth.LoginLimiter.MAX_FAILS):
        assert login(client, "aaaaaaaaaaaaaaaa").status_code == 401
    blocked = login(client, "aaaaaaaaaaaaaaaa")
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers


def test_forged_cookie_gets_nothing():
    client = on()
    vid, _ = make_visitor("小贵")
    client.cookies.set(auth.COOKIE_NAME, auth.sign("不是那个密钥", vid, time.time() + 600))
    assert client.get("/api/bootstrap").status_code == 401


# ------------------------------------------------------------------ 隔离
def test_two_visitors_cannot_see_each_other():
    a, acode = make_visitor("甲")
    b, bcode = make_visitor("乙")
    ca, cb = on(), on()
    assert login(ca, acode).status_code == 200 and login(cb, bcode).status_code == 200

    cid = ca.post("/api/conversations", json={"character_id": _first_char()}).json()["conversation"]["id"]
    assert cid in [c["id"] for c in ca.get("/api/conversations").json()["conversations"]]
    mine = [c["id"] for c in cb.get("/api/conversations").json()["conversations"]]
    assert cid not in mine, "乙的列表里不该出现甲的会话"
    assert cb.get(f"/api/conversations/{cid}").status_code == 404
    assert cb.post(f"/api/conversations/{cid}/read").status_code == 404
    assert cb.post("/api/chat", json={"conversation_id": cid, "content": "偷看"}).status_code == 404, \
        "会话 id 在请求体里，门口按路径查不到，端点里必须自己挡"
    assert ca.post("/api/chat", json={"conversation_id": cid, "content": "你好"}).status_code == 200
    assert ca.get(f"/api/conversations/{cid}").json()["conversation"]["owner"] == a


def test_visitor_cannot_edit_shared_library_but_can_chat():
    _, code = make_visitor("小丁")
    client = on()
    assert login(client, code).status_code == 200
    assert client.post("/api/characters", json={"name": "塞一个"}).status_code == 403
    assert client.patch("/api/settings", json={"user_name": "改成他"}).status_code == 403
    assert client.get("/api/characters").status_code == 200, "角色库是共享的，要能读才能聊"
    cid = client.post("/api/conversations", json={"character_id": _first_char()}).json()["conversation"]["id"]
    assert client.post("/api/chat", json={"conversation_id": cid, "content": "在吗"}).status_code == 200


def test_admin_visitor_is_the_host_and_sees_everything():
    """`--admin` 不是"权限大一点的访客"，而是主人本人的另一个登录：
    他新建的会话归主机（owner 空串，和本机自己建的同一格），并且看得见所有人的会话。
    这是刻意的 —— 不然他换个设备登录就看不到自己的历史了。
    代价是：别随手 --admin，那等于把别人的聊天记录也交给他看。"""
    vid, code = make_visitor("主人", admin=True)
    client = on()
    assert login(client, code).status_code == 200
    cid = client.post("/api/conversations", json={"character_id": _first_char()}).json()["conversation"]["id"]
    assert client.get(f"/api/conversations/{cid}").json()["conversation"]["owner"] == ""

    # 普通访客的会话，主人这份看得见
    other, other_code = make_visitor("普通访客")
    v = on()
    login(v, other_code)
    hidden = v.post("/api/conversations", json={"character_id": _first_char()}).json()["conversation"]["id"]
    ids = [c["id"] for c in client.get("/api/conversations").json()["conversations"]]
    assert hidden in ids and cid in ids
    # 反过来不行：访客看不见主人的
    assert cid not in [c["id"] for c in v.get("/api/conversations").json()["conversations"]]
    # 没开认证时创建的，也是「本人」那一格
    plain = TestClient(create_app(settings_override=Settings()))
    cid2 = plain.post("/api/conversations", json={"character_id": _first_char()}).json()["conversation"]["id"]
    assert plain.get(f"/api/conversations/{cid2}").json()["conversation"]["owner"] == ""


def test_bridge_token_bypasses_the_cookie_gate():
    client = on()
    assert client.get("/api/bootstrap").status_code == 401
    assert client.get("/api/bootstrap", headers={auth.BRIDGE_HEADER: BRIDGE}).status_code == 200
    assert client.get("/api/bootstrap", headers={auth.BRIDGE_HEADER: BRIDGE + "x"}).status_code == 401


def test_revoking_a_visitor_kills_his_cookie_immediately():
    vid, code = make_visitor("待撤")
    client = on()
    assert login(client, code).status_code == 200
    assert client.get("/api/bootstrap").status_code == 200
    assert store().revoke_visitor(vid) is True
    assert client.get("/api/bootstrap").status_code == 401, "cookie 还有效但人已经没了"


def test_character_view_previews_are_scoped_to_the_visitor():
    """角色列表上的「上次聊了什么 / 红点」也是内容 —— 不能跨人漏。"""
    a, ac = make_visitor("丙")
    b, bc = make_visitor("丁")
    ca, cb = on(), on()
    login(ca, ac)
    login(cb, bc)
    char = _first_char()
    ca.post("/api/conversations", json={"character_id": char})
    ca.post("/api/chat", json={"conversation_id": _last_conv_id(ca), "content": "只有甲知道的一句话"})
    view_a = next(v for v in ca.get("/api/characters").json()["characters"] if v["id"] == char)
    view_b = next(v for v in cb.get("/api/characters").json()["characters"] if v["id"] == char)
    assert view_a["last_at"] > 0 and "只有甲知道" in view_a["last_preview"]
    assert view_b["last_at"] == 0 and view_b["last_preview"] == "", "乙不该看到甲的预览"


# ------------------------------------------------------------------ CLI
def test_cli_invite_verbs_match_what_the_docs_say():
    """第一版把 name 做成裸位置参数（`invite 小明`），而 README / 帮助里写的是
    `invite add 小明` —— 命令上了服务器才炸。所以四种动词都要能解析，
    少动词的写法必须明确报错，而不是悄悄建出一个叫 "add" 的访客。"""
    for argv in (["invite", "list"], ["invite", "token"], ["invite", "revoke", "vdeadbeef"]):
        try:
            cli_main(argv)
        except SystemExit as exc:
            raise AssertionError("%s 不该解析失败：%s" % (" ".join(argv), exc))
    with pytest.raises(SystemExit):
        cli_main(["invite", "小明"])          # 少了 add 就别猜意图（它会去建一个叫小明的访客）


def test_cli_invite_add_issues_a_code_that_actually_logs_in(capsys):
    assert cli_main(["invite", "add", "小明"]) == 0
    out = capsys.readouterr().out
    m = re.search(r"访问口令[:：]\s*(\S+)", out)
    assert m, out
    code = m.group(1)
    client = TestClient(create_app(settings_override=Settings(
        auth_enabled=True, auth_session_secret=_secret_from_store(), auth_bridge_token=BRIDGE)))
    assert login(client, code).status_code == 200, "CLI 发的口令要真能登录"
    assert client.get("/api/bootstrap").status_code == 200

    capsys.readouterr()
    assert cli_main(["invite", "list"]) == 0
    listing = capsys.readouterr().out
    assert "小明" in listing and code not in listing, "列表里不该再出现口令"

    vid = re.search(r"(v[0-9a-f]{8})", out).group(1)
    capsys.readouterr()
    assert cli_main(["invite", "revoke", vid]) == 0
    assert client.get("/api/bootstrap").status_code == 401, "撤销要当场生效"


def _secret_from_store() -> str:
    from animechat.config import load_settings
    return load_settings().auth_session_secret


def _first_char() -> str:
    client = TestClient(create_app(settings_override=Settings()))
    chars = client.get("/api/characters").json()["characters"]
    if not chars:
        client.post("/api/characters", json={"name": "测试角色"})
        chars = client.get("/api/characters").json()["characters"]
    return chars[0]["id"]


def _last_conv_id(client) -> int:
    return max(c["id"] for c in client.get("/api/conversations").json()["conversations"])
