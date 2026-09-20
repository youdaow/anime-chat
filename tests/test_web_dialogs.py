"""真的把弹窗渲染函数在 node 里跑一遍。

为什么要有这个文件：这个仓库的前端没有构建、也没有 JS 测试框架，平时全靠
test_web_assets.py 那种「源码里有没有某个字符串」的断言。那种断言挡不住
「函数一进来就抛错」。

「新建角色」就坏过一次：openCharEditor 里的
    tweakBtn.disabled = !char.avatar_source
在 char 为 null（新建时 openCharEditor(null, ctx)）时当场 TypeError，
整个弹窗根本弹不出来，POST /api/characters 一次都没发出去。
而 test_web_assets.py 当时全绿 —— 因为它断言的正是那行崩溃代码，
等于把 bug 钉成了「必须保持的样子」。

所以这里补一层执行冒烟：真调一次 openCharEditor（弹窗）和 renderSettings（内嵌在「我」页的表单）。
node 不在环境里就跳过（不硬要求前端工具链），但 scripts/test.ps1 会带上它。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SMOKE = ROOT / "tests" / "js" / "char_editor_smoke.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="没装 node，跳过前端执行冒烟"
)


def test_char_editor_and_settings_dialogs_actually_render():
    assert SMOKE.is_file(), "冒烟脚本丢了：" + str(SMOKE)
    r = subprocess.run(
        ["node", str(SMOKE)],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "弹窗渲染冒烟没过：\n" + out
    assert "SMOKE: PASS" in out, out


def test_smoke_script_is_not_a_no_op():
    """冒烟脚本自己也得防退化：以前那种「断言全被 if 跳过」的写法，跑起来永远是 PASS。
    这里数一下它到底有几条 check()，太少就说明被掏空了。"""
    body = SMOKE.read_text(encoding="utf-8")
    n = body.count("check(")
    assert n >= 18, "冒烟脚本只剩 " + str(n) + " 条断言，八成是被掏空了"
    assert "process.exit(failed ? 1 : 0)" in body, "失败没退出码，pytest 就看不出它挂了"
