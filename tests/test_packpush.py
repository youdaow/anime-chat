# -*- coding: utf-8 -*-
"""一键发布（packpush）用本地裸仓库真跑：不碰网络也能证明提交/推送行为正确。

为什么要真跑 git：像"这次没变化就别提交""上次提交没推上去要补推"
"从库里删掉的图要从仓库里消失"这几条，光读代码看不出对错，只有把裸仓库
当远端跑一遍才知道。
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from animechat import packpush


def _bare(tmp_path: Path) -> Path:
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(("git", "init", "--bare", "-b", "main", str(bare)),
                   capture_output=True, check=True)
    return bare


def _remote_files(bare: Path, ref: str = "main") -> list[str]:
    # -c core.quotePath=false：否则非 ASCII 路径会被转成 "\346\226\260" 这种八进制串，
    # 测试里就没法直接和中文字面名比较（踩过）。
    out = subprocess.run(("git", "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", ref),
                         cwd=str(bare), capture_output=True, text=True,
                         encoding="utf-8",
                         env={**os.environ, "LC_ALL": "C.UTF-8", "GIT_CONFIG_NOSYSTEM": "1"})
    return [x for x in out.stdout.splitlines() if x.strip()]


def _manifest(names):
    return {"stickers": [{"file": n, "label": n, "tags": [], "emotion": "neutral", "note": ""}
                         for n in names]}


def _files(names):
    return [(n, b"x" + n.encode("utf-8")) for n in names]


def _pack(tmp_path, names):
    out = tmp_path / "pack"
    files = _files(names)
    stat = packpush.write_pack(out, "stickers", _manifest(names), files)
    return out, stat


def test_first_sync_pushes_one_commit(tmp_path):
    if not packpush.git_available():
        pytest.skip("这台机器没有 git")
    bare = _bare(tmp_path)
    out, stat = _pack(tmp_path, ["开心-abc.webp", "傲娇-def.webp"])
    r = packpush.git_sync(out, str(bare), "main", "第一次同步")
    assert r["committed"] and r["pushed"], r
    assert stat["files"] == 2
    remote = _remote_files(bare)
    assert "stickers/stickers.json" in remote
    assert "stickers/开心-abc.webp" in remote, "中文文件名要能原样推上去：" + str(remote)
    assert "README.md" in remote


def test_second_sync_with_same_content_pushes_nothing(tmp_path):
    """重复点一键发布不该刷历史：没变化就一句"已是最新"，一个新提交都不建。"""
    if not packpush.git_available():
        pytest.skip("这台机器没有 git")
    bare = _bare(tmp_path)
    out, _ = _pack(tmp_path, ["开心-abc.webp"])
    packpush.git_sync(out, str(bare), "main", "first")
    commits_before = subprocess.run(("git", "rev-list", "--count", "main"), cwd=str(bare),
                                    capture_output=True, text=True).stdout.strip()
    out2, _ = _pack(tmp_path, ["开心-abc.webp"])
    r = packpush.git_sync(out2, str(bare), "main", "second")
    assert r["committed"] is False and r["pushed"] is False, r
    assert "最新" in r["note"], r["note"]
    commits_after = subprocess.run(("git", "rev-list", "--count", "main"), cwd=str(bare),
                                   capture_output=True, text=True).stdout.strip()
    assert commits_before == commits_after, "无变化却多出了提交：" + commits_before + " → " + commits_after


def test_added_and_removed_stickers_are_reflected(tmp_path):
    if not packpush.git_available():
        pytest.skip("这台机器没有 git")
    bare = _bare(tmp_path)
    out, _ = _pack(tmp_path, ["留着的-aaa.webp", "要删的-bbb.webp"])
    packpush.git_sync(out, str(bare), "main", "first")
    assert "stickers/要删的-bbb.webp" in _remote_files(bare)

    # 用户把"要删的"从库里删了 → 重新铺时应从仓库消失
    out2 = tmp_path / "pack"
    keep = "留着的-aaa.webp"
    add = "新加的-ccc.webp"
    stat = packpush.write_pack(out2, "stickers", _manifest([keep, add]),
                               _files([keep, add]))
    assert stat["removed"] == 1, "应清掉仓库里已不存在的图：" + str(stat)
    r = packpush.git_sync(out2, str(bare), "main", "second")
    assert r["committed"] and r["pushed"], r
    remote = _remote_files(bare)
    assert "stickers/要删的-bbb.webp" not in remote, "删掉的图还留在仓库里：" + str(remote)
    assert "stickers/新加的-ccc.webp" in remote


def test_dry_run_then_real_push_recovers_unpushed_commit(tmp_path):
    """上次提交成功但推送失败（断网/登录态过期）后，下次即使"没新变化"也必须补推。"""
    if not packpush.git_available():
        pytest.skip("这台机器没有 git")
    bare = _bare(tmp_path)
    out, _ = _pack(tmp_path, ["开心-abc.webp"])
    r1 = packpush.git_sync(out, str(bare), "main", "只提交不推送", push=False)
    assert r1["committed"] and r1["pushed"] is False, r1
    assert _remote_files(bare) == [], "dry-run 不该推任何东西"

    r2 = packpush.git_sync(out, str(bare), "main", "补推", push=True)
    assert r2["pushed"] is True, "本地领先的提交必须补推：" + str(r2)
    assert r2["ahead"] >= 1, str(r2)
    assert "stickers/stickers.json" in _remote_files(bare)


def test_write_pack_keeps_git_dir_intact(tmp_path):
    """曾经用 shutil.rmtree 整包重建，撞上 .git 只读对象直接崩；只该动表情文件。"""
    if not packpush.git_available():
        pytest.skip("这台机器没有 git")
    bare = _bare(tmp_path)
    out, _ = _pack(tmp_path, ["开心-abc.webp"])
    packpush.git_sync(out, str(bare), "main", "first")
    assert (out / ".git").is_dir()
    # 再铺一次：必须还能跑（旧实现会在这里抛 PermissionError）
    out2, _ = _pack(tmp_path, ["开心-abc.webp", "傲娇-def.webp"])
    r = packpush.git_sync(out2, str(bare), "main", "second")
    assert r["pushed"] is True, r
    assert (out2 / ".git").is_dir()


def test_stickers_json_content_is_readable(tmp_path):
    if not packpush.git_available():
        pytest.skip("这台机器没有 git")
    out, _ = _pack(tmp_path, ["开心-abc.webp"])
    raw = (out / "stickers" / "stickers.json").read_text(encoding="utf-8")
    obj = json.loads(raw)
    assert obj["stickers"][0]["file"] == "开心-abc.webp"
    assert "sticker" in raw.lower() or "stickers" in obj


def test_bat_launcher_stays_pure_ascii(tmp_path):
    """cmd 会把 ( ) 块里的多字节中文咬断（实测报 '环境：先跑' is not recognized）。
    所以 bat 只能 ASCII，中文一律交给 python 打印。"""
    bat = Path(__file__).resolve().parents[1] / "onekey-push.bat"
    assert bat.is_file(), "根目录要有一键发布的 bat"
    data = bat.read_bytes()
    bad = [i for i, b in enumerate(data) if b > 127]
    assert not bad, "bat 里出现非 ASCII 字节（cmd 会解析错）：" + str(bad[:5])
    text = data.decode("ascii")
    assert "commit_pack.py" in text
    assert "PYTHONUTF8" in text, "不设 UTF-8 的话 python 的中文输出会乱码"
    assert "\r\n" in text, "bat 必须 CRLF 换行"
