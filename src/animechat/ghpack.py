"""GitHub 表情仓库：把本地表情库发布成公开素材包，也能从别人的仓库拉回来用。

仓库约定（人和程序都能直接看懂、手工维护）：

    <repo>/<path>/stickers.json      {"stickers": [{"file","label","tags","emotion","note"}, ...]}
    <repo>/<path>/p01-66685f6552.webp

拉取走 raw.githubusercontent.com（公开仓库不需要 Token），发布走 Git Data API（要 Token），
文件名沿用本地那套 "原名-内容sha1前10.扩展名"，所以同图重复拉取会被表情库
按内容去重合并，不会每同步一次就多一份副本。
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import shutil
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import Settings, user_sticker_dir
from .stickers import IMAGE_EXTS, StickerLibrary

API = "https://api.github.com"
RAW = "https://raw.githubusercontent.com"
UA = "animechat-stickerpack/1.0"
MANIFEST = "stickers.json"
MAX_FILE = 12 * 1024 * 1024
MAX_FILES = 600

_REPO_RE = re.compile(r"^[\w.-]{1,80}/[\w.-]{1,100}$")
_REF_RE = re.compile(r"^[\w./-]{1,120}$")
_PACK_RE = re.compile(r"^[\w./\-]{0,120}$")
# 本地文件名尾巴上的 "-<10 位十六进制>" 就是内容哈希，拉取时先摘掉，
# 交给 add_file 重新算回来，本地和仓库的文件名就能一模一样。
_DIGEST_TAIL = re.compile(r"-[0-9a-f]{10}$")


class PackError(RuntimeError):
    pass


def parse_repo(text: str) -> tuple[str, str, str]:
    """"owner/name[:ref]" -> (owner, name, ref)；ref 缺省 main。仓库名写错立刻报错，
    免得拼出一个看着像但 404 的 URL 让用户以为是网络问题。"""
    raw = str(text or "").strip().strip("/")
    raw = raw.removeprefix("https://github.com/").removeprefix("github.com/")
    raw = raw.removeprefix("https://").removeprefix("http://")
    raw = raw.removeprefix("github.com/")
    repo, _, ref = raw.partition(":")
    # 从网页「Code ▸ HTTPS」复制来的地址结尾常带 .git，留着会拼出 404 的 raw 地址。
    if repo.endswith(".git"):
        repo = repo[:-4]
    ref = (ref or "main").strip() or "main"
    if not _REPO_RE.match(repo):
        raise PackError("仓库要写成 owner/名字（可选 owner/名字:分支），收到的是：" + (raw or "空"))
    if not _REF_RE.match(ref):
        raise PackError("分支名不合法：" + ref)
    owner, name = repo.split("/", 1)
    return owner, name, ref


def pack_dir(path: str) -> str:
    p = str(path or "stickers").strip().strip("/") or "stickers"
    if not _PACK_RE.match(p) or ".." in Path(p).parts:
        raise PackError("仓库里的目录名不合法（只能有字母数字 . _ - /）：" + p)
    return p


def _headers(token: str = "") -> dict:
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True)


def _entry_name(item: dict) -> str:
    # 清单里用 file（和发布时写出去的形状一致），但也接受 name，
    # 免得别人手写 stickers.json 时两种写法只认一种。
    name = str(item.get("file") or item.get("name") or "").strip()
    if not name or ".." in Path(name).parts or name.startswith("/"):
        return ""
    return name


# ------------------------------------------------------------------ 拉取

def _manifest_url(repo: str, ref: str, sub: str) -> str:
    return "%s/%s/%s/%s/%s/%s" % (RAW, repo, quote(ref, safe=""), sub, "", MANIFEST)


def _parse_manifest(obj: object) -> list[dict]:
    items = obj.get("stickers") if isinstance(obj, dict) else None
    if not isinstance(items, list):
        raise PackError("stickers.json 里找不到 stickers 数组")
    out: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = _entry_name(it)
        if not name or Path(name).suffix.lower() not in IMAGE_EXTS:
            continue
        tags = it.get("tags")
        tags = [str(t).strip() for t in tags if str(t).strip()][:16] if isinstance(tags, list) else []
        out.append({"name": name, "label": str(it.get("label") or "")[:40], "tags": tags,
                    "emotion": str(it.get("emotion") or ""), "note": str(it.get("note") or "")[:200]})
        if len(out) >= MAX_FILES:
            break
    return out


async def fetch_manifest(s: Settings) -> tuple[str, list[dict]]:
    """返回 (repo, 清单条目)；仓库里没有清单就报错说清楚，别当成空包。"""
    owner, name, ref = parse_repo(s.github_repo)
    repo = owner + "/" + name
    sub = pack_dir(s.github_pack_path)
    url = _manifest_url(repo, ref, sub)
    async with _client() as c:
        try:
            r = await c.get(url, headers=_headers())
        except httpx.HTTPError as exc:
            raise PackError("连不上 raw.githubusercontent.com：" + exc.__class__.__name__) from exc
        if r.status_code == 404:
            raise PackError("仓库里没有找到 " + sub + "/" + MANIFEST + "（分支 " + ref + "）。"
                            "先在设置里点「发布本地表情」，或确认仓库名和分支没写错。")
        if r.status_code >= 400:
            raise PackError("读取清单失败 HTTP " + str(r.status_code))
        try:
            obj = json.loads(r.text)
        except ValueError as exc:
            raise PackError("stickers.json 不是合法 JSON：" + str(exc)[:60]) from exc
    return repo, _parse_manifest(obj)


async def import_pack(s: Settings, lib: StickerLibrary, limit: int = 0) -> dict:
    """把仓库里的表情包下载进本地库。已存在的按内容去重（标签会合并）。"""
    repo, entries = await fetch_manifest(s)
    owner, name, ref = parse_repo(s.github_repo)
    sub = pack_dir(s.github_pack_path)
    todo = entries[:limit] if limit and limit > 0 else entries
    added: list[str] = []
    merged: list[str] = []
    failed: list[str] = []
    stash = user_sticker_dir().parent
    tmp = stash / ("pack-" + str(int(time.time() * 1000)))
    tmp.mkdir(parents=True, exist_ok=True)
    # 上次被中断的导入会留下临时目录，顺手收掉；只碰足够旧的，别动并发中那批。
    for stale in stash.glob("pack-*"):
        if stale.is_dir() and stale != tmp:
            try:
                if time.time() - stale.stat().st_mtime > 1800:
                    shutil.rmtree(stale, ignore_errors=True)
            except OSError:
                pass
    # 一张开一个客户端 = 整库拉一遍要做 74 次 TLS 握手，慢到接口超时；全程共用一个。
    c = _client(timeout=60.0)
    try:
        for entry in todo:
            fname = entry["name"]
            url = "%s/%s/%s/%s/%s" % (RAW, quote(owner) + "/" + quote(name), quote(ref, safe=""), sub, quote(fname))
            try:
                r = await c.get(url, headers=_headers(s.github_token))
                if r.status_code >= 400:
                    failed.append(fname + "(HTTP " + str(r.status_code) + ")")
                    continue
                data = r.content
                if not data or len(data) > MAX_FILE:
                    failed.append(fname + "(过大)")
                    continue
                stem = _DIGEST_TAIL.sub("", Path(fname).stem) or "pack"
                local = tmp / (stem + Path(fname).suffix.lower())
                local.write_bytes(data)
                before = {st.id for st in lib.all()}
                st = lib.add_file(local, tags=entry["tags"] or None, label=entry["label"],
                                  emotion=entry["emotion"], note=entry["note"],
                                  origin="pack", dedupe_bytes=data)
                (added if st.id not in before else merged).append(st.id)
            except (httpx.HTTPError, ValueError, OSError) as exc:
                failed.append(fname + "(" + exc.__class__.__name__ + ")")
            await asyncio.sleep(0)   # 让出事件循环，大列表下载不卡住接口
    finally:
        await c.aclose()
        for leftover in sorted(tmp.glob("*")):
            leftover.unlink(missing_ok=True)
        try:
            tmp.rmdir()
        except OSError:
            pass   # 目录被杀软占着也别让整次导入报错
    lib.refresh()
    return {"repo": repo, "total": len(todo), "added": len(added), "merged": len(merged),
            "failed": failed[:12], "failed_count": len(failed)}


# ------------------------------------------------------------------ 发布

def build_manifest(lib: StickerLibrary) -> tuple[dict, list[tuple[str, bytes]]]:
    """把本地表情库摊平成仓库形态：一份清单 + 待上传文件（含内容）。内置素材不进包。"""
    files: list[tuple[str, bytes]] = []
    entries: list[dict] = []
    for st in lib.all():
        if st.origin == "builtin":
            continue
        path = lib.files.get(st.id)
        if path is None or not Path(path).is_file():
            continue
        size = Path(path).stat().st_size
        if size > MAX_FILE:
            continue
        data = Path(path).read_bytes()
        digest = hashlib.sha1(data).hexdigest()[:10]
        stem = _DIGEST_TAIL.sub("", Path(path).stem) or "sticker"
        name = (stem + "-" + digest + Path(path).suffix.lower())[:120]
        files.append((name, data))
        entries.append({"file": name, "label": st.label, "tags": st.tags,
                        "emotion": st.emotion, "note": st.note})
        if len(files) >= MAX_FILES:
            break
    return {"stickers": entries}, files

# ---------------------------------------------------------------- 发布


def _blob_sha(data: bytes) -> str:
    # git 的对象 id 本地就能算，不用先问服务器，所以内容没变的一张都不用传。
    h = hashlib.sha1()
    h.update(b"blob " + str(len(data)).encode("ascii") + bytes([0]))
    h.update(data)
    return h.hexdigest()


async def _git(c, token: str, method: str, url: str, body: dict | None = None, params: dict | None = None) -> dict:
    send = getattr(c, method)
    kw: dict = {"headers": _headers(token)}
    if body is not None:
        kw["json"] = body
    if params is not None:
        kw["params"] = params
    r = await send(url, **kw)
    if r.status_code >= 400:
        raise PackError(_git_error(r, url))
    try:
        obj = r.json()
    except ValueError as exc:
        raise PackError("GitHub 返回的不是 JSON（可能被网关拦了）") from exc
    return obj if isinstance(obj, dict) else {}


# GitHub 权限标识 -> 设置页面上的中文叫法，省得用户对着 contents=write 找不到勾在哪。
_PERM_CN = {"contents": "Contents（仓库内容）", "metadata": "Metadata（元数据）",
            "pull_requests": "Pull requests", "issues": "Issues"}
_ACCESS_CN = {"read": "只读", "write": "读写", "admin": "管理员"}


def _git_error(r, url: str) -> str:
    """把 GitHub 的状态码翻成人能照着做的下一步；401/403 最常见的错法是 Token 权限不够。"""
    code = r.status_code
    if code in (401, 403):
        # GitHub 会直接说出缺哪一项。实测 Fine-grained Token 漏勾 Contents 时，403 上带的
        # 是 x-accepted-github-permissions: contents=write（文档里管它叫 required），两个名字都读。
        need = str(r.headers.get("x-required-github-permissions")
                   or r.headers.get("x-accepted-github-permissions") or "").strip()
        if need:
            parts = []
            for item in need.split(","):
                nm, sep, acc = item.strip().partition("=")
                if not nm:
                    continue
                label = _PERM_CN.get(nm, nm)
                if sep:
                    label += "需要" + _ACCESS_CN.get(acc, acc)
                parts.append(label)
            if parts:
                return ("Token 权限不够，GitHub 要求：" + "、".join(parts) + "。到 GitHub 的 Token"
                        " 设置里把该仓库的 Contents 改成「Read and write」并保存，或换一个勾了"
                        " repo 的 classic Token。")
        return ("Token 被拒绝（HTTP " + str(code) + "）：检查 Token 是否过期，以及有没有"
                " repo / Contents: Read and write 权限。")
    if code == 404:
        return ("找不到仓库或分支（HTTP 404）：确认 owner/名字 和分支写对，且这个 Token 对它有写权限。")
    if code == 409:
        return "仓库或分支还是空的（HTTP 409）：先在 GitHub 上建一个提交（哪怕只有 README）。"
    detail = ""
    try:
        msg = r.json().get("message")
        detail = str(msg)[:120] if msg else ""
    except ValueError:
        pass
    return "GitHub 报错 HTTP " + str(code) + (("：" + detail) if detail else "")

async def publish(s: Settings, lib: StickerLibrary) -> dict:
    """把本地表情库整个推上仓库，合成一个提交。

    用 Git Data API 而不是 Contents API：后者每个文件一次提交，发一次表情包
    就把仓库历史刷出几十条 "animechat: update xxx"，看都看不懂。这里只上传
    内容真变了的 blob，一次 tree + 一次 commit + 一次 ref 更新收工。
    """
    if not s.github_token.strip():
        raise PackError("发布需要一个有 repo 权限的 GitHub Token，在设置里填一次就行。")
    owner, name, ref = parse_repo(s.github_repo)
    sub = pack_dir(s.github_pack_path)
    token = s.github_token.strip()
    manifest, files = build_manifest(lib)
    if not files:
        # 只剩内置素材时也走这里：那是随程序发的，推上去只是给别人复制一份官方包。
        raise PackError("本地表情库是空的（或只剩内置素材），没有可发布的东西。")
    payload = files + [(MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))]
    wanted = [(sub + "/" + fn, data) for fn, data in payload]
    async with _client(timeout=120.0) as c:
        base = API + "/repos/" + owner + "/" + name
        rref = await _git(c, token, "get", base + "/git/ref/heads/" + quote(ref, safe=""))
        commit_sha = str(((rref.get("object") or {}) if isinstance(rref.get("object"), dict) else {}).get("sha") or "")
        if not commit_sha:
            raise PackError("分支 " + ref + " 上还没有提交：先在 GitHub 上建一个提交再发布。")
        rcommit = await _git(c, token, "get", base + "/git/commits/" + commit_sha)
        tree_sha = str((rcommit.get("tree") or {}).get("sha") or "") if isinstance(rcommit.get("tree"), dict) else ""
        if not tree_sha:
            raise PackError("读不到分支 " + ref + " 的目录树，换个分支名试试。")
        existing: dict[str, str] = {}
        rtree = await _git(c, token, "get", base + "/git/trees/" + tree_sha, params={"recursive": "1"})
        if not rtree.get("truncated"):
            for node in rtree.get("tree") or []:
                if isinstance(node, dict) and node.get("type") == "blob":
                    existing[str(node.get("path"))] = str(node.get("sha"))
        # truncated 时当作全部不存在：多传几张 blob 无害，base_tree 会保住其它文件。
        todo: list[tuple[str, bytes, str]] = []
        for path, data in wanted:
            sha = _blob_sha(data)
            if existing.get(path) == sha:
                continue
            todo.append((path, data, sha))
        skipped = len(wanted) - len(todo)
        if not todo:
            return {"repo": owner + "/" + name, "ref": ref, "path": sub, "files": len(wanted),
                    "uploaded": 0, "skipped": skipped, "commit": "", "unchanged": True, "failed": [], "failed_count": 0}
        shas: dict[str, str] = {}
        for _path, data, sha in todo:
            if sha in shas:
                continue
            rblob = await _git(c, token, "post", base + "/git/blobs",
                              body={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
            got = str(rblob.get("sha") or "")
            if got and got != sha:
                # 我们算的 id 和服务器给的对不上，说明内容在链路上被动过，别再往下写。
                raise PackError("上传校验不一致（git blob id 对不上），已停止发布以免写坏仓库。")
            shas[sha] = got or sha
        entries = [{"path": p, "mode": "100644", "type": "blob", "sha": sha} for p, _d, sha in todo]
        rnew = await _git(c, token, "post", base + "/git/trees",
                         body={"base_tree": tree_sha, "tree": entries})
        new_tree = str(rnew.get("sha") or "")
        if not new_tree:
            raise PackError("建目录树失败：GitHub 没返回 tree sha。")
        message = "animechat: 同步表情库（" + str(len(todo)) + " 个文件）"
        rc = await _git(c, token, "post", base + "/git/commits",
                       body={"message": message, "tree": new_tree, "parents": [commit_sha]})
        new_commit = str(rc.get("sha") or "")
        if not new_commit:
            raise PackError("建提交失败：GitHub 没返回 commit sha。")
        await _git(c, token, "patch", base + "/git/refs/heads/" + quote(ref, safe=""),
                   body={"sha": new_commit})
    return {"repo": owner + "/" + name, "ref": ref, "path": sub, "files": len(wanted),
            "uploaded": len(todo), "skipped": skipped, "commit": new_commit, "unchanged": False,
            "failed": [], "failed_count": 0}
