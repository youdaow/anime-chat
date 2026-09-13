# -*- coding: utf-8 -*-
"""一键把本地表情库提交并推送到 GitHub 仓库（走 git，不需要 GitHub Token）。

用法：
    python scripts/commit_pack.py                 # 铺 + 提交 + 推送
    python scripts/commit_pack.py 自定义提交信息
    python scripts/commit_pack.py --dry-run       # 只提交，不推送

根目录的 onekey-push.bat 双击即调用本脚本。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from animechat.config import load_settings          # noqa: E402
from animechat.ghpack import build_manifest, pack_dir, parse_repo  # noqa: E402
from animechat.packpush import NO_GIT, git_available, git_sync, write_pack  # noqa: E402
from animechat.stickers import library              # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:]]
    push = "--dry-run" not in args
    message = " ".join(a for a in args if not a.startswith("--")).strip()

    root = Path(__file__).resolve().parents[1]
    if not git_available():
        print(NO_GIT)
        return 1
    s = load_settings()
    if not (s.github_repo or "").strip():
        print("还没配仓库：打开应用 设置 -> GitHub 表情仓库，填「用户名/仓库名」并保存。")
        return 1
    try:
        owner, name, ref = parse_repo(s.github_repo)
        sub = pack_dir(s.github_pack_path)
    except Exception as exc:            # PackError 的文案本来就是写给人看的
        print(str(exc))
        return 1

    manifest, files = build_manifest(library())
    if not files:
        print("本地表情库是空的（或只剩内置素材），没有可提交的东西。")
        return 1
    out = root / "pack-export"
    stat = write_pack(out, sub, manifest, files)
    print("已导出 %d 张到 %s（清理掉库里已删除的 %d 张）"
          % (stat["files"], out / sub, stat["removed"]))
    if not message:
        message = "animechat: 同步表情库（%d 张）" % stat["files"]
    try:
        r = git_sync(out, "https://github.com/%s/%s.git" % (owner, name), ref, message, push=push)
    except Exception as exc:
        print("提交失败：" + str(exc))
        print("常见原因：网络不通、GitHub 登录态过期（在命令行跑一次 git push 重新登录），或仓库被删。")
        return 1
    if r.get("pushed"):
        print("已推送 → https://github.com/%s/%s （%d 个文件变化）" % (owner, name, r["changed"]))
    else:
        print(r["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
