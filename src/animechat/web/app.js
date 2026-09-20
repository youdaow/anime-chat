/* 主应用：侧栏、聊天流、表情面板。所有状态都在这一个模块里，panels.js 通过 ctx 回调它。 */

import {
  el, append, clear, qs, toast, api, avatarNode, fmtTime, fmtAgo, lightbox,
  confirmDialog, openModal, closeModal, dismissModals, titleBar, spinner,
} from "/web/ui.js";
import { openCharEditor, renderSettings, openStickerLibrary, openContext, openAbout, openAICreate } from "/web/panels.js";
import { playSend, playReceive, prime as primeSfx, isOn as sfxOn, setOn as sfxSet } from "/web/sfx.js";

const state = {
  page: "chat",       // 当前页面：chat / contacts / me 三个栏根页 + thread / card 两个推入页
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
  // 当前会话成员的角色卡快照（含已隐藏的），由 openConversation 写入
  convCharsLocal: null,
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

/* ------------------------------------------------------- 页面 / 键盘适配 */

/* 窄屏仍然要用它做两件事：输入框提示语（触屏没有 Enter）、表情面板的展开方式。
   布局本身不再分叉——三个选项卡在桌面和手机上是一样的。一律走 matchMedia，
   别读 offsetWidth：抽屉收起时侧栏仍在 DOM 里，量出来的宽度会骗人。 */
const narrowMq = window.matchMedia ? window.matchMedia("(max-width: 900px)") : null;
function isNarrow() { return !!(narrowMq && narrowMq.matches); }

/* 三个页面：对话 / 联系人 / 我。哪一页在屏幕上由 data-tab + hidden 决定，
   样式只认这两样，所以这里只管把它们改对。 */
const TABS = ["chat", "contacts", "me"];
const TAB_STORE_KEY = "animechat.tab";
/* 推入页：不属于任何选项卡，从哪个 tab 进来的，选项卡就还亮哪一个。
   微信就这样——聊天全屏时底部 tab 根本看不见，← 回列表后 tab 才回来。 */
const PUSH_ROOT = { thread: "chat", card: "contacts" };

function currentTab() { return PUSH_ROOT[state.page] || (TABS.indexOf(state.page) >= 0 ? state.page : "chat"); }

/* 转场：整块画面一起走。进 → 新页从右边推进来，旧页那一栏（页面 + 底部选项卡）往左
   让开并压暗；退 → 反过来。选项卡必须跟着它所属的那一栏动：以前只有页面在动、选项卡
   钉在原地，返回时那 95px 像另一张没参与拼接的图。
   只给「推入 / 退回」播，切选项卡不播 —— 微信也没给底部切换加位移，加了像翻页。
   时长和 style.css 里那个 animation-duration 对齐：JS 的 SLIDE_MS 是 animationend
   不来时的兜底藏页时机，比动画短就会「还在播，旧页已经被藏了」。 */
const SLIDE_MS = 300;
let slideSeq = 0;

function slidePages(prevEl, nextEl, dir) {
  const mine = ++slideSeq;
  const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (!prevEl || !nextEl || prevEl === nextEl || !dir || reduce) return;
  prevEl.hidden = false;        // showPage 刚把它藏了，动画这几帧得让它留在屏上
  // 推入页是全屏的，本来就把选项卡盖住；会动的那个选项卡永远属于「栏根」那一侧
  const rootCls = dir === "push" ? "push-out" : "back-in";
  const pushCls = dir === "push" ? "push-in" : "back-out";
  const moving = [[prevEl, dir === "push" ? rootCls : pushCls],
                  [nextEl, dir === "push" ? pushCls : rootCls]];
  const bar = qs("tabbar");
  if (bar) moving.push([bar, rootCls]);
  for (const [el, cls] of moving) el.classList.add(cls);
  const settle = () => {
    if (mine !== slideSeq) return;              // 中途又切了页：让最后那次说了算
    prevEl.hidden = true;
    for (const [el, cls] of moving) el.classList.remove(cls);
  };
  nextEl.addEventListener("animationend", settle, { once: true });
  // 页面在后台时动画根本不跑，animationend 不会来；不兜底旧页就永久糊在屏幕上
  setTimeout(settle, SLIDE_MS + 200);
}

function showPage(name) {
  const from = state.page;
  state.page = name;
  const app = document.getElementById("app");
  if (app) app.dataset.page = name;
  const tab = currentTab();
  for (const btn of qs("tabbar").children) {
    const on = btn.dataset.tab === tab;
    btn.classList.toggle("on", on);
    btn.setAttribute("aria-current", on ? "page" : "false");
  }
  for (const page of document.querySelectorAll(".page")) page.hidden = page.id !== "page-" + name;
  slidePages(document.getElementById("page-" + from), document.getElementById("page-" + name),
             PUSH_ROOT[name] ? "push" : (PUSH_ROOT[from] ? "back" : ""));
  paintTabBadge();
  if (name === "thread") paintNewMessages();
  if (name === "me") mountMePage();
  if (name === "chat") renderChatList();
}

function switchTab(name) {
  const tab = TABS.indexOf(name) >= 0 ? name : "chat";
  try { window.localStorage.setItem(TAB_STORE_KEY, tab); } catch (err) { /* 无痕模式：记不住就算了 */ }
  showPage(tab);   // 选项卡永远回到那一栏的根页：在聊天里点「对话」就是回列表
}

/* 「对话」上的红点 = 所有未读之和。列表行里每人还有一个自己的红点。 */
function paintTabBadge() {
  const badge = qs("tab-unread");
  if (!badge) return;
  let n = 0;
  for (const conv of state.conversations) n += convUnread(conv);
  badge.hidden = n <= 0;
  badge.textContent = fmtUnread(n);
}

/* 绑选项卡 + 恢复上次那一栏。刷新前停在「我」页，刷新后还在「我」页——
   不然每次调完设置刷新都被扔回列表。推入页不恢复：刷新就回栏根，符合微信的直觉。 */
function initTabs() {
  let saved = "chat";
  try { saved = window.localStorage.getItem(TAB_STORE_KEY) || "chat"; } catch (err) { /* 读不到就用默认栏 */ }
  showPage(TABS.indexOf(saved) >= 0 ? saved : "chat");
  for (const btn of qs("tabbar").children) {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  }
  if (narrowMq) {
    // 转屏 / 拖窗口过 900px：只有输入框提示语要跟着换
    const onBreak = () => paintInputHint();
    if (narrowMq.addEventListener) narrowMq.addEventListener("change", onBreak);
    else if (narrowMq.addListener) narrowMq.addListener(onBreak);
  }
}

/* 「我」页 = 我自己的人设 + 全部设置，内嵌在 #settings-host 里。
   每次切到这一页重画一次：别的标签页刚改过的值、或保存后回回来的掩码 Key，
   都不该留在旧格子里（以前是弹窗，每次打开本来就会重画，行为保持一致）。 */
async function mountMePage() {
  const host = qs("settings-host");
  if (!host) return;
  if (!state.settings) {
    clear(host).appendChild(spinner("还在连本机服务…"));
    return;
  }
  try {
    await renderSettings(ctx, host);
  } catch (err) {
    clear(host).appendChild(el("p", { class: "tip", text: "设置没加载出来：" + err.message }));
  }
}

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
  initTabs();
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
    toast("还没有角色，到「联系人」页「＋新建」或导入一张角色卡", "err");
    switchTab("contacts");
    return;
  }
  // 只把上次那个人、最近那条会话装载好；停在用户上次看的那一页（initTabs 定的），
  // 不该一开页面就把他从「我」页拽回聊天。
  selectCharacter(target.id, { openLatest: true });
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
  renderChatList();
  // 汇总红点也要在这里刷一次：initTabs() 跑的时候还没有任何会话数据，
  // 只靠切页时那一次画，首屏会出现「行上有 2、选项卡上写 0」。
  paintTabBadge();
  renderContacts();
  renderHead();
  return data;
}

async function loadConversations() {
  const data = await api("/api/conversations");
  state.conversations = data.conversations || [];
  /* 服务端算出来的未读里，也会把「你正开着的那个会话」里刚落地的那条回复算成未读
     （POST /read 可能还没跑到）。正看着的不该亮红点，所以这里再本地归零一次。 */
  if (state.convId && document.visibilityState === "visible") clearUnreadLocal(state.convId);
  renderChatList();
  renderContacts();
  paintTabBadge();
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

/* 每个角色一行的数据：最后一句、什么时候、几条没看。
   算的是 state.conversations（发完消息会重拉），不是角色视图 —— 那份要整页
   bootstrap 才刷新，聊完一句回到列表还停在旧预览上，看着像没收到回复。
   群聊摊到每个成员头上，跟后端 _char_views 同一套口径。 */
function chatRows() {
  const seen = {};
  const unread = {};
  for (const conv of state.conversations) {
    for (const pid of convIds(conv)) {
      unread[pid] = (unread[pid] || 0) + convUnread(conv);
      if (conv.updated_at && (!seen[pid] || conv.updated_at > seen[pid].at)) {
        // 预览是消息原文，角色回的是多条时里面带真实换行；列表一行只有一行高，
        // 换行不压掉会把整行撑歪。
        seen[pid] = { at: conv.updated_at, text: String(conv.preview || "").replace(/\s+/g, " ").trim() };
      }
    }
  }
  return { seen, unread };
}

/* 「对话」= 微信式会话列表：一行一个人，副标题是最后一句，右上角是时间。
   排序按最近聊过的在前；一句都没聊过的排在后面，按名字排（也是微信的直觉）。 */
function renderChatList() {
  const box = qs("chat-list");
  if (!box) return;
  const q = sideQuery();
  const { seen, unread } = chatRows();
  const lastOf = (char) => seen[char.id] || { at: char.last_at || 0, text: char.last_preview || "" };
  // 只列聊过的人。没聊过的角色在这里出现，这一页就又变成了一份通讯录 ——
  // 两页给同一堆名字看，分工等于没有。要找人开聊去「联系人」。
  const chars = state.characters
    .filter((c) => lastOf(c).at > 0)
    .filter((c) => matchesQuery(q, [c.name, c.title, (c.tags || []).join(" ")]))
    .slice()
    .sort((a, b) => lastOf(b).at - lastOf(a).at || String(a.name).localeCompare(String(b.name), "zh"));
  clear(box);
  if (!chars.length) {
    box.appendChild(el("div", { class: "pill", text: q ? "没有匹配「" + q + "」的聊天" : "还没有聊天，到「联系人」点一个人开始" }));
    return;
  }
  for (const char of chars) {
    const n = unread[char.id] || 0;
    const last = lastOf(char);
    const chatted = !!last.at;
    box.appendChild(el("div", {
      class: "row" + (n ? " has-unread" : ""),
      onclick: () => openThreadFor(char.id),
      title: char.name + " — " + (char.title || ""),
    }, [
      avatarNode(char),
      el("div", { class: "row-main" }, [
        el("b", { text: char.name }),
        el("span", { text: last.text || (chatted ? "[图片]" : "还没有聊过") }),
      ]),
      el("div", { class: "row-side" }, [
        el("time", { text: chatted ? fmtAgo(last.at) : "" }),
        n ? el("span", {
          class: "unread-count", text: fmtUnread(n),
          title: n + " 条未读回复", "aria-label": n + " 条未读回复",
        }) : null,
      ]),
    ]));
  }
}

/* 「联系人」= 通讯录：只有头像和名字，点一个人先看名片。
   列表行不放 ⋯ —— 管理动作全在名片上，一行两个入口反而谁都找不到。 */
function renderContacts() {
  const q = sideQuery();
  const chars = state.characters.filter((c) => matchesQuery(q, [c.name, c.title, c.short_desc, (c.tags || []).join(" ")]));
  const charList = clear(qs("char-list"));
  if (!chars.length) {
    charList.appendChild(el("div", { class: "pill", text: q ? "没有匹配「" + q + "」的角色" : "还没有角色" }));
  }
  for (const char of chars) {
    charList.appendChild(el("div", {
      class: "row row-person" + (char.id === state.charId ? " on" : ""),
      onclick: () => openCard(char.id),
      title: char.name + " — " + (char.title || ""),
    }, [
      avatarNode(char),
      el("div", { class: "row-main" }, [el("b", { text: char.name })]),
    ]));
  }
}

/* 点人 → 进聊天。群聊里也有他，但绝不能让群聊抢走单聊：拉过群之后点人只会
   反复打开那个群，1 对 1 就再也进不去了。 */
async function openThreadFor(cid) {
  state.charId = cid;
  const mine = state.conversations.filter((c) => convIds(c).indexOf(cid) >= 0);
  const solo = mine.filter((c) => convIds(c).length === 1);
  if (solo.length) {
    await openConversation(solo[0].id);
    // openConversation 失败只 toast、不改 state.convId —— 那就别推一个空聊天上去
    if (state.convId === solo[0].id) showPage("thread");
    return;
  }
  const before = state.convId;
  await newConversation(cid);
  if (state.convId && state.convId !== before) showPage("thread");
}

function openCard(cid) {
  const char = charById(cid);
  if (!char) { toast("找不到这个角色", "err"); return; }
  renderCard(char);
  showPage("card");
}

/* 名片：微信点联系人看到的那一页。人设摊开给人看，底部一个绿色「发消息」。 */
function renderCard(char) {
  const box = clear(qs("card-body"));
  const line = (label, value) => (value ? el("div", { class: "card-line" }, [
    el("span", { class: "card-label", text: label }),
    el("span", { class: "card-value", text: String(value) }),
  ]) : null);
  box.appendChild(el("div", { class: "card-head" }, [
    avatarNode(char, "avatar card-avatar"),
    el("b", { text: char.name }),
    el("span", { class: "card-title", text: char.title || "" }),
  ]));
  box.appendChild(el("div", { class: "card-group" }, [
    line("性格", char.personality),
    line("说话方式", char.speaking_style),
    line("口头禅", (char.catchphrases || []).join("、")),
    line("喜欢", (char.likes || []).join("、")),
    line("讨厌", (char.dislikes || []).join("、")),
    line("底线", char.boundaries),
    line("当前场景", char.scenario),
    el("div", { class: "card-line" }, [
      el("span", { class: "card-label", text: "语气示范" }),
      el("span", { class: "card-value", text: (char.example_dialogs || []).length + " 组" }),
    ]),
    line("用图频率", ({ off: "不用表情包", light: "情绪到位才发", rich: "几乎每条都配一张" })[char.sticker_style] || ""),
  ]));
  box.appendChild(el("div", { class: "card-actions" }, [
    el("button", { class: "primary card-send", text: "发消息", onclick: () => openThreadFor(char.id) }),
    el("button", { class: "plain", text: "编辑人设 / 外观", onclick: () => openCharEditor(char, ctx) }),
    el("button", { class: "plain", text: "更多操作（复制 / 导出 / 隐藏 / 删除）", onclick: () => openCharMenu(char) }),
  ]));
}

function charName(id) {
  const char = charById(id);
  return char ? char.name : id;
}
function currentChar() {
  return state.characters.find((c) => c.id === state.charId) || null;
}

function charById(cid) {
  // 先查公开列表：刚改过名字/头像要用最新的，成员表只是打开这条会话那一刻的快照。
  const fresh = state.characters.find((c) => c.id === cid);
  if (fresh) return fresh;
  // 会话里可能有个已经隐藏的角色，它不在公开列表里；不查成员表的话
  // 它的气泡就没名字、头像退成「角」字。
  if (state.convCharsLocal && cid) {
    const fromConv = state.convCharsLocal.find((c) => c.id === cid);
    if (fromConv) return fromConv;
  }
  return null;
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

function setConvCharsLocal(list) {
  state.convCharsLocal = Array.isArray(list) ? list.slice() : null;
}

// 这条会话是群聊吗：参与者不止一个
function isGroup() { return convChars().length > 1; }

// 这条消息是谁发的：群聊看 speaker，单聊就是会话主角
function speakerOf(msg) {
  if (msg.role === "user") return null;
  if (msg.speaker) return charById(msg.speaker);
  return currentChar();
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
  if (!options.openLatest) {
    renderContacts();
    renderHead();
    return;
  }
  /* 点一个人 = 想跟这个人单独聊。群聊里也有他，但绝不能让群聊抢走单聊：
     否则拉过群之后点角色只会反复打开那个群，1 对 1 再也进不去了。
     切到对话页由点进来的那一方负责——boot 恢复上次会话时不该顺手改页。 */
  const mine = state.conversations.filter((c) => convIds(c).indexOf(cid) >= 0);
  const solo = mine.filter((c) => convIds(c).length === 1);
  if (solo.length) openConversation(solo[0].id);
  else newConversation(cid);   // 一句都没聊过，先给他建一条
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
    // 成员卡片随会话一起回来：隐藏掉的角色不在公开角色列表里，
    // 没有这份快照，它说过的话就画不出名字和头像。
    setConvCharsLocal(data.participant_characters);
    // 群聊的 character_id 只是第一个成员；如果我是从某个角色的视角点进来的，
    // 就别把选中的人改成别人，否则侧栏高亮和"＋新会话"都会跳到群主身上。
    if (convIds(data.conversation).indexOf(state.charId) < 0) {
      state.charId = data.conversation.character_id;
    }
    state.speaker = null;
    /* 会话画到屏幕上才算读到。标签页在后台时不清水位：切到后台那一刻到货的回复，
       正是红点要提示的东西，回到前台由 visibilitychange 补这次清零。 */
    if (document.visibilityState === "visible") markRead(id);
    renderContacts();
    renderHead();
    renderThread();
    paintNewMessages();
    qs("composer").hidden = false;
    /* 只有真的停在对话页才抢焦点：人在「联系人」页翻列表时，后台把上一条会话
       装载好就够了，突然弹软键盘会把列表整个遮住。窄屏同理不抢。 */
    if (!isNarrow() && state.page === "thread") qs("input").focus({ preventScroll: true });
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
    notes.push(el("button", { class: "plain", text: "去设置", onclick: () => switchTab("me") }));
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
      el("h3", { text: hasConv ? "说点什么吧" : "还没开始聊" }),
      el("p", { text: hasConv
        ? "角色会按自己的性格回你，并在合适的时候甩表情包。"
        : "回「对话」列表点一个人就开一轮；要先加角色就去「联系人」页。" }),
      el("button", {
        class: "primary",
        text: hasConv ? "写第一条消息" : "回对话列表挑人",
        onclick: () => { if (hasConv) { qs("input").focus({ preventScroll: true }); } else { switchTab("chat"); } },
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
    meta.appendChild(el("button", { text: "打开设置", onclick: () => switchTab("me") }));
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

/* 聊天区在不在屏幕上：以前问的是「窄屏抽屉开没开」，现在就是「在哪一页」。
   不在对话页时到货的消息不滚动、不消红点，只攒着，切回来再画。 */
function chatVisible() { return state.page === "thread"; }

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
  let streamStatusEl = null;
  const setStatus = (status) => {
    if (!streamStatusEl) return;
    if (status === "receiving") {
      streamStatusEl.textContent = "正在输入…";
      streamStatusEl.hidden = false;
      return;
    }
    streamStatusEl.textContent = status === "done" || status === "failed" || status === "timeout" ? "" : status;
    streamStatusEl.hidden = !status || status === "done" || status === "failed" || status === "timeout";
  };

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
  streamStatusEl = el("div", { class: "stream-status", text: "正在输入…" });
  const statusSlot = qs("live-status");
  clear(statusSlot);
  statusSlot.hidden = false;
  statusSlot.appendChild(streamStatusEl);

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
    }, live, state.abort.signal, setStatus);
  } catch (err) {
    if (err.name !== "AbortError") {
      live.failed = true;
      live.fail(err.message);
      live.finish(false);
    }
    // 主动停止不是失败：已收到的正文/表情仍是有效回复，要固化为“已中断”消息。
    else if (!live.failed) {
      live.stopped = true;
      setStatus && setStatus("已停止");
      live.finish(true);
    }
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
    setStatus && setStatus(live.failed ? "failed" : (live.stopped ? "stopped" : "done"));
    if (streamStatusEl && streamStatusEl.parentNode) streamStatusEl.remove();
    statusSlot.hidden = true;
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

// 建群聊的入口随侧栏会话列表一起去掉了：群聊的代码还在（后端 participants、
// 轮转、飞书那边的模拟群聊都照旧跑），只是网页上不再开这个口子。
// 已有的群聊记录留在库里，飞书那边照常能聊。

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
      meta.appendChild(el("button", { text: "打开设置", onclick: () => switchTab("me") }));
    },
  };
  return live;
}

async function streamChat(payload, live, signal, setStatus) {
  let watchdog = null;
  let progressTimer = null;
  let watchdogFired = false;
  let timedOut = false;
  const timeoutMs = Math.max(10, Number((state.settings && state.settings.llm_timeout) || 120) || 120) * 1000;
  const startTime = Date.now();
  const clearTimers = () => {
    if (watchdog) { clearTimeout(watchdog); watchdog = null; }
    if (progressTimer) { clearTimeout(progressTimer); progressTimer = null; }
  };
  const clearWatchdogOnly = () => { if (watchdog) { clearTimeout(watchdog); watchdog = null; } };
  const armWatchdog = () => {
    clearWatchdogOnly();
    watchdog = setTimeout(() => {
      watchdogFired = true;
      if (!signal || signal.aborted) return;
      timedOut = true;
      if (progressTimer) { clearTimeout(progressTimer); progressTimer = null; }
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      if (signal && !signal.aborted) signal.abort();
      const msg = "等待回复超时（" + elapsed + "s）。请检查网络，或到设置里把超时调大。";
      live.failed = true;
      live.errorText = msg;
      live.fail("等待超时（" + elapsed + "s）", msg);
      live.finish(false);
      setStatus && setStatus("timeout");
    }, timeoutMs + 2000);
  };
  const scheduleStatus = () => {
    if (watchdogFired || !setStatus) return;
    if (progressTimer) clearTimeout(progressTimer);
    progressTimer = setTimeout(() => {
      const t = ((Date.now() - startTime) / 1000).toFixed(1);
      setStatus(t >= 30 ? "等待模型返回中（已等待 " + t + "s）…" : t + "s");
      scheduleStatus();
    }, 1000);
  };
  armWatchdog();
  scheduleStatus();
  try {
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
      clearTimers();
      setStatus && setStatus("failed");
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
        handleEvent(rawEvent, live, setStatus);
        cut = buf.indexOf("\n\n");
      }
    }
    if (buf.trim()) handleEvent(buf, live, setStatus);
    clearTimers();
    setStatus && setStatus(live.failed ? "failed" : "done");
    live.finish(false);
  } catch (err) {
    clearTimers();
    // 超时分支已经生成失败消息；这里不能再把同一个 AbortError 当成用户主动停止。
    if (err.name === "AbortError" && timedOut) return;
    throw err;
  }
}

function handleEvent(raw, live, setStatus) {
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
    setStatus && setStatus("receiving");
    live.text(data.t || "");
  } else if (name === "reasoning") {
    setStatus && setStatus("receiving");
    live.think(data.t || "");
  } else if (name === "sticker") {
    setStatus && setStatus("receiving");
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
    setStatus && setStatus("error");
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
      el("div", { text: p.note || "默认走 Bing 图片（免 Key，不用注册），它没结果时自动退到 DuckDuckGo。" }),
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
  // 不再是「新建一轮对话」：会话列表去掉之后，一个角色只有聊着的那一条，
  // 再建一条等于造一条再也点不回去的记录。要重新开始去聊天头部的「清空记录」。
  mk("聊天", () => openThreadFor(char.id), "primary");
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
  mk("清空记录，从头再聊", "删除", () => deleteCurrentConversation(), "danger");
  openModal(el("div", {}, [titleBar("会话操作", charName(state.charId) || ""), rows]));
}

/* 会话列表没了，「置顶 / 重命名 / 打开某一条」这些操作就没有了对象：
   一个角色对应一条能点开的会话，这里只留「从头再来」这一个动作。 */
async function deleteCurrentConversation() {
  if (!state.convId) return;
  const conv = state.conversation || { id: state.convId };
  if (!(await confirmDialog("清空和 " + (charName(state.charId) || "TA") + " 的记录？",
        "所有消息记录一起删掉，不可恢复；角色人设不受影响。", "删除"))) return;
  await api("/api/conversations/" + conv.id, { method: "DELETE" });
  await loadConversations();
  state.convId = null;
  const mine = state.conversations.filter((c) => c.character_id === state.charId);
  if (mine.length) openConversation(mine[0].id); else newConversation(state.charId);
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
  // 聊天头部点头像 = 看这个人的名片（微信聊天头部点名字也是进资料页）
  qs("head-avatar").addEventListener("click", () => { if (currentChar()) openCard(currentChar().id); });
  // 两个推入页的 ←：聊天回「对话」列表，名片回「联系人」
  qs("btn-thread-back").addEventListener("click", () => showPage("chat"));
  qs("btn-card-back").addEventListener("click", () => showPage("contacts"));
  // 列表空着的时候「＋找人聊」把人带去通讯录（微信的 ✜ 也是这个作用）
  qs("btn-find-chat").addEventListener("click", () => switchTab("contacts"));
  qs("btn-context").addEventListener("click", () => { if (state.convId) openContext(state.convId, ctx); });
  qs("btn-del-conv").addEventListener("click", () => deleteCurrentConversation());
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
  qs("side-q").addEventListener("input", (ev) => { state.sideQ = ev.target.value; renderContacts(); });
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
  // 设置整块内嵌在「我」页里（见 mountMePage），这里只剩一个「关于」入口
  qs("btn-about").addEventListener("click", () => openAbout(state.boot, ctx));
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
      else if (PUSH_ROOT[state.page]) showPage(PUSH_ROOT[state.page]);   // 聊天 / 名片里：←  equivalent
      else if (state.page !== "chat") switchTab("chat");                 // 栏根页：回「对话」列表
      const lb = document.querySelector(".lightbox");
      if (lb) lb.remove();
    }
  });
  renderSendButton();
}

boot();
