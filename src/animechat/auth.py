"""访客口令、登录 cookie、撞库限速。

默认整套是关的（Settings.auth_enabled=False）：本机自己用不该被登录页挡一道，既有
测试也全部照旧。打开它的唯一理由是「这套界面要给陌生人访问」—— 界面上本来没有任何
鉴权，却能读走全部聊天记录、删会话、改设置里的密钥。

三条规矩值得记一下：

1. **口令不落明文**，库里只存 scrypt(盐, 口令)。明文 HTTP 下这挡不住嗅探，但挡住
   一次数据库泄露就把所有人口令带走的情况。
2. **cookie 里没有口令**，只有「访客 id . 过期时间 . HMAC」。所以撤销一个访客
   （库里删掉那行）他手上的 cookie 立刻失效，不必等到期。
3. **不能拿「来自 127.0.0.1」当免检**。走 nginx 反代以后所有外网请求在应用眼里都是
   127.0.0.1，那样门等于没关。桥接要过这道门就带 x-animechat-token。

4. **访客不需要口令**：没登记过的设备拿到的是一枚自签名的匿名 cookie（见 new_anon_id），
   各看各的会话。口令在这套里只剩一个用途——**认出主人本人**，因为只有库里的行能带 admin。
"""
from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import time

# 去掉了 0/o/1/i/l/u：口令是要念给人、也真的会被人在手机上一个字一个字敲进去的。
ALPHABET = "23456789abcdefghjkmnpqrstvwxyz"
CODE_LEN = 16          # 16 × log2(31) ≈ 79 bit：猜不出来，也短到人打得动
COOKIE_NAME = "animechat_session"
BRIDGE_HEADER = "x-animechat-token"

# scrypt 取「单次几十毫秒、内存 16MB」这档：只有登录才用，不心疼；
# 但撞库的人每一条都要付这个钱。
_N, _R, _P, _DKLEN = 16384, 8, 1, 32


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def new_secret() -> str:
    return secrets.token_urlsafe(32)


# 匿名设备的 id 前缀。库里的真访客是 store 给的 "v"+8hex，这个 "a"+16hex 不可能撞上，
# 靠前缀就能判出「这是台没登记过的设备」—— 所以它**不需要**在库里占一行。
ANON_PREFIX = "a"


def new_anon_id() -> str:
    """给一台新设备发身份。不落库：朋友点开链接就该是他自己的空页面，
    而不是先要有人替他登记。代价是这种身份**没法撤销**（无行可删），
    收口的手段只剩改签名密钥（那会让所有人的 cookie 一起作废，包括主人自己的）。"""
    return ANON_PREFIX + secrets.token_hex(8)


def is_anon(visitor_id: str) -> bool:
    v = str(visitor_id or "")
    return v.startswith(ANON_PREFIX) and len(v) > len(ANON_PREFIX)


def normalize_code(code: str) -> str:
    """大小写混着无所谓，空格多半是手机自动插的 —— 都吃掉。"""
    return "".join((code or "").split()).lower()


def hash_code(code: str) -> str:
    raw = normalize_code(code).encode("utf-8")
    salt = secrets.token_bytes(16)
    dig = hashlib.scrypt(raw, salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "scrypt$" + salt.hex() + "$" + dig.hex()


def bucket_of(code: str) -> str:
    """查表用的分桶标签：sha256 的一小段，本身不涉密（口令有 79 bit 熵，
    这 16 个十六进制位反推不出任何东西），但让登录只对相关候选跑一次 scrypt。"""
    return hashlib.sha256(("animechat-v1|" + normalize_code(code)).encode("utf-8")).hexdigest()[:16]


def verify_code(code: str, stored: str) -> bool:
    """定长比较，且任何异常形状都算不匹配 —— 脏数据不该变成放行。"""
    try:
        tag, salt_hex, dig_hex = (stored or "").split("$")
        if tag != "scrypt":
            return False
        salt, want = bytes.fromhex(salt_hex), bytes.fromhex(dig_hex)
        if not salt or not want:
            return False
        got = hashlib.scrypt(normalize_code(code).encode("utf-8"), salt=salt,
                             n=_N, r=_R, p=_P, dklen=len(want))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want)


def _mac(secret: str, body: str) -> str:
    return hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).hexdigest()


def sign(secret: str, visitor_id: str, expires: float) -> str:
    body = visitor_id + "." + str(int(expires))
    return body + "." + _mac(secret, body)


def unsign(secret: str, value: str, now: float | None = None) -> tuple[str, float] | None:
    """验签 + 验过期，通过就返回 (visitor_id, expires)，否则 None。

    没配 secret 时一律拒绝：那属于「配置没做完就上线」，宁可让人登录不上，
    也不要悄悄变成一个谁都能伪造 cookie 的门。

    比较一律转成 bytes：hmac.compare_digest 收 str 时**只接受纯 ASCII**，
    而 cookie 是外部输入 —— 传一个带非 ASCII 的值就会 TypeError，
    等于让人随手在鉴权入口上打出一个 500（实测踩过）。
    """
    if not secret:
        return None
    parts = (value or "").split(".")
    if len(parts) != 3:
        return None
    vid, exp, mac = parts
    try:
        expires = float(exp)
    except ValueError:
        return None
    if not math.isfinite(expires) or (time.time() if now is None else now) >= expires:
        return None
    if not hmac.compare_digest(mac.encode("utf-8"), _mac(secret, vid + "." + exp).encode("utf-8")):
        return None
    return vid, expires


class LoginLimiter:
    """按来源 IP 限制连续失败。没它的话，明文 HTTP + 一个能猜的口令等于挂着让人试。

    只记在进程内（这台部署是单进程 uvicorn，cli.py 没有 --workers）。重启清零 ——
    够把在线爆破抬到不可行；要防住「重启后再来一轮」就得往库里写，那份复杂度现在不值。
    """

    WINDOW = 600.0
    MAX_FAILS = 8

    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        hits = [t for t in self._fails.get(key, []) if now - t < self.WINDOW]
        if hits:
            self._fails[key] = hits
        else:
            self._fails.pop(key, None)
        return hits

    def wait_seconds(self, key: str, now: float | None = None) -> float:
        """还要等多久才让再试；0 = 现在就能试。"""
        now = time.time() if now is None else now
        hits = self._recent(key, now)
        if len(hits) < self.MAX_FAILS:
            return 0.0
        return max(0.0, self.WINDOW - (now - hits[0]))

    def fail(self, key: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        hits = self._recent(key, now)
        hits.append(now)
        self._fails[key] = hits[-self.MAX_FAILS:]

    def clear(self, key: str) -> None:
        self._fails.pop(key, None)
