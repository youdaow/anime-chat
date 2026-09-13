"""「我」这一侧也要有头像：上传 / 联网找 / 微调位置 / 撤掉，全走本机。

一个 IM 里只有自己那侧没有脸，怎么看都没做完。这里锁几件真实会坏的事：
① 非正方形上传进来必须被裁成正方形（不然气泡旁边是个被压扁的脸）；② 换头像必须让
浏览器回源（文件名一直是 me.png，没有版本号就会被缓存糊住，用户只觉得点了没反应）；
③ 微调位置只对「本机还留着原图」的头像有意义；④ 前端那格头像必须真的从设置里取。
"""

import io
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from animechat import images as images_mod
from animechat import media
from animechat import me
from animechat import websearch
from animechat.config import load_settings
from animechat.server import create_app

WEB = Path(__file__).resolve().parent.parent / "src" / "animechat" / "web"


@pytest.fixture()
def client():
    app = create_app()
    with TestClient(app) as c:
        yield c


def _img(w, h, color=(200, 40, 40), fmt="PNG"):
    """左边画一道黑条：整张同色的话，框哪儿裁出来都一模一样，
    「位置」这个参数是不是真的生效就测不出来。"""
    im = Image.new("RGB", (w, h), color)
    im.paste(Image.new("RGB", (max(1, w // 6), h), (0, 0, 0)), (0, 0))
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


def _size_of(url):
    path = media.resolve(url)
    assert path is not None, "落地文件找不到：" + url
    with Image.open(path) as im:
        return im.size


def _upload(client, name, blob, mime="image/png"):
    return client.post("/api/me/avatar", files={"file": (name, blob, mime)})


# ---------------------------------------------------------------- 上传落地

def test_uploaded_portrait_is_squashed_to_a_square(client):
    """横长条必须变成 256×256：气泡旁边那个圆只有 34px，比例不对就是压扁的脸。"""
    d = _upload(client, "wide.png", _img(900, 300)).json()
    s = d["settings"]
    assert s["user_avatar"] == "/media/avatars/me.png"
    assert _size_of(s["user_avatar"]) == (256, 256)
    assert load_settings().user_avatar == s["user_avatar"], "没落进 settings.json 就是没保存"


def test_swapping_avatar_busts_the_browser_cache(client):
    """文件名一直是 me.png，素材又是 max-age=3600：没有版本号界面看着就像「点了没反应」。"""
    one = _upload(client, "a.png", _img(400, 400)).json()["settings"]
    assert one["user_avatar_at"] > 0
    two = _upload(client, "b.png", _img(400, 400, (30, 30, 200))).json()["settings"]
    assert two["user_avatar_at"] > one["user_avatar_at"], "换了头像但版本号没动"
    off = client.post("/api/me/avatar-clear").json()["settings"]
    assert off["user_avatar"] == "" and off["user_avatar_at"] == 0
    assert media.resolve("/media/avatars/me.png") is None


def test_oversized_upload_is_refused_before_reading_pixels(client):
    """12MB 那道闸是共用的（角色头像、我的头像）：超了当场拒，别去开一张可能是
    伪装的巨型图。"""
    blob = b"\x00" * (12 * 1024 * 1024 + 1)
    r = _upload(client, "big.png", blob)
    assert r.status_code == 400 and "太大" in r.json()["detail"]
    assert load_settings().user_avatar == ""


def test_too_small_and_not_an_image_are_rejected_cleanly(client):
    bad = _upload(client, "tiny.png", _img(60, 60))
    assert bad.status_code == 400 and "太小" in bad.json()["detail"], bad.text[:200]
    junk = _upload(client, "x.png", b"not a png at all")
    assert junk.status_code == 400 and "打不开" in junk.json()["detail"]
    empty = _upload(client, "e.png", b"")
    assert empty.status_code == 400
    assert load_settings().user_avatar == "", "被拒的图不该留下半个头像"


# ---------------------------------------------------------------- 微调位置

def test_position_can_be_reframed_without_the_network(client, monkeypatch):
    """暂存原图之后要能纯本机重框：框错半张脸不该逼着人再下一次图。"""
    calls = []
    monkeypatch.setattr(websearch, "download", lambda *a, **k: calls.append(1))
    d = _upload(client, "wide.png", _img(800, 300)).json()
    assert d["avatar_source"] == "/media/avatars/me-src.png"
    assert d["settings"]["user_avatar_source"] == "/media/avatars/me-src.png", (
        "界面拿这个字段决定「微调位置」能不能按，不带上就只能点了才知道")
    # 800×300 的长边不到 1024 所以不缩，「右下角那块」在原图就是 x=500,y=0,side=300
    right = client.post("/api/me/avatar-crop", json={"x": 500, "y": 0, "side": 300}).json()
    assert _size_of(right["settings"]["user_avatar"]) == (256, 256)
    assert not calls, "重框一步都不该联网"
    # 落地文件名恒定是 me.png，所以下一次裁会覆盖这一次的字节：要比就先攒下来
    a = media.resolve(right["settings"]["user_avatar"]).read_bytes()
    left = client.post("/api/me/avatar-crop", json={"x": 0, "y": 0, "side": 300}).json()
    b = media.resolve(left["settings"]["user_avatar"]).read_bytes()
    assert a != b, "位置参数是摆设的话这两张该一模一样"
    # 左边那块带黑条（见 _img），右边那块是纯色：这才证明框真的挪了
    with Image.open(media.AVATAR_DIR / "me.png") as im:
        px = im.convert("RGB").load()
    assert px[4, 128] != px[251, 128], "最后一张应该只剩纯色：说明左边的黑条被裁掉了"


def test_crop_out_of_bounds_is_clamped_not_refused(client):
    """手拖出来的数差一两个像素太正常了：越界钳回来，别弹错误烦人。
    钳边还要钳「对」：不钳的话 Pillow 会拿黑边补齐，头像变成一块纯黑。"""
    _upload(client, "a.png", _img(500, 500))
    r = client.post("/api/me/avatar-crop", json={"x": 99999, "y": -50, "side": 10000})
    assert r.status_code == 200, r.text[:200]
    assert _size_of(r.json()["settings"]["user_avatar"]) == (256, 256)
    with Image.open(me.avatar_path()) as im:
        px = im.convert("RGB").load()
    assert px[250, 128] == (200, 40, 40), "越界之后应该贴着右边缘取，不是补一块黑：" + str(px[250, 128])


def test_crop_needs_a_stashed_source(client):
    client.post("/api/me/avatar-clear")
    r = client.post("/api/me/avatar-crop", json={"x": 10, "y": 10, "side": 200})
    assert r.status_code == 400 and "没留下原图" in r.json()["detail"]


def test_absurd_crop_numbers_are_refused(client):
    """非数字和 NaN 当场挡下；越界不挡，交给裁剪钳回来（见上一条测试）。
    NaN 得用原生 JSON 发（json.dumps 的 NaN 字面量），Python 那边是 float("nan")。"""
    _upload(client, "a.png", _img(500, 500))
    for body in ({"x": "abc", "y": 0, "side": 100}, {"x": 0, "y": 0, "side": 0}, {}):
        r = client.post("/api/me/avatar-crop", json=body)
        assert r.status_code == 400, body
    r = client.post("/api/me/avatar-crop", content=b'{"x": NaN, "y": 0, "side": 100}',
                    headers={"content-type": "application/json"})
    assert r.status_code == 400, r.text[:120]


def test_uploading_wipes_the_old_stashed_source(client, monkeypatch):
    """换图时把上一个别的格式的暂存清掉：不然攒一堆原图占用户的硬盘。
    有 Pillow 时暂存统一转成 PNG，所以这条要走「没装 Pillow」那条分支才看得到 .jpg。"""
    monkeypatch.setattr(images_mod, "pillow", lambda: None)
    _upload(client, "a.jpg", _img(400, 400, fmt="JPEG"), "image/jpeg")
    assert (media.AVATAR_DIR / "me-src.jpg").is_file()
    monkeypatch.undo()
    d = _upload(client, "b.png", _img(400, 400)).json()
    assert d["avatar_source"] == "/media/avatars/me-src.png"
    assert not (media.AVATAR_DIR / "me-src.jpg").exists(), "攒一堆原图占用户的硬盘"


# ---------------------------------------------------------------- 联网找

def test_web_pick_downloads_and_reports_source_size(client, monkeypatch):
    got = {}

    async def fake_search(q, s, limit=18):
        got["q"] = q
        item = websearch.RemoteSticker(label="候选", image_url="http://x/a.png",
                                      thumb_url="http://x/t.png", source="x",
                                      page="http://x/p", width=600, height=400)
        return [item], "bing", ""

    async def fake_download(url, referer=""):
        return _img(600, 400), ".png"

    monkeypatch.setattr(websearch, "search_portrait", fake_search)
    monkeypatch.setattr(websearch, "download", fake_download)
    d = client.post("/api/me/avatar-search", json={"q": "白发少女 头像"}).json()
    assert d["results"][0]["image_url"] == "http://x/a.png" and got["q"] == "白发少女 头像"
    w = client.post("/api/me/avatar-web", json={"image_url": "http://x/a.png", "page": "http://x/p"}).json()
    assert _size_of(w["settings"]["user_avatar"]) == (256, 256)
    assert w["src_w"] == 600 and w["src_h"] == 400, "报回原图尺寸，前端才换算得出取景框"
    assert w["avatar_source"] == "/media/avatars/me-src.png"


def test_search_without_a_word_does_not_query_the_network(client, monkeypatch):
    """没名字也没关键词时别拿空串去搜（结果全是垃圾），直接让人填。"""
    hit = []

    async def fake_search(q, s, limit=18):
        hit.append(q)
        return [], "bing", ""

    monkeypatch.setattr(websearch, "search_portrait", fake_search)
    client.patch("/api/settings", json={"user_name": "你"})
    r = client.post("/api/me/avatar-search", json={})
    assert r.status_code == 400 and "搜" in r.json()["detail"]
    assert not hit


def test_my_real_name_is_used_as_the_default_query(client, monkeypatch):
    """填过「我的名字」就不用再想关键词：拿它去搜，省一次输入。"""
    seen = []

    async def fake_search(q, s, limit=18):
        seen.append(q)
        return [], "bing", ""

    monkeypatch.setattr(websearch, "search_portrait", fake_search)
    client.patch("/api/settings", json={"user_name": "小明"})
    d = client.post("/api/me/avatar-search", json={}).json()
    assert seen == ["小明"] and d["query"] == "小明"


# ---------------------------------------------------------------- 没 Pillow 时

def test_upload_still_works_without_pillow(client, monkeypatch):
    """Pillow 是可选依赖：缺了不该连头像都换不成，原样存下就行。"""
    monkeypatch.setattr(images_mod, "pillow", lambda: None)
    raw = _img(320, 320)
    d = _upload(client, "a.jpg", raw, "image/jpeg").json()
    assert d["settings"]["user_avatar"] == "/media/avatars/me.jpg"
    assert media.resolve(d["settings"]["user_avatar"]).read_bytes() == raw


def test_staging_without_pillow_reports_zero_size(client, monkeypatch):
    monkeypatch.setattr(images_mod, "pillow", lambda: None)
    raw = _img(200, 120)
    url, w, h = me.stage_source(raw, ".jpg")
    assert url.endswith("me-src.jpg") and (w, h) == (0, 0)
    assert media.resolve(url).read_bytes() == raw


# ---------------------------------------------------------------- 前端接线

def test_bootstrap_carries_what_the_bubble_needs(client):
    s = client.get("/api/bootstrap").json()["settings"]
    for key in ("user_name", "user_avatar", "user_avatar_at", "user_avatar_source"):
        assert key in s, key


def test_own_bubble_actually_renders_an_avatar():
    """后端存得再好，前端不画也是零：锁三处接线而不是某句文案。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    css = (WEB / "style.css").read_text(encoding="utf-8")
    assert "meProfile()" in app and "avatar: s.user_avatar" in app, "自己的气泡没从设置里取头像"
    assert 'me ? avatarNode(meProfile(), "avatar sm")' in app, "消息行要在自己那侧摆一个头像节点"
    assert 'updated_at: s.user_avatar_at' in app, "拿不到版本号就等于换完头像还是旧图"
    assert "/api/me/avatar" in panels and "/api/me/avatar-clear" in panels, "设置里没有落地的接口"
    assert "cur().user_avatar_source" in panels, "「微调位置」得按设置里的字段决定能不能按"
    assert '"/api/me/avatar" + name' in panels, "找候选那套弹窗要能打在 /api/me 上（和角色共用一套）"
    m = re.search(r'[.]me-avatar input\[type="file"\] \{[^}]*\}', css)
    assert m and "width: auto" in m.group(0), \
        "文件框必须自己收着：.field input 的 width:100% 会把它拉成一条、撑破整个设置面板"


def test_shared_dialogs_never_touch_a_null_char():
    """找候选 / 微调位置这两个弹窗角色和「我自己」共用一套，进来时 target 是
    {me: true}、char 就是 null。以前标题那行直接写了 char.name，于是自己这一侧
    点「微调位置」当场 TypeError、弹窗根本不出现（单测碰不到，浏览器一点就炸）。
    所以把规矩钉住：这两个函数里每一次 char 的解引用，同一行必须出现 isMe。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    # 按「下一个 export function」切出函数体，别把后面别的函数也扫进来
    bounds = [m.start() for m in re.finditer(r"^export function (\w+)", panels, re.M)]
    regions = []
    for name in ("openAvatarSearch", "openAvatarCrop"):
        head = panels.index("export function " + name)
        after = [b for b in bounds if b > head]
        regions.append(panels[head:after[0] if after else len(panels)])
    counts = []
    checked = 0
    for region in regions:
        lines = region.splitlines()
        hits = [i for i, ln in enumerate(lines) if re.search(r"\bchar\s*\.", ln)]
        counts.append(len(hits))
        for i in hits:
            checked += 1
            # 三元允许跨行写（ep() 就是这样），所以看这一行连同上文 2 行、下文 1 行
            window = " ".join(lines[max(0, i - 2):i + 2])
            assert "isMe" in window, \
                "自己这一侧 char 是 null，这行会当场抛错：" + lines[i].strip()
    assert min(counts) >= 3, f"这测试得真的在看代码，不能退化成空转（两个弹窗各 {counts} 行）"


def test_settings_patch_cannot_forge_the_avatar_field(client):
    """user_avatar 只能由那几条接口写：否则任何一次 PATCH 设置都能塞进一个任意路径。"""
    r = client.patch("/api/settings", json={"user_avatar": "/media/stickers/../../secret"})
    assert r.json()["settings"]["user_avatar"] == "", "设置接口不该接受这个字段"
    assert me.avatar_path().is_file() is False


def test_clearing_leaves_no_secret_or_stray_file(client):
    client.patch("/api/settings", json={"llm_api_key": "sk-super-secret-value"})
    _upload(client, "a.png", _img(400, 400))
    s = client.post("/api/me/avatar-clear").json()["settings"]
    assert "super" not in json.dumps(s, ensure_ascii=False)
    assert "…" in s["llm_api_key"] or s["llm_api_key"] == ""
    assert not list(media.AVATAR_DIR.glob("me*")), "撤掉头像就该把本机文件也清干净"
