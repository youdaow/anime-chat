import zlib
from pathlib import Path

from PIL import Image

from animechat.config import user_sticker_dir
from animechat.stickers import StickerLibrary, library


def make_png(tmp_path: Path, name: str) -> Path:
    """按名字变色，保证不同 name 生成不同字节——否则会被按内容去重合并成一张。"""
    path = user_sticker_dir() / name
    path.parent.mkdir(parents=True, exist_ok=True)
    h = zlib.crc32(name.encode("utf-8"))
    Image.new("RGBA", (24, 24), (200 + h % 55, 90 + (h >> 8) % 160, 120 + (h >> 16) % 130, 255)).save(path)
    return path


def test_same_bytes_imported_twice_merges_instead_of_duplicating(tmp_path):
    """重跑抓取脚本或同一张图传两次，不该在库里堆副本；标签要并起来。"""
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (20, 20), (10, 20, 30, 255)).save(buf, format="PNG")
    blob = buf.getvalue()
    lib = StickerLibrary()
    n0 = len(lib.all())
    p1 = tmp_path / "dup-a.png"      # 原件放在表情库目录外，模拟上传用的临时文件
    p1.write_bytes(blob)
    first = lib.add_file(p1, tags=["抱抱"], emotion="love", label="贴贴")
    p2 = tmp_path / "dup-b.png"
    p2.write_bytes(blob)
    second = lib.add_file(p2, tags=["贴贴", "充电"], emotion="love", label="换个名字")
    assert first.id == second.id
    assert len(lib.all()) == n0 + 1, "不该多出副本"
    tags = set(lib.get(first.id).tags or [])
    assert {"抱抱", "贴贴", "充电"} <= tags, "标签应合并：" + str(sorted(tags))
    assert (lib.get(first.id).label or "") == "贴贴", "已有的 label 不被后一次覆盖"
    assert len([p for p in user_sticker_dir().iterdir() if p.name.startswith("dup-")]) == 1


def test_add_and_resolve_by_tag():
    lib = StickerLibrary()
    src = make_png(None, "我的傲娇.png")
    st = lib.add_file(src, tags=["傲娇", "别误会"], emotion="tsundere", label="傲娇")
    assert lib.resolve("傲娇").id == st.id
    assert lib.resolve(st.id).id == st.id
    assert lib.resolve("别误会了啦") is not None  # 模糊命中
    assert lib.resolve("完全无关的东西xyz") is None


def test_filename_convention_tags():
    lib = StickerLibrary()
    src = make_png(None, "开心+哈哈+笑死.png")
    st = lib.add_file(src)
    assert "哈哈" in st.tags and "笑死" in st.tags


def test_search_prefers_exact_and_favorite_rises():
    lib = StickerLibrary()
    a = lib.add_file(make_png(None, "aaa.png"), tags=["加油"], label="加油", emotion="hype")
    b = lib.add_file(make_png(None, "bbb.png"), tags=["努力", "加油"], label="努力", emotion="hype")
    ids = [s.id for s in lib.search("加油")]
    assert a.id in ids and b.id in ids
    lib.patch(b.id, favorite=True)
    assert lib.search("加油")[0].id == b.id


def test_user_can_delete_own_and_builtin_is_hidden_not_gone():
    lib = StickerLibrary()
    st = lib.add_file(make_png(None, "可删.png"), tags=["删我"])
    assert lib.delete(st.id) is True
    assert lib.get(st.id) is None
    fake_builtin = next((s for s in lib.items.values() if s.origin == "builtin"), None)
    if fake_builtin is None:
        return  # 素材还没 build-assets
    sid = fake_builtin.id
    # 界面上点"删除"内置表情 = 隐藏：库里消失、重启也不回来，但包内素材文件不能动
    assert lib.delete(sid) is True
    assert lib.get(sid) is None
    assert StickerLibrary().get(sid) is None, "隐藏必须写进 data 侧，重启还生效"
    from animechat.config import BUILTIN_STICKER_DIR
    assert (BUILTIN_STICKER_DIR / (sid + ".png")).is_file()
    assert lib.unhide_builtins() >= 1
    assert lib.get(sid) is not None, "unhide 之后要能放回来"
    # 隐藏之后再改标签不该把 hidden 弄丢（patch 是合并写的）
    assert lib.delete(sid) is True
    assert lib.patch(sid, label="改个名") is None
    assert lib.unhide_builtins() >= 1


def test_pick_for_emotion_is_seeded_and_prefers_prefs():
    lib = StickerLibrary()
    for i in range(3):
        lib.add_file(make_png(None, "e" + str(i) + ".png"), tags=["t" + str(i)], emotion="happy")
    first = lib.pick_for_emotion("happy", prefs=["t2"], seed=42)
    second = lib.pick_for_emotion("happy", prefs=["t2"], seed=42)
    assert first.id == second.id
    assert first.tags[0] == "t2"


def test_patch_builtin_writes_override_not_package_file():
    lib = library()
    builtin = next((s for s in lib.items.values() if s.origin == "builtin"), None)
    if builtin is None:
        return  # 素材还没 build-assets，跳过
    from animechat.config import BUILTIN_STICKER_DIR

    before = (BUILTIN_STICKER_DIR / (builtin.id + ".png")).read_bytes()
    lib.patch(builtin.id, favorite=not builtin.favorite, label="改过的名字")
    after_st = lib.get(builtin.id)
    assert after_st.favorite is (not builtin.favorite)
    assert after_st.label == "改过的名字"
    assert (BUILTIN_STICKER_DIR / (builtin.id + ".png")).read_bytes() == before  # 包内文件没动
    lib.patch(builtin.id, favorite=builtin.favorite, label=builtin.label)
