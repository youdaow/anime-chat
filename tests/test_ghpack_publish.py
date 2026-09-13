# -*- coding: utf-8 -*-
"""发布走 Git Data API：一个提交装下整个表情库。

真发布要有 Token，测试里不能碰 GitHub，所以用 MockTransport 把整条 API 链假装一遍。
断言的是调用形状：只建一个提交、只传内容变了的 blob、内容没变时一个写请求都不该发。
这些正是"会不会把用户仓库历史刷烂"的关键。
"""

import base64
import hashlib
import json

import httpx

from animechat import ghpack
from animechat.stickers import StickerLibrary


def _git_sha(data):
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode("ascii") + bytes([0]))
    h.update(data)
    return h.hexdigest()


class FakeRepo:
    def __init__(self):
        self.files = {"README.md": b"# hi"}
        self.commits = 0
        self.ref_patches = 0
        self.blobs = 0
        self.calls = []

    def handler(self, request):
        method, path = request.method, request.url.path
        self.calls.append((method, path))
        base = "/repos/mio/pack"
        if method == "GET" and path.startswith(base + "/git/ref/heads/"):
            return httpx.Response(200, json={"object": {"sha": "C0"}})
        if method == "GET" and path.startswith(base + "/git/commits/"):
            return httpx.Response(200, json={"tree": {"sha": "TREE0"}})
        if method == "GET" and path.startswith(base + "/git/trees/"):
            nodes = [{"path": p, "type": "blob", "sha": _git_sha(d)} for p, d in self.files.items()]
            return httpx.Response(200, json={"tree": nodes, "truncated": False})
        if method == "POST" and path == base + "/git/blobs":
            data = base64.b64decode(json.loads(request.content.decode())["content"])
            self.blobs += 1
            self.uploaded = getattr(self, "uploaded", {})
            self.uploaded[_git_sha(data)] = data
            return httpx.Response(201, json={"sha": _git_sha(data)})
        if method == "POST" and path == base + "/git/trees":
            body = json.loads(request.content.decode())
            assert body["base_tree"] == "TREE0", "必须带 base_tree，否则会把仓库里其它文件抹掉"
            self.tree_entries = body["tree"]
            return httpx.Response(201, json={"sha": "TREE1"})
        if method == "POST" and path == base + "/git/commits":
            self.commits += 1
            body = json.loads(request.content.decode())
            assert body["tree"] == "TREE1"
            assert body["parents"] == ["C0"]
            return httpx.Response(201, json={"sha": "C1"})
        if method == "PATCH" and path.startswith(base + "/git/refs/heads/"):
            self.ref_patches += 1
            for entry in getattr(self, "tree_entries", []):
                data = getattr(self, "uploaded", {}).get(entry["sha"])
                if data is not None:
                    self.files[entry["path"]] = data
            return httpx.Response(200, json={"object": {"sha": "C1"}})
        raise AssertionError("未预期的请求: " + method + " " + path)

    def writes(self):
        return [c for c in self.calls if c[0] in ("POST", "PATCH")]


def _use(monkeypatch, fake):
    def factory(timeout=30.0):
        return httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(ghpack, "_client", factory)


def _lib(tmp_path, names):
    lib = StickerLibrary()
    for n in names:
        src = tmp_path / (n + ".webp")
        src.write_bytes(b"PNGMAGIC-" + n.encode("utf-8"))
        lib.add_file(src, tags=[n], label=n, emotion="happy", origin="upload")
    return lib


def _settings(**kw):
    base = {"github_repo": "mio/pack", "github_token": "ghp_test", "github_pack_path": "stickers"}
    base.update(kw)
    from animechat.config import Settings
    return Settings.model_validate(base)

def test_publish_makes_exactly_one_commit(tmp_path, monkeypatch):
    import asyncio

    fake = FakeRepo()
    _use(monkeypatch, fake)
    lib = _lib(tmp_path, ["开心", "傲娇", "无语"])
    out = asyncio.run(ghpack.publish(_settings(), lib))

    assert fake.commits == 1, "整个表情库应该合成一个提交，而不是每张图一条历史：" + str(fake.commits)
    assert fake.ref_patches == 1, "分支引用只该被推进一次"
    assert out["uploaded"] == 4, "3 张图 + 1 份清单：" + str(out["uploaded"])
    assert out["skipped"] == 0
    assert out["commit"] == "C1"
    paths = sorted(e["path"] for e in fake.tree_entries)
    assert "stickers/stickers.json" in paths, "清单必须一起进提交：" + str(paths)
    assert all(x.startswith("stickers/") for x in paths), "所有文件都要落在配置的目录下：" + str(paths)
    assert "README.md" in fake.files, "base_tree 没带对的话仓库里原有的文件会被抹掉"


def test_publish_skips_bytes_that_are_already_there(tmp_path, monkeypatch):
    """第二次发布只该传真正新增的那张：不比较内容就会每次都把整个库重推一遍。"""
    import asyncio

    fake = FakeRepo()
    _use(monkeypatch, fake)
    lib = _lib(tmp_path, ["开心", "傲娇"])
    asyncio.run(ghpack.publish(_settings(), lib))
    assert fake.blobs == 3, "2 张图 + 清单：" + str(fake.blobs)

    fake2 = FakeRepo()
    fake2.files = dict(fake.files)          # 仓库已经和本地一致
    fake2.uploaded = dict(getattr(fake, "uploaded", {}))
    _use(monkeypatch, fake2)
    lib_again = StickerLibrary()            # 同一个数据目录，库里就是那两张
    out2 = asyncio.run(ghpack.publish(_settings(), lib_again))
    assert out2["unchanged"] is True, "内容没变就该直接收工：" + str(out2)
    assert fake2.writes() == [], "无变化时一个写请求都不该发：" + str(fake2.writes())


def test_publish_adds_only_the_new_sticker_the_second_time(tmp_path, monkeypatch):
    import asyncio

    fake = FakeRepo()
    _use(monkeypatch, fake)
    lib = _lib(tmp_path, ["开心"])
    asyncio.run(ghpack.publish(_settings(), lib))
    before = fake.blobs

    lib2 = _lib(tmp_path, ["加油"])           # 往同一个库再加一张（要重新读库才看得见）
    asyncio.run(ghpack.publish(_settings(), lib2))
    # 第二次的 tree 里只多了新图 + 变了内容的清单
    paths = sorted(e["path"] for e in fake.tree_entries)
    assert any("加油" in x for x in paths), "新图要进提交：" + str(paths)
    assert fake.blobs > before


def test_publish_reports_token_problem_in_actionable_words(tmp_path, monkeypatch):
    import asyncio

    # 真实踩过的形状：Fine-grained Token 只给了 Metadata 读，写仓库时 GitHub
    # 回 403 + x-required-github-permissions: contents=write。文案必须把这翻译成人话。
    class Boom:
        def __init__(self, code=403, headers=None):
            self.code = code
            # GitHub 真实回的是 x-accepted-github-permissions（文档里叫 required），两个都认
            self.headers = headers or {"x-accepted-github-permissions": "contents=write"}

        def handler(self, request):
            return httpx.Response(self.code, headers=self.headers,
                                  json={"message": "Resource not accessible by personal access token"})

    lib = _lib(tmp_path, ["开心"])
    import pytest

    monkeypatch.setattr(ghpack, "_client",
                        lambda timeout=30.0: httpx.AsyncClient(transport=httpx.MockTransport(Boom().handler)))
    with pytest.raises(ghpack.PackError) as e:
        asyncio.run(ghpack.publish(_settings(), lib))
    msg = str(e.value)
    assert "Contents" in msg and "读写" in msg, "要说出缺哪项权限及要多大：" + msg
    assert "Read and write" in msg or "classic" in msg, "要给出下一步怎么做：" + msg

    # GitHub 没给权限头时也不能含糊，至少要说 Token 和权限
    quiet = Boom(code=401, headers={})
    monkeypatch.setattr(ghpack, "_client",
                        lambda timeout=30.0: httpx.AsyncClient(transport=httpx.MockTransport(quiet.handler)))
    with pytest.raises(ghpack.PackError) as e2:
        asyncio.run(ghpack.publish(_settings(), lib))
    assert "Token" in str(e2.value), str(e2.value)


def test_publish_refuses_empty_local_library(tmp_path, monkeypatch):
    import asyncio

    fake = FakeRepo()
    _use(monkeypatch, fake)
    from animechat.config import Settings
    empty = Settings.model_validate({"github_repo": "mio/pack", "github_token": "ghp_test"})
    import pytest

    with pytest.raises(ghpack.PackError) as e:
        asyncio.run(ghpack.publish(empty, StickerLibrary()))
    assert "空" in str(e.value)


def test_blob_sha_matches_git_algorithm():
    """我们自己算的 git blob id 必须和 GitHub 一致，否则每次发布都会误判"全都变了"。"""
    assert _git_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    # 5c1b1494... 是 git hash-object -t blob 实测值，不是推算的
    assert _git_sha(b"hola\n") == "5c1b14949828006ed75a3e8858957f86a2f7e2eb"
    assert ghpack._blob_sha(b"hola\n") == _git_sha(b"hola\n")
