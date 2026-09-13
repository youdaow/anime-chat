"""双击启动飞书桥接用的入口脚本（配合根目录的 feishu.bat）。

为什么不用纯 .bat 写这些：cmd 按系统 ANSI 代码页读批处理文件，中文 echo 十有
八九是乱码；逻辑放进 Python，输出走 UTF-8，窗口里就是正常中文。

它做三件事，顺序不能换：
  1. 自检凭据。缺 App ID / Secret 就别白起一个连不上的进程。
  2. 确保本机 animechat 网页服务在跑。桥接要调 /api/chat，服务没起的话飞书那边
     只会收到一句「连不上本机的 animechat」，容易被误以为桥接坏了。
  3. 在当前窗口起桥接。日志看得见，Ctrl+C 就停 —— 故意不开新窗口，
     免得出现「有两个桥接在抢消息」这种更难查的局面。
"""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _boot_animechat():
    """import animechat，失败就把 <项目>/src 塞进 sys.path 再试一次。

    正常装法（pip install -e）不需要这个，但从别的目录双击、或者虚拟环境被
    重建过的时候，多这一道能省一次「找不到模块」的来回。
    """
    try:
        import animechat  # noqa: F401
        return
    except ImportError:
        sys.path.insert(0, str(ROOT / "src"))
        import animechat  # noqa: F401


def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.6)
        return s.connect_ex(("127.0.0.1", port)) == 0


def health_ok(port: int, timeout: float = 25.0) -> bool:
    """等 /api/health 返回 ok:true。

    只测端口通不通不够：8899 可能被别的程序占着，那种情况要明说，
    不然用户会以为「服务起来了啊怎么还不行」。
    """
    url = f"http://127.0.0.1:{port}/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.5) as resp:
                if json.loads(resp.read().decode("utf-8", "ignore")).get("ok"):
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.5)
    return False


def start_web_server(port: int) -> None:
    """另开一个控制台窗口跑网页服务，让它一直活着。

    用新窗口而不是隐藏进程：那是个会打日志的常驻服务，藏在后台出问题就查不着了。
    """
    cmd = [sys.executable, "-m", "animechat.cli", "run", "--port", str(port)]
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    subprocess.Popen(cmd, cwd=str(ROOT), creationflags=flags)


def main(argv: list[str]) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    try:
        _boot_animechat()
    except ImportError as exc:
        print("[X] 找不到 animechat 包：" + str(exc))
        print("    先跑一次 scripts\\install.ps1 装环境。")
        return 1

    from animechat import feishu
    from animechat.config import load_settings

    dry = "--dry-run" in argv       # 只检查不启动，方便排查
    s = load_settings()

    print("=" * 62)
    print(" 飞书桥接启动自检")
    print("=" * 62)
    bad = 0
    for name, good, note in feishu.check_config(s):
        print(f"  [{'OK  ' if good else 'FAIL'}] {name}  ->  {note}")
        bad += 0 if good else 1
    if bad:
        print("-" * 62)
        print("[X] 还有 " + str(bad) + " 项没就绪，照上面 FAIL 的说明补一下。")
        print("    App ID / App Secret 在 open.feishu.cn/app 的应用详情页")
        print("    「凭证与基础信息 → 应用凭证」，填到网页 设置 → 飞书 里保存。")
        return 1

    port = int(s.port or 8899)
    if port_open(port):
        if health_ok(port, timeout=3.0):
            print(f"  [OK  ] 本机服务  ->  http://127.0.0.1:{port} 已在跑")
        else:
            print(f"  [WARN] {port} 端口有东西，但应答的不是 animechat。")
            print("        多半是别的程序占着这个端口。要么把它关掉，要么改端口：")
            print(f"        {sys.executable} -m animechat.cli run --port 8900")
            print("        然后在设置里把「桥接回调地址」填成 http://127.0.0.1:8900")
            return 1
    else:
        print(f"  [..  ] 本机服务没在跑，正在启动 http://127.0.0.1:{port} ...")
        start_web_server(port)
        if not health_ok(port):
            print("[X] 网页服务 25 秒内没起来。")
            print("    看看刚弹出来的那个窗口里写了什么，贴给我。")
            return 1
        print(f"  [OK  ] 本机服务  ->  http://127.0.0.1:{port} 已就绪")

    if dry:
        print("-" * 62)
        print("[OK] 自检通过（--dry-run：没有真的启动桥接）")
        return 0

    print("-" * 62)
    print(" 桥接已启动。下面这些要知道：")
    print("   · 这个窗口请一直开着，关了飞书立刻就不回话")
    print("   · 想停就按 Ctrl+C")
    print("   · 手机飞书里搜你的应用名，点开就能聊；发 /帮助 看命令")
    print("   · 表情图不显示的话，看这个窗口有没有打 [feishu] 开头的行")
    print("=" * 62)
    try:
        return int(feishu.run_bot(s, echo=print) or 0)
    except KeyboardInterrupt:
        print("\n已退出。")
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
