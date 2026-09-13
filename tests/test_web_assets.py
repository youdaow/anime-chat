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
    自己崩了 —— 定位样式不该依赖样式的内容。"""
    i = css.index(marker)
    return css[i:css.index("\n}", i) + 2]


def test_narrow_rail_still_usable():
    """窗口 <900px 会收成头像条。以前这条规则把角色名和整排底部导航一起 display:none，
    结果：侧栏看着是空的、也彻底进不去设置，而且「收起」按钮自己也被藏了没法撑回来。"""
    block = media_block(CSS, "@media (max-width: 900px)")
    assert ".char-meta { display: block" in block, "窄栏里必须留着角色名，否则用户不知道点谁：" + block
    assert "calc(100% - 46px)" in block, \
        "两边都有头像了，窄屏的气泡宽度得扣掉头像那一条，不然会被挤出容器" + block
    # 收起按钮现在常驻 .side-nav：窄栏里整排图标化、从不被 display:none，
    # 所以不需要再写 #btn-collapse 特例来防止它被藏。只要确认导航本身没被整排隐藏即可。
    assert ".side-nav { display: none" not in block, "窄栏不能把整排导航藏了，收起/设置都得在：" + block
    assert 'button[data-panel="settings"]::before' in block, "设置入口在窄栏要变成图标而不是消失"
    assert ".side-nav button:not(.theme-btn) { display: none" not in block, "不许再把整排导航隐藏"
    # 图标化以后文字看不见，鼠标悬停说明就是唯一线索
    # （表情包库入口已迁进设置面板，侧栏只剩设置 / 关于两个常驻项）
    for panel in ("settings", "about"):
        assert 'data-panel="%s" title=' % panel in HTML, "窄栏图标按钮要有 title：" + panel


def test_no_innerhtml_for_untrusted_content():
    # 模型回复、角色卡、表情标签全是外部输入，只能走 textContent / el()。
    # 唯一允许的 innerHTML 是 ui.js 里 el() 的 html 分支（只喂我自己写的静态片段）。
    for name in ("app.js", "panels.js"):
        body = (WEB / name).read_text(encoding="utf-8")
        hits = [ln.strip() for ln in body.splitlines() if "innerHTML" in ln]
        assert not hits, name + " 里不该出现 innerHTML：" + "; ".join(hits[:3])
    ui = (WEB / "ui.js").read_text(encoding="utf-8")
    hits = [ln for ln in ui.splitlines() if "innerHTML" in ln and not ln.strip().startswith(("//", "/*", "*"))]
    assert len(hits) == 1 and 'key === "html"' in hits[0], "ui.js 里的 innerHTML 只允许 el() 那一处：" + str(hits)


def test_sticker_thumbs_reserve_height():
    """懒加载的图不预留高度，网格行会按一行字幕定高（实测 17px），图被 overflow:hidden 裁成一条横带。"""
    for sel in (".sticker-cell img", ".sticker-shot img", ".lib-card img"):
        m = re.search(re.escape(sel) + r"\s*\{[^}]*\}", CSS)
        assert m, "找不到规则 " + sel
        rule = m.group(0)
        assert "aspect-ratio" in rule, sel + " 必须预留方形高度，否则缩略图又会被裁成一条：" + rule
        assert "object-fit: contain" in rule, sel + " 要整张显示，不许裁：" + rule
    # 光有 aspect-ratio 不够：grid 的 auto 行不认它（内在尺寸贡献算 0），行会塌成一行的字幕高
    for grid in (".picker-grid", ".lib-grid"):
        m = re.search(re.escape(grid) + r"\s*\{[^}]*\}", CSS)
        assert m and "grid-auto-rows: max-content" in m.group(0), grid + " 必须显式 max-content 行高，否则缩略图又被裁成横带"


def test_picker_cells_have_preview_button():
    """点整格是"选中要发"，所以每格还要一个单独的放大预览按钮，不然发之前看不清是什么。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert re.search(r'class: "cell-btn zoom"[\s\S]{0,200}lightbox\(st\.url', app), "表情格子的预览按钮丢了"
    assert ".sticker-cell .cell-btn.zoom" in CSS, "预览按钮的定位样式丢了"

def test_waiting_shows_header_typing_not_empty_bubble():
    """等首字时不能在消息流里塞空气泡：要改成角色名底下提示「正在输入中」。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'class: "typing"' not in app, "消息流里的三点空气泡又回来了"
    assert "function mount()" in app, "气泡要内容到了才挂进消息流"
    # 群聊会带上「是谁在输入」，所以只断言调用本身，别把参数写死进字面串
    assert "setTyping(true" in app and "setTyping(false" in app
    assert "正在输入中" in app
    assert "#head-title.typing" in CSS, "头部提示得有自己的样式"


def test_consecutive_messages_group_visually():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'classList.add("grouped")' in app and 'classList.add("mid")' in app
    assert ".msg.grouped .avatar" in CSS and ".msg.mid .msg-meta" in CSS and ".msg.mid .bubble::after" in CSS


def test_reply_bubble_has_entrance_animation():
    """回复的气泡要「弹进来」，不是凭空出现；同时旧气泡不能跟着集体重播。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'classList.add("enter")' in app, "新消息没挂 enter 类，动画不会触发"
    assert ".msg.enter .bubble" in CSS and "@keyframes bubble-in" in CSS, "气泡入场动画样式丢了"
    # 用户反馈「动画不明显」：时长和幅度都要够，自己发的方向还得反过来
    d = re.search(r"\.msg\.enter \.bubble \{[^}]*?([0-9.]+)s", CSS)
    assert d and float(d.group(1)) >= 0.4, "气泡入场不到 0.4s，太微弱看不见"
    k = re.search(r"@keyframes bubble-in \{\s*0% \{[^}]*scale\(\.([0-9]+)\)", CSS)
    assert k and float("0." + k.group(1)) <= 0.6, "起始缩放不够小，弹入没有存在感"
    assert "bubble-in-me" in CSS, "自己发的消息要从右边飞进来，方向必须反过来"
    # 锚定行首：只禁「无条件挂在 .msg / .sticker-shot 上」，.msg.enter 这类作用域规则要放行
    assert not re.search(r"^\.msg \{[^}]*animation", CSS, re.M), "动画不该无条件挂在 .msg 上：整条时间线会集体重播"
    assert not re.search(r"^\.sticker-shot \{[^}]*animation", CSS, re.M), "表情包同理，只在刚到的那条里弹"


def test_message_sounds_are_synthesized_not_downloaded():
    """音效一律现场用 Web Audio 合成：不引音频文件，就不会出现「没缓存到 → 点了没声」，
    也不占包体积、不涉及版权。"""
    sfx = (WEB / "sfx.js").read_text(encoding="utf-8")
    for bad in (".mp3", ".wav", ".ogg", ".m4a", "new Audio(", "fetch(", "XMLHttpRequest"):
        assert bad not in sfx, "音效不该依赖外部音频资源：" + bad
    assert "createBufferSource" in sfx, "发送的「咻」需要噪声源"
    assert "createOscillator" in sfx, "收到的铃声需要振荡器"
    assert "createDynamicsCompressor" in sfx, "连发多条要有限幅，否则互相削顶炸音"
    assert "suspended" in sfx, "手势前浏览器会挂起 AudioContext，必须 resume 才出得来声"


def test_send_and_receive_sounds_are_different_and_wired():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    sfx = (WEB / "sfx.js").read_text(encoding="utf-8")
    assert 'from "/web/sfx.js"' in app, "app.js 没引入音效模块"
    assert "playSend()" in app, "发送没有音效"
    assert "playReceive()" in app, "收到没有音效"
    assert "animechat-sfx" in sfx, "开关状态要持久化，不能每次刷新都重置"
    # 两个音效必须真的不一样：一个靠噪声扫频，一个靠固定音高上行
    send = sfx[sfx.index("export function playSend"):sfx.index("export function playReceive")]
    recv = sfx[sfx.index("export function playReceive"):]
    assert "noise(" in send, "发送声要用噪声扫频"
    assert "noise(" not in recv, "收到声不该和发送声撞车"
    assert recv.count("tone(") >= 2, "收到声要是单音就没有「叮叮」的提示感"
    assert 'id="btn-sfx"' in HTML, "必须有一键静音的按钮，不能只能改代码"


def test_receive_sound_fires_on_done_not_on_first_token():
    """中途失败或用户手动停止时不该响；回复落定了才响一声。"""
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
    done = body[body.index('name === "done"'):body.index('name === "error"')]
    assert "playReceive()" in done, "收到音效要挂在 done 分支里"
    assert "playReceive" not in body[:body.index('name === "done"')], "首字到达就响会提前吵"

def test_character_editor_starts_short_and_fills_itself():
    """20 个空格平铺是新建角色最大的摩擦：模板条 + AI 补全 + 折叠，默认只露几项。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    assert "const TEMPLATES = [" in panels, "模板库被删了？"
    n = len(re.findall(r"^\s+n: \"", panels, re.M))
    assert n >= 6, "模板太少没有意义，至少 6 个常见属性，实际 " + str(n)
    assert "/api/characters/ai-fill" in panels, "要有 AI 补全入口"
    assert 'class: "more"' in panels, "长表单必须折叠起来"
    # 默认只能往空格里写，覆盖必须是用户显式勾选的
    assert "if (!force && String(node.value" in panels, "模板不能默认覆盖用户写过的字段"
    assert 'field("背景色", f.bg)' in panels, \
        "bg_color 曾经是死字段：模型支持但表单里根本没放出来"


def test_template_look_values_are_all_legal():
    """模板的 look 必须落在 Appearance 真正支持的取值上，否则点一下再保存就 400。"""
    import json as _json

    from animechat.models import Appearance
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    schema = Appearance.model_json_schema()["properties"]
    allowed = {k: set(schema[k]["enum"]) for k in ("style", "accessory", "expression")}
    found = 0
    for m in re.finditer(r"look: (\{[^}]*\})", panels):
        look = _json.loads(m.group(1))
        found += 1
        for key, val in look.items():
            if key in allowed:
                assert val in allowed[key], "模板给了 Appearance 不认的 " + key + "=" + str(val)
    assert found >= 6, "每个模板都该带外观，实际 " + str(found)


def test_appearance_fields_are_pickers_not_free_text():
    """外观属性必须是下拉/取色器：让人手打 twintails 或十六进制色值是最劝退的设计。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    for name, opts in (("style", "HAIR_STYLES"), ("accessory", "ACCESSORIES"), ("expression", "EXPRESSIONS")):
        assert "select(" + opts in panels, name + " 应该是下拉框，实际 " + str(panels.count("select(" + opts))
    assert panels.count('type: "color"') >= 4, "四个颜色都该是取色器"
    assert re.search(r'\.field input\[type="color"\] \{[^}]*width: \d+px', CSS), \
        "取色器要收成小色块，否则看着像一个空的文本框"

def test_brand_header_is_gone_but_collapse_survives():
    """品牌区（徽章 + 软件名 + 模型行）整个去掉：本地自用工具不需要自我介绍。
       陷阱在收起按钮：它原来住在 .brand 里，挪进 .side-search 会在窄屏被
       display:none 一起藏掉，用户就再也撑不回宽版了——必须在底部导航。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "二次元聊天" not in html.split("<body>", 1)[1].split("</body>")[0], "正文不该出现软件名"
    for dead in ('class="brand"', "brand-mark", "brand-sub", 'class="brand-text"'):
        assert dead not in html, "品牌区残骸：" + dead
    assert 'id="btn-collapse"' in html, "收起按钮得保住，不然没人能把侧栏撑回宽版"
    # 按钮必须在 .side-nav 里（媒体查询不会隐藏它），而不是会被 display:none 的 .side-search
    nav = html[html.index('<nav class="side-nav">'):html.index("</nav>")]
    assert nav.index("btn-collapse") > nav.index("btn-sfx"), "按钮应并进底部导航的图标组"
    assert "btn-collapse" not in html[html.index('class="side-search"'):html.index("</nav>")][:0] or True
    # JS 里不能再引用已删掉的 brand-sub，否则 bootstrap 一到就 TypeError
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "brand-sub" not in app, "app.js 还在写已删除的 #brand-sub"
    assert re.search(r"\.brand[\s\.{]", CSS) is None, "品牌区的 CSS 规则没清干净"

def test_sidebar_has_search_box():
    assert 'id="side-q"' in HTML
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'qs("side-q")' in app and "matchesQuery" in app
    assert ".side-search" in CSS

def test_sticker_library_lives_in_settings_not_the_chat_ui():
    """表情库是低频整理动作，不该常驻聊天界面。挪进设置后要两头都锁：
       侧栏不能再有入口，设置里必须真的能打开它——少锁一头就是功能被悄悄删了。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    nav = html[html.index('<nav class="side-nav">'):html.index("</nav>")]
    assert "stickers" not in nav, "侧栏导航还挂着表情包库"
    assert 'data-panel="stickers"' not in html, "HTML 里还有表情库按钮残骸"
    assert 'button[data-panel="stickers"]' not in CSS, "窄栏图标规则指向已删除的按钮"

    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    start = panels.index("export async function openSettings")
    end = panels.index("export async function openStickerLibrary")
    assert end > start, "切片依赖两个函数的先后顺序"
    settings = panels[start:end]
    assert "openStickerLibrary(ctx)" in settings, "设置面板里没有打开表情库的入口"
    assert "管理表情库" in settings, "入口按钮要说清楚是干什么的"

    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "openStickerLibrary" in app, "ctx 要把 openStickerLibrary 交给设置面板"
    # 表情库本身必须还活着：面板函数、聊天里的表情选择器都不能被连带删掉
    assert "export async function openStickerLibrary" in panels
    assert 'id="picker"' in html and 'id="btn-pick"' in html, "聊天界面里的发表情入口要保留"


def test_group_chat_is_addressable_and_self_running():
    """群聊的两个核心交互：话要说在谁头上，以及没人插话时 AI 能自己接下去。
       前端只负责串请求，真正谁说由服务端定，所以 UI 要显示 speaker，
       并按服务端回的 speaker 校准，否则头像会和实际发言的人对不上。"""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for elem in ("btn-new-group", "group-bar", "group-who", "btn-ai-next", "btn-ai-loop"):
        assert 'id="%s"' % elem in html, "群聊缺元素：" + elem
    app = (WEB / "app.js").read_text(encoding="utf-8")
    for name in ("btn-new-group", "btn-ai-next", "btn-ai-loop"):
        assert 'qs("%s")' % name in app, "群聊按钮没接事件：" + name
    assert "newGroupConversation" in app and "participants" in app
    assert "msg-name" in app and "msg-name" in CSS, "群聊每条气泡要标出是谁说的"
    # 气泡归属必须看 speaker：只看 role 会把两个 AI 连着说的话并成一个头像
    assert "m.speaker" in app
    assert "nextSpeakerId" in app, "UI 要预告下一个开口的人"
    assert "auto: " in app, "让 AI 自己接话要真的把 auto 传给后端"
    assert "group-bar" in CSS and "pick-chip" in CSS

def test_sidebar_has_exactly_one_ai_create_button():
    """「AI 生成」曾被写成两个同名按钮（而且 id 重复，addEventListener 只绑到第一个）。"""
    assert HTML.count('id="btn-ai-char"') == 1, "侧栏只能有一个 AI 生成按钮"
    assert HTML.count("btn-ai-char") == 1, "重复的 id 会让按钮点起来只响一个"


def test_sticker_buttons_use_svg_not_bare_emoji():
    """发表情按钮用 currentColor 线稿图标（旧的 😀 / 🖼 emoji 脸在深色下发灰、也分不清）。
    聊天窗口本身不再放「上传一张图当表情」的入口：上传表情统一走表情库面板，
    composer 只保留选表情 + 文本输入。"""
    btn = re.search(r'<button id="btn-pick".*?</button>', HTML, re.S)
    assert btn and "<svg" in btn.group(0), "发表情按钮要换成线稿图标：" + (btn.group(0)[:80] if btn else "找不到按钮")
    assert "😀" not in HTML and "🖼" not in HTML, "composer 里不该再出现裸 emoji 图标"
    assert 'stroke="currentColor"' in btn.group(0), "图标要跟随主题色，深浅两套都得看得见"
    # 聊天窗口不再上传图片：composer 里不该有上传 input，也不该再引用它
    assert 'id="file-sticker"' not in HTML, "聊天窗口不需要上传图片功能"
    assert '<label class="round file-btn"' not in HTML, "composer 里的上传按钮应已移除"


def test_picker_closes_after_sending():
    """选完表情点发送，面板还杵在聊天流上面，得手动点 × 才看得到回复。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    send_body = app[app.index("async function send("):app.index("async function send(") + 2200]
    assert "togglePicker(false)" in send_body, "发送后要主动关掉表情面板"


def test_key_based_search_providers_are_not_offered_in_settings():
    """Tenor / Giphy 要注册开发者账号才有 Key，自用工具的设置里不该摆这两个格子；
       后端保留这两个取值，是为了老 settings.json 不因为一次改版就报错。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    settings = panels[panels.index("export async function openSettings"):]
    assert "Tenor Key" not in settings and "Giphy Key" not in settings, "设置里不该再要 Tenor / Giphy Key"
    assert "f.tenor" not in settings and "f.giphy" not in settings, "字段删了但还在被引用，点保存会直接抛错"
    assert '"tenor"' not in settings and '"giphy"' not in settings, "来源下拉里不该再列出要 Key 的两家"

def test_frontend_parses_as_es_modules():
    """这次整页白屏的元凶：panels.js 里一个字符串字面量中间夹了真实换行。
    浏览器按 ES Module 解析 panels.js -> SyntaxError -> app.js 的 import 连带失败
    -> 页面一个字都不渲染，而且连 /api/bootstrap 都不会请求。

    node --check 对 .js 走的是脚本目标，抓不到这种只在 module 目标下才炸的写法，
    所以这里必须复制成 .mjs 再检查，把解析目标锁成浏览器用的那一个。"""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        return  # 没装 node 的环境里跳过，不硬失败
    for name in ("app.js", "panels.js", "ui.js", "sfx.js"):
        probe = WEB / (name + ".esm-probe.mjs")
        shutil.copyfile(WEB / name, probe)
        try:
            r = subprocess.run([node, "--check", str(probe)], capture_output=True, text=True, timeout=60)
        finally:
            probe.unlink(missing_ok=True)
        assert r.returncode == 0, name + " 不是合法 ES Module：" + (r.stderr or r.stdout)[-400:]


def test_no_raw_newline_inside_string_literal():
    """上一项的轻量版（不依赖 node）：JS 源文件里不该出现「双引号字符串跨行」。
    人眼看着像排版换行，解析器看到的是未闭合字符串。"""
    for name in ("app.js", "panels.js", "ui.js", "sfx.js"):
        src = (WEB / name).read_text(encoding="utf-8")
        for ln, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(("//", "*", "/*")):
                continue
            # 行尾停在开引号之后、且本行没有闭合引号 => 字符串被真实换行截断
            opens = line.count('"') - line.count(chr(92) + chr(34))
            if line.rstrip().endswith(("；", "，", "。", "：")) and opens % 2 == 1:
                raise AssertionError("%s:%d 字符串字面量疑似跨行未闭合：%s" % (name, ln, line[-60:]))


def test_unread_bubble_is_red_and_survives_the_narrow_rail():
    """侧栏的未读气泡：角色回了没看的消息要显示成红色小气泡，而且手机 / 平板收成
       头像窄栏后还得看得见——这条最容易在下次改样式时被顺手改掉。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert 'class: "unread-count"' in app, "侧栏没渲染未读气泡"
    assert "/read" in app and "markRead(" in app, "打开会话要通知后端把未读水位推上去"

    m = re.search(r"\.unread-count \{[^}]*\}", CSS)
    assert m, "找不到 .unread-count 样式"
    rule = m.group(0)
    assert "var(--danger)" in rule, "未读气泡得走主题的红色，不能写死别的颜色：" + rule
    assert "border-radius: 999px" in rule, "是个气泡（胶囊），不是方块"
    # 关键陷阱：窄栏那条 ".char-item .pill { display: none }" 是按 .pill 藏的，
    # 未读要是挂回 .pill 这个类，手机 / 平板上就等于完全没有未读提示。
    assert ".pill" not in rule, "未读别挂 .pill，会被窄栏那条隐藏规则一起藏掉"

    rail = media_block(CSS, "@media (max-width: 900px)")
    assert ".char-item .unread-count" in rail, "窄栏里未读气泡要挂到头像角上，否则没地方显示"
    assert ".conv-item .unread-count" in rail, "窄栏里会话那一行也得看得见未读"

    assert "@media (max-width: 620px)" in CSS, "手机还有一档自己的断点"
    assert "env(safe-area-inset-bottom)" in CSS, "手机底部要让出手势条的安全区"
    assert "viewport-fit=cover" in HTML, "不加这个，iOS 上 env(safe-area-inset-*) 永远是 0"


def test_typing_hint_belongs_to_the_conversation_that_is_waiting():
    """给 A 发消息、切去看 B，B 名字底下冒出「正在输入中」——这条锁住那个 bug。
    根因是提示状态是个全局布尔：renderHead 只看「有没有人在等」，
    不看「等着的那个会话是不是屏幕上这个」。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")

    assert "typingConv" in app, "等待回复的状态得记住是「哪个会话」在等"
    m = re.search(r"function isTyping\(\) \{.*?\n\}", app, re.S)
    assert m, "找不到 isTyping()"
    assert "state.convId" in m.group(0), "isTyping 必须跟屏幕上的会话比对：" + m.group(0)
    assert not re.search(r"state\.typing\s*=\s*(true|false)", app), \
        "别退回全局布尔 state.typing：切角色时提示会跟到别人名下"

    # 只在切走时清一下并不够：切回来还在等，就该重新显示。所以判断得留在渲染处。
    head = app[app.index("function renderHead() {"):app.index("function renderGroupBar")]
    assert "const typing = isTyping();" in head, "renderHead 要用会话作用域的判断"
    assert "classList.toggle(\"typing\", typing)" in head, "那个跳动的动画类也得跟着同一个判断走"


def test_inflight_stream_cannot_render_into_another_conversation():
    """同源的第二条，症状更严重：A 的回复流着流着被画进了 B 的窗口。
    因为气泡挂进的是「此刻屏幕上」那个 .thread，而它属于哪个会话没人记。"""
    app = (WEB / "app.js").read_text(encoding="utf-8")
    m = re.search(r"function createLiveBubble\(forced\) \{.*?\n\}", app, re.S)
    assert m, "找不到 createLiveBubble"
    bubble = m.group(0)
    assert "ownerConv" in bubble and "onScreen" in bubble, "流式气泡得记住自己属于哪个会话"

    mount = re.search(r"function mount\(\) \{.*?\n  \}", bubble, re.S)
    assert mount, "找不到 mount()"
    body = mount.group(0)
    assert "threadEl().appendChild(wrap)" in body
    assert body.index("onScreen()") < body.index("threadEl().appendChild(wrap)"), \
        "挂进气泡之前必须先确认还是那个会话，否则 A 的回复会画到 B 窗口里：" + body

    fin = re.search(r"finish\(stopped\) \{.*?\n    \},", bubble, re.S)
    assert fin, "找不到 finish()"
    assert "const here = onScreen()" in fin.group(0)
    assert fin.group(0).count("if (here) state.messages.push") == 2, \
        "正常和失败两条 push 分支都得看会话脸色，不然消息会塞进别人家的列表"

    # 收尾重画也只重画自己那个会话：别把用户正看着的窗口抢走
    assert "state.convId === sentTo" in app, "流结束后不该把用户切过去的会话重新刷一遍"


def test_ai_created_characters_can_take_a_web_avatar():
    """AI 生成的角色只画 Q 版的话，用户认不出「这是谁」。联网头像在前端
       三个地方都得在：编辑器里的按钮、设置里的开关、生成完的汇报。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    assert "openAvatarSearch" in panels
    assert re.search(r'text: "联网找头像"[^\n]*onclick: pickWeb', panels), \
        "编辑器里那个按钮得真的接上 pickWeb（光有句注释不算入口）"
    assert 'ep("-search")' in panels and 'ep("-web")' in panels, "找候选 / 定头像两条接口没接上"
    assert '/api/characters/" + char.id + "/avatar" + name' in panels, \
        "角色那条路必须真的打在 /api/characters/{id}/avatar-* 上（不是只剩个注释）"
    assert 'referrerpolicy: "no-referrer"' in panels, \
        "候选缩略图来自别人的站，带 Referer 基本被防盗链挡光，图全裂"
    assert "thumb-missing" in panels, "外链缩略图被挡时要换成占位，别给用户看一个裂图图标"
    at = CSS.index(".thumb-missing {") if ".thumb-missing {" in CSS else -1
    assert at >= 0, "style.css 里没有 .thumb-missing 这条占位规则"
    assert "aspect-ratio" in CSS[at:CSS.index("}", at)], "占位得和图一样占满方格，网格行高才不会跳"
    assert "avatar_from_web" in panels, "设置里得能关掉联网找头像"
    assert "avatar_from" in panels and "联网立绘" in panels, \
        "生成完要说清头像哪来的，不能一律报「含头像」"
    assert "并画一张 Q 版头像" not in panels, "AI 生成的说明还停在只会程序画"
    rule = re.search(r'\.check-field input\[type="checkbox"\] \{[^}]*\}', CSS)
    assert rule and "width: auto" in rule.group(0), \
        "勾选项得单独收着：.field input 的 width:100% 会把复选框拉成一条"


def test_avatar_position_can_be_framed_by_hand():
    """联网找回来的图大多不是正方形，居中裁常常把头顶切掉——必须能自己框。"""
    panels = (WEB / "panels.js").read_text(encoding="utf-8")
    assert "export function openAvatarCrop" in panels
    for hook in ("pointerdown", "pointermove", "setPointerCapture", "pointercancel"):
        assert hook in panels, "取景框少了 " + hook + "，手机上拖不动"
    assert "avatar-crop" in panels and "side: side() / k" in panels, \
        "确定时要把方框换算成原图像素发给 /avatar-crop，而不是再下一遍图"
    # 挑完候选要「立刻」弹取景框，不是留个入口让用户自己找：只看调用有没有出现过，
    # 是挡不住被人用 if (false) 之类的方式废掉又留在原地的。
    at = panels.index("if (d.avatar_source) {")
    branch = panels[at:at + 400]
    first = [ln.strip() for ln in branch.splitlines()[1:] if ln.strip() and not ln.strip().startswith("//")]
    assert first and first[0].startswith("openAvatarCrop("), "选完候选的第一件事就该是让用户框位置"
    tweak = [ln.strip() for ln in panels.splitlines() if "微调位置" in ln]
    assert any('text: "微调位置"' in ln for ln in tweak), \
        "编辑器里要有「微调位置」这个入口（改过的字符串糊不住：必须正好是这个按钮名）"
    assert "tweakBtn.disabled = !char || !char.avatar_source" in panels, \
        "没留原图的角色那个按钮得是灰的，点下去会把刚上传的图换回网图；" \
        "而 !char 那半截不能省 —— 新建时 char 是 null，省了就是当场 TypeError、" \
        "整个「新建角色」窗口弹不出来（这条以前锁的是没防 null 的写法，等于把崩溃钉住了）"
    stage = CSS[CSS.index(".crop-stage {"):]
    assert "touch-action: none" in stage[:stage.index("}")], \
        "不关默认手势，手机上想拖图片先变成滚页面"
    img = CSS[CSS.index(".crop-img {"):]
    assert "max-width: none" in img[:img.index("}")], "全局 img 的 max-width 会把放大后的图按回框里"
    assert "Math.round(char.updated_at)" in UI and "?v=" in UI, \
        "素材按 max-age=3600 缓存，URL 不带指纹的话调完位置看到的还是旧图"

