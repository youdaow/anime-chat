"""静态资源不变量。最容易踩的两个坑都在这里锁住。"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "src" / "animechat" / "web"
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "style.css").read_text(encoding="utf-8")
JS = "\n".join((WEB / n).read_text(encoding="utf-8") for n in ("app.js", "panels.js", "ui.js", "sfx.js"))
UI = (WEB / "ui.js").read_text(encoding="utf-8")


def test_api_error_shows_fastapi_detail():
    """FastAPI 的 HTTPException 把文案放在 detail；前端要接住，
       否则用户只会看到泛化的 "HTTP 400"。"""
    assert "payload.detail" in UI


def test_web_assets_are_revalidated():
    """前端没有版本号，必须每次回源校验，否则改了样式刷新还是旧的。"""
    from fastapi.testclient import TestClient

    from animechat.server import create_app

    with TestClient(create_app()) as client:
        for path in ("/web/style.css", "/web/app.js", "/web/ui.js", "/web/sfx.js"):
            got = client.get(path)
            assert got.status_code == 200, path
            assert "no-cache" in got.headers.get("cache-control", ""), path + " -> " + str(got.headers.get("cache-control"))
        assert "no-store" in client.get("/").headers.get("cache-control", "")


def test_hidden_attribute_still_hides_things():
    # 作者样式里的 display 会盖掉浏览器默认样式表的 [hidden]{display:none}，
    # 少了这条兜底，弹窗遮罩会在关闭状态下铺满整屏吃掉所有点击。
    assert re.search(r"\[hidden\]\s*\{[^}]*display:\s*none\s*!important", CSS), \
        "style.css 缺少 [hidden] { display: none !important }"


def hidden_class_has_display_rule():
    """确认那条兜底不是摆设：确实有带 hidden 的元素自己设了 display。"""
    classes = set()
    for tag in re.findall(r"<[^>]*\bhidden\b[^>]*>", HTML):
        for cls in re.findall(r'class="([^"]+)"', tag):
            classes.update(cls.split())
    offenders = set()
    for cls in classes:
        for block in re.findall(r"\." + re.escape(cls) + r"\b[^{]*\{([^}]*)\}", CSS):
            if re.search(r"display:\s*(grid|flex|block|inline-flex|inline-block)", block):
                offenders.add(cls)
    return offenders


def test_override_is_load_bearing():
    assert hidden_class_has_display_rule(), "带 hidden 的元素都没设 display，兜底规则可以简化"


def test_every_js_referenced_id_exists_in_html():
    html_ids = set(re.findall(r'\bid="([^"]+)"', HTML))
    used = set(re.findall(r'qs\("([^"]+)"\)', JS)) | set(re.findall(r'getElementById\("([^"]+)"\)', JS))
    missing = sorted(used - html_ids)
    assert not missing, "JS 引用了 index.html 里不存在的 id：" + ", ".join(missing)


def test_no_probe_or_debug_pages_shipped():
    leftovers = sorted(p.name for p in WEB.iterdir() if p.name.startswith("_") or ".probe" in p.name)
    assert not leftovers, "web 目录里混进了调试文件：" + ", ".join(leftovers)


def test_messages_container_starts_empty():
    """#messages 每次渲染都被 renderThread 清空，所以它在 HTML 里必须是空壳。
    旧版在里面放了静态 #empty 占位：第一次渲染就把它销毁，之后
    qs("empty").hidden = true 抛 "Cannot set properties of null"，
    openConversation 在 renderThread 之前就中断——表现是切换角色后
    标题变了、聊天窗口却不动，错误还被 catch 成一条 toast。"""
    m = re.search(r'<div class="messages" id="messages">(.*?)</div>', HTML, re.S)
    assert m, "index.html 里找不到 #messages"
    assert not m.group(1).strip(), "#messages 里不得有静态内容，会被 renderThread 清掉：" + m.group(1).strip()[:60]


def test_empty_state_is_drawn_in_javascript():
    """配套的另一半：空状态由 renderThread 自己画，不再依赖静态占位节点。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'class: "empty"' in app, "renderThread 里要自己画空状态"
    assert 'id="empty"' not in HTML, "空状态不该是静态节点"
    assert 'qs("empty")' not in app and "getElementById(\"empty\")" not in app, "别再引用被销毁的 #empty"


def test_handle_event_does_not_touch_closure_locals():
    """handleEvent 是顶层函数，text/textNode 属于 createLiveBubble 的闭包。
    在那儿写 text = data.content 会 ReferenceError（ES 模块强制严格模式），
    结果每条正常回复的 done 收尾都把自己的气泡报成"出错了：text is not defined"。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    start = app.index("function handleEvent(")
    depth, end = 0, start
    for i in range(start, len(app)):
        if app[i] == "{":
            depth += 1
        elif app[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    body = app[start:end + 1]
    for token in ("textNode", "text ="):
        assert token not in body, "handleEvent 里出现了闭包变量 " + token + "，要走 live.setContent()"


def test_app_uses_inner_scroll_not_page_scroll():
    """整页只有 #messages 一个滚动区。少一条 min-height:0 / 行高约束，.main 就会被
    长会话撑到比视口高，body(overflow:hidden) 被 focus 滚到底，侧栏整块滚出屏幕——
    用户看到的就是"左边什么都没有、点不了角色"，而且滚不回来。"""
    def block_of(selector):
        i = CSS.index(selector)
        return CSS[i:CSS.index("}", i)]
    app = block_of("#app {")
    assert "grid-template-rows: minmax(0" in app, "#app 必须锁死行高，否则列会被内容撑高：" + app.strip()
    assert "height: 100vh" in app
    main = block_of(".main {")
    assert "min-height: 0" in main and "overflow: hidden" in main, ".main 要能收缩并自锁：" + main.strip()
    msgs = block_of(".messages {")
    assert "overflow: auto" in msgs and "min-height: 0" in msgs, "#messages 才是唯一该滚动的区域：" + msgs.strip()
    side = block_of(".side-scroll {")
    assert "overflow: auto" in side and "min-height: 0" in side
    assert re.search(r"html \{[^}]*overflow: hidden", CSS), "html 也得禁滚动，focus 才滚不动页面"


def test_main_input_focus_never_scrolls_page():
    """focus 默认会滚动最近的滚动祖先；主输入框曾被它把整页顶走（见上一条）。
    弹窗里的输入框反过来需要滚动定位，所以只约束主输入框这一个。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    bare = [ln.strip() for ln in app.splitlines() if re.search(r'qs\("input"\)\.focus\(\)', ln)]
    assert not bare, 'qs("input").focus() 必须带 preventScroll：' + str(bare[:3])
    assert app.count('focus({ preventScroll: true })') >= 3, "主输入框的三个 focus 点都要防滚动"


def media_block(css: str, marker: str) -> str:
    """取出某条 @media 的整块（到那条顶格的 } 为止）。

    这些测试以前靠「块里恰好有一句 max-width: 88%」来定位结尾，数值一改测试就
    自己崩了 —— 定位样式不该依赖样式的内容。

    必须从行首匹配：注释里也会提到 "@media (max-width: 900px)" 这种字面量，
    拿 index() 找到的第一个匹配会落在注释中间，切出来的块驴唇不对马嘴。"""
    m = re.search(r"^" + re.escape(marker), css, re.M)
    assert m, "CSS 里找不到顶格的 " + marker
    i = m.start()
    return css[i:css.index("\n}", i) + 2]


def test_narrow_screen_is_push_navigation():
    """\u7a84\u5c4f(<900px)\u662f Telegram \u5f0f\u63a8\u5165\u5bfc\u822a\uff0c\u4e0d\u662f\u53cc\u680f\u5e76\u6392\u3001\u4e5f\u4e0d\u662f\u628a\u5165\u53e3\u6574\u6392\u85cf\u6389\u3002

    \u65e7\u5b9e\u73b0\u628a\u641c\u7d22\u6846\u3001\uff0b\u65b0\u5efa / AI \u751f\u6210 / \u5bfc\u5165\u3001\u89d2\u8272\u4e0e\u4f1a\u8bdd\u7684\u300c\u22ef\u300d\u83dc\u5355\u5168\u90e8 display:none\uff0c
    \u89e6\u5c4f\u60ac\u505c\u4e0d\u51fa\u8fd9\u4e9b\u6309\u94ae\u2014\u2014\u7b49\u4e8e\u5728\u624b\u673a\u4e0a\u628a\u8fd9\u4e9b\u529f\u80fd\u6574\u4e2a\u5220\u4e86\u3002\u73b0\u5728\u7a84\u5c4f\u662f\u5355\u680f\uff1a
    \u5217\u8868\u4e0e\u804a\u5929\u533a\u5404\u5360\u6ee1\u4e00\u5c4f\u53e0\u5728\u540c\u4e00\u683c\uff0c\u804a\u5929\u533a\u9ed8\u8ba4\u6ed1\u5230\u53f3\u4fa7\u5c4f\u5916\uff0c\u70b9\u4f1a\u8bdd\u6ed1\u5165\u3001\u5934\u90e8 \u2190 \u6ed1\u56de\uff0c
    \u5217\u8868\u5c4f\u59cb\u7ec8\u5c31\u662f\u5b8c\u6574\u7248\uff08\u6240\u6709\u5165\u53e3\u90fd\u5728\uff09\u3002
    """
    block = media_block(CSS, "@media (max-width: 900px)")
    for gone in (".side-search { display: none", ".ghost-only { display: none",
                 ".side-nav { display: none", ".side-tools { display: none",
                 ".char-meta { display: none", ".conv-meta { display: none"):
        assert gone not in block, "\u7a84\u5c4f\u4e0d\u8bb8\u6574\u6392\u85cf\u6389\u5165\u53e3\uff08\u5217\u8868\u5c4f\u5c31\u662f\u5b8c\u6574\u7248\uff09\uff1a" + gone
    assert "grid-column: 1" in block, "\u4fa7\u680f\u4e0e\u804a\u5929\u533a\u5f97\u53e0\u5728\u540c\u4e00\u683c\uff0c\u5426\u5219\u7a84\u5c4f\u8fd8\u662f\u53cc\u680f\uff1a" + block[:200]
    assert "translateX(100%)" in block, "\u804a\u5929\u533a\u8981\u9ed8\u8ba4\u6ed1\u51fa\u53f3\u4fa7\u5c4f\u5916\uff0c\u624d\u80fd\u5148\u770b\u5230\u5217\u8868"
    assert "#app.show-chat .main { transform: none" in block, "\u70b9\u5f00\u4f1a\u8bdd\u8981\u628a\u804a\u5929\u533a\u6ed1\u56de\u6765\u76d6\u4f4f\u5217\u8868"
    assert "transition: transform" in block, "\u63a8\u5165 / \u6ed1\u51fa\u5f97\u6709\u8fc7\u6e21\uff0c\u786c\u5207\u770b\u8d77\u6765\u50cf\u574f\u4e86"
    assert "#btn-collapse" in block, "\u7a84\u5c4f\u6ca1\u6709\u300c\u6536\u8d77 / \u5c55\u5f00\u300d\u8fd9\u56de\u4e8b\uff0c\u90a3\u4e2a\u6309\u94ae\u5f97\u85cf\u8d77\u6765\u522b\u5360\u4f4d"
    assert "calc(100% - 46px)" in block, "\u7a84\u5c4f\u6c14\u6ce1\u5bbd\u5ea6\u8981\u6263\u6389\u5934\u50cf\u5360\u7684\u90a3\u4e00\u6761\uff1a" + block
    assert "#app.show-chat .back-btn" in block, "\u8fd4\u56de\u952e\u53ea\u80fd\u5728\u804a\u5929\u6001\u51fa\u73b0\uff0c\u5217\u8868\u6001\u6ca1\u5f97\u9000"
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "function showChat()" in app and "function hideChat()" in app, "\u63a8\u5165 / \u9000\u56de\u8981\u6709\u5355\u4e00\u5b9e\u73b0"
    assert "showChat()" in app, "\u70b9\u5f00\u4f1a\u8bdd\u4e0d\u6ed1\u5165\u804a\u5929\uff0c\u7a84\u5c4f\u7b49\u4e8e\u70b9\u4e86\u6ca1\u53cd\u5e94"
    assert 'id="btn-back"' in HTML and 'qs("btn-back")' in app, "\u8fd4\u56de\u952e\u5f97\u6709\u8282\u70b9\u4e5f\u6709\u63a5\u7ebf"
    assert "rail-open" not in CSS and "rail-open" not in app, "\u62bd\u5c49\u65b9\u6848\u6b8b\u7559\uff1a\u540c\u4e00\u4e2a\u7a84\u5c4f\u4e0d\u80fd\u65e2\u662f\u62bd\u5c49\u53c8\u662f\u63a8\u5165"
    for panel in ("settings", "about"):
        assert 'data-panel="%s" title=' % panel in HTML, "\u5e95\u90e8\u5bfc\u822a\u6309\u94ae\u8981\u6709 title\uff1a" + panel

def test_narrow_screen_does_not_auto_open_keyboard():
    """openConversation 一进来就 focus 输入框：手机上软键盘立刻弹起，把刚打开的
    历史整个遮住，用户看到的是「点开会话 → 一片空白 + 键盘」。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    fn = app[app.index("async function openConversation("):app.index("/* 等回复时不往消息流塞气泡")]
    assert "if (!isNarrow()) qs(\"input\").focus" in fn, \
        "窄屏不能自动抢焦点弹键盘：" + fn[-400:]
    # 桌面端仍然要抢焦点，那是效率不是 bug
    assert "qs(\"input\").focus({ preventScroll: true })" in fn, "宽屏的自动聚焦别一起删了"

