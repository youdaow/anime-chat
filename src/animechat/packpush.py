# -*- coding: utf-8 -*-
"""一键发布表情库到 GitHub 的纯逻辑：铺文件 + git 对齐 / 提交 / 推送。

放在包里（而不是只写在 scripts 里）是为了能用本地裸仓库真跑一遍：
"没变化就别提交""上次没推上去的要补推""库里删掉的图要从仓库里移除"
这几种情况光看代码判断不准，必须跑。真正推送走 git，用的是电脑上的 GitHub
登录态，不需要往应用里填 Token。
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .ghpack import MANIFEST

NO_GIT = "没找到 git 命令。装一个 Git for Windows（https://git-scm.com/download/win）后重跑一键发布。"


def git_available() -> bool:
    try:
        return subprocess.run(("git", "--version"), capture_output=True).returncode == 0
    except OSError:
        return False

def git(out: Path, *args: str, check: bool = True, env: dict | None = None) -> str:
    """在 out 里跑一条 git 命令。check=False 时失败只返回输出，不抛错。

    GIT_TERMINAL_PROMPT=0 是必须的：凭据过期时 git 会弹交互提问，双击运行的
    窗口就永久卡在那儿；关掉它才能拿到明确的失败信息。其余环境一律沿用系统的，
    别自作聪明清空（清掉 SystemRoot 会让 Windows 版 git 连域名都解析不了，踩过）。
    """
    base = dict(os.environ)
    base["GIT_TERMINAL_PROMPT"] = "0"
    if env:
        base.update(env)
    p = subprocess.run(("git",) + args, cwd=str(out), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", input="", env=base)
    if check and p.returncode != 0:
        raise GitError("git " + " ".join(args) + " 失败：" + (p.stderr or p.stdout or "").strip()[:300])
    return (p.stdout or "").strip()


def write_pack(out: Path, sub: str, manifest: dict, files: list) -> dict:
    """把仓库内容铺到 out/sub：写新的、删掉库里已不存在的。绝不动 .git。"""
    target = out / sub
    target.mkdir(parents=True, exist_ok=True)
    wanted = {MANIFEST} | {name for name, _d in files}
    for name, data in files:
        (target / name).write_bytes(data)
    (target / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    removed = 0
    for p in sorted(target.iterdir()):
        if p.is_file() and p.name not in wanted:
            p.chmod(0o600)
            p.unlink()
            removed += 1
    readme = out / "README.md"
    text = ("# 表情包库\n\n"
            "anime-chat 的表情包导出包：stickers/stickers.json 是标签清单，同目录放图片。\n\n"
            "在 anime-chat 的 设置 -> GitHub 表情仓库 里填本仓库即可拉取；"
            "同图重复拉取按内容自动合并，不会越同步越多。\n")
    if not readme.is_file() or readme.read_text(encoding="utf-8", errors="ignore") != text:
        readme.write_text(text, encoding="utf-8")
    return {"files": len(files), "removed": removed}


def git_sync(out: Path, url: str, ref: str, message: str, push: bool = True) -> dict:
    """对齐远端 → 暂存 → 提交（仅在有变化时）→ 推送。"""
    fresh = not (out / ".git").is_dir()
    if fresh:
        git(out, "init", "-q", "-b", ref)
        git(out, "config", "user.name", "animechat")
        git(out, "config", "user.email", "animechat@users.noreply.github.com")
    git(out, "config", "core.autocrlf", "false")
    cur = git(out, "remote", "get-url", "origin", check=False)
    if not cur:
        git(out, "remote", "add", "origin", url)
    elif cur != url:
        git(out, "remote", "set-url", "origin", url)
    git(out, "fetch", "-q", "origin", check=False)
    have_remote = bool(git(out, "rev-parse", "--verify", "-q", "refs/remotes/origin/" + ref, check=False))
    if fresh and have_remote:
        # 把 HEAD 挪到远端最新提交，工作区文件保持不变，这样提交是"接着推"而不是"盖掉"
        git(out, "reset", "-q", "--soft", "origin/" + ref)
        git(out, "reset", "-q")
    git(out, "add", "-A")
    staged = git(out, "diff", "--cached", "--name-only").splitlines()
    committed = False
    if staged:
        git(out, "commit", "-q", "-m", message)
        committed = True
    # 本地领先远端多少个提交（含上次推送失败留下的）：这些都必须补推，
    # 不能因为"这次没新变化"就跳过，否则仓库永远差着上次没推上去的那几个。
    ahead = 0
    if have_remote:
        ahead = len([x for x in git(out, "rev-list", "origin/" + ref + "..HEAD",
                     check=False).splitlines() if x.strip()])
    else:
        ahead = len([x for x in git(out, "rev-list", "HEAD", check=False).splitlines() if x.strip()])
    to_push = committed or ahead > 0
    result = {"committed": committed, "changed": len(staged), "ahead": ahead, "note": message}
    if not to_push:
        result["pushed"] = False
        result["note"] = "仓库已是最新，没有要提交的变化"
        return result
    if push:
        git(out, "push", "-q", "origin", "HEAD:" + ref)
        result["pushed"] = True
    else:
        result["pushed"] = False
        result["note"] = message + "（--dry-run：未推送）"
    return result
