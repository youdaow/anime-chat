"""GitHub 表情仓库：仓库名解析、清单生成、端点报错文案，全部离线可测。"""

import json

import pytest
from fastapi.testclient import TestClient

from animechat import ghpack
from animechat.server import create_app


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def test_repo_spec_accepts_the_forms_people_actually_type():
    assert ghpack.parse_repo("mio/anime-stickers") == ("mio", "anime-stickers", "main")
    # 从网页「Code ▸ HTTPS」整条复制会带 .git 尾巴；留着它拼出来的 raw 地址必然 404。
    assert ghpack.parse_repo("https://github.com/mio/pack.git") == ("mio", "pack", "main")
    assert ghpack.parse_repo("mio/pack.git:dev") == ("mio", "pack", "dev")
    # 真实踩过的坑：仓库名就是一个连字符（GitHub 上确实存在这种名字）
    assert ghpack.parse_repo("https://github.com/youdaow/-.git") == ("youdaow", "-", "main")
    assert ghpack.parse_repo("  mio/pack/  ") == ("mio", "pack", "main")
    for bad in ("", "mio", "mio/", "a b/c", "https://github.com/"):
        with pytest.raises(ghpack.PackError):
            ghpack.parse_repo(bad)


def test_pack_path_cannot_escape_the_repo():
    assert ghpack.pack_dir("") == "stickers"
    assert ghpack.pack_dir("/packs/anime/") == "packs/anime"
    for bad in ("..", "../secrets", "a/../b", "packs?*"):
        with pytest.raises(ghpack.PackError):
            ghpack.pack_dir(bad)


def test_manifest_round_trips_label_tags_and_emotion():
    entries = ghpack._parse_manifest({"stickers": [
        {"file": "p01-abc.webp", "label": "开心", "tags": ["开心", "哈哈"], "emotion": "happy", "note": "笑"},
        {"file": "notes.txt", "label": "不是图"},
        {"name": "p02-def.png", "emotion": "nope"},
        "垃圾条目",
    ]})
    assert [e["name"] for e in entries] == ["p01-abc.webp", "p02-def.png"], "非图片缺扩展名的条目要丢掉"
    assert entries[0]["tags"] == ["开心", "哈哈"] and entries[0]["emotion"] == "happy"
    with pytest.raises(ghpack.PackError):
        ghpack._parse_manifest({"nope": []})


def test_build_manifest_skips_builtin_and_rewrites_digest_name(tmp_path, monkeypatch):
    """发布出去的命名必须和本地一致（原名-内容sha1.扩展名），否则每次发布都换一批文件名，
       别人拉两次就得到两份一样的图。内置素材不进包：那是随程序发的，不该混进用户分享。"""
    from animechat.stickers import StickerLibrary

    lib = StickerLibrary()
    src = tmp_path / "开心.webp"
    src.write_bytes(b"\x89PNG\r\n\x1a\nfake-webp-bytes")
    lib.add_file(src, tags=["开心"], label="开心", emotion="happy", note="测试", origin="upload")
    manifest, files = ghpack.build_manifest(lib)
    assert len(files) == 1
    name, data = files[0]
    import hashlib
    digest = hashlib.sha1(data).hexdigest()[:10]
    assert name == "开心-" + digest + ".webp", "文件名要带内容哈希尾巴：" + name
    assert manifest["stickers"][0]["file"] == name
    assert manifest["stickers"][0]["emotion"] == "happy"
    # 再发布一次，名字必须完全稳定
    again, _ = ghpack.build_manifest(lib)
    assert again["stickers"][0]["file"] == name


def test_endpoints_say_what_is_missing_instead_of_500(client):
    r = client.get("/api/stickers/pack/preview")
    assert r.status_code == 400 and "owner" in r.json()["detail"], r.text
    r = client.post("/api/stickers/pack/publish")
    assert r.status_code == 400 and ("Token" in r.json()["detail"] or "仓库" in r.json()["detail"]), r.text


def test_publish_requires_a_token_not_a_crash(client):
    client.patch("/api/settings", json={"github_repo": "mio/pack", "github_token": ""})
    r = client.post("/api/stickers/pack/publish")
    assert r.status_code == 400 and "Token" in r.json()["detail"]


def test_import_reports_missing_manifest_clearly(client, monkeypatch):
    """仓库没建清单是最常见的首次使用状态，文案要直接说出下一步该干什么。"""
    import httpx

    client.patch("/api/settings", json={"github_repo": "mio/pack", "github_pack_path": "stickers"})

    async def fake_get(self, url, **kw):
        return httpx.Response(404, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    r = client.post("/api/stickers/pack/import", json={})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "stickers.json" in detail and "发布" in detail, detail

def test_handwritten_meta_still_dedupes_after_refresh(tmp_path, monkeypatch):
    """回归：手改/脚本导入的 meta 常常没有 sha1，去重就看不见那张图，
    从仓库拉回同一张会白多一份副本（真实发生过，74 张变 129 张）。"""
    import httpx

    from animechat import config
    from animechat.stickers import StickerLibrary

    lib = StickerLibrary()
    src = tmp_path / "同一张.webp"
    src.write_bytes(b"PNGMAGIC-same-bytes")
    lib.add_file(src, tags=["同一张"], label="同一张", origin="upload")
    meta_path = config.DATA_DIR / "stickers_meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for name, info in meta.items():
        if isinstance(info, dict):
            info.pop("sha1", None)          # 模拟手写 meta 的状态
    meta_path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    lib.refresh()
    healed = {k: v for k, v in json.loads(meta_path.read_text(encoding="utf-8")).items() if isinstance(v, dict)}
    assert healed and all(v.get("sha1") for v in healed.values()), "refresh 要把缺的 sha1 补回去，否则去重看不见"

    blob = src.read_bytes()
    manifest = {"stickers": [{"file": "同一张-abc.webp", "label": "同一张",
                              "tags": ["同一张"], "emotion": "neutral", "note": ""}]}

    def handler(request):
        if request.url.path.endswith("stickers.json"):
            return httpx.Response(200, json=manifest)
        return httpx.Response(200, content=blob)

    monkeypatch.setattr(ghpack, "_client",
                        lambda timeout=30.0: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    from animechat.config import Settings
    s = Settings.model_validate({"github_repo": "mio/pack", "github_pack_path": "stickers"})
    import asyncio
    out = asyncio.run(ghpack.import_pack(s, lib))
    assert out["added"] == 0, "同一张图不能因为缺 sha1 就多存一份：" + str(out)
    assert out["merged"] == 1, str(out)
    assert len([x for x in lib.all() if x.origin != "builtin"]) == 1


def test_import_reuses_one_client_for_the_whole_pack(monkeypatch):
    """回归：曾经每张图新建一个 httpx 客户端，74 张 = 74 次 TLS 握手，
    慢到接口直接超时。整库导入只允许开一个连接池。"""
    import httpx

    from animechat import config
    from animechat.stickers import StickerLibrary

    lib = StickerLibrary()
    made = []
    manifest = {"stickers": []}
    for i in range(6):
        src = config.DATA_DIR.parent / ("_unused_%d.webp" % i) if False else None
        manifest["stickers"].append({"file": "图%d-abc%d.webp" % (i, i), "label": "图%d" % i,
                                     "tags": ["图"], "emotion": "neutral", "note": ""})

    def handler(request):
        if request.url.path.endswith("stickers.json"):
            return httpx.Response(200, json=manifest)
        return httpx.Response(200, content=b"PNGMAGIC-bytes-" + request.url.path.encode("utf-8"))

    def factory(timeout=30.0):
        made.append(timeout)
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(ghpack, "_client", factory)
    from animechat.config import Settings
    s = Settings.model_validate({"github_repo": "mio/pack", "github_pack_path": "stickers"})
    import asyncio
    out = asyncio.run(ghpack.import_pack(s, lib))
    assert out["total"] == 6
    # 2 = 读清单一次 + 下载全部图片共用一次；过去是 1 + N（每张一次握手）
    assert len(made) == 2, "整库导入只该开 清单+下载 两个客户端，实际开了 " + str(len(made)) + " 个"
