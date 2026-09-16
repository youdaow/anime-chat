/* 主应用：侧栏、聊天流、表情面板。所有状态都在这一个模块里，panels.js 通过 ctx 回调它。 */

import {
  el, append, clear, qs, toast, api, avatarNode, fmtTime, fmtAgo, lightbox,
  confirmDialog, openModal, closeModal, dismissModals, titleBar, spinner,
} from "/web/ui.js";
import { openCharEditor, openSettings, openStickerLibrary, openContext, openAbout, openAICreate } from "/web/panels.js";
import { playSend, playReceive, prime as primeSfx, isOn as sfxOn, setOn as sfxSet } from "/web/sfx.js";

const state = {
  boot: null,
  characters: [],
  stickers: [],
  emotions: [],
  conversations: [],
  settings: null,
  charId: null,
  convId: null,
  conversation: null,
  messages: [],
  attach: [],
  streaming: false,
  abort: null,
  /* 哪个会话在等回复（null = 没有）。存会话 id 而不是一个全局布尔：
     以前切角色后 renderHead 照旧读到 true，于是「你在给丛雨发消息，
     丰川祥子名字底下写着正在输入中」。 */
  typingConv: null,
  typingName: "",
  sideQ: "",
  // 群聊：本轮由谁开口（null = 让服务端按轮转决定）；autoing = 正在让 AI 自己聊
  speaker: null,
  autoing: false,
  picker: { tab: "all", q: "", emotion: "", web: [], loading: false, provider: "", note: "" },
};

const ctx = {
  state,
  api,
  toast,
  refreshBootstrap: loadBootstrap,
  refreshConversations: loadConversations,
  refreshStickers: loadStickers,
  openConversation,
  selectCharacter,
  newConversation,
  openStickerLibrary,
};

/* ------------------------------------------------------- 窄屏 / 键盘适配 */

/* 侧栏在 ≤900px 变成覆盖式抽屉（见 style.css 那条媒体查询）。判断一律走
   matchMedia，别读 offsetWidth：抽屉收起时侧栏仍在 DOM 里，量出来的宽度会骗人。 */
const narrowMq = window.matchMedia ? window.matchMedia("(max-width: 900px)") : null;
function isNarrow() { return !!(narrowMq && narrowMq.matches); }

function isChatOpen() { return document.getElementById("app").classList.contains("show-chat"); }

/* 切到聊天视图：窄屏下聊天区从右侧滑入盖住列表。只管类名，滑动交给 CSS transition。
   宽屏两栏并排，这个类没有视觉作用，加着也无害。 */
function showChat() { document.getElementById("app").classList.add("show-chat"); }

/* 退回列表：聊天区滑回右侧屏外。← 返回键和 Esc 都走它。 */
function hideChat() { document.getElementById("app").classList.remove("show-chat"); paintNewMessages(); }

/* 输入框提示语：手机上「Enter 发送 / Shift+Enter 换行」既打不出来、又把真正有用的
   提示挤到看不见（实测 390px 上「Shift+Enter 换行」被截成半行）。触屏上换行靠
   键盘的回车、发送靠「发送」按钮，所以提示要说人话。 */
const INPUT_HINT_WIDE = "说点什么…（Enter 发送 / Shift+Enter 换行）";
const INPUT_HINT_NARROW = "说点什么…（点「发送」发出）";

function paintInputHint() {
  const input = qs("input");
  if (!input) return;
  input.placeholder = isNarrow() ? INPUT_HINT_NARROW : INPUT_HINT_WIDE;
}

function initNarrowNav() {
  const back = qs("btn-back");
  if (back) back.addEventListener("click", () => hideChat());
  if (narrowMq) {
    // 转屏 / 拖回宽屏：宽屏是并排双栏，聊天态残留没意义，退回列表；提示语跟着换
    const onBreak = (ev) => { if (!ev.matches) hideChat(); paintInputHint(); };
    if (narrowMq.addEventListener) narrowMq.addEventListener("change", onBreak);
    else if (narrowMq.addListener) narrowMq.addListener(onBreak);
  }
}

/* iOS Safari 的软键盘只压缩 visualViewport，#app 那 100dvh 一动不动，于是
   输入框连刚打的字一起被键盘埋掉（发消息全程摸黑）。这里把「布局视口 −
   可视视口」写进 --kb，样式从 #app 高度里扣掉它，输入区就被顶到键盘正上方。
   Android 的键盘压缩的是布局视口本身，差值算出来是 0，不会重复补偿。 */
function initKeyboardInset() {
  const vv = window.visualViewport;
  const app = document.getElementById("app");
  if (!vv || !app || !app.style) return;
  let raf = 0;
  const apply = () => {
    raf = 0;
    // innerHeight 在 iOS 上始终是不被键盘影响的布局高度
    const kb = Math.max(0, Math.round(window.innerHeight - vv.height - vv.offsetTop));
    // 变量挂到 :root 而不是 #app：弹窗的 .modal-root 是 #app 的兄弟节点，
    // 挂 #app 上它继承不到，键盘照样埋掉抽屉底部的「保存」。
    const root = document.documentElement;
    if (root.style) root.style.setProperty("--kb", kb + "px");
    app.style.setProperty("--kb", kb + "px");
    app.classList.toggle("kb-up", kb > 0);
  };
  const schedule = () => { if (!raf) raf = window.requestAnimationFrame(apply); };
  vv.addEventListener("resize", schedule);
  vv.addEventListener("scroll", schedule);
  apply();
}

/* ---------------------------------------------------------------- 启动 */

async function boot() {
  initTheme();
  bindStatic();
  initNarrowNav();
  initKeyboardInset();
  paintInputHint();
  try {
    await loadBootstrap();
  } catch (err) {
    // 起不来时消息区还是空的，直接把原因画进去（这里不能依赖任何静态占位节点）
    const box = qs("messages");
    clear(box);
    box.appendChild(el("div", { class: "thread" }, [
      el("div", { class: "empty" }, [
        el("div", { class: "empty-art", text: "⚠️" }),
        el("h3", { text: "起不来：" + err.message }),
        el("p", { text: "服务没起来？终端里跑 animechat run，然后刷新页面。" }),
      ]),
    ]));
    return;
  }
  const last = state.boot.prefs.last_character;
  const target = state.characters.find((c) => c.id === last) || state.characters[0];
  if (!target) {
    toast("还没有角色，左侧「＋新建」或导入一张角色卡", "err");
    return;
  }
  selectCharacter(target.id, { openLatest: !isNarrow() });   // 窄屏先停在会话列表，点进去才滑入聊天
}

async function loadBootstrap() {
  const data = await api("/api/bootstrap");
  state.boot = data;
  state.characters = data.characters || [];
  state.stickers = data.stickers || [];
  state.emotions = data.emotions || [];
  state.settings = data.settings;
  state.conversations = data.conversations || [];
  renderBanner();
  renderSidebar();
  renderHead();
  return data;
}

async function loadConversations() {
  const data = await api("/api/conversations");
  state.conversations = data.conversations || [];
  /* 服务端算出来的未读里，也会把「你正开着的那个会话」里刚落地的那条回复算成未读
     （POST /read 可能还没跑到）。正看着的不该亮红点，所以这里再本地归零一次。 */
  if (state.convId && document.visibilityState === "visible") clearUnreadLocal(state.convId);
  renderSidebar();
}

async function loadStickers() {
  const data = await api("/api/stickers?limit=200");
  state.stickers = data.stickers || [];
  renderPicker();
  renderComposerHint();
  return state.stickers;
}

/* ---------------------------------------------------------------- 侧栏 */

function sideQuery() { return String(state.sideQ || "").trim().toLowerCase(); }
function matchesQuery(q, parts) { return !q || parts.filter(Boolean).join(" ").toLowerCase().includes(q); }

/* ---------------------------------------------------- 未读（角色回复没看到的条数） */

// 后端只数角色的回复：用户自己发出去的那条不该给自己点红点。
function convUnread(conv) { return Number(conv && conv.unread_count) || 0; }

function fmtUnread(n) {
  const v = Number(n) || 0;
  return v > 99 ? "99+" : String(v);   // 四位数会把行尾挤歪，手机上看也更像故障
}

/* 某个角色名下的未读总数。群聊摊到每个成员头上，和后端 _char_views 同一套算法：
   手机上侧栏是抽屉、要点开才看得见，这个数字就是「谁有新回复」的唯一线索。 */
function charUnread(cid) {
  let n = 0;
  for (const c of state.conversations) {
    if (convIds(c).indexOf(cid) >= 0) n += convUnread(c);
  }
  return n;
}

// 只清本地状态、不发请求：渲染前先归零，红点才不会闪一下又消失
function clearUnreadLocal(id) {
  const conv = state.conversations.find((c) => c.id === id);
  if (conv) conv.unread_count = 0;
  if (state.conversation && state.conversation.id === id) state.conversation.unread_count = 0;
}

/* 会话真的画到屏幕上了才算读到。POST 挂了不影响看消息，下次打开会再补一次。 */
function markRead(id) {
  clearUnreadLocal(id);
  api("/api/conversations/" + id + "/read", { method: "POST" }).catch(() => { /* 静默 */ });
}

function renderSidebar() {
  const q = sideQuery();
  const chars = state.characters.filter((c) => matchesQuery(q, [c.name, c.title, c.short_desc, (c.tags || []).join(" ")]));
  const charList = clear(qs("char-list"));
  if (!chars.length) {
    charList.appendChild(el("div", { class: "pill", text: q ? "没有匹配「" + q + "」的角色" : "还没有角色" }));
  }
  for (const char of chars) {
    const unread = charUnread(char.id);
    const item = el("div", {
      class: "char-item" + (char.id === state.charId ? " on" : "") + (unread ? " has-unread" : ""),
      onclick: () => selectCharacter(char.id, { openLatest: true }),
      title: char.name + " — " + (char.title || ""),
    }, [
      avatarNode(char),
      el("div", { class: "char-meta" }, [
        el("b", { text: char.name }),
        // 副标题显示「上次聊了什么」而不是角色介绍：列表一眼能回忆起对话进度。
        // 没聊过的新角色留一句提示，不拿介绍去填。
        el("span", { text: char.last_preview || "还没有聊过" }),
      ]),
      // 有未读就换成红色气泡显示条数，没有才显示灰色的会话数：一块地方，
      // 优先说「有新东西」，其次才是「一共有几个会话」。
      unread
        ? el("div", { class: "unread-count", text: fmtUnread(unread), title: unread + " 条未读回复", "aria-label": unread + " 条未读回复" })
        : el("div", { class: "pill", text: String(char.conversation_count || 0) }),
      el("button", {
        class: "ghost-only", text: "⋯", title: "角色菜单",
        onclick: (ev) => { ev.stopPropagation(); openCharMenu(char); },
      }),
    ]);
    charList.appendChild(item);
  }

  const convList = clear(qs("conv-list"));
  // 群聊的 character_id 只是第一个成员；按参与者匹配，
  // 否则拉了祥子和睦的群，点睦就看不到那个群了。
  const mine = state.conversations.filter((c) => convIds(c).indexOf(state.charId) >= 0)
    .filter((c) => matchesQuery(q, [c.title, convIds(c).map(charName).join(" ")]));
  if (!mine.length) {
    convList.appendChild(el("div", { class: "pill", text: q ? "没有匹配的会话" : "还没有会话，点上面「＋新会话」" }));
  }
  for (const conv of mine) {
    const unread = convUnread(conv);
    convList.appendChild(el("div", {
      class: "conv-item" + (conv.id === state.convId ? " on" : "") + (unread ? " has-unread" : ""),
      onclick: () => openConversation(conv.id),
    }, [
      el("div", { class: "conv-meta" }, [
        el("b", { text: (conv.pinned ? "📌 " : "") + convLabel(conv) }),
        el("span", { text: fmtAgo(conv.updated_at) + " · " + conv.message_count + " 条" + (conv.summary ? " · 有记忆" : "") }),
      ]),
      unread ? el("div", {
        class: "unread-count", text: fmtUnread(unread),
        title: unread + " 条未读回复", "aria-label": unread + " 条未读回复",
      }) : null,
      el("button", {
        class: "ghost-only", text: "⋯", title: "会话菜单",
        onclick: (ev) => { ev.stopPropagation(); openConvMenu(conv); },
      }),
    ]));
  }
}

function charName(id) {
  const char = state.characters.find((c) => c.id === id);
  return char ? char.name : id;
}
function currentChar() {
  return state.characters.find((c) => c.id === state.charId) || null;
}

function charById(cid) {
  return state.characters.find((c) => c.id === cid) || null;
}

/* 会话里的角色：群聊 participants 有若干个，单聊就一个。
   老会话可能没有 participants 字段，退回 character_id。 */
function convIds(conv) {
  if (!conv) return [];
  return (conv.participants && conv.participants.length) ? conv.participants : [conv.character_id];
}

function convChars(conv) {
  const ids = convIds(conv || state.conversation);
  return ids.map(charById).filter(Boolean);
}

// 会话在侧栏叫什么：群聊列出成员，单聊沿用原来的说法
function convLabel(conv) {
  if (conv.title) return conv.title;
  const who = convIds(conv).map(charName);
  return who.length > 1 ? who.join("、") : (who[0] || "新对话");
}

function isGroup() { return convChars().length > 1; }

// 这条消息是谁发的：群聊看 speaker，单聊就是会话主角
function speakerOf(msg) {
  if (msg.role === "user") return null;
  return charById(msg.speaker) || currentChar();
}

/* 下一个该谁说话：跟服务端 next_speaker 同一套规则（看最后一条助手消息是谁，
   取下一个顺位），否则 UI 预告的人和真正开口的人会不一致。 */
function nextSpeakerId() {
  const ids = convChars().map((c) => c.id);
  if (!ids.length) return null;
  if (state.speaker && ids.indexOf(state.speaker) >= 0) return state.speaker;
  let last = "";
  for (let i = state.messages.length - 1; i >= 0; i -= 1) {
    const m = state.messages[i];
    if (m.role === "assistant" && m.speaker) { last = m.speaker; break; }
  }
  const at = ids.indexOf(last);
  return at < 0 ? ids[0] : ids[(at + 1) % ids.length];
}

function selectCharacter(cid, opts) {
  const options = opts || {};
  state.charId = cid;
  // 滑入聊天交给 openConversation：那里才是真的有了会话内容   // 抽屉里挑完人就收起来，别让人再点一次遮罩
  renderSidebar();
  renderHead();
  if (options.openLatest) {
    // 点角色 = 想跟这个人单独聊。群聊里也有他，但绝不能让群聊抢走单聊：
    // 否则拉过群之后点角色只会反复打开那个群，1 对 1 再也进不去了。
    const mine = state.conversations.filter((c) => convIds(c).indexOf(cid) >= 0);
    const solo = mine.filter((c) => convIds(c).length === 1);
    if (solo.length) openConversation(solo[0].id);
    else newConversation(cid);   // 没有单聊就新开一个，群聊留在下面的会话列表里
  }
}

async function newConversation(cid) {
  const charId = cid || state.charId;
  if (!charId) return;
  try {
    const data = await api("/api/conversations", { method: "POST", json: { character_id: charId } });
    await loadConversations();
    state.charId = charId;
    openConversation(data.conversation.id);
  } catch (err) {
    toast(err.message, "err");
  }
}

async function openConversation(id) {
  try {
    const data = await api("/api/conversations/" + id);
    state.convId = id;
    state.conversation = data.conversation;
    state.messages = data.messages || [];
    // 群聊的 character_id 只是第一个成员；如果我是从某个角色的视角点进来的，
    // 就别把选中的人改成别人，否则侧栏高亮和"＋新会话"都会跳到群主身上。
    if (convIds(data.conversation).indexOf(state.charId) < 0) {
      state.charId = data.conversation.character_id;
    }
    state.speaker = null;
    /* 会话画到屏幕上才算读到。标签页在后台时不清水位：切到后台那一刻到货的回复，
       正是红点要提示的东西，回到前台由 visibilitychange 补这次清零。 */
    if (document.visibilityState === "visible") markRead(id);
    if (isNarrow()) showChat();
    renderSidebar();
    renderHead();
    renderThread();
    paintNewMessages();
    qs("composer").hidden = false;
    /* 窄屏别抢焦点：一聚焦就弹软键盘，键盘把刚打开的历史整个遮住，
       用户进来只看到一片空白。要打字他自己会点输入框。 */
    if (!isNarrow()) qs("input").focus({ preventScroll: true });
  } catch (err) {
    toast(err.message, "err");
  }
}

/* 等回复时不往消息流塞气泡，改在角色名底下显示「正在输入中」（微信/Telegram 的做法）。
   记的是「哪个会话在等」，所以切去看别的角色自然不会跟过去，切回来还在等就还会显示。 */
function setTyping(on, who) {
  state.typingName = who || "";
  const next = on ? state.convId : null;
  if (state.typingConv === next) return;
  state.typingConv = next;
  renderHead();
}

// 屏幕上这个会话是不是真的在等回复
function isTyping() {
  return state.typingConv !== null && state.typingConv === state.convId;
}

/* ---------------------------------------------------------------- 头部 */

function renderHead() {
  const chars = convChars();
  const head = qs("chat-head");
  if (!chars.length) { head.hidden = true; return; }
  head.hidden = false;
  const group = chars.length > 1;
  const char = chars[0];
  const avatarBox = clear(qs("head-avatar"));
  avatarBox.classList.toggle("stacked", group);   // 群聊把头像叠起来
  for (const c of chars) avatarBox.appendChild(avatarNode(c, "avatar sm"));
  qs("head-name").textContent = group
    ? ((state.conversation && state.conversation.title) || chars.map((c) => c.name).join("、"))
    : char.name;
  const title = qs("head-title");
  const typing = isTyping();
  if (typing) {
    title.textContent = state.typingName ? "「" + state.typingName + "」正在输入中" : "正在输入中";
  } else {
    title.textContent = group ? (chars.length + " 个人 · 轮流发言，也可以你插一句") : (char.title || "");
  }
  title.classList.toggle("typing", typing);
  // 顶部只留群聊的成员名。单聊不再铺任何角色标签（tags、表情使用频率都撤了）：
  // 那些是设定信息，聊天时用不上还占一行。
  const chips = clear(qs("head-chips"));
  if (group) {
    for (const c of chars) chips.appendChild(el("span", { class: "chip", text: c.name }));
  }
  if (state.settings && state.settings.mock_mode) {
    chips.appendChild(el("span", { class: "chip mock", text: "Mock 假模型" }));
  }
  renderGroupBar();
}

function renderBanner() {
  const banner = qs("banner");
  const s = state.settings;
  if (!s) { banner.hidden = true; return; }
  const notes = [];
  if (s.mock_mode) {
    notes.push(el("span", {}, [
      el("b", { text: "当前是 Mock 模式：" }),
      document.createTextNode("回复由本机假生成，只用来验证流程与表情逻辑。接真模型：在设置 → 模型 的「接入方式」里挑一家平台（DeepSeek / 通义千问 / 智谱 / Kimi / OpenAI，地址已预置，只填 Key），或选「自定义 / 中转站」自己填 Base URL。"),
    ]));
    notes.push(el("button", { class: "plain", text: "去设置", onclick: () => openSettings(ctx) }));
  }
  if (s.env_overridden && s.env_overridden.length) {
    notes.push(el("span", { text: "注意：" + s.env_overridden.join("、") + " 被环境变量覆盖，界面上的改动不会生效。" }));
  }
  if (!state.stickers.length) {
    notes.push(el("span", {}, [
      document.createTextNode("表情库是空的（内置素材还没生成）。跑一次 "),
      el("code", { text: "animechat build-assets" }),
      document.createTextNode("，或往 data/stickers 丢图。"),
    ]));
  }
  banner.hidden = !notes.length;
  clear(banner);
  for (const n of notes) banner.appendChild(n);
}

/* ---------------------------------------------------------------- 消息流 */

function renderThread() {
  const box = qs("messages");
  clear(box);
  const thread = el("div", { class: "thread" });
  box.appendChild(thread);
  const list = state.messages;
  // 分组看"是不是同一个人"，不是只看 role：群里两个 AI 连着说话
  // 要是被并成一组，就只剩一个头像，分不清谁说的了。
  const who = (m) => (m.role === "user" ? "user" : (m.speaker || ""));
  const near = (a, b) => !!a && !!b && who(a) === who(b) && Math.abs((b.created_at || 0) - (a.created_at || 0)) < 300;
  for (let i = 0; i < list.length; i += 1) {
    const node = messageNode(list[i]);
    // Telegram 式分组：同一方、间隔 5 分钟内的连续消息收拢——只留组首头像，组中不带时间行和尾巴
    if (near(list[i - 1], list[i])) node.classList.add("grouped");
    if (near(list[i], list[i + 1])) node.classList.add("mid");
    thread.appendChild(node);
  }
  if (!state.messages.length) {
    // 空状态由这里自己画：#messages 每次渲染都会被清空，任何放在它里面的静态节点都会被销毁
    const hasConv = !!state.convId;
    thread.appendChild(el("div", { class: "empty" }, [
      el("div", { class: "empty-art", text: hasConv ? "💬" : "🌸" }),
      el("h3", { text: hasConv ? "说点什么吧" : "挑一个角色开始聊天" }),
      el("p", { text: hasConv
        ? "角色会按自己的性格回你，并在合适的时候甩表情包。"
        : "左侧是角色列表。点开任意角色就能开一轮新对话；角色会按自己的性格决定什么时候给你甩表情包。" }),
      el("button", {
        class: "primary",
        text: hasConv ? "写第一条消息" : "开始一轮新对话",
        onclick: () => { if (hasConv) { qs("input").focus({ preventScroll: true }); } else { newConversation(state.charId); } },
      }),
    ]));
  }
  scrollBottom();
}

function messageNode(msg) {
  const me = msg.role === "user";
  const char = speakerOf(msg);
  const body = el("div", { class: "msg-body" });
  // 群聊必须标清是谁说的，否则一堆气泡分不出人（微信/Telegram 群聊都这样）
  if (!me && isGroup() && char) {
    body.appendChild(el("div", { class: "msg-name", text: char.name }));
  }
  const stickers = msg.sticker_objects || stickersOf(msg.stickers);
  if (stickers.length) {
    const row = el("div", { class: "sticker-row" });
    for (const st of stickers) row.appendChild(stickerNode(st));
    body.appendChild(row);
  }
  const text = String(msg.content || "");
  const errText = (!me && msg.meta && msg.meta.error) ? String(msg.meta.error) : "";
  let bubble;
  if (errText) {
    const lines = errText.split("\n");
    bubble = el("div", { class: "bubble error" }, [
      el("span", { text: "出错了：" + lines[0] }),
      lines.length > 1 ? el("span", { class: "hint", text: lines.slice(1).join(" ") }) : null,
    ]);
  } else {
    bubble = el("div", {
      class: "bubble" + (text ? "" : " empty-text"),
      text: text || (stickers.length ? "" : "…"),
    });
  }
  body.appendChild(bubble);
  if (!me && msg.meta && msg.meta.reasoning) {
    body.insertBefore(thinkNode(String(msg.meta.reasoning)), bubble);
  }
  const meta = el("div", { class: "msg-meta" });
  meta.appendChild(el("span", { text: fmtTime(msg.created_at) }));
  if (msg.emotion) meta.appendChild(el("span", { class: "chip emo", text: emotionLabel(msg.emotion) }));
  if (!me && msg.meta && msg.meta.model) {
    const info = msg.meta.model + (msg.meta.elapsed_ms ? " · " + (msg.meta.elapsed_ms / 1000).toFixed(1) + "s" : "");
    meta.appendChild(el("span", { class: "chip", text: info, title: "提示词 " + (msg.meta.prompt_chars || 0) + " 字" }));
  }
  if (!me && msg.meta && msg.meta.attempts && msg.meta.attempts.length) {
    meta.appendChild(el("button", {
      text: "路由 " + msg.meta.attempts.length + " 次", title: "看网关这次打了哪些上游",
      onclick: () => showAttempts(msg.meta.attempts),
    }));
  }
  if (!me && msg.meta && msg.meta.stopped) meta.appendChild(el("span", { class: "chip", text: "已中断" }));
  if (errText) {
    meta.appendChild(el("button", { text: "重试", onclick: () => send({ regenerate: true }) }));
    meta.appendChild(el("button", { text: "打开设置", onclick: () => openSettings(ctx) }));
  }
  meta.appendChild(el("button", { text: "复制", onclick: () => {
    navigator.clipboard.writeText(text).then(() => toast("已复制"), () => toast("浏览器不让复制", "err"));
  } }));
  meta.appendChild(el("button", { text: me ? "重发这条" : "重新生成", onclick: () => {
    if (me) { qs("input").value = text; qs("input").focus({ preventScroll: true }); }
    else regenerate();
  } }));
  meta.appendChild(el("button", { text: "删到这儿", onclick: async () => {
    if (!(await confirmDialog("删除这条及之后的消息？", "会话不会被删除，只回滚到这条之前。", "删除"))) return;
    try { await api("/api/messages/" + msg.id, { method: "DELETE" }); await openConversation(state.convId); }
    catch (err) { toast(err.message, "err"); }
  } }));
  body.appendChild(meta);

  return el("div", { class: "msg" + (me ? " me" : "") }, [
    // 两边都有脸才是 IM。自己那张来自设置，没设头像时 avatarNode 会退回名字首字。
    me ? avatarNode(meProfile(), "avatar sm") : avatarNode(char, "avatar sm"),
    body,
  ]);
}

/* 「我」这一侧的头像和名字都存在设置里，这里伪造成 avatarNode 认识的那个形状：
   一个界面里两套头像渲染逻辑迟早会漂。updated_at 对应 user_avatar_at —— 换头像时刷新的
   版本号，文件名没变，只能靠它击穿浏览器缓存。 */
function meProfile() {
  const s = state.settings || {};
  return {
    name: s.user_name || "我",
    avatar: s.user_avatar || "",
    updated_at: s.user_avatar_at || 0,
  };
}

function stickersOf(ids) {
  return (ids || []).map((id) => state.stickers.find((s) => s.id === id)).filter(Boolean);
}

function stickerNode(st) {
  const shot = el("div", { class: "sticker-shot", title: (st.label || "") + " · " + (st.tags || []).join("/") }, [
    el("img", { src: st.url, alt: st.label || "表情包", loading: "lazy" }),
    st.auto ? el("span", { class: "auto-tag", text: "自动" }) : null,
  ]);
  shot.addEventListener("click", () => lightbox(st.url, st.label));
  return shot;
}

function emotionLabel(key) {
  const found = state.emotions.find((e) => e.key === key);
  return found ? found.label : key;
}

/* reasoning 模型（qwen3 / r1 这类）会先流式输出思考。不显示出来，用户看到的就是
   半天不出字，以为坏了。默认折叠，出正文时自动收起。 */
function thinkNode(text, open) {
  const body = el("div", { class: "think-body", text: text || "" });
  return el("details", { class: "think", open: !!open }, [
    el("summary", { text: "思考过程（" + (text || "").length + " 字）" }),
    body,
  ]);
}

function showAttempts(attempts) {
  const list = el("ul", { class: "attempts" });
  for (const a of attempts) {
    list.appendChild(el("li", {
      text: (a.provider_slug || a.provider || "?") + " / " + (a.model || "?") + "  →  "
        + (a.status || "?") + " (" + (a.http_status || "-") + ", " + (a.latency_ms || 0) + "ms)"
        + (a.error_code ? " " + a.error_code : ""),
    }));
  }
  openModal(el("div", {}, [
    titleBar("这次网关的真实选路", "hub.attempts"),
    el("p", { class: "sub", text: "逐次尝试的顺序，能看出为什么慢、为什么换了模型。" }),
    list,
  ]));
}

function isNearBottom(box, threshold = 72) {
  return box.scrollHeight - (box.scrollTop + box.clientHeight) <= threshold;
}

function chatVisible() {
  return !isNarrow() || isChatOpen();
}

function scrollBottom() {
  const box = qs("messages");
  box.scrollTop = box.scrollHeight;
  paintNewMessages();
}

function paintNewMessages() {
  const btn = qs("btn-new-messages");
  if (!btn) return;
  const box = qs("messages");
  const show = chatVisible() && !isNearBottom(box);
  if (show === btn.hidden) {
    btn.hidden = !show;
    btn.textContent = "↓ 新消息";
  }
}

function liveMessage(live, stopped, text) {
  const meta = {};
  if (live.reasoning) meta.reasoning = live.reasoning;
  if (live.model) meta.model = live.model;
  if (live.elapsedMs) meta.elapsed_ms = live.elapsedMs;
  if (live.promptChars) meta.prompt_chars = live.promptChars;
  if (live.attempts && live.attempts.length) meta.attempts = live.attempts.slice();
  if (stopped) meta.stopped = true;
  return {
    id: live.messageId || -Date.now(),
    role: "assistant",
    content: text,
    stickers: live.stickers.slice(),
    emotion: live.emotion,
    sticker_objects: live.stickerObjects || [],
    created_at: Date.now() / 1000,
    speaker: live.speaker || null,
    meta: live.failed ? Object.assign({ error: live.errorText || "请求失败" }, meta) : meta,
  };
}

function finalizeLiveMessage(live, stopped, text, wrap) {
  if (live.finalized) return;
  live.finalized = true;
  const msg = liveMessage(live, stopped, text);
  // 切走之后 state.messages 是别人家的数组，一条都不许往里塞
  const here = live.ownerConv != null && state.convId === live.ownerConv;
  if (here) {
    const index = state.messages.findIndex((item) => item.id === msg.id);
    if (index >= 0) state.messages[index] = msg;
    else state.messages.push(msg);
  }
  // 流结束时用户可能已经切走：旧会话 DOM 里的 live wrap 必须当场清掉，
  // 不能等下一次 renderThread 才处理，否则它会短暂留在错误的消息流里。
  if (!here) {
    if (wrap && wrap.parentNode) wrap.remove();
    return;
  }
  /* 失败的气泡本身就是终态（红字 + 重试 / 打开设置），换成 messageNode 反而把那两个
     按钮弄丢；所以只记账、不重绘。 */
  if (live.failed || !wrap || !wrap.parentNode) return;
  const permanent = messageNode(msg);
  wrap.parentNode.replaceChild(permanent, wrap);
  if (isNearBottom(threadEl())) scrollBottom(); else paintNewMessages();
}

// #messages 里可能残留空白文本节点，所以永远显式取 .thread，不用 firstChild
function threadEl() {
  const box = qs("messages");
  let thread = box.querySelector(".thread");
  if (!thread) {
    clear(box);
    thread = el("div", { class: "thread" });
    box.appendChild(thread);
  }
  return thread;
}

/* ---------------------------------------------------------------- 发送与流式 */

async function send(opts) {
  const options = opts || {};
  if (state.streaming) return;
  if (!state.convId) { toast("先选一个角色", "err"); return; }
  const input = qs("input");
  const text = String(input.value || "").trim();
  const stickers = state.attach.map((s) => s.id);
  // options.auto：群聊里让 AI 直接接上一句，本轮没有用户发言
  if (options.auto && !isGroup()) { toast("只有群聊才能让 AI 直接接话", "err"); return; }
  if (!options.regenerate && !options.auto && !text && !stickers.length) return;

  // 用户自己的消息先本地落一屏，不等接口
  const wasNearBottom = isNearBottom(threadEl());
  if (!options.regenerate && !options.auto) {
    const optimistic = {
      id: -Date.now(), role: "user", content: text, stickers: stickers,
      sticker_objects: state.attach.slice(), created_at: Date.now() / 1000, meta: {},
    };
    state.messages.push(optimistic);
    const mine = messageNode(optimistic);
    mine.classList.add("enter");   // 刚发出的那条才播入场动画
    threadEl().appendChild(mine);
    playSend();
    state.attach = [];
    renderAttach();
    input.value = "";
    autosize(input);
    // 表情面板挡着聊天流，选完发完就该让位；以前要手动点 × 才能看到回复。
    if (!qs("picker").hidden) togglePicker(false);
  }
  if (wasNearBottom) scrollBottom(); else paintNewMessages();

  // 这一轮点名让谁说；发完就交回给轮转，下一条自动换人
  const speaker = options.speaker || null;
  const sentTo = state.convId;   // 这轮到底是在跟哪个会话说话，中途切走也要认得
  state.streaming = true;
  state.abort = new AbortController();
  renderSendButton();
  setTyping(true, speaker ? charName(speaker) : "");

  const live = createLiveBubble();
  try {
    await streamChat({
      conversation_id: sentTo,
      content: (options.regenerate || options.auto) ? "" : text,
      stickers: (options.regenerate || options.auto) ? [] : stickers,
      regenerate: !!options.regenerate, auto: !!options.auto, speaker: speaker || "",
    }, live, state.abort.signal);
  } catch (err) {
    if (err.name !== "AbortError") {
      live.failed = true;
      live.fail(err.message);
      live.finish(false);
    }
    // 主动停止不是失败：已收到的正文/表情仍是有效回复，要固化为“已中断”消息。
    else live.finish(true);
  } finally {
    state.streaming = false;
    state.abort = null;
    setTyping(false);
    renderSendButton();
    loadConversations();
    loadStickers();
    /* 刚回的那句要不要算读到，只看「此刻屏幕上是不是这个会话、标签页在前台」：
       中途切去看别人 / 切到后台的话，它就是一条正经未读，红点得留着。 */
    if (state.convId && document.visibilityState === "visible") markRead(state.convId);
    // 成功回复已经由流式收尾局部落屏，这里不用再整段拉历史重绘；
    // 只在用户还看着这个会话时刷新新消息按钮状态。
    if (!live.failed && state.convId === sentTo) paintNewMessages();
    renderGroupBar();
  }
}

/* ---------------------------------------------------------- 群聊控制 */

/* 群聊才显示的一条：预告下一个开口的人、允许点名、以及让她们自己聊。
   点名是临时的一次性指定，发完就交回轮转，否则整场都会被按死在同一个人身上。 */
function renderGroupBar() {
  const bar = qs("group-bar");
  if (!bar) return;
  const on = isGroup() && !!state.convId;
  bar.hidden = !on;
  if (!on) return;
  const who = clear(qs("group-who"));
  const next = charById(nextSpeakerId());
  who.appendChild(el("span", { class: "group-next", text: "下一个：" + (next ? next.name : "?")
    + (state.speaker ? "（你点的名）" : "（自动轮转）") }));
  for (const c of convChars()) {
    who.appendChild(el("button", {
      class: "pick-chip" + (state.speaker === c.id ? " on" : ""), text: c.name,
      title: state.speaker === c.id ? "取消点名，回到自动轮转" : "下一轮让 " + c.name + " 先说",
      onclick: () => { state.speaker = state.speaker === c.id ? null : c.id; renderGroupBar(); },
    }));
  }
  const busy = state.streaming;
  qs("btn-ai-next").disabled = busy;
  qs("btn-ai-loop").disabled = busy && !state.autoing;
  qs("btn-ai-loop").textContent = state.autoing ? "停下" : "自己聊几轮";
}

async function aiNextTurn() {
  if (state.streaming) return;
  await send({ auto: true, speaker: nextSpeakerId() });
}

// 自己聊：一轮接一轮，每轮都重新算下一个顺位；再点一次就停。
async function aiAutoLoop(rounds) {
  if (state.autoing) { state.autoing = false; renderGroupBar(); return; }
  state.autoing = true;
  renderGroupBar();
  for (let i = 0; i < rounds && state.autoing; i += 1) {
    await send({ auto: true, speaker: nextSpeakerId() });
    if (!state.autoing) break;
    await new Promise((r) => setTimeout(r, 900));   // 给上一句留出读的时间
  }
  state.autoing = false;
  renderGroupBar();
}

async function newGroupConversation() {
  const chars = state.characters;
  if (chars.length < 2) { toast("至少要两个角色才能建群聊", "err"); return; }
  const picked = new Set(state.charId ? [state.charId] : []);
  const list = el("div", { class: "pick-list" });
  const hint = el("p", { class: "tip" });
  const title = el("input", { type: "text", placeholder: "群名（可留空，默认用成员名字）" });
  const ok = el("button", { class: "primary", text: "建群" });
  const paint = () => {
    hint.textContent = picked.size < 2
      ? "再挑一个，至少两个才聊得起来（当前 " + picked.size + " 个）"
      : "已选 " + picked.size + " 个人，会按这个顺序轮流发言";
    ok.disabled = picked.size < 2;
  };
  for (const c of chars) {
    const box = el("input", { type: "checkbox" });
    box.checked = picked.has(c.id);
    box.addEventListener("change", () => {
      if (box.checked) picked.add(c.id); else picked.delete(c.id);
      paint();
    });
    list.appendChild(el("label", { class: "pick-row" }, [
      box, avatarNode(c, "avatar sm"),
      el("span", { class: "pick-meta" }, [
        el("b", { text: c.name }), el("span", { text: c.title || "" }),
      ]),
    ]));
  }
  ok.onclick = async () => {
    const ids = Array.from(picked);
    if (ids.length < 2) return;
    try {
      const data = await api("/api/conversations", { method: "POST", json: {
        character_id: ids[0], participants: ids, title: title.value.trim(),
      } });
      closeModal();
      await loadConversations();
      state.charId = ids[0];
      openConversation(data.conversation.id);
      toast("群聊建好了，她们各自打了招呼");
    } catch (err) { toast(err.message, "err"); }
  };
  openModal(el("div", {}, [
    titleBar("拉个群", "让几个 AI 自己聊起来"),
    el("p", { class: "sub", text: "选两个以上角色。她们轮流发言、也会互相接话；你随时插一句，说完还是接着轮。" }),
    list, title, hint,
    el("div", { class: "row-actions" }, [ok, el("button", { class: "plain", text: "取消", onclick: closeModal })]),
  ]), { wide: true });
  paint();
}

function regenerate() {
  if (!state.messages.length) return;
  // 丢掉最后一条助手回复，重画整个会话（比在 DOM 里找节点可靠），再让模型重来
  let lastAssistant = -1;
  for (let i = state.messages.length - 1; i >= 0; i -= 1) {
    if (state.messages[i].role === "assistant") { lastAssistant = i; break; }
  }
  if (lastAssistant < 0) { toast("还没有可重新生成的回复", "err"); return; }
  state.messages.splice(lastAssistant);
  renderThread();
  send({ regenerate: true });
}

function createLiveBubble() {
  /* 这条流属于发起它的那个会话。用户中途切去看别人，气泡、滚动、state.messages
     都不许跟到新会话里去——以前首字一到就 threadEl().appendChild(wrap)，
     而 threadEl() 拿的是「此刻屏幕上」那个 .thread，于是 A 的回复画进了 B 的窗口。 */
  const ownerConv = state.convId;
  const ownerConversation = state.conversation;
  const ownerIds = convIds(ownerConversation);
  const ownerIsGroup = ownerIds.length > 1;
  const onScreen = () => state.convId === ownerConv;
  const body = el("div", { class: "msg-body" });
  const stickerRow = el("div", { class: "sticker-row" });
  const bubble = el("div", { class: "bubble" });
  const meta = el("div", { class: "msg-meta" });
  body.appendChild(stickerRow);
  body.appendChild(bubble);
  body.appendChild(meta);
  const wrap = el("div", { class: "msg" }, [body]);
  /* 说话的人只以服务端 start 事件里的 speaker 为准；点名 / 重新生成时，
     不再提前把头像挂到轮转顺序里的下一个人脸上。 */
  let avatarEl = null;
  let nameEl = null;
  function applySpeaker(speaker) {
    const char = speaker ? charById(speaker) : null;
    if (!avatarEl && char) {
      avatarEl = avatarNode(char, "avatar sm");
      wrap.insertBefore(avatarEl, body);
    } else if (avatarEl && char) {
      const next = avatarNode(char, "avatar sm");
      avatarEl.replaceWith(next);
      avatarEl = next;
    } else if (avatarEl && !char) {
      avatarEl.remove();
      avatarEl = null;
    }
    if (ownerIsGroup && char) {
      if (!nameEl) {
        nameEl = el("div", { class: "msg-name", text: char.name });
        body.insertBefore(nameEl, stickerRow);
      } else {
        nameEl.textContent = char.name;
      }
    } else if (nameEl) {
      nameEl.remove();
      nameEl = null;
    }
  }
  /* 等首字时不再先甩一个空气泡：微信/Telegram 都是「对方名字底下显示正在输入」，
     气泡要等内容真的到了才出现。所以节点先建好挂着，第一次有内容才挂进消息流。 */
  let mounted = false;
  let abandoned = false;   // 用户已经切走：这条流只继续攒内容，不再画屏
  function mount() {
    if (mounted || abandoned) return;
    if (!onScreen()) { abandoned = true; return; }
    mounted = true;
    wrap.classList.add("enter");   // 首字到达这一刻，气泡从头像那侧弹进来
    threadEl().appendChild(wrap);
    setTyping(false);
    if (isNearBottom(threadEl())) scrollBottom();
  }

  let text = "";
  let textNode = null;
  let thinkBox = null;
  let thinkBody = null;
  let thinkSummary = null;
  const live = {
    ownerConv,
    finalized: false,
    failed: false,
    stopped: false,
    stickers: [],
    emotion: null,
    reasoning: "",
    think(part) {
      mount();
      live.reasoning += part;
      if (!thinkBox) {
        thinkSummary = el("summary", {});
        thinkBody = el("div", { class: "think-body" });
        thinkBox = el("details", { class: "think", open: true }, [thinkSummary, thinkBody]);
        body.insertBefore(thinkBox, bubble);
      }
      thinkBody.textContent = live.reasoning;
      thinkSummary.textContent = "思考中…（" + live.reasoning.length + " 字）";
      if (mounted && thinkBox.open) scrollBottom();
    },
    foldThink() {
      if (!thinkBox) return;
      thinkBox.open = false;
      thinkSummary.textContent = "思考过程（" + live.reasoning.length + " 字）";
    },
    // done 事件带回落库后的最终正文，只能走这里改闭包里的 text/textNode：
    // 直接在 handleEvent 里 text = ... 会 ReferenceError（模块是严格模式），
    // 于是每条成功回复都会被自己的收尾代码报成"出错了"。
    setContent(value) {
      text = String(value || "");
      if (text) mount();
      if (!textNode) {
        if (!text) return;
        clear(bubble);
        textNode = document.createTextNode("");
        bubble.appendChild(textNode);
        live.foldThink();
      }
      textNode.nodeValue = text;
    },
    text(part) {
      mount();
      text += part;
      if (!textNode) {
        clear(bubble);
        textNode = document.createTextNode("");
        bubble.appendChild(textNode);
        live.foldThink();     // 开始说正事了，思考过程让位
      }
      textNode.nodeValue = text;
      if (mounted && isNearBottom(threadEl())) scrollBottom();   // 切走了就别滚动人家正在看的会话
    },
    sticker(st) {
      mount();
      live.stickers.push(st.id);
      stickerRow.appendChild(stickerNode(Object.assign({}, st, { auto: st.auto })));
      if (isNearBottom(threadEl())) scrollBottom(); else paintNewMessages();
    },
    emotionSet(key) {
      live.emotion = key;
      clear(meta);
      meta.appendChild(el("span", { class: "chip emo", text: emotionLabel(key) }));
    },
    updateSpeaker(speaker) {
      applySpeaker(speaker);
    },
    finish(stopped) {
      mount();
      live.foldThink();
      finalizeLiveMessage(live, stopped || live.stopped, text, wrap);
    },
    fail(message, hint) {
      mount();
      live.failed = true;
      live.errorText = message + (hint ? "\n" + hint : "");
      bubble.className = "bubble error";
      clear(bubble);
      bubble.appendChild(el("span", { text: "出错了：" + message }));
      if (hint) bubble.appendChild(el("span", { class: "hint", text: hint }));
      clear(meta);
      meta.appendChild(el("button", { text: "重试", onclick: () => send({ regenerate: true }) }));
      meta.appendChild(el("button", { text: "打开设置", onclick: () => openSettings(ctx) }));
    },
  };
  return live;
}

async function streamChat(payload, live, signal) {
  const res = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
  if (!res.ok) {
    let message = "HTTP " + res.status;
    let hint = "";
    try {
      const data = await res.json();
      const err = data && data.error;
      if (err) {
        message = typeof err.message === "string" ? err.message : JSON.stringify(err);
        hint = err.hint || "";
      }
    } catch (ignored) { /* 非 JSON 错误体 */ }
    live.fail(message, hint);
    live.finish(false);   // 记进 state.messages（幂等），不然刷新后错误凭空消失
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  while (true) {
    const chunk = await reader.read();
    if (chunk.done) break;
    buf += decoder.decode(chunk.value, { stream: true });
    let cut = buf.indexOf("\n\n");
    while (cut >= 0) {
      const rawEvent = buf.slice(0, cut);
      buf = buf.slice(cut + 2);
      handleEvent(rawEvent, live);
      cut = buf.indexOf("\n\n");
    }
  }
  if (buf.trim()) handleEvent(buf, live);
  live.finish(false);
}

function handleEvent(raw, live) {
  let name = "message";
  const dataLines = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) name = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (!dataLines.length) return;
  let data;
  try { data = JSON.parse(dataLines.join("\n")); } catch (ignored) { return; }

  if (name === "start") {
    live.messageId = data.message_id;
    if (data.speaker) {
      live.speaker = data.speaker;
      live.updateSpeaker(data.speaker);
    }
  } else if (name === "text") {
    live.text(data.t || "");
  } else if (name === "reasoning") {
    live.think(data.t || "");
  } else if (name === "sticker") {
    live.sticker(data);
    live.stickerObjects = (live.stickerObjects || []).concat([data]);
  } else if (name === "emotion") {
    live.emotionSet(data.key);
  } else if (name === "meta") {
    live.attempts = data.attempts || [];
  } else if (name === "done") {
    live.model = data.model || "";
    live.elapsedMs = data.elapsed_ms || 0;
    live.promptChars = data.prompt_chars || 0;
    if (data.stopped) live.stopped = true;
    live.info = (live.model || "") + (live.elapsedMs ? " · " + (live.elapsedMs / 1000).toFixed(1) + "s" : "")
      + (live.promptChars ? " · 提示词 " + live.promptChars + " 字" : "");
    if (data.emotion) live.emotionSet(data.emotion);
    // 以服务端最终落库内容为准（流式尾巴上的空白可能被裁掉）
    if (typeof data.content === "string") live.setContent(data.content);
    playReceive();   // 回复落定了再响，中途失败/中断不吵你
    renderComposerHint();
  } else if (name === "error") {
    live.fail(data.message || "未知错误", data.hint || "");
  }
}

/* ---------------------------------------------------------------- 表情面板 */

function togglePicker(force) {
  const picker = qs("picker");
  const want = force === undefined ? picker.hidden : force;
  picker.hidden = !want;
  if (want) {
    renderPicker();
    if (isNarrow()) {
      // 面板是 .main 的最后一个 flex 子节点（在输入框下方），不滚一下可能整块还在屏幕外
      if (picker.scrollIntoView) picker.scrollIntoView({ block: "end", behavior: "smooth" });
    } else {
      qs("picker-q").focus({ preventScroll: true });
    }
  }
}

function filteredStickers() {
  const p = state.picker;
  let pool = state.stickers.slice();
  if (p.tab === "favorite") pool = pool.filter((s) => s.favorite);
  else if (p.tab === "builtin") pool = pool.filter((s) => s.origin === "builtin");
  else if (p.tab === "user") pool = pool.filter((s) => s.origin !== "builtin");
  if (p.emotion) pool = pool.filter((s) => s.emotion === p.emotion);
  const q = p.q.trim().toLowerCase();
  if (q) {
    pool = pool.filter((s) => {
      const hay = [s.id, s.label, s.emotion, s.emotion_label].concat(s.tags || []).join(" ").toLowerCase();
      return hay.indexOf(q) >= 0;
    });
  }
  return pool;
}

function renderPicker() {
  const p = state.picker;
  for (const btn of qs("picker-tabs").children) btn.classList.toggle("on", btn.dataset.tab === p.tab);
  const emoRow = clear(qs("picker-emotions"));
  emoRow.appendChild(el("button", {
    class: p.emotion ? "" : "on", text: "全部情绪",
    onclick: () => { p.emotion = ""; renderPicker(); },
  }));
  for (const emo of state.emotions) {
    if (emo.key === "neutral" && p.tab !== "all") continue;
    emoRow.appendChild(el("button", {
      class: p.emotion === emo.key ? "on" : "", text: emo.label,
      onclick: () => { p.emotion = p.emotion === emo.key ? "" : emo.key; renderPicker(); },
    }));
  }
  const grid = clear(qs("picker-grid"));
  if (p.tab === "web") { renderWebPicker(grid); return; }
  const list = filteredStickers();
  if (!list.length) {
    grid.appendChild(el("div", { class: "picker-loading", text: "没有匹配的表情。换个标签，或去「联网搜索」。" }));
  }
  for (const st of list) {
    const picked = state.attach.some((s) => s.id === st.id);
    const cell = el("div", { class: "sticker-cell" + (picked ? " picked" : ""), title: (st.tags || []).join("、") }, [
      el("img", { src: st.url, alt: st.label, loading: "lazy" }),
      el("figcaption", { text: st.label || st.id }),
      // 点整格是"选中要发"，所以预览得给个单独按钮，不然没法在发之前看清这张是什么
      el("button", { class: "cell-btn zoom", title: "放大预览", text: "🔍",
        onclick: (ev) => { ev.stopPropagation(); lightbox(st.url, st.label); } }),
    ]);
    cell.addEventListener("click", () => toggleAttach(st));
    grid.appendChild(cell);
  }
  const foot = clear(qs("picker-foot"));
  foot.appendChild(el("span", { text: "共 " + list.length + " 张 · 点选加入，最多 3 张" }));
  foot.appendChild(el("button", { text: "管理表情库", onclick: () => openStickerLibrary(ctx) }));
  if (state.attach.length) {
    foot.appendChild(el("button", { text: "清空已选", onclick: () => { state.attach = []; renderAttach(); renderPicker(); } }));
  }
}

async function renderWebPicker(grid) {
  const p = state.picker;
  if (p.loading) { grid.appendChild(spinner("联网搜索中…")); return; }
  if (!p.web.length) {
    grid.appendChild(el("div", { class: "picker-loading" }, [
      el("div", { text: "输入关键词后回车搜索（例如：傲娇 表情 / 柴田雪成 戳图）" }),
      el("div", { text: p.note || "默认走 DuckDuckGo（无需 Key）。想更稳可以在设置里填 Tenor Key。" }),
    ]));
    return;
  }
  for (const item of p.web) {
    const cell = el("div", { class: "sticker-cell", title: item.label }, [
      el("img", { src: item.thumb_url || item.image_url, alt: item.label, loading: "lazy", referrerpolicy: "no-referrer" }),
      el("figcaption", { text: (item.label || "").slice(0, 12) }),
      el("button", {
        class: "cell-btn", text: "＋", title: "存进表情库并发出去",
        onclick: async (ev) => {
          ev.stopPropagation();
          const btn = ev.currentTarget;
          btn.textContent = "…";
          try {
            const data = await api("/api/stickers/import", {
              method: "POST",
              json: Object.assign({ url: item.image_url, referer: item.page || "", query: p.q,
                label: (item.label || "").slice(0, 20) }, webTags(p.q, p.emotion)),
            });
            await loadStickers();
            toggleAttach(data.sticker);
            toast("已存进表情库");
          } catch (err) {
            btn.textContent = "＋";
            toast("存不下：" + err.message, "err");
          }
        },
      }),
    ]);
    cell.addEventListener("click", () => lightbox(item.image_url, item.label));
    grid.appendChild(cell);
  }
  const foot = clear(qs("picker-foot"));
  foot.appendChild(el("span", { text: "来源：" + p.provider + " · 点 ＋ 存进表情库（别直接外链，容易挂）" }));
  if (p.note) foot.appendChild(el("span", { text: p.note }));
}

// 联网搜索的关键词 → 中文标签 + 情绪。库里全是中文标签，只挂一个英文搜索词的话，
// 模型下次挑图时根本对不上；情绪为空则会被兜底成 neutral，等于这张图基本不会被发。
const WEB_EMO = [
  [/cry|tear|sad|depress|sob/i, "sad", ["哭", "难过", "呜呜"]],
  [/laugh|lol|haha|happy|smile|joy|giggle|glee/i, "happy", ["开心", "哈哈", "笑死"]],
  [/angry|rage|furious|smash|punch|slap/i, "angry", ["生气", "炸毛", "达咩"]],
  [/blush|shy|tsundere|embarrass|hmph/i, "tsundere", ["害羞", "傲娇", "脸红"]],
  [/hug|cuddle|kiss|love|heart|melt|smooch/i, "love", ["抱抱", "贴贴", "喜欢"]],
  [/shock|surpri|omg|stun|wow|mind.?blown/i, "shock", ["震惊", "什么", "石化"]],
  [/sleep|yawn|tired|bed|drows|nap/i, "sleepy", ["困", "打哈欠", "想睡"]],
  [/think|consider|analyz|hmm|wonder|ponder/i, "think", ["思考", "想想", "分析"]],
  [/sweat|awkward|cringe|shrug|embarr/i, "awkward", ["尴尬", "汗", "无语"]],
  [/dance|hype|party|excite|sparkle|cheer|pump/i, "hype", ["冲", "兴奋", "跳舞"]],
  [/hello|hi|wave|greet|hey/i, "happy", ["打招呼", "挥手", "嗨"]],
];

function webTags(q, chosenEmotion) {
  const query = String(q || "").trim();
  let emotion = "";
  let zh = [];
  for (const row of WEB_EMO) {
    if (row[0].test(query)) { emotion = row[1]; zh = row[2]; break; }
  }
  if (chosenEmotion) emotion = chosenEmotion;   // 手动筛了情绪就听他的
  const tags = [query].concat(zh).filter(Boolean).slice(0, 8);
  return { tags, emotion };
}

async function doWebSearch() {
  const p = state.picker;
  const q = p.q.trim();
  if (!q) { toast("先输入关键词", "err"); return; }
  p.loading = true;
  p.web = [];
  renderPicker();
  try {
    const data = await api("/api/stickers/search-web", { method: "POST", json: { q, limit: 24 } });
    p.web = data.results || [];
    p.provider = data.provider;
    p.note = data.note || "";
  } catch (err) {
    p.note = err.message;
    p.web = [];
  } finally {
    p.loading = false;
    renderPicker();
  }
}

function toggleAttach(st) {
  const index = state.attach.findIndex((s) => s.id === st.id);
  if (index >= 0) state.attach.splice(index, 1);
  else {
    if (state.attach.length >= 3) { toast("一条消息最多 3 张表情包"); return; }
    state.attach.push(st);
  }
  renderAttach();
  renderPicker();
}

function renderAttach() {
  const row = clear(qs("attach-row"));
  row.hidden = !state.attach.length;
  for (const st of state.attach) {
    row.appendChild(el("div", { class: "attach-item" }, [
      el("img", { src: st.url, alt: st.label }),
      el("button", { text: "×", title: "移除", onclick: () => toggleAttach(st) }),
    ]));
  }
  renderComposerHint();
}

function renderComposerHint() {
  const char = currentChar();
  const bits = [];
  if (char) {
    const mode = state.settings ? state.settings.sticker_mode : "rich";
    if (char.sticker_style === "off") bits.push("这个角色只用文字");
    else if (mode === "off") bits.push("自动表情已关（模型主动发的仍会显示）");
    else bits.push(mode === "rich" ? "表情：积极" : "表情：克制");
  }
  if (state.attach.length) bits.push("已选 " + state.attach.length + " 张待发送");
  qs("composer-hint").textContent = bits.join(" · ");
}

/* ---------------------------------------------------------------- 菜单与静态绑定 */

function openCharMenu(char) {
  const rows = el("div", { class: "row-actions", style: "flex-direction:column;align-items:stretch" });
  const mk = (label, fn, cls) => rows.appendChild(el("button", {
    class: cls || "plain", text: label, onclick: () => { closeModal(); fn(); },
  }));
  mk("新建一轮对话", () => newConversation(char.id), "primary");
  mk("编辑人设 / 外观", () => openCharEditor(char, ctx));
  if (!char.builtin) mk("改头像（重新画一张）", () => openCharEditor(char, ctx));
  mk("复制一份再改", async () => {
    try { const d = await api("/api/characters/" + char.id + "/duplicate", { method: "POST" }); await loadBootstrap(); selectCharacter(d.character.id); }
    catch (err) { toast(err.message, "err"); }
  });
  mk("导出角色卡 JSON（SillyTavern 通用）", () => downloadFile("/api/characters/" + char.id + "/export.json"));
  mk("导出 PNG 角色卡（人设写进图片）", () => downloadFile("/api/characters/" + char.id + "/export.png"));
  if (char.builtin) {
    mk("恢复出厂 / 取消隐藏", async () => {
      try { await api("/api/characters/" + char.id + "/restore", { method: "POST" }); await loadBootstrap(); }
      catch (err) { toast(err.message, "err"); }
    });
  }
  mk(char.builtin ? "隐藏这个角色" : "删除这个角色", async () => {
    if (!(await confirmDialog(char.builtin ? "隐藏内置角色？" : "删除自定义角色？",
        char.builtin ? "只是从列表里藏起来，随时可以「恢复出厂」找回来。" : "会话记录会保留，但角色本身删掉。", "确定"))) return;
    try {
      await api("/api/characters/" + char.id, { method: "DELETE" });
      await loadBootstrap();
      if (state.charId === char.id) selectCharacter(state.characters[0] ? state.characters[0].id : null);
    } catch (err) { toast(err.message, "err"); }
  }, "danger");
  openModal(el("div", {}, [titleBar(char.name, char.builtin ? "内置" : "自定义"), el("p", { class: "sub", text: char.title || "" }), rows]));
}

async function compactNow() {
  if (!state.convId) { toast("还没有会话可总结"); return; }
  toast("正在总结…");
  try {
    const data = await api("/api/conversations/" + state.convId + "/compact", { method: "POST" });
    toast(data.note || "记忆已更新（" + (data.summary || "").length + " 字）");
  } catch (err) { toast(err.message, "err"); }
}

/* 窄屏头部放不下「上下文 / 总结记忆 / 删除」三个文字按钮（360px 会横向溢出），
   收成一个 ⋯。三件事和原来一模一样，只是换了个入口，所以直接复用现有处理函数。 */
function openHeadMenu() {
  const rows = el("div", { class: "row-actions", style: "flex-direction:column;align-items:stretch" });
  const mk = (label, hint, fn, cls) => rows.appendChild(el("button", {
    class: cls || "plain", text: label, title: hint, onclick: () => { closeModal(); fn(); },
  }));
  mk("看本次发给模型的内容", "上下文", () => { if (state.convId) openContext(state.convId, ctx); });
  mk("把较早的对话压缩成角色记忆", "总结记忆", compactNow);
  mk("删除当前会话", "删除", () => {
    if (state.convId) openConvMenu(state.conversation || { id: state.convId });
  }, "danger");
  openModal(el("div", {}, [titleBar("会话操作", charName(state.charId) || ""), rows]));
}

async function openConvMenu(conv) {
  const rows = el("div", { class: "row-actions", style: "flex-direction:column;align-items:stretch" });
  const mk = (label, fn, cls) => rows.appendChild(el("button", { class: cls || "plain", text: label, onclick: () => { closeModal(); fn(); } }));
  mk("打开", () => openConversation(conv.id), "primary");
  mk(conv.pinned ? "取消置顶" : "置顶", async () => {
    await api("/api/conversations/" + conv.id, { method: "PATCH", json: { pinned: !conv.pinned } });
    await loadConversations();
  });
  mk("重命名", () => {
    const input = el("input", { type: "text", value: conv.title || "", placeholder: "会话标题" });
    openModal(el("div", {}, [
      titleBar("重命名会话"),
      el("div", { class: "field" }, [input]),
      el("div", { class: "row-actions" }, [
        el("button", { class: "primary", text: "保存", onclick: async () => {
          await api("/api/conversations/" + conv.id, { method: "PATCH", json: { title: input.value } });
          closeModal();
          await loadConversations();
          if (state.convId === conv.id) state.conversation.title = input.value, renderHead();
        } }),
      ]),
    ]));
    input.focus();
  });
  mk("删除会话", async () => {
    if (!(await confirmDialog("删除这个会话？", "所有消息记录一起删掉，不可恢复。", "删除"))) return;
    await api("/api/conversations/" + conv.id, { method: "DELETE" });
    await loadConversations();
    if (state.convId === conv.id) {
      state.convId = null;
      const mine = state.conversations.filter((c) => c.character_id === state.charId);
      if (mine.length) openConversation(mine[0].id); else newConversation(state.charId);
    }
  }, "danger");
  openModal(el("div", {}, [titleBar("会话"), el("p", { class: "sub", text: charName(conv.character_id) + " · " + conv.message_count + " 条消息" }), rows]));
}

function downloadFile(path) {
  const a = el("a", { href: path, download: "" });
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function renderSendButton() {
  const btn = qs("btn-send");
  btn.textContent = state.streaming ? "停止" : "发送";
  btn.classList.toggle("stop", state.streaming);
  btn.title = state.streaming ? "中断本次回复（已收到的部分会保留）" : "发送（Enter）";
}

function autosize(node) {
  node.style.height = "auto";
  node.style.height = Math.min(180, node.scrollHeight) + "px";
}

function initTheme() {
  const saved = localStorage.getItem("animechat-theme");
  const theme = saved || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  document.documentElement.dataset.theme = theme;
  qs("btn-theme").textContent = theme === "dark" ? "☀" : "☾";
}

function bindStatic() {
  qs("btn-new-char").addEventListener("click", () => openCharEditor(null, ctx));
  qs("btn-ai-char").addEventListener("click", () => openAICreate(ctx));
  qs("btn-new-conv").addEventListener("click", () => newConversation(state.charId));
  qs("head-avatar").addEventListener("click", () => { if (currentChar()) openCharEditor(currentChar(), ctx); });
  qs("btn-context").addEventListener("click", () => { if (state.convId) openContext(state.convId, ctx); });
  qs("btn-del-conv").addEventListener("click", () => { if (state.convId) openConvMenu(state.conversation || { id: state.convId }); });
  qs("btn-head-menu").addEventListener("click", openHeadMenu);
  qs("btn-compact").addEventListener("click", compactNow);
  qs("file-card").addEventListener("change", async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const data = await api("/api/characters/import", { method: "POST", form });
      await loadBootstrap();
      selectCharacter(data.character.id);
      toast("已导入角色：" + data.character.name);
    } catch (err) { toast("导入失败：" + err.message, "err"); }
    ev.target.value = "";
  });

  qs("btn-pick").addEventListener("click", () => togglePicker());
  qs("picker-close").addEventListener("click", () => togglePicker(false));
  qs("picker-q").addEventListener("input", (ev) => { state.picker.q = ev.target.value; if (state.picker.tab !== "web") renderPicker(); });
  qs("picker-q").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); if (state.picker.tab === "web") doWebSearch(); }
  });
  for (const btn of qs("picker-tabs").children) {
    btn.addEventListener("click", () => {
      state.picker.tab = btn.dataset.tab;
      if (state.picker.tab === "web" && state.picker.q) doWebSearch(); else renderPicker();
    });
  }
  qs("btn-new-messages").addEventListener("click", scrollBottom);
  qs("messages").addEventListener("scroll", paintNewMessages);
  qs("side-q").addEventListener("input", (ev) => { state.sideQ = ev.target.value; renderSidebar(); });
  const input = qs("input");
  input.addEventListener("input", () => autosize(input));
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); send(); }
    if (ev.key === "Escape") togglePicker(false);
  });
  qs("btn-send").addEventListener("click", () => {
    if (state.streaming && state.abort) { state.abort.abort(); return; }
    send();
  });
  for (const btn of document.querySelectorAll(".side-nav button[data-panel]")) {
    btn.addEventListener("click", () => {
      // 表情库入口已经挪进设置面板了，这里只剩两个常驻项
      if (btn.dataset.panel === "settings") openSettings(ctx);
      else openAbout(state.boot, ctx);
    });
  }
  qs("btn-new-group").addEventListener("click", newGroupConversation);
  qs("btn-ai-next").addEventListener("click", aiNextTurn);
  qs("btn-ai-loop").addEventListener("click", () => aiAutoLoop(6));
  const sfxBtn = qs("btn-sfx");
  const paintSfx = () => { if (sfxBtn) sfxBtn.textContent = sfxOn() ? "🔊" : "🔇"; };
  paintSfx();
  if (sfxBtn) sfxBtn.addEventListener("click", () => { sfxSet(!sfxOn()); paintSfx(); if (sfxOn()) playReceive(); });
  // 浏览器在用户第一次交互前会挂起 AudioContext，借任意一次手势解锁
  ["pointerdown", "keydown"].forEach((ev) => window.addEventListener(ev, primeSfx, { once: true }));
  qs("btn-theme").addEventListener("click", () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("animechat-theme", next);
    qs("btn-theme").textContent = next === "dark" ? "☀" : "☾";
  });
  qs("btn-collapse").addEventListener("click", () => {
    if (isNarrow()) { hideChat(); return; }   // 窄屏是抽屉，没有「收起」这回事
    const app = document.getElementById("app");
    // 别拿 inline style 猜当前状态：窄屏默认宽度来自媒体查询，inline 是空的，
    // 于是第一次点击会把"74px"再写一遍，按钮看起来是坏的。读实际算出来的列宽。
    const first = parseFloat(String(getComputedStyle(app).gridTemplateColumns).split(" ")[0]) || 302;
    const wide = first <= 150;
    app.style.gridTemplateColumns = (wide ? "302px" : "74px") + " minmax(0, 1fr)";
    app.dataset.wide = wide ? "1" : "0";
    qs("btn-collapse").textContent = wide ? "«" : "»";
  });
  /* 切回标签页 / 从别的 App 回到手机前台：正在看的这个会话到此算读完了。顺手拉一次
     会话列表，人在后台时到货的回复、或另一个标签页里刚落地的那句，红点才会补上或消掉。 */
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    if (state.convId) markRead(state.convId);
    loadConversations();
  });
  qs("modal-backdrop").addEventListener("click", dismissModals);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (!qs("modal-root").hidden) dismissModals();
      else if (!qs("picker").hidden) togglePicker(false);
      else if (isChatOpen()) hideChat();
      const lb = document.querySelector(".lightbox");
      if (lb) lb.remove();
    }
  });
  renderSendButton();
}

boot();
