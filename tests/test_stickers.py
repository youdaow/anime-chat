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


# -------------------------------------------------- meta 拆分：仓库那半 / 本机那半
def _meta_files():
    from animechat.config import user_sticker_dir
    from animechat.stickers import LOCAL_META_NAME, META_NAME
    d = user_sticker_dir().parent
    return d / META_NAME, d / LOCAL_META_NAME


def _read(path):
    import json
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def test_qq_sticker_meta_stays_out_of_the_shared_file():
    """qq 那批图被 .gitignore 排除、永不进仓库，它们的定义也不该进 stickers_meta.json。
       混在一起的结果是仓库里堆上千条指向不存在文件的条目，工作区永远显示「已修改」。"""
    make_png(None, "qq商店-abc.png")
    make_png(None, "傲娇+哼.png")
    lib = StickerLibrary()
    for st in list(lib.items.values()):
        lib.patch(st.id, label=st.label or "改名")
    shared_path, local_path = _meta_files()
    shared, local = _read(shared_path), _read(local_path)
    qq_in_shared = [k for k in shared if k.startswith("qq")]
    assert not qq_in_shared, "qq 的定义漏进了仓库那份：" + str(qq_in_shared[:3])
    assert any(k.startswith("qq") for k in local), "qq 的定义该留在本机那份"
    assert any(not k.startswith("__") for k in shared), "普通（联网抓的）表情定义仍要跟仓库走"


def test_use_counts_land_in_the_local_file_only():
    """被用过几次是本机统计，不是表情定义：换台机器这数字毫无意义，而且每发一张图
       就改一次 —— 正是「仓库那份常驻已修改」的另一半来源（qq 那批是最大的一半）。"""
    make_png(None, "公开图.png")
    lib = StickerLibrary()
    st = next(s for s in lib.items.values() if s.origin != "builtin")
    lib.use(st.id)
    shared, local = (_read(p) for p in _meta_files())
    name = lib.files[st.id].name
    assert "__uses__" not in shared, "使用次数不许进仓库那份"
    assert (local.get("__uses__") or {}).get(name) == st.uses, "次数该按文件名记在本机那份里"
    assert "uses" not in (shared.get(name) or {}), "仓库那份的条目里不该留 uses 字段"
    # 读回来形状不变，选图加权（按 -uses 排）才不受拆分影响
    assert lib.get(st.id).uses == st.uses


def test_legacy_single_meta_file_is_split_on_load():
    """老库只有一个文件、里面混着两类数据。第一次加载就该拆开，否则要等到谁去改标签
       才动 —— 在那之前工作区一直脏着，而且没人知道那 1613 条是哪来的。"""
    import json
    shared_path, local_path = _meta_files()
    assert not local_path.exists()
    shared_path.write_text(json.dumps({
        "__uses__": {"tsun": 3},
        "__builtin_overrides__": {"tsun": {"hidden": False}},
        "qq收到-xyz.png": {"label": "杂鱼", "tags": ["杂鱼"], "emotion": "tsundere"},
        "公开图.png": {"label": "抱抱", "tags": ["抱抱"], "emotion": "love"},
    }, ensure_ascii=False), encoding="utf-8")

    assert StickerLibrary().migrate_meta_split() is not None   # 构造时已经拆过
    shared, local = _read(shared_path), _read(local_path)
    assert "qq收到-xyz.png" not in shared and "qq收到-xyz.png" in local
    assert "__uses__" not in shared and "__uses__" in local
    assert "__builtin_overrides__" in shared, "内置图的覆盖属于包里那张图，要跟仓库走"
    assert "公开图.png" in shared
    # 拆完内存里还得能看见全部（合并读）
    lib = StickerLibrary()
    assert lib._meta().get("qq收到-xyz.png", {}).get("label") == "杂鱼"


def test_unfavoriting_restores_the_shared_file_byte_for_byte():
    """收藏一次再取消，仓库那份要回到原样。把 "favorite": false 这种默认值写进去的话，
       那条 entry 就永久算「已修改」—— 动一次表情库就再也不能干净地提交。"""
    make_png(None, "公开图.png")
    lib = StickerLibrary()
    st = next(s for s in lib.items.values() if s.origin != "builtin")
    shared_path = _meta_files()[0]
    before = shared_path.read_text(encoding="utf-8")
    lib.patch(st.id, favorite=True)
    assert shared_path.read_text(encoding="utf-8") != before, "收藏这件事总得写下来"
    lib.patch(st.id, favorite=False)
    after = shared_path.read_text(encoding="utf-8")
    assert after == before, "取消收藏后回不到原样，差在：" + str(set(after.splitlines()) ^ set(before.splitlines()))
    assert lib.get(st.id).favorite is False


def test_batch_scripts_go_through_the_library_not_one_meta_file():
    """批量脚本自己 json.load 单个文件 = 只看得到仓库那半。qq* 那批定义在 local 那份里，
       漏读就把它们当成「不存在」，然后整批重导、新条目盖掉旧条目，打好的视觉标签静悄悄
       没了 —— 真撞上过：1613 张退化成 {"sha1": ...}，靠 sync 脚本留的 .bak 才捞回来。
       refresh() 会替没人认领的图补一条光秃秃的条目，所以这件事发生时谁都看不出来。"""
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("import_qq_stickers.py", "sync_library_from_desktop.py",
                 "tag_stickers_vision.py", "sticker_dupe.py"):
        src = (scripts / name).read_text(encoding="utf-8")
        assert "read_meta()" in src, name + " 要用库的合并视图读 meta"
        assert ".write_meta(" in src, name + " 要用库的拆分写回 meta"
        assert "META_NAME" not in src, name + " 不许再自己拼 meta 文件路径"


def test_deploy_package_skips_machine_local_sticker_state(tmp_path, monkeypatch):
    """部署包里带上 local 那份，等于把这台机器的使用计数盖到服务器上去；带上 qq* 那几百 MB
       私人聊天表情，等于把它们传上一台别人也碰得到的机器。两条都按 .gitignore 的策略跳过。"""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import build_deploy

    data = tmp_path / "repo" / "data"
    (data / "stickers").mkdir(parents=True)
    for name in ("stickers_meta.json", "stickers_meta.local.json",
                 "stickers_meta.json.bak-20260915122837", "animechat.db", "settings.json"):
        (data / name).write_text("{}", encoding="utf-8")
    (data / "stickers_quarantine").mkdir()
    (data / "stickers" / "qq商店-0b10acd309.png").write_bytes(b"x")
    (data / "stickers" / "傲娇+哼.png").write_bytes(b"x")
    monkeypatch.setattr(build_deploy, "ROOT", tmp_path / "repo")
    monkeypatch.setattr(build_deploy, "BUILD_DIR", tmp_path / "out" / "anime-chat")

    build_deploy.copy_data()

    out = tmp_path / "out" / "anime-chat" / "data"
    assert (out / "stickers_meta.json").is_file(), "仓库那份表情定义是要上服务器的"
    assert {p.name for p in (out / "stickers").iterdir()} == {"傲娇+哼.png"}
    left = {p.name for p in out.iterdir()}
    assert left == {"stickers", "stickers_meta.json"}, "本机专属/敏感文件漏进部署包：" + str(left)


def _flat(color, fmt):
    import io
    buf = io.BytesIO()
    Image.new("RGBA", (32, 32), color).save(buf, format=fmt)
    return buf.getvalue()


def test_dupe_signature_is_pixels_frame_by_frame_not_file_bytes(tmp_path):
    """查重要认「解出来是不是同一张图」，不能只比文件字节：库里那 79 组重复里有一批是
       同一份像素挂着不同后缀（真 GIF 名叫 .jpg），只比 sha1 抓不到 —— 同步脚本就是
       这样把带着手写标签的 p01…p54 当新图重导了一遍。
       反过来动图必须逐帧比：只算第一帧的话，两套「开头一样、后半段不一样」的表情会
       被判成重复，被删掉的那张谁也不会发现。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import sticker_dupe as sd

    png = tmp_path / "同图.png"
    gif = tmp_path / "同图.jpg"          # 后缀骗人：里面是 GIF，解出来跟 png 逐点相同
    png.write_bytes(_flat((240, 30, 90, 255), "PNG"))
    gif.write_bytes(_flat((240, 30, 90, 255), "GIF"))
    sp, sg = sd.signature(png), sd.signature(gif)
    assert sp[0] != sg[0], "字节 sha 本来就该不同，否则测不出旧判据的漏网"
    assert sp[1] == sg[1], "同一份像素必须算同一张"

    def anim(name, tail):
        p = tmp_path / name
        first = Image.new("RGBA", (24, 24), (255, 0, 0, 255))
        first.save(p, save_all=True, append_images=[Image.new("RGBA", (24, 24), tail)])
        return p

    a = anim("a.gif", (0, 0, 255, 255))
    b = anim("b.gif", (0, 255, 0, 255))
    assert sd.signature(a)[1] != sd.signature(b)[1], "只有第一帧相同的两套动图不是重复"


def test_dupe_keeper_keeps_the_richer_record():
    """移走一张不心疼，丢一条打过的手写/视觉标签才心疼。名次：收藏 > 用过 > 标签条数
       > note 是人写的 > label 是人写的 > 有情绪标签 > 文件更小。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import sticker_dupe as sd

    hand = {"tags": ["无语", "呆滞", "凝视"], "note": "对方说了很无语的话", "label": "无语"}
    auto = {"tags": ["收到"], "note": "QQ本地表情导入 来源收到", "label": "收到·78ffe099d3"}
    assert sd.keeper_rank("p09.gif", hand, 5000) > sd.keeper_rank("qq收到-x.jpg", auto, 1000)
    used = dict(auto, tags=["收到", "无语"], emotion="flat")
    assert sd.keeper_rank("qq收到-y.jpg", used, 1000) > sd.keeper_rank("qq收到-x.jpg", auto, 1000)
    assert sd.keeper_rank("qq收到-small.jpg", auto, 1000) > sd.keeper_rank("qq收到-big.jpg", auto, 9000)


def test_library_index_sees_batches_beyond_its_own(tmp_path):
    """同步脚本判「库里有没有这张」必须看全库，判「该不该隔离」才只看自己导的那批。
       两个集合都从磁盘现场扫出来 —— meta 里那个 sha1 只是导入当时的抄录，不能当现状。"""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import sync_library_from_desktop as sy

    blob = _flat((10, 200, 30, 255), "PNG")
    (tmp_path / "p01.webp").write_bytes(blob)
    (tmp_path / "qq收到-x.gif").write_bytes(_flat((10, 200, 30, 255), "GIF"))
    meta = {"p01.webp": {"tags": ["傲娇"], "note": "得意地命令对方"},
            "qq收到-x.gif": {"tags": ["收到"], "note": "QQ本地表情导入 来源收到"}}
    everywhere, ours = sy.library_index(tmp_path, meta)
    assert set(everywhere) == set(ours), "同一张图的两份副本要并成一个指纹"
    assert list(everywhere.values()) == ["p01.webp"], "全库那份得认老批次，否则它会被当新图重导"
    assert list(ours.values()) == ["qq收到-x.gif"], "隔离范围只能是自己导入的那批"
