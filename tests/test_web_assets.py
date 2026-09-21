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


def test_host_only_entries_are_hidden_for_anonymous_devices():
    """认证开着时，陌生人打开页面就是访客：整页「我」对他每一键都是 403。
    真正的守卫在服务端（VISITOR_WRITE 白名单），这里锁的是前端别再留门把手，
    而且判断依据必须是服务端给的 visitor.admin，不是自己猜。"""
    assert "data.visitor" in JS, "访客/主人要按 bootstrap 的 visitor 来分"
    assert "applyHostOnly()" in JS and "state.host === false" in JS
    assert 'data-tab="me"' in JS, "「我」这一栏要能被藏掉"


def test_the_admin_door_is_one_button_not_a_signposted_gate():
    """口令是主人的事，不该在陌生人进来的第一屏露出「要口令」的样子；
    同时主人也得有个口子下来 —— 他要能退出到访客视角去自查。"""
    assert 'id="btn-account"' in HTML
    assert "登录后台" in JS and "退出登录" in JS and '/api/logout' in JS
    assert 'href="/login"' not in HTML, "主页面不许挂登录页链接，只留这一个按钮"


def test_ops_hints_in_the_banner_are_for_the_host_only():
    """黄条上讲的全是"怎么修这台机器"：哪个环境变量盖住了设置、去哪儿敲 build-assets、
    去哪儿填 Key。访客没有设置面板，也不该被提示服务器目录长什么样 —— 一律按身份收口。"""
    assert "const host = state.host !== false;" in JS
    assert "if (host && s.env_overridden" in JS
    assert "if (host && !state.stickers.length)" in JS
    # 只报字段名等于说空话：auth_enabled 界面上根本没有格子，得报出真的变量名
    assert '"ANIMECHAT_" + String(k).toUpperCase()' in JS


def test_env_prefix_matches_what_the_banner_prints():
    """前端是拿字段名拼出 `ANIMECHAT_XXX` 给人看的 —— 服务端哪天改了前缀，这里就该红。"""
    from animechat.config import ENV_PREFIX

    assert ENV_PREFIX == "ANIMECHAT_"


def test_a_brand_new_device_is_not_shoved_into_a_chat():
    """全新身份的第一屏该是空列表：boot 不许替他建会话（建了还会挂一条未读红点）。
    只有他自己点「联系人」里的某个人，才真的开一条。"""
    assert "create: false" in JS, "boot 装载上次会话时要明说：没有就别建"
    assert "else if (options.create === false)" in JS, "selectCharacter 要认这个开关"


def test_the_front_page_toolbar_carries_theme_switch_and_a_bare_plus():
    """三件事都是「访客那一屏」的事：深浅色开关以前只长在「我」页头上（访客没有那一栏，
    等于换不了主题）；开聊那颗以前写成「＋找人聊」一整句；列表页底下还压着一行操作说明。"""
    chat = HTML[HTML.index('id="page-chat"'):HTML.index('id="page-thread"')]
    assert 'id="btn-theme"' in chat, "深浅色开关要长在第一屏，访客也够得着"
    assert HTML.count('id="btn-theme"') == 1, "开关只该有一个（两个同名 id 只会剩一个能用）"
    assert 'id="btn-find-chat" class="icon-btn"' in chat and "＋</button>" in chat
    assert "找人聊</button>" not in chat, "按钮上别再写字，一个加号够了"
    assert "page-note" not in HTML, "页面里不摆操作说明"


def test_press_feedback_is_for_everyone_and_shares_one_motion_language():
    """以前按压反馈只写在 @media (hover: none) 里 —— 桌面上点什么都没回应。
    现在凡是点得动的东西都有一次形变，而且时长/曲线只有 :root 那一处来源，
    免得又长出第二套手感。"""
    motion = CSS[CSS.index("交互反馈（不分输入设备）"):]
    assert "button:active" in motion and "transform: scale(.975)" in motion
    for token in ("--t-press:", "--t-state:", "--t-badge:", "--ease:"):
        assert token in motion, token + " 该在这块里定义"
    assert "var(--t-press)" in motion and "var(--ease)" in motion
    touch = CSS[CSS.index("@media (hover: none)"):CSS.index("手机浏览器的地址栏")]
    assert ":active" not in touch, "按压反馈不该只给触屏（那一档只管悬停显示不了的按钮）"
    assert ".sticker-cell .cell-btn.zoom { opacity: 1; }" in touch
    rm = CSS[CSS.index("@media (prefers-reduced-motion"):]
    assert "transition: none" in rm and "transform: none" in rm
    assert ".tab-badge, .unread-count" in rm, "红点弹入也要能被晕动偏好关掉"


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
    side = block_of(".page-scroll {")
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


def test_three_pages_share_one_tabbar():
    """桌面和手机是同一套 DOM：三个页面叠在一格，底部三个选项卡决定谁在屏幕上。

    以前窄屏(≤900px)要另走一套 Telegram 式推入导航（列表和聊天叠一格、聊天区
    translateX 滑进滑出），宽屏走双栏侧边栏 + « 折叠，JS 里同时伺候 isNarrow /
    .show-chat / data-wide 三套状态，互相踩出过「给 A 发消息、B 名字底下写着
    正在输入中」那类 bug。这里锁两件事：① 那三套状态不许回来；
    ② 窄屏仍然不许整排藏掉入口（触屏悬停不出按钮，藏了等于在手机上删功能）。"""
    def block_of(selector):
        i = CSS.index(selector)
        return CSS[i:CSS.index("}", i)]
    page = block_of(".page {")
    assert "grid-column: 1" in page and "grid-row: 1" in page, "三个页面得叠在同一格：" + page.strip()
    assert "min-height: 0" in page and "overflow: hidden" in page, "每页自锁，别把 #app 撑高：" + page.strip()
    assert ".page[hidden] { display: none !important; }" in CSS, \
        ".main / .page-head 自带 display，不 !important 压住，切页会变成三页同屏"
    app = block_of("#app {")
    assert "grid-template-rows: minmax(0, 1fr) auto" in app, "第二行给选项卡，且行高锁死：" + app.strip()

    nav = re.search(r'<nav class="tabbar".*?</nav>', HTML, re.S)
    assert nav, "index.html 里要有那个 .tabbar"
    tabs = re.findall(r'data-tab="(chat|contacts|me)"', nav.group(0))
    assert tabs == ["chat", "contacts", "me"], "底部必须正好是这三个选项卡，顺序也算：" + str(tabs)
    for page_id in ("page-chat", "page-contacts", "page-me"):
        assert 'id="%s"' % page_id in HTML, "缺页面容器：" + page_id

    # 注释里允许提这些名字（那段历史正是这里要防着重演的原因），规则 / 标记里不许有。
    # 例外：translateX(100%) 现在只许出现在推入转场的关键帧里 —— 那是播 300ms 的位移，
    # 不是「页面平时停在屏幕外、靠类切可见」的布局状态。先把那几行摘掉再扫。
    css_rules = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    css_rules = "\n".join(l for l in css_rules.splitlines() if not l.lstrip().startswith("@keyframes page-"))
    html_tags = re.sub(r"<!--.*?-->", "", HTML, flags=re.S)
    gone = ("show-chat", "translateX(100%)", "btn-back", "btn-collapse", "data-wide",
            "back-btn", ".sidebar", ".side-nav", "data-panel=")
    for word in gone:
        assert word not in css_rules, "推入导航 / 折叠侧栏的残留回来了（CSS）：" + word
        assert word not in html_tags, "推入导航 / 折叠侧栏的残留回来了（HTML）：" + word
    js = (WEB / "app.js").read_text(encoding="utf-8")
    for word in ("showChat", "hideChat", "isChatOpen", "btn-collapse"):
        assert word not in js, "JS 里不该再有推入导航的状态：" + word

    block = media_block(CSS, "@media (max-width: 900px)")
    for hidden in (".side-search { display: none", ".ghost-only { display: none",
                   ".page-head { display: none", ".side-tools { display: none",
                   ".char-meta { display: none", ".tabbar { display: none"):
        assert hidden not in block, "窄屏不许整排藏掉入口（列表屏就是完整版）：" + hidden
    assert "calc(100% - 46px)" in block, "窄屏气泡宽度要扣掉头像占的那一条：" + block[:200]


def test_narrow_screen_does_not_auto_open_keyboard():
    """openConversation 一进来就 focus 输入框：手机上软键盘立刻弹起，把刚打开的
    历史整个遮住，用户看到的是「点开会话 → 一片空白 + 键盘」。
    加了选项卡之后多一条：人在「联系人」页翻列表时也不许抢焦点（会话是后台装载的）。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    fn = app[app.index("async function openConversation("):app.index("/* 等回复时不往消息流塞气泡")]
    assert 'if (!isNarrow() && state.page === "thread") qs("input").focus' in fn, \
        "窄屏、以及没在聊天页时都不能自动抢焦点弹键盘：" + fn[-400:]
    # 桌面端仍然要抢焦点，那是效率不是 bug
    assert "qs(\"input\").focus({ preventScroll: true })" in fn, "宽屏的自动聚焦别一起删了"


def test_new_message_button_exists_and_is_hidden_by_default():
    button = re.search(r'<button[^>]*id="btn-new-messages"[^>]*>', HTML)
    assert button, "index.html 里要有 #btn-new-messages 按钮"
    assert 'hidden' in button.group(0), "按钮默认得是隐藏态"
    assert 'aria-label' in button.group(0), "按钮要有无障碍标签"


def test_new_message_button_is_after_messages_and_before_picker():
    messages_index = HTML.index('id="messages"')
    button_index = HTML.index('id="btn-new-messages"')
    picker_index = HTML.index('id="picker"')
    assert messages_index < button_index < picker_index, \
        "#messages -> #btn-new-messages -> #picker 的 DOM 顺序不能乱，否则窄屏可能被表情面板盖住"


def test_new_message_button_click_scrolls_to_bottom():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'qs("btn-new-messages").addEventListener("click", scrollBottom)' in app, \
        "新消息按钮点一下直接滚到底，不要重画历史"


def test_open_conversation_paints_new_messages_button():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    fn = app[app.index("async function openConversation("):app.index("/* 等回复时不往消息流塞气泡")]
    assert "paintNewMessages();" in fn, "开会话后要刷新新消息按钮状态"


def test_select_character_avoids_redundant_rendering_when_open_latest():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    fn = app[app.index("function selectCharacter("):app.index("function newConversation(")]
    assert 'if (!options.openLatest) {' in fn and 'renderContacts();' in fn and 'renderHead();' in fn, \
        "openLatest=true 时应让 openConversation 统一负责渲染，避免重复重绘联系人列表和头部"


def test_near_bottom_threshold_uses_72px():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "function isNearBottom(box, threshold = 72)" in app, \
        "near-bottom 阈值必须锁 72px，避免上下 10px 内反复横跳"


def test_failed_stream_persists_in_messages():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'meta: live.failed ? Object.assign({ error: live.errorText || "请求失败" }, meta) : meta' in app, \
        "失败流要把错误写入消息对象，否则刷新后错误原因丢失"
    fail_line = "    live.fail(message, hint);"
    fail_idx = app.index(fail_line)
    after_fail = app[fail_idx + len(fail_line):app.index("return;", fail_idx)]
    assert "live.finish(false);" in after_fail, "非 200 分支要在 fail 后收尾并持久化"


def test_finalize_live_message_only_replaces_owner_wrap():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    fn = app[app.index("function finalizeLiveMessage("):app.index("// #messages 里可能残留空白文本节点")]
    assert "const here = live.ownerConv != null && state.convId === live.ownerConv;" in fn, \
        "局部替换前要校验会话归属"
    assert "if (!here) {" in fn and "wrap.remove();" in fn, \
        "切走别的会话时要立即清掉旧 thread 里的 live wrap"
    assert "if (live.failed || !wrap || !wrap.parentNode) return;" in fn, \
        "失败气泡或没有挂载的 wrap 不要重绘成普通消息"


def test_abort_keeps_partial_stream_as_stopped_message():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    send_fn = app[app.index("async function send("):app.index("/* ---------------------------------------------------------- 群聊控制")]
    catch_fn = send_fn[send_fn.index("  } catch (err) {"):send_fn.index("  } finally {")]
    abort_if = catch_fn.index('if (err.name !== "AbortError") {')
    assert "live.failed = true;" not in catch_fn[:abort_if], "AbortError 不能被预标记为失败"
    assert "live.failed = true;" in catch_fn and "live.fail(err.message);" in catch_fn, \
        "真实请求失败仍要进入失败分支"
    assert "live.stopped = true;" in catch_fn and "live.finish(true);" in catch_fn, \
        '主动停止要把已收到的正文/表情固化为"已中断"消息'


def test_new_message_button_tracks_scroll_and_page_visibility():
    """「↓ 新消息」只在真的看着对话页时才提示。以前问的是「窄屏抽屉开没开」，
    现在问「在哪一页」——在联系人页翻列表时到货，按钮不该隔着页面亮。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'qs("messages").addEventListener("scroll", paintNewMessages);' in app, \
        "滚动消息流时要刷新新消息按钮"
    assert 'function chatVisible() { return state.page === "thread"; }' in app, \
        "聊天区可见性 = 现在停在聊天这一页（列表页 / 名片页上到货不该滚动、不该消红点）"
    assert "chatVisible() && !isNearBottom(box)" in app, "按钮条件：不在底部 + 聊天区在屏幕上"


def test_live_bubble_defers_speaker_to_start_event():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    create = app[app.index("function createLiveBubble("):app.index("async function streamChat(")]
    assert "nextSpeakerId()" not in create, "createLiveBubble 里不能再提前猜下一个群聊发言人"
    assert "function applySpeaker(speaker) {" in create, "要有统一的 applySpeaker 辅助函数"
    start = app.index('if (name === "start") {')
    end = app.index('} else if (name === "text")', start)
    start_handler = app[start:end]
    assert "live.updateSpeaker(data.speaker)" in start_handler, \
        "start 事件要立刻把头像/署名改成服务端指定的 speaker"


def test_narrow_picker_is_inflow_drawer():
    block = media_block(CSS, "@media (max-width: 900px)")
    assert ".picker {" in block, "窄屏媒体查询里必须重写 .picker，确保抽屉化"
    picker_block = block[block.index(".picker {"):block.index("}", block.index(".picker {")) + 1]
    for forbidden in ("position: absolute", "left: 22px", "right: 22px", "bottom: 92px"):
        assert forbidden not in picker_block, "窄屏 .picker 不能是 absolute 浮层：" + picker_block


def test_new_message_button_has_safe_area_on_narrow_screen():
    block = media_block(CSS, "@media (max-width: 900px)")
    assert ".new-messages { bottom: calc(10px + env(safe-area-inset-bottom));" in block, \
        "窄屏新消息按钮也要留安全区，避免被手势条或 picker 盖住"

def test_viewport_locks_mobile_scaling():
    assert 'name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=1, user-scalable=no"' in HTML, \
        "移动端 viewport 必须禁止缩放，避免手指捏合放大聊天页面"
    assert "touch-action: pan-x pan-y;" in CSS, "消息区要允许滚动但禁止浏览器捏合缩放"


def test_streaming_watchdog_and_visible_progress():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "async function streamChat(payload, live, signal, setStatus) {" in app, \
        "streamChat 要接收进度回调"
    assert "let watchdog = null;" in app and "armWatchdog();" in app, \
        "发送请求后必须启动客户端 watchdog"
    assert "let progressTimer = null;" in app and "clearTimers();" in app, \
        "进度刷新不能重置真正的超时 deadline"
    assert "if (signal && !signal.aborted) signal.abort();" in app, \
        "watchdog 超时时才中断当前请求，避免误杀正常完成"
    assert 'live.fail("等待超时（" + elapsed + "s）"' in app, \
        "超时必须在聊天界面显示明确错误"
    assert 't >= 30 ? "等待模型返回中（已等待 " + t + "s）…"' in app, \
        "等待超过 30 秒要显示更明确的进度文案"
    assert 'id="live-status"' in HTML, "HTML 要有流式请求状态槽"
    assert 'streamStatusEl = el("div", { class: "stream-status", text: "正在输入…" });' in app, \
        "每次发送都要创建可见等待状态"
    assert "if (streamStatusEl && streamStatusEl.parentNode) streamStatusEl.remove();" in app, \
        "流结束后要清理临时状态元素"
    assert "statusSlot.hidden = false;" in app and "statusSlot.hidden = true;" in app, \
        "等待状态槽要在发送时显示、结束时隐藏"
    assert "streamStatusEl.hidden = false;" in app, "收到流式正文时状态槽仍要保持可见"
    assert "if (!signal || signal.aborted) return;" in app, \
        "watchdog 不能覆盖用户刚刚发起的主动停止"
    assert 'if (err.name === "AbortError" && timedOut) return;' in app, \
        "watchdog 触发的 AbortError 不能覆盖已经生成的超时错误"


def test_group_bubbles_resolve_hidden_members():
    """/api/conversations/{id} 会带回 participant_characters（含已隐藏的成员），
       但接口给不等于前端在用：以前 setConvCharsLocal 定义了却没有任何调用点，
       charById 于是只能查公开列表，隐藏角色说过的话画出来既没名字、头像还是个「角」字。
       这两句断言就是那条断掉的线。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "setConvCharsLocal(data.participant_characters)" in app, \
        "会话详情里的成员快照要真的写进 state，不然 participant_characters 白传"
    opener = app[app.index("async function openConversation("):]
    assert "setConvCharsLocal(" in opener[:opener.index("renderThread()")], \
        "要在画会话之前写入，渲染时才有成员表可查"
    byid = app[app.index("function charById("):]
    byid = byid[:byid.index("\n}") + 2]
    assert byid.index("state.characters") < byid.index("convCharsLocal"), \
        "公开列表要排在成员快照之前：刚改过名字/头像的角色不能用旧快照画"


def test_js_only_reaches_for_ids_that_exist():
    """侧栏的会话整块删掉时，最容易漏的是 JS 里那句 qs("conv-list")：元素没了，
       qs() 返回 null，于是 null.addEventListener 在 bindStatic() 里抛错 —— 整页白屏，
       而且连 /api/bootstrap 都不会发（跟 panels.js 那次字符串换行是同一个结局）。
       源码扫一遍 id 就能挡住，不用等浏览器。"""
    ids = set(re.findall(r'id="([^"]+)"', HTML))
    assert ids, "index.html 一个 id 都没解析出来，这个测试就白写了"
    for name in ("app.js", "panels.js", "ui.js", "sfx.js"):
        body = (WEB / name).read_text(encoding="utf-8")
        used = set(re.findall(r'qs\(\s*["\x27]([A-Za-z0-9_-]+)["\x27]', body))
        missing = sorted(used - ids)
        assert not missing, name + " 里这些 qs() 指向不存在的元素：" + "、".join(missing)


def test_row_badge_only_counts_the_conversation_a_tap_opens():
    """红点按人累加所有会话、点进去却固定进单聊 —— 飞书群里那 2 条在网页上既打不开也
       清不掉，就是用户报的「点进去查看后红点还在」（实测：丛雨行上挂 2，两条都在
       id=69 那个飞书群，她的单聊未读是 0）。
       现在「点一行会进哪条」只有 rowConv 一个出处，红点、点击、汇总三处共用同一口径。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    res = app[app.index("function rowConv("):app.index("function chatRows(")]
    assert "ids.length !== 1" in res, "rowConv 只认单聊：群聊不许抢走 1 对 1 的入口"
    assert "convUnread(rowConv(pid))" in app, "行上的红点要按 rowConv 那条会话算"
    paint = app[app.index("function paintTabBadge("):app.index("function initTabs(")]
    assert "chatRows()" in paint, "「对话」上的汇总红点不许自己再把所有会话加一遍"
    assert "n += convUnread(conv)" not in paint, "汇总口径又跑出去了：行上没点、选项卡还挂着数"
    for fn in ("function openThreadFor(", "function selectCharacter("):
        at = app.index(fn)
        assert "rowConv(cid)" in app[at:at + 640], fn + " 不许自己再筛一遍单聊"


def test_chat_list_row_shows_preview_time_and_unread():
    """「对话」页是微信式会话列表：一行一个人，副标题是最后一句，右上角是时间 + 红点。
       关键是数据要从 state.conversations 现算，不能拿角色视图里那份 —— 那份要整页
       bootstrap 才刷新，聊完一句回到列表还停在旧预览上，看着就像「它没回」。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'id="chat-list"' in HTML, "对话页要有那个列表容器"
    rows = app[app.index("function chatRows("):app.index("function renderChatList(")]
    assert "for (const conv of state.conversations)" in rows, "预览/时间/未读都从会话表现算"
    assert "convUnread(rowConv(pid))" in rows and "conv.preview" in rows
    assert "replace(/\\s+/g" in rows, "预览里的真实换行要压成一行，不然列表行被撑歪"
    list_fn = app[app.index("function renderChatList("):app.index("function renderContacts(")]
    assert "fmtAgo(" in list_fn, "没有时间列就不是微信那个形状"
    assert "unread-count" in list_fn and "openThreadFor(" in list_fn, "点一行要进聊天"
    # 这一页只列聊过的人。把没聊过的角色也铺进来，它就和「联系人」是同一堆名字，
    # 两页分工再次塌掉（用户原话：对话和联系人的用处是一样的）。
    assert "lastOf(c).at > 0" in list_fn, "对话页不许列没聊过的角色"
    # 首屏：initTabs() 跑的时候会话还没加载，只靠切页时画一次汇总红点，
    # 会出现「行上亮着 2、选项卡写着 0」。bootstrap 落地后必须再刷一次。
    boot = app[app.index("async function loadBootstrap("):app.index("async function loadConversations(")]
    assert "renderChatList();" in boot and "paintTabBadge();" in boot, \
        "bootstrap 拿到会话后要同时刷列表和汇总红点：" + boot


def test_contacts_open_a_card_before_chatting():
    """联系人 = 通讯录：点一个人先到名片，名片上按「发消息」才开始聊。
       列表行里不再摆 ⋯ —— 一行两个入口，用户按半天不知道哪个是聊天。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    contacts = app[app.index("function renderContacts("):app.index("/* 点人 → 进聊天")]
    assert "openCard(char.id)" in contacts, "通讯录行要点开名片"
    assert "openThreadFor(" not in contacts, "通讯录不该直接进聊天（那是名片上「发消息」的事）"
    assert "ghost-only" not in contacts, "行里不该再有 ⋯：管理动作在名片上"
    card = app[app.index("function renderCard("):app.index("function charName(")]
    assert 'text: "发消息"' in card and "openThreadFor(char.id)" in card, "名片要有「发消息」进聊天"
    assert "openCharMenu(char)" in card, "复制 / 导出 / 隐藏 / 删除收在名片的更多操作里"


def test_the_card_shows_four_short_lines_and_nothing_else():
    """名片只摊 简介 / 口头禅 / 喜欢 / 讨厌。性格内核、说话方式、底线、当前场景、语气示范
    那些是给模型看的长文，摆在名片上就是一堵墙，谁都不会读 —— 要看要改走「编辑人设」。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    card = app[app.index("function renderCard("):app.index("function charName(")]
    assert 'line("简介", char.description)' in card
    assert 'line("口头禅"' in card and 'line("喜欢"' in card and 'line("讨厌"' in card
    for gone in ("性格", "说话方式", "底线", "当前场景", "语气示范", "用图频率"):
        assert gone not in card, "名片上不该再摆「%s」" % gone


def test_pushed_pages_cover_the_tabbar():
    """聊天和名片是推入页：占满整格、把底部选项卡盖住（微信聊天时看不见 tab）。
    少了 grid-row: 1 / -1，推入页只占第一行，底下露出一条选项卡，看着像没全屏。"""
    assert ".push { grid-row: 1 / -1;" in CSS, "推入页要跨两行盖住选项卡"
    for pid in ("page-thread", "page-card"):
        tag = re.search(r'<main[^>]*id="%s"[^>]*>' % pid, HTML)
        assert tag, "缺推入页：" + pid
        cls = re.search(r'class="([^"]*)"', tag.group(0))
        assert cls and "push" in cls.group(1).split(), pid + " 少了 .push 类：" + (cls and cls.group(1))
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'qs("btn-thread-back").addEventListener("click", () => showPage("chat"));' in app, \
        "聊天的 ← 要回对话列表"
    assert 'qs("btn-card-back").addEventListener("click", () => showPage("contacts"));' in app, \
        "名片的 ← 要回联系人"
    stack = app[app.index("const PUSH_ROOT ="):app.index("function switchTab(")]
    assert 'thread: "chat", card: "contacts"' in stack, "推入页要说清自己属于哪个栏，tab 才继续亮着"


def test_entering_a_pushed_page_slides_it_in():
    """点角色进聊天要有转场，而且整块画面一起走：新页从右边推进来，旧页那一栏（页面 +
       底部选项卡）往左让开并压暗，← 反过来。
       两处时长必须对齐 —— JS 的 SLIDE_MS 是 animationend 不来时的兜底藏页时机，
       CSS 才是真正在播的那个；JS 比 CSS 短的话，动画还在播旧页就被藏了，画面跳一下。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    js_ms = re.search(r"const SLIDE_MS = (\d+);", app)
    assert js_ms, "app.js 里要有一个 SLIDE_MS，兜底藏旧页的时间才说得清"
    dur = re.search(r"animation-duration: ([.\d]+)s;", CSS)
    assert dur, "转场时长在 CSS 里写一处就好（页面和选项卡共用同一个块）"
    assert float(dur.group(1)) * 1000 <= int(js_ms.group(1)), \
        "动画播 " + dur.group(1) + "s，但 JS 在 " + js_ms.group(1) + "ms 就把旧页藏了"
    for rule, kf in ((".page.push-in", "page-push-in"), (".page.push-out", "page-push-out"),
                     (".page.back-in", "page-back-in"), (".page.back-out", "page-back-out"),
                     (".tabbar.push-out", "page-push-out"), (".tabbar.back-in", "page-back-in")):
        assert "@keyframes " + kf + " {" in CSS, "缺关键帧：" + kf
        assert rule + " { animation-name: " + kf + "; }" in CSS, "缺转场规则：" + rule + " → " + kf
    # 选项卡以前不参与：返回时底下那条钉着不动，看着像两张没拼上的图
    assert "if (bar) moving.push([bar, rootCls])" in app, "底部选项卡要跟着它那一栏的根页一起动"
    # 只有「进推入页 / 退回栏根」两种情况有位移，切选项卡不许有
    assert 'PUSH_ROOT[name] ? "push" : (PUSH_ROOT[from] ? "back" : "")' in app, \
        "转场方向要按推入页判定，切栏不该播位移"
    assert "prefers-reduced-motion" in app, \
        "有晕动偏好时要在 JS 里就不播 —— 光靠 CSS 关掉动画，animationend 不会来，旧页藏不掉"
    assert "prevEl.hidden = false" in app, "动画期间旧页得留在屏上，否则只看见新页凭空出现"
    app_rule = CSS.split("#app { display: grid;")[1].split("}")[0]
    assert "overflow: hidden" in app_rule, \
        "新页有一帧整页停在 translateX(100%)，#app 不裁掉会闪出一条横向滚动条"


def test_picker_hint_matches_the_real_default_source():
    """表情面板的空状态以前写「默认走 DuckDuckGo（无需 Key）。想更稳可以在设置里填 Tenor Key」，
       两句都不成立：默认是 Bing（DuckDuckGo 只是退路，实测这台机器上常被风控），而设置里
       Tenor/Giphy 那两格早拆了 —— 界面却在劝人去注册一个填了也没用的 Key。
       这类藏在 app.js 里的用户可见文案，别的扫描都只读 panels.js，管不到它。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "默认走 Bing" in app, "搜图提示要说清真实默认来源"
    for word in ("Tenor Key", "Giphy Key"):
        assert word not in JS, "整个前端都不该再要这两个 Key：" + word
    assert "默认走 DuckDuckGo" not in JS, "DuckDuckGo 是退路，不是默认"


def test_context_budget_caps_below_the_smallest_model_window():
    """上限从前端的 40000 降到 30000，理由是实测：这台网关的 free-best 是一条
       fallback 链，链上最小的窗口只有 32,768 token（z-ai/glm-5.2:free），而中文
       实测约 0.84 token/字 —— 40000 字就是 33,600 token，还没算不计进预算的人设
       system prompt。拉满不是变慢，是整条路由 free_router_exhausted、这条回复直接失败。
       所以数值得写在提示语里，否则下一个人只会把它拉回 40000。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    line = [l for l in panels.splitlines() if l.strip().startswith("context: el(")]
    assert line, "设置里那格上下文预算的输入框不见了"
    assert 'max: "30000"' in line[0], "预算上限要卡在 30000 字（≈25k token）：" + line[0].strip()
    assert 'max: "40000"' not in panels, "40000 字≈33.6k token，越过链上最小的 32k 窗口"
    assert "30000 字约 25k token" in panels, "为什么是这个数，得写在提示语里"
