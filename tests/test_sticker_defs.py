"""联网抓回来的表情凭什么「加了却不发」。

角色挑表情有两条路：模型写 [sticker:标签] 时按标签搜，没写时按情绪兜底挑一张 —— 两条路都要吃
「定义」。而 /api/stickers/import 以前把远程文件名（一串 CDN 哈希）当 label、把搜索词当唯一标签、
情绪判不出来就记 neutral：提示词里是一串乱码，情绪兜底又永远绕开它。用户库里那 19 张「杂鱼」
就是这么躺着的。这里锁三层：认情绪的别名层、入库时现场起定义、以及把库里已有的坏定义补回来。
"""

import io
import json
import zlib

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from animechat import emotion as emo
from animechat import prompt
from animechat import websearch as w
from animechat.config import user_sticker_dir
from animechat.models import Sticker
from animechat.server import create_app
from animechat.stickerdef import backfill, clean, define, looks_junk
from animechat.stickers import StickerLibrary


def _png_bytes(seed: str) -> bytes:
    """不同名字不同颜色：免得被按内容去重合成一张。"""
    h = zlib.crc32(seed.encode("utf-8"))
    im = Image.new("RGBA", (24, 24), (200 + h % 55, 90 + (h >> 8) % 160, 120 + (h >> 16) % 130, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def meta_path():
    return user_sticker_dir().parent / "stickers_meta.json"


def meta_of(name: str) -> dict:
    if not meta_path().is_file():
        return {}
    data = json.loads(meta_path().read_text(encoding="utf-8"))
    got = data.get(name)
    return got if isinstance(got, dict) else {}


def seed(name: str, info: dict) -> str:
    """丢一张图 + 手写一份 meta，模拟「以前那次联网导入」留在盘上的样子。"""
    d = user_sticker_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(_png_bytes(name))
    data = json.loads(meta_path().read_text(encoding="utf-8")) if meta_path().is_file() else {}
    data[name] = info
    meta_path().write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    lib = StickerLibrary()          # 构造即扫库，坏定义应该在这一步被补好
    sid = next((k for k, p in lib.files.items() if p.name == name), None)
    assert sid, "没扫到这张图：" + name
    return sid


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


# ------------------------------------------------------------------ 别名层


def test_the_reply_lexicon_cannot_name_a_sticker():
    """两套必须分开：LEXICON 判「这句话什么情绪」，标签里那些词它一个都不认。
    拿它去认标签，联网抓回来的表情就全被判成 neutral。"""
    for text in ("杂鱼", "tsundere gif", "傲娇"):
        assert emo.detect(text)[0] == "neutral", text
        assert emo.guess(text) == "tsundere", text


def test_guess_prefers_the_more_specific_word():
    assert emo.guess("困惑 问号") == "think", "「困惑」里含着「困」，按字面命中会判成困倦"


def test_english_alias_has_to_be_a_whole_word():
    assert emo.guess("sad") == "sad"
    # 子串匹配会把这两个判成 sad / happy —— 词边界不是可有可无的
    assert emo.guess("sadly") == "neutral", "sadly 里含 sad，但它在说的是「遗憾地」"
    assert emo.guess("unhappy") == "neutral", "unhappy 里含 happy，意思正好相反"


def test_guess_refuses_to_invent():
    assert emo.guess("柴田雪成") == "neutral"
    assert emo.guess("") == "neutral"


# ------------------------------------------------------------------ 起定义


def test_a_cdn_hash_is_not_a_definition():
    """左边这些是用户库里真实存在的 label：远程站哈希 / base62 串 / 手机导出名。"""
    for junk in ("b284e22577144f5daa67", "9lddQjsw k5ltZcT3cSs", "67b145e9b7102657.jpg",
                 "d24225fdc27b776497fc", "IMG_2024.png", "", "  "):
        assert looks_junk(junk), junk
    for ok in ("杂鱼", "太色情了", "tsundere", "smile_face", "快叫我主人"):
        assert not looks_junk(ok), ok


def test_define_turns_a_search_query_into_a_usable_definition():
    d = define(query="杂鱼", title="杂鱼 - 高清表情包 - 千图网", filename="b284e22577144f5daa67.png")
    assert d["label"] == "杂鱼"
    assert d["emotion"] == "tsundere", "情绪判出来才有机会被按情绪挑中"
    assert "傲娇" in d["tags"], "情绪中文名要进标签，模型写「傲娇」也找得到这张"
    assert not any(looks_junk(t) for t in d["tags"]), str(d["tags"])
    joined = " ".join(d["tags"])
    assert "千图网" not in joined and "高清表情包" not in joined, "站点名和废话不该变成标签"


def test_define_keeps_what_was_already_good():
    d = define(label="太色情了", tags=["太色情了", "害羞"], emotion="awkward", filename="IMG_2024.png")
    assert d["label"] == "太色情了" and d["emotion"] == "awkward"
    assert d["tags"][:2] == ["太色情了", "害羞"], "用户给的标签排前面，派生的往后放"


def test_define_falls_back_to_the_emotion_name_for_english_only():
    d = define(query="tsundere gif", title="Tsundere PNGs & 4K Images", filename="x.png")
    assert d["label"] == "傲娇", "提示词是中文的，别把英文站名当名字塞进去"
    assert "tsundere" in d["tags"], "英文原词留着，模型写哪种都能命中"


def test_clean_strips_site_noise_but_keeps_meaning():
    assert clean("哭 表情包") == ["哭"]
    assert clean("高清表情包") == []
    assert clean("千图网") == [], "剥到最后剩个「千」是残渣不是词"
    assert clean("67b145e9b7102657.jpg") == []


# ------------------------------------------------------------------ 库里的效果


def test_emotions_property_reads_the_tags():
    st = Sticker(id="x", label="b284e22577144f5daa67", tags=["杂鱼"], emotion="neutral", url="/m/x.png")
    assert "tsundere" in st.emotions, "字段是 neutral，可标签写着「杂鱼」"


def test_the_same_definition_is_listed_only_once_in_the_vocab():
    """联网一次搜进来十几张同样的「杂鱼」，词表不该被撑成十几条。"""
    lib = StickerLibrary()
    for n in ("dup-a", "dup-b", "dup-c"):
        src = user_sticker_dir() / (n + "-src.png")
        src.write_bytes(_png_bytes(n))
        lib.add_file(src, tags=["杂鱼", "傲娇"], label="杂鱼", emotion="tsundere")
    entries = prompt.sticker_vocab(lib).split("、")
    same = [e for e in entries if e.startswith("杂鱼/傲娇")]
    assert len(same) == 1, "同一定义只列一次：" + str(same)
    assert len(entries) == len(lib.all()) - 2, "其余每张还是要各占一条"


def test_a_web_sticker_with_a_bad_field_still_gets_sent():
    """用户报的那件事：表情加进去了，角色从来不发。"""
    sid = seed("zako.jpg", {"label": "b284e22577144f5daa67", "emotion": "neutral",
                           "tags": ["杂鱼"], "origin": "url",
                           "note": "https://cdn.example/b284e22577144f5daa67.png"})
    lib = StickerLibrary()
    st = lib.get(sid)
    assert st.label == "杂鱼" and st.emotion == "tsundere", "扫库时该把坏定义补上"
    assert lib.pick_for_emotion("tsundere").id == sid, "按情绪兜底挑图要能挑到它"
    assert lib.search("杂鱼")[0].id == sid
    assert lib.resolve("杂鱼").id == sid, "模型写 [sticker:杂鱼] 也走得通"


def test_an_alias_query_pulls_in_the_whole_emotion():
    """搜「杂鱼」时，只标了「傲娇」的那张也该被认出来 —— 这是别名加权在起作用，
    不是字符重叠碰上的。"""
    sid = seed("zako3.png", {"label": "abcdef123456", "emotion": "neutral",
                            "tags": ["杂鱼"], "origin": "url"})
    lib = StickerLibrary()
    src = user_sticker_dir() / "nu-src.png"
    src.write_bytes(_png_bytes("nu"))
    other = lib.add_file(src, tags=["傲娇"], label="傲娇", emotion="tsundere")
    ids = [s.id for s in lib.search("杂鱼")]
    assert ids[0] == sid, "标签里就写着杂鱼的排最前"
    assert other.id in ids, "同情绪的别那张也得认出来，不然换个说法就又搜空了"


def test_a_user_kept_neutral_sticker_is_still_pickable_by_its_tags():
    """自动补定义管不到的那张（用户自己标过、字段就是 neutral），按情绪挑图也得靠
    emotions 从标签里认出来 —— 不然这条兜底路就只认字段，等于又回到加进去不发的老路。"""
    sid = seed("kept.png", {"label": "我自己起的名字", "emotion": "neutral",
                           "tags": ["杂鱼", "得意"], "defined": "user"})
    lib = StickerLibrary()
    assert lib.get(sid).emotion == "neutral", "用户填的情绪没被自动改"
    assert lib.pick_for_emotion("tsundere").id == sid, "但按情绪兜底挑图时它得在候选里"


def test_backfill_marks_it_and_then_stops_touching():
    seed("zako2.png", {"label": "9lddQjsw k5ltZcT3cSs", "emotion": "neutral",
                      "tags": ["抱抱 贴贴"], "origin": "url"})
    first = meta_of("zako2.png")
    assert first["defined"] == "auto" and first["emotion"] == "love"
    StickerLibrary()      # 再扫一次
    assert meta_of("zako2.png") == first, "补过一次就该稳住，别每扫一次库改一遍"


def test_names_the_user_gave_are_left_alone():
    seed("mine.png", {"label": "我自己起的名字", "emotion": "neutral",
                     "tags": ["随便"], "defined": "user"})
    info = meta_of("mine.png")
    assert info["label"] == "我自己起的名字" and info["emotion"] == "neutral"


def test_stickers_that_already_have_a_definition_are_not_touched():
    seed("good.png", {"label": "太色情了", "emotion": "awkward",
                     "tags": ["太色情了", "害羞"], "origin": "upload"})
    assert "defined" not in meta_of("good.png"), "没改过就别往人家 meta 里塞键"


def test_backfill_has_nothing_to_do_when_the_query_says_nothing():
    sid = seed("plain.png", {"label": "abcdef123456", "emotion": "neutral",
                            "tags": ["zzz"], "origin": "url"})
    st = StickerLibrary().get(sid)
    assert st.emotion == "neutral", "认不出情绪就老实留着 neutral，别硬猜成开心"


def test_patch_counts_as_a_user_definition(tmp_path):
    lib = StickerLibrary()
    src = tmp_path / "web-1.png"
    src.write_bytes(_png_bytes("web-1"))
    st = lib.add_file(src, tags=["杂鱼"], label="abcdef123456", emotion="neutral", origin="url")
    name = lib.files[st.id].name
    lib.patch(st.id, emotion="happy")
    assert meta_of(name)["defined"] == "user"
    again = StickerLibrary()
    assert again.get(st.id).emotion == "happy", "用户改完的情绪不该被自动补定义改回去"


# ------------------------------------------------------------------ 接口


def test_import_endpoint_stores_a_definition_not_a_hash(client, monkeypatch):
    n = [0]

    async def fake_download(url, referer=""):
        n[0] += 1
        return _png_bytes("import-" + str(n[0])), ".png"

    monkeypatch.setattr(w, "download", fake_download)
    r = client.post("/api/stickers/import", json={
        "url": "https://cdn.example/b284e22577144f5daa67.png",
        "query": "杂鱼", "label": "b284e22577144f5daa67", "tags": [], "emotion": ""})
    assert r.status_code == 200, r.text
    st = r.json()["sticker"]
    assert st["label"] == "杂鱼" and st["emotion"] == "tsundere"
    assert st["emotion_label"] == "傲娇"
    vocab = prompt.sticker_vocab(StickerLibrary())
    assert "b284e22577144f5daa67" not in vocab, "那串哈希不该再出现在发给模型的词表里"
    assert "杂鱼" in vocab and "(傲娇)" in vocab, "词表里要看得见这张在演什么"


def test_import_still_honours_an_explicit_emotion(client, monkeypatch):
    async def fake_download(url, referer=""):
        return _png_bytes("explicit"), ".png"

    monkeypatch.setattr(w, "download", fake_download)
    r = client.post("/api/stickers/import", json={
        "url": "https://cdn.example/x.png", "query": "杂鱼", "emotion": "sad"})
    assert r.json()["sticker"]["emotion"] == "sad", "用户手动筛了情绪就听他的"

