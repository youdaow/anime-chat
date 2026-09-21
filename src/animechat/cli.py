"""命令行入口：run / doctor / build-assets / add-sticker / import-card / export-card / feishu / invite。"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from . import config, providers


def _fix_console() -> None:
    """Windows 控制台默认跟随系统 ANSI 代码页（简体中文机器是 GBK），中文输出会糊成方块。
    先把控制台输入输出代码页切到 UTF-8，再让 Python 的两个流也用 UTF-8，两边对齐才不乱。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    except Exception:
        pass                      # 输出被重定向、没有控制台时忽略
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:
                pass


def _banner(url: str) -> str:
    s = config.load_settings()
    lines = [
        "",
        "  二次元角色聊天 AI · animechat",
        "  界面地址   " + url,
        "  数据目录   " + str(config.DATA_DIR),
        "  模型       " + ("Mock（未填 Key，回复是本机假生成的）" if s.mock_mode
                          else s.llm_base_url + "  model=" + s.llm_model),
        "  表情库     " + str(config.user_sticker_dir()) + "（往里丢图会自动入库）",
        "",
    ]
    return "\n".join(lines)


def cmd_run(args: argparse.Namespace) -> int:
    import uvicorn

    s = config.load_settings()
    host = args.host or s.host
    port = args.port or s.port
    if args.mock:
        config.save_settings({"llm_api_key": ""})
    print(_banner("http://" + host + ":" + str(port) + "/"))
    uvicorn.run("animechat.server:app", host=host, port=port, reload=args.reload, log_level=args.log_level)
    return 0


def cmd_feishu(args: argparse.Namespace) -> int:
    """飞书桥接。单独一个命令而不是塞进 run：它是常驻进程，且只在配了凭据时才该跑。"""
    from . import feishu

    s = config.load_settings()
    if args.check:
        rows = feishu.check_config(s)
        print("飞书桥接自检")
        print("-" * 62)
        for name, good, detail in rows:
            print("  [" + ("OK  " if good else "FAIL") + "] " + name
                  + ("  -> " + detail if detail else ""))
        print("-" * 62)
        ready = all(g for _, g, _ in rows)
        print("可以跑了：animechat feishu" if ready else "还差东西，见上面 FAIL")
        print("提示：要用「/角色」下拉卡片换人，得在飞书后台『事件与回调』里再加一条")
        print("      回调 card.action.trigger（订阅方式同样选『使用长连接』），加完重新发布版本。")
        print("      不加也不影响正常聊天：文字版 /角色 名字 照常能换人，只是点不了下拉。")
        return 0 if ready else 1
    if args.unbind or args.bindings:
        from .characters import book
        from .store import store

        db = store()
        if args.bindings:
            rows = feishu.list_bindings(db)
            if not rows:
                print("还没有任何飞书会话绑过角色")
                return 0
            names = {c.id: c.name for c in book().list()}
            print("飞书会话绑定")
            print("-" * 62)
            for chat_id, cid in rows.items():
                print("  " + chat_id + "  ->  " + names.get(cid, cid + "（这个角色已经没了）"))
            return 0
        n = (feishu.unbind_all(db) if args.unbind == "all"
             else feishu.unbind_character(db, args.unbind))
        print(f"清了 {n} 条绑定" if n else "没有匹配的绑定")
        return 0
    return feishu.run_bot(s, echo=print)


def cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    print("animechat 自检  (python " + sys.version.split()[0] + ")")
    print("-" * 62)

    def check(name: str, good: bool, detail: str = "", warn: bool = False) -> None:
        nonlocal ok
        mark = "OK  " if good else ("WARN" if warn else "FAIL")
        print("  [" + mark + "] " + name + ("  -> " + detail if detail else ""))
        if not good and not warn:
            ok = False

    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        import uvicorn  # noqa: F401
        check("依赖 fastapi/httpx/uvicorn", True)
    except Exception as exc:
        check("依赖 fastapi/httpx/uvicorn", False, str(exc))

    try:
        import PIL  # noqa: F401
        check("Pillow（表情与头像生成）", True, PIL.__version__ if hasattr(PIL, "__version__") else "")
    except Exception as exc:
        check("Pillow（表情与头像生成）", False, str(exc), warn=True)
        print("         没有 Pillow 就无法程序化生成表情/头像，跑 animechat build-assets 前请先安装")

    try:
        config.ensure_dirs()
        probe = config.DATA_DIR / ".write-probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        check("数据目录可写", True, str(config.DATA_DIR))
    except Exception as exc:
        check("数据目录可写", False, str(exc))

    from .stickers import library

    lib = library()
    st = lib.stats()
    check("内置表情已生成", st["builtin"] > 0, str(st["builtin"]) + " 张" +
          ("" if st["builtin"] else "（跑 animechat build-assets）"), warn=st["builtin"] == 0)
    check("用户表情目录", True, (str(st["user"]) + " 张") if st["user"] else "空（往 data/stickers 丢图即可）")

    from .characters import book
    from .media import resolve as resolve_media

    chars = book().list()
    check("角色库", bool(chars), str(len(chars)) + " 个角色")
    missing_avatar = [c.id for c in chars if not c.avatar or resolve_media(c.avatar) is None]
    check("角色头像齐全", not missing_avatar, ",".join(missing_avatar) or "全部就位", warn=bool(missing_avatar))

    s = config.load_settings()
    # Mock 现在有两种来路：选了内置 Mock，或者压根没填 Key —— doctor 要说清是哪一种，
    # 不然用户看着一个空 Base URL 不知道为什么算「配置好了」
    if s.mock_mode:
        why = ("内置 Mock（不联网）" if providers.family_of(s.llm_provider) == "mock"
               else "没填 Key，走内置 Mock")
    else:
        why = s.llm_base_url + " / " + s.llm_model
    check("模型配置", True, why, warn=s.mock_mode)
    if not s.mock_mode:
        import httpx

        base = s.llm_base_url.rstrip("/")
        if not base.endswith("/v1"):
            base += "/v1"
        try:
            r = httpx.get(base + "/models", headers={"Authorization": "Bearer " + s.llm_api_key}, timeout=6.0)
            check("模型服务可达", r.status_code < 400, "HTTP " + str(r.status_code), warn=r.status_code >= 400)
            if r.status_code in (401, 403):
                print("         地址通、Key 不通：核对这家平台给你的 Key，以及 Base URL 和 Key 是不是同一家")
        except Exception as exc:
            check("模型服务可达", False, str(exc)[:90], warn=True)
            print("         连不上这个地址：先确认 Base URL 填对（一般以 /v1 结尾），再看网络和代理")

    from .store import store

    stats = store().stats()
    check("SQLite", True, str(stats["conversations"]) + " 会话 / " + str(stats["messages"]) + " 消息")

    # 飞书没配是正常状态，不是故障，所以只 WARN 不 FAIL；配了就把差的那几项说清。
    from . import feishu as _fs

    rows = _fs.check_config(s)
    on = bool((s.feishu_app_id or "").strip() or (s.feishu_app_secret or "").strip())
    missing = [n for n, g, _ in rows if not g]
    check("飞书桥接", not missing,
          ("已配置" if not missing else "缺 " + "、".join(missing)) if on else "未配置（可选）",
          warn=True)
    print("-" * 62)
    print("结论：" + ("一切正常" if ok else "有阻塞项，见上面 FAIL"))
    return 0 if ok else 1


def cmd_build_assets(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parents[2]
    for script in ("make_sample_stickers.py", "make_avatars.py"):
        path = root / "scripts" / script
        if not path.is_file():
            print("[跳过] 找不到 " + str(path))
            continue
        print("== 运行 " + script)
        code = _runpy(path)
        if code != 0:
            return code
    from .stickers import library

    library().refresh()
    print("表情库现在认识：" + json.dumps(library().stats(), ensure_ascii=False))
    return 0


def _runpy(path: Path) -> int:
    import runpy
    import sys

    old = sys.argv
    sys.argv = [str(path)]
    try:
        runpy.run_path(str(path), run_name="__main__")
        return 0
    except SystemExit as exc:  # 脚本自己 exit 了
        return int(exc.code or 0)
    except Exception as exc:
        print("[失败] " + str(exc))
        return 1
    finally:
        sys.argv = old


def cmd_add_sticker(args: argparse.Namespace) -> int:
    src = Path(args.path)
    if not src.is_file():
        print("文件不存在：" + str(src))
        return 1
    from .stickers import library

    # add_file 自己会复制到 data/stickers 并写 meta；先手动拷一份会留下没标签的孤儿文件
    st = library().add_file(src, tags=[t.strip() for t in args.tags.split(",") if t.strip()],
                            label=args.label or src.stem, emotion=args.emotion)
    print("已入库：" + st.id + "  标签=" + "、".join(st.tags) + "  情绪=" + st.emotion)
    return 0


def cmd_unhide_stickers(args: argparse.Namespace) -> int:
    """界面上"删除"内置表情其实是隐藏（素材在包里），这条命令把它们放回来。"""
    from .stickers import library

    n = library().unhide_builtins()
    print(("已恢复 " + str(n) + " 张内置表情") if n else "没有被隐藏的内置表情")
    return 0


def cmd_import_card(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.is_file():
        print("文件不存在：" + str(path))
        return 1
    from .characters import book

    char = book().import_payload(path.read_bytes(), path.name)
    print("已导入角色：" + char.name + " (id=" + char.id + ", 来源=" + char.source + ")")
    return 0


def cmd_export_card(args: argparse.Namespace) -> int:
    from .characters import book

    out = Path(args.out)
    if out.suffix.lower() == ".png":
        out.write_bytes(book().export_png(args.cid))
    else:
        out.write_text(json.dumps(book().export_card(args.cid), ensure_ascii=False, indent=2), encoding="utf-8")
    print("已导出：" + str(out.resolve()))
    return 0


def _invite_code(value: str | None) -> str:
    """取口令：没给就随机发一个；给 "-" 就从键盘读（不回显）。

    为什么要留 stdin 这条路：他换口令的理由正是"旧口令出现在别的地方了"，而命令行参数
    会留在 shell 历史里 —— 同一个毛病换个地方犯。写 `-` 就绕开这一层。
    """
    from . import auth
    if value is None:
        return auth.new_code()
    if value == "-":
        import sys
        if not sys.stdin.isatty():
            # 管道里喂进来的：没有终端可以关回显，那就别一边做不到一边吐一句
            # "Password input may be echoed" 吓人（getpass 的默认行为）。
            return (sys.stdin.readline() or "").strip()
        import getpass
        return getpass.getpass("口令（输入时不回显）：")
    return value


def cmd_invite(args) -> int:
    """发 / 列 / 改 / 收访问口令。动词是子命令（`invite add 小明`、`invite list`、
    `invite set <id> 新口令`、`invite revoke <id>`、`invite token`）—— README 和帮助里都
    这么写，让人打完报「不认得参数」是最难看的失败（第一版就是这样，命令上了服务器才发现）。

    口令只在这一刻打印一次，库里存的是 salted scrypt 摘要 —— 忘了就重新发一个，
    去翻数据库也翻不出来。

    顺手把两个密钥补齐：auth_session_secret 空着的时候登录一定失败（auth.unsign 拒绝
    没有密钥的签名，这是故意的 —— 宁可登录不上，也不留一个谁都能伪造 cookie 的门）。
    """
    from . import auth
    from .config import load_settings, save_settings
    from .store import store

    action = getattr(args, "inv_cmd", None) or "list"
    s = load_settings()
    db = store()

    if action == "token":
        if not s.auth_bridge_token:
            save_settings({"auth_bridge_token": auth.new_secret()})
            s = load_settings()
        print("飞书桥令牌（存进 settings.json 的 auth_bridge_token，桥自己会带上）：")
        print("  " + s.auth_bridge_token)
        return 0
    if action == "revoke":
        ok = db.revoke_visitor(args.vid)
        print(("已撤销 " + args.vid + "（他手上的 cookie 当场失效）") if ok
              else ("没有这个访客：" + args.vid))
        return 0 if ok else 1
    if action == "set":
        code = _invite_code(args.code)
        bad = auth.weak_reason(code)
        if bad:
            print("这个口令不能用：" + bad)
            return 2
        if not db.set_visitor_code(args.vid, auth.hash_code(code), auth.bucket_of(code)):
            print("没有这个访客：" + args.vid + "（先看一眼 invite list）")
            return 1
        print("已换好口令（只显示这一次）: " + code)
        print("注意：换口令**不会**把已经登录的设备踢下线 —— cookie 认的是访客 id。")
        print("      要让某人立刻失去权限：invite revoke " + args.vid)
        return 0
    if action == "list":
        rows = db.list_visitors()
        if not rows:
            print("还没有访客。发一个：python -m animechat.cli invite add 名字")
            return 0
        for r in rows:
            seen = (time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_seen"]))
                    if r["last_seen"] else "从没登录过")
            print("  %-10s %-12s %-4s 上次登录 %s"
                  % (r["id"], r["name"] or "（没名字）", "主人" if r["admin"] else "访客", seen))
        return 0

    patch = {}
    if not s.auth_session_secret:
        patch["auth_session_secret"] = auth.new_secret()
    if not s.auth_bridge_token:
        patch["auth_bridge_token"] = auth.new_secret()
    if patch:
        save_settings(patch)

    code = _invite_code(getattr(args, "code", None))
    bad = auth.weak_reason(code)
    if bad:
        print("这个口令不能用：" + bad)
        return 2
    vid = db.add_visitor(args.name, auth.hash_code(code), auth.bucket_of(code), admin=args.admin)
    print("访客 id : " + vid)
    print("访问口令: " + code + "   （只显示这一次，忘了就重发一个）")
    if not s.auth_enabled:
        print("")
        print("注意：这个命令行看到的认证是关的。")
        print("  服务器上多半是 systemd 用 Environment=ANIMECHAT_AUTH_ENABLED=1 打开的，")
        print("  而这个变量只在服务进程里，手动跑 cli 读不到 —— 想确认就试一次：")
        print("    curl -s -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:8899/api/conversations")
        print("  返回 401 就是已经开了。真没开的话：systemctl edit animechat 加那行，再 restart。")
    return 0


def main(argv: list[str] | None = None) -> int:
    _fix_console()
    parser = argparse.ArgumentParser(prog="animechat", description="二次元角色聊天 AI")
    sub = parser.add_subparsers(dest="cmd")

    p_run = sub.add_parser("run", help="启动应用（默认）")
    p_run.add_argument("--host", default=None)
    p_run.add_argument("--port", type=int, default=None)
    p_run.add_argument("--reload", action="store_true", help="开发模式，改代码自动重载")
    p_run.add_argument("--mock", action="store_true", help="强制清空 Key，用内置假模型跑流程")
    p_run.add_argument("--log-level", default="info")
    p_run.set_defaults(fn=cmd_run)

    p_doc = sub.add_parser("doctor", help="环境与素材自检")
    p_doc.set_defaults(fn=cmd_doctor)

    p_build = sub.add_parser("build-assets", help="程序化生成内置表情包和角色头像")
    p_build.set_defaults(fn=cmd_build_assets)

    p_add = sub.add_parser("add-sticker", help="把一张图加进表情库")
    p_add.add_argument("path")
    p_add.add_argument("--tags", default="", help="逗号分隔的触发标签")
    p_add.add_argument("--label", default="", help="显示名")
    p_add.add_argument("--emotion", default="", help="情绪 key，如 happy/sad/angry")
    p_add.set_defaults(fn=cmd_add_sticker)

    p_unh = sub.add_parser("unhide-stickers", help="恢复被隐藏的内置表情")
    p_unh.set_defaults(fn=cmd_unhide_stickers)

    p_imp = sub.add_parser("import-card", help="导入 JSON / PNG 角色卡")
    p_imp.add_argument("path")
    p_imp.set_defaults(fn=cmd_import_card)

    p_exp = sub.add_parser("export-card", help="导出角色卡（.json 或 .png）")
    p_exp.add_argument("cid")
    p_exp.add_argument("out")
    p_exp.set_defaults(fn=cmd_export_card)

    p_fs = sub.add_parser("feishu", help="飞书机器人桥接（单独进程，长连接，不需要公网地址）")
    p_fs.add_argument("--check", action="store_true", help="只自检配置，不连飞书")
    p_fs.add_argument("--bindings", action="store_true", help="列出飞书会话 ↔ 角色的绑定")
    p_fs.add_argument("--unbind", default="", metavar="角色id|all",
                      help="清掉某个角色（或全部）的飞书绑定")
    p_fs.set_defaults(fn=cmd_feishu)

    p_inv = sub.add_parser("invite", help="发 / 列 / 收访问口令（把这套界面交给别人用之前先跑它）")
    inv = p_inv.add_subparsers(dest="inv_cmd")
    p_inv_add = inv.add_parser("add", help="发一个新口令（口令只显示这一次）")
    p_inv_add.add_argument("name", help="给谁用的，例如「小明」")
    p_inv_add.add_argument("--admin", action="store_true",
                           help="这个人也是主人身份：能改角色/表情/设置，并看得见所有人的会话")
    p_inv_add.add_argument("--code", default=None, metavar="自定义口令",
                           help="自己定口令（默认随机发一个）。不想留在 shell 历史里就写 --code - ，会问你输入")
    inv.add_parser("list", help="列出现有访客")
    p_inv_set = inv.add_parser("set", help="换掉某个身份的口令；已登录的设备不会被踢下线")
    p_inv_set.add_argument("vid", metavar="访客id")
    p_inv_set.add_argument("code", nargs="?", default=None, metavar="自定义口令",
                           help="新口令。留空 = 随机发一个；写 - = 输入时不回显")
    p_inv_rev = inv.add_parser("revoke", help="撤销一个访客，他的 cookie 当场失效")
    p_inv_rev.add_argument("vid", metavar="访客id")
    inv.add_parser("token", help="打印飞书桥用的 x-animechat-token")
    p_inv.set_defaults(fn=cmd_invite)

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"run", "doctor", "build-assets", "add-sticker", "unhide-stickers",
                                   "import-card", "export-card", "feishu", "invite"}:
        argv = ["run"] + argv
    args = parser.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":  # python -m animechat.cli 也要能跑
    sys.exit(main())
