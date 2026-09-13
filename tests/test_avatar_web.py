"""AI 生成角色时那张头像要能联网找。

以前 ai-create 只会 draw_avatar（程序画的 Q 版圆脸），用户看不出「这是谁」；
表情库早就有联网搜图那条链路（websearch），头像只是没接上。这里锁四件事：
① 搜「像」的排序规则和搜表情不一样（图标、小缩略图要往后甩）；
② 落地的图必须裁成正方形、缩到 256，横长条立绘不能直接进侧栏；
③ 离线 / 防盗链 / 图太小都不能把生成流程带崩，退回 Q 版；
④ 设置里那个开关关掉，就一次网都不联。
"""

import io
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from animechat import characters as characters_mod
from animechat import images as images_mod
from animechat import llm
from animechat import media
from animechat import server as server_mod
from animechat import websearch as w
from animechat.characters import Character, CharacterBook
from animechat.config import Settings
from animechat.server import create_app

CARD = {"name": "测试酱", "title": "来自某游戏的少女", "description": "活泼的测试角色",
        "personality": "嘴硬心软", "speaking_style": "语速快，爱用反问句",
        "catchphrases": ["真的假的"], "likes": ["唱歌"], "dislikes": ["迟到"],
        "greeting": "哼，你来啦。", "scenario": "放学后的教室",
        "example_dialogs": [{"user": "在干嘛", "char": "关你什么事"}],
        "boundaries": "不聊脏话", "sticker_style": "light"}


def async_wrap(fn):
    """把同步函数包成 awaitable，省得每个假 provider 都写一遍 async。"""
    async def go(*args, **kw):
        return fn(*args, **kw)
    return go


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def _flat(w_px: int, h_px: int, fmt: str = "PNG", color=(180, 60, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w_px, h_px), color).save(buf, format=fmt)
    return buf.getvalue()


def _center_marker() -> bytes:
    """300×160 的黑底白块：只有真的「居中裁成正方形」，白块才会留在新图正中。"""
    im = Image.new("RGB", (300, 160), (0, 0, 0))
    for x in range(140, 160):
        for y in range(70, 90):
            im.putpixel((x, y), (255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _online(monkeypatch) -> None:
    """允许联网找头像（conftest 默认关掉，免得别的测试真发请求）。"""
    monkeypatch.setenv("ANIMECHAT_AVATAR_FROM_WEB", "1")


def _fake_net(monkeypatch, cands, ok_urls=None, fails=False):
    """假搜图 + 假下载：只记录调用，不碰网络。"""
    calls = {"search": 0, "download": []}

    async def fake_search(q, settings, limit=18):
        calls["search"] += 1
        if fails:
            raise w.SearchError("没网")
        return list(cands)[:limit], "bing", ""

    async def fake_download(url, referer=""):
        calls["download"].append((url, referer))
        if ok_urls is not None and url not in ok_urls:
            raise w.SearchError("原站拒绝下载")
        return _flat(320, 320), ".png"

    monkeypatch.setattr(w, "search_portrait", fake_search)
    monkeypatch.setattr(w, "download", fake_download)
    return calls


# ------------------------------------------------------------------ 搜索


def test_portrait_query_carries_the_series():
    q = server_mod.portrait_query(Character(id="a", name="测试酱", title="来自某游戏的少女",
                                            tags=["某游戏", "二次元"]))
    assert "测试酱" in q and "某游戏" in q
    assert "立绘" in q and "portrait" in q, "中英混写才同时吃到中文站和 wikia/MAL 那批官方图"


@pytest.mark.asyncio
async def test_search_portrait_never_asks_the_gif_libraries(monkeypatch):
    """Tenor / Giphy 是动图库，搜「角色名 立绘」只会回来一堆表情包。"""
    ran: list[str] = []

    def mk(name):
        async def go(*args, **kw):
            ran.append(name)
            return []
        return go

    for fn in ("_tenor", "_giphy", "_bing", "_duckduckgo"):
        monkeypatch.setattr(w, fn, mk(fn[1:]))
    s = Settings(tenor_api_key="k", giphy_api_key="k", sticker_search_provider="auto")
    with pytest.raises(w.SearchError):
        await w.search_portrait("测试酱 立绘", s, limit=5)
    assert ran == ["bing", "duckduckgo"], "找头像只走免 Key 的图片引擎：" + str(ran)

    ran.clear()
    with pytest.raises(w.SearchError):
        await w.search("测试酱 表情", s, limit=5)
    assert ran == ["tenor", "giphy", "bing", "duckduckgo"], "搜表情照旧先问有 Key 的：" + str(ran)


@pytest.mark.asyncio
async def test_search_portrait_pushes_icons_and_tiny_thumbs_last(monkeypatch):
    good = w.RemoteSticker("角色", "https://static.wikia.nocookie.net/a.png", "", "bing")
    icon = w.RemoteSticker("站徽", "https://cdn.example.com/site-logo.png", "", "bing")
    tiny = w.RemoteSticker("缩略", "https://other.example.com/b.jpg", "", "bing", width=64, height=64)

    async def fake_bing(client, q, limit):
        return [icon, tiny, good]

    monkeypatch.setattr(w, "_bing", fake_bing)
    res, provider, _note = await w.search_portrait("测试酱 立绘", Settings(), limit=10)
    assert provider == "bing"
    assert res[0].image_url == good.image_url, "官方 wiki 的图排最前"
    assert res[-1].image_url in (icon.image_url, tiny.image_url), "图标 / 小图垫底：" + str([r.image_url for r in res])


# ------------------------------------------------------------------ 落地


def test_web_avatar_is_center_cropped_to_a_square():
    book = CharacterBook()
    char = book.save(Character(id="crop1", name="裁剪"))
    saved = book.set_avatar_from_web(char, _center_marker(), ".png")
    assert saved.avatar and saved.avatar.endswith("crop1-web.png")
    path = media.resolve(saved.avatar)
    assert path is not None and path.is_file()
    im = Image.open(path)
    assert im.size == (256, 256), "必须裁成正方形再缩到 256，侧栏里才不会是压扁的脸"
    cx, cy = im.width // 2, im.height // 2
    assert sum(im.convert("RGB").getpixel((cx, cy))) > 300, "白块还在正中 -> 裁的是中间那块"
    assert sum(im.convert("RGB").getpixel((2, 2))) < 120, "四角得是黑的"


def test_web_avatar_refuses_a_tiny_thumb():
    book = CharacterBook()
    char = book.save(Character(id="tiny1", name="小图"))
    with pytest.raises(ValueError) as exc:
        book.set_avatar_from_web(char, _flat(80, 80), ".png")
    assert "太小" in str(exc.value)
    assert char.avatar is None, "拒了就该什么都没动"


def test_web_avatar_keeps_raw_bytes_when_pillow_is_missing(monkeypatch):
    """Pillow 是可选依赖：缺了不该连头像都换不成，原样存下就行。"""
    book = CharacterBook()
    char = book.save(Character(id="nopil", name="没装Pillow"))
    monkeypatch.setattr(images_mod, "pillow", lambda: None)
    saved = book.set_avatar_from_web(char, _flat(320, 320), ".jpg")
    assert saved.avatar == "/media/avatars/nopil-web.jpg"
    assert media.resolve(saved.avatar).read_bytes() == _flat(320, 320)


def test_web_avatar_replaces_the_drawn_one_and_cleans_it_up():
    book = CharacterBook()
    char = book.ensure_avatar(book.save(Character(id="swap1", name="换脸")))
    drawn = char.avatar
    assert drawn and "gen_" in drawn
    saved = book.set_avatar_from_web(char, _flat(320, 320), ".png")
    assert saved.avatar.endswith("swap1-web.png")
    assert media.resolve(drawn) is None, "程序画的那张已经没人引用了，别留在盘上"


# ------------------------------------------------------------------ 接口


def test_avatar_search_lists_candidates_without_saving(client, monkeypatch):
    _online(monkeypatch)
    cid = client.post("/api/characters", json={"name": "卡皮酱", "title": "某游戏"}).json()["character"]["id"]
    calls = _fake_net(monkeypatch, [w.RemoteSticker("x", "https://cdn.example/a.png", "", "bing")])
    r = client.post("/api/characters/" + cid + "/avatar-search", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["results"][0]["image_url"] == "https://cdn.example/a.png"
    assert "卡皮酱" in body["query"], "q 留空时服务端得自己按角色拼关键词"
    assert calls["search"] == 1
    assert client.get("/api/characters/" + cid).json()["character"]["avatar"] is None, \
        "列候选只是给清单，点哪张才落哪张"
    assert not list(media.AVATAR_DIR.glob("*")), "搜一下不该写任何文件"


def test_avatar_search_404s_for_missing_character(client, monkeypatch):
    _online(monkeypatch)
    _fake_net(monkeypatch, [])
    assert client.post("/api/characters/nope/avatar-search", json={}).status_code == 404


def test_avatar_web_applies_the_picked_image(client, monkeypatch):
    _online(monkeypatch)
    cid = client.post("/api/characters", json={"name": "点图酱"}).json()["character"]["id"]
    calls = _fake_net(monkeypatch, [], ok_urls={"https://cdn.example/pick.png"})
    r = client.post("/api/characters/" + cid + "/avatar-web",
                    json={"image_url": "https://cdn.example/pick.png", "page": "https://ref.example/post"})
    assert r.status_code == 200, r.text
    assert r.json()["character"]["avatar"] == "/media/avatars/" + cid + "-web.png"
    assert calls["download"] == [("https://cdn.example/pick.png", "https://ref.example/post")], \
        "出处页要当 Referer 传下去，防盗链的站才下得动"


def test_avatar_web_still_refuses_internal_addresses(client, monkeypatch):
    """候选地址由搜索结果提供，SSRF 那道防护不能因为换了个新接口就绕开。"""
    cid = client.post("/api/characters", json={"name": "内网酱"}).json()["character"]["id"]
    for bad in ("http://127.0.0.1:8899/media/stickers/x.png", "file:///etc/passwd",
                "http://localhost/a.png"):
        r = client.post("/api/characters/" + cid + "/avatar-web", json={"image_url": bad})
        assert r.status_code == 400, bad
        assert client.get("/api/characters/" + cid).json()["character"]["avatar"] is None


def test_avatar_web_reports_an_unusable_image(client, monkeypatch):
    """下回来了但不是张能用的头像（太小），要说得清为什么不行。"""
    cid = client.post("/api/characters", json={"name": "坏图酱"}).json()["character"]["id"]

    async def fake_download(url, referer=""):
        return _flat(60, 60), ".png"

    monkeypatch.setattr(w, "download", fake_download)
    r = client.post("/api/characters/" + cid + "/avatar-web", json={"image_url": "https://cdn.example/x.png"})
    assert r.status_code == 400 and "太小" in r.json()["detail"]


# ------------------------------------------------------------------ ai-create


def _ready(client, monkeypatch, name="测试酱"):
    async def fake(settings, messages, *, temperature=None, max_tokens=None):
        return json.dumps(dict(CARD, name=name))

    monkeypatch.setattr(llm, "complete_chat", fake)
    client.patch("/api/settings", json={"llm_api_key": "sk-x", "llm_base_url": "https://example.test/v1"})


def test_ai_create_prefers_a_web_avatar(client, monkeypatch):
    _ready(client, monkeypatch)
    _online(monkeypatch)
    calls = _fake_net(monkeypatch, [w.RemoteSticker("立绘", "https://cdn.example/p.png", "", "bing")])
    r = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["avatar_from"] == "web"
    assert body["character"]["avatar"].endswith("-web.png")
    assert calls["search"] == 1 and len(calls["download"]) == 1
    assert not list(media.AVATAR_DIR.glob("gen_*")), \
        "既然拿到立绘了，就别再顺手画一张 Q 版留在盘上"


def test_ai_create_falls_back_to_drawn_avatar_when_offline(client, monkeypatch):
    """没网不是失败的理由：人设照样要生成，头像退回程序画的 Q 版。"""
    _ready(client, monkeypatch)
    _online(monkeypatch)
    _fake_net(monkeypatch, [], fails=True)
    body = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"}).json()
    assert body["avatar_from"] == "drawn"
    assert "gen_" in body["character"]["avatar"]


def test_ai_create_skips_a_candidate_it_cannot_use(client, monkeypatch):
    """前两张下不动（防盗链 / 裁不动这么小的图）就换第三张，别在一张图上卡住生成。"""
    _ready(client, monkeypatch)
    _online(monkeypatch)
    cands = [w.RemoteSticker("a", "https://cdn.example/a.png", "", "bing"),
             w.RemoteSticker("b", "https://cdn.example/b.png", "", "bing"),
             w.RemoteSticker("c", "https://cdn.example/c.png", "", "bing")]

    async def fake_download(url, referer=""):
        if url.endswith("a.png"):
            raise w.SearchError("原站拒绝下载")
        if url.endswith("b.png"):
            return _flat(120, 120), ".png"
        return _flat(400, 400), ".png"

    monkeypatch.setattr(w, "search_portrait", async_wrap(lambda q, s, limit=18: (cands, "bing", "")))
    monkeypatch.setattr(w, "download", fake_download)
    body = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"}).json()
    assert body["avatar_from"] == "web"
    assert body["character"]["avatar"].endswith("-web.png")


def test_ai_create_gives_up_after_a_few_bad_downloads(client, monkeypatch):
    _ready(client, monkeypatch)
    _online(monkeypatch)
    many = [w.RemoteSticker(str(i), "https://cdn.example/%d.png" % i, "", "bing") for i in range(8)]
    hits: list[str] = []

    async def fake_download(url, referer=""):
        hits.append(url)
        raise w.SearchError("都下不动")

    monkeypatch.setattr(w, "search_portrait", async_wrap(lambda q, s, limit=18: (many, "bing", "")))
    monkeypatch.setattr(w, "download", fake_download)
    body = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"}).json()
    assert len(hits) == server_mod.AVATAR_TRIES, "试几张就该收手去画 Q 版，不能把八个候选都等一遍超时"
    assert body["avatar_from"] == "drawn"


def test_ai_create_stays_offline_when_the_toggle_is_off(client, monkeypatch):
    """关掉「联网找头像」就一次请求都不该发——有人就是不想用别人的图。"""
    _ready(client, monkeypatch)          # conftest 已把 ANIMECHAT_AVATAR_FROM_WEB 设为 0

    def boom(*args, **kw):
        raise AssertionError("关了开关还去搜图")

    monkeypatch.setattr(w, "search_portrait", boom)
    body = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"}).json()
    assert body["avatar_from"] == "drawn"
    assert "gen_" in body["character"]["avatar"]


def test_toggle_off_is_reported_to_the_client(client):
    assert client.get("/api/settings").json()["settings"]["avatar_from_web"] is False
# ------------------------------------------------------------- 手动框位置


def _edges() -> bytes:
    """400×200 的黑底，左右各一块白。只有框的位置对了，才留得住对应那一块。"""
    im = Image.new("RGB", (400, 200), (0, 0, 0))
    for x0 in (20, 320):
        for x in range(x0, x0 + 60):
            for y in range(70, 130):
                im.putpixel((x, y), (255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _lum(url: str, x: int, y: int) -> int:
    path = media.resolve(url)
    assert path is not None, url
    im = Image.open(path)
    im.load()
    assert im.size == (256, 256), "头像必须是正方形，不然侧栏里就是被压扁的脸"
    return im.convert("L").getpixel((x, y))


def test_crop_rect_decides_which_half_survives():
    """居中裁在这里恰好两头都切掉——这就是「不能只靠居中」的证据。"""
    book = CharacterBook()
    char = book.save(Character(id="crop1", name="裁切"))
    left = book.set_avatar_from_web(char, _edges(), ".png", crop=(0, 0, 200))
    assert _lum(left.avatar, 60, 128) > 200, "框在左边，左边那块白就该在头像里"
    assert _lum(left.avatar, 190, 128) < 60
    mid = book.set_avatar_from_web(left, _edges(), ".png")
    assert _lum(mid.avatar, 60, 128) < 60 and _lum(mid.avatar, 190, 128) < 60


def test_crop_out_of_range_is_clamped_instead_of_erroring():
    """手拖出来的坐标差一两个像素太正常了：越界钳回来，别弹错误打断。"""
    book = CharacterBook()
    char = book.save(Character(id="crop2", name="钳边"))
    far = book.set_avatar_from_web(char, _edges(), ".png", crop=(9999, -9999, 200))
    assert _lum(far.avatar, 190, 128) > 200, "x 越界就贴到最右边"
    assert _lum(far.avatar, 60, 128) < 60


def test_tiny_crop_box_is_not_upscaled_into_mush():
    """缩略图拖出个 12px 的框，要按最小可接受的那块裁，而不是把 12px 拉到 256。"""
    book = CharacterBook()
    char = book.save(Character(id="crop3", name="放大"))
    r = book.set_avatar_from_web(char, _edges(), ".png", crop=(0, 0, 12))
    assert _lum(r.avatar, 60, 128) > 200


def test_staged_source_is_kept_at_a_sane_size():
    """官方立绘动辄 3000×1500 好几 MB，留一张侧栏 36px 的图用不着。"""
    book = CharacterBook()
    char = book.save(Character(id="stage1", name="暂存"))
    url, w, h = book.stage_avatar_source(char, _flat(3000, 1500, "JPEG"), ".jpg")
    assert url.endswith("stage1-src.png") and (w, h) == (1024, 512)
    url2, _, _ = book.stage_avatar_source(char, _edges(), ".png")
    assert url2 == url
    assert [p.name for p in media.AVATAR_DIR.glob("stage1-src*")] == ["stage1-src.png"], \
        "换一张就覆盖掉，别攒一堆原图占用户的硬盘"


def test_staging_without_pillow_keeps_the_raw_bytes(monkeypatch):
    monkeypatch.setattr(images_mod, "pillow", lambda: None)
    book = CharacterBook()
    char = book.save(Character(id="stage2", name="没装库"))
    raw = _flat(200, 120, "JPEG")
    url, w, h = book.stage_avatar_source(char, raw, ".jpg")
    assert url.endswith("stage2-src.jpg") and (w, h) == (0, 0)
    assert media.resolve(url).read_bytes() == raw


def _edges_net(monkeypatch):
    async def fake_download(url, referer=""):
        return _edges(), ".png"

    monkeypatch.setattr(w, "download", fake_download)


def test_avatar_web_stages_the_source_for_later_tweaking(client, monkeypatch):
    cid = client.post("/api/characters", json={"name": "暂存酱"}).json()["character"]["id"]
    _edges_net(monkeypatch)
    body = client.post("/api/characters/" + cid + "/avatar-web",
                       json={"image_url": "https://cdn.example/e.png"}).json()
    assert body["avatar_source"] == "/media/avatars/" + cid + "-src.png"
    assert (body["src_w"], body["src_h"]) == (400, 200)
    assert client.get(body["avatar_source"]).status_code == 200, "取景框要能直接加载这张暂存图"


def test_avatar_crop_reframes_from_disk_without_touching_the_network(client, monkeypatch):
    """「微调位置」是本机重裁：再下一遍既慢，原图也可能早就不在了。"""
    cid = client.post("/api/characters", json={"name": "微调酱"}).json()["character"]["id"]
    _edges_net(monkeypatch)
    first = client.post("/api/characters/" + cid + "/avatar-web",
                        json={"image_url": "https://cdn.example/e.png"}).json()
    assert _lum(first["character"]["avatar"], 60, 128) < 60, "居中那版两头都没留住"

    def boom(*a, **kw):
        raise AssertionError("微调位置不该再联网下载")

    monkeypatch.setattr(w, "download", boom)
    r = client.post("/api/characters/" + cid + "/avatar-crop", json={"x": 200, "y": 0, "side": 200})
    assert r.status_code == 200, r.text
    av = r.json()["character"]["avatar"]
    assert av == "/media/avatars/" + cid + "-web.png", "还是那张头像，只是重新裁了一遍"
    assert _lum(av, 190, 128) > 200, "框挪到右边，右边那块白就该进来了"


def test_avatar_crop_needs_the_staged_original(client):
    cid = client.post("/api/characters", json={"name": "没图酱"}).json()["character"]["id"]
    r = client.post("/api/characters/" + cid + "/avatar-crop", json={"x": 0, "y": 0, "side": 200})
    assert r.status_code == 400 and "原图" in r.json()["detail"]


def test_avatar_crop_rejects_junk_coordinates(client, monkeypatch):
    cid = client.post("/api/characters", json={"name": "乱填酱"}).json()["character"]["id"]
    _edges_net(monkeypatch)
    client.post("/api/characters/" + cid + "/avatar-web", json={"image_url": "https://cdn.example/e.png"})
    for bad in ({"x": "abc", "y": None, "side": 200}, {"x": 0, "y": 0, "side": 0}, {"side": 100}):
        r = client.post("/api/characters/" + cid + "/avatar-crop", json=bad)
        assert r.status_code == 400, str(bad)
    av = client.get("/api/characters/" + cid).json()["character"]["avatar"]
    assert _lum(av, 190, 128) < 60, "被拒的那几次不该把已经存好的头像改坏"


def test_character_view_says_whether_tweaking_is_possible(client, monkeypatch):
    cid = client.post("/api/characters", json={"name": "置灰酱"}).json()["character"]["id"]
    views = {c["id"]: c for c in client.get("/api/bootstrap").json()["characters"]}
    assert views[cid]["avatar_source"] is None, "没找过头像，「微调位置」就该是灰的"
    _edges_net(monkeypatch)
    client.post("/api/characters/" + cid + "/avatar-web", json={"image_url": "https://cdn.example/e.png"})
    views = {c["id"]: c for c in client.get("/api/bootstrap").json()["characters"]}
    assert views[cid]["avatar_source"].endswith("-src.png")


def test_ai_create_leaves_the_source_for_a_later_reframe(client, monkeypatch):
    """AI 生成完想挪一下位置，不该逼用户重新找一遍图。"""
    _ready(client, monkeypatch)
    _online(monkeypatch)
    _fake_net(monkeypatch, [w.RemoteSticker("立绘", "https://cdn.example/p.png", "", "bing")])
    body = client.post("/api/characters/ai-create", json={"query": "测试酱，出自某游戏"}).json()
    assert (media.AVATAR_DIR / (body["character"]["id"] + "-src.png")).is_file()

