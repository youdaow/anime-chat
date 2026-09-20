/* 真的把弹窗渲染函数跑一遍，确认它没在「一进来就抛错」。
 *
 * 用法：node tests/js/char_editor_smoke.mjs
 * 由 tests/test_web_dialogs.py 调起来；node 不在就自动跳过。
 *
 * 为什么值得跑：「新建角色」坏过一次 —— openCharEditor 里那行
 * tweakBtn.disabled = !char.avatar_source 在 char 为 null 时当场 TypeError，
 * 于是整个弹窗根本弹不出来，POST /api/characters 一次都没发出去。
 * 源码断言当时全绿（那行字符串写得没问题），只有真的调用一次才暴露。
 *
 * panels.js 里 `from "/web/ui.js"` 是浏览器路径，node 解析不了，所以把当前源码
 * 拷进临时目录、只改这一个说明符再 import —— 测的永远是仓库里现在这份，不会漂。
 */

import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, "..", "..", "src", "animechat", "web");

await import("./dom_stub.mjs");
const { modalRoot, modalBody, makeNode } = await import("./dom_stub.mjs");

const dir = mkdtempSync(join(tmpdir(), "animechat-smoke-"));
writeFileSync(join(dir, "ui.js"), readFileSync(join(WEB, "ui.js"), "utf8"));
writeFileSync(join(dir, "panels.js"),
  readFileSync(join(WEB, "panels.js"), "utf8").replaceAll('"/web/ui.js"', '"./ui.js"'));
writeFileSync(join(dir, "dom_stub.mjs"), readFileSync(join(HERE, "dom_stub.mjs"), "utf8"));

const { openCharEditor } = await import(pathToFileURL(join(dir, "panels.js")).href);

const CTX = {
  state: { settings: {}, characters: [], stickers: [], emotions: [], conversations: [] },
  api: async () => ({}),
  toast: () => {},
  refreshBootstrap: async () => {},
  selectCharacter: () => {},
  newConversation: () => {},
  openConversation: () => {},
  openStickerLibrary: () => {},
};

function count(node, pred, acc = { n: 0 }) {
  if (!node || typeof node.tagName !== "string") return acc;
  if (pred(node)) acc.n += 1;
  for (const c of node.children || []) count(c, pred, acc);
  return acc;
}
const isBtn = (text) => (n) => n.tagName === "BUTTON" && n.textContent === text;
const reset = () => { modalBody.children.length = 0; modalRoot.hidden = true; };

let failed = false;
function check(label, cond, extra) {
  if (!cond) { failed = true; console.log("FAIL " + label + (extra ? "  " + extra : "")); }
}

/* ------------------------------------------------ 1. 新建：char 是 null */
reset();
let crashed = null;
try {
  openCharEditor(null, CTX);
} catch (err) {
  crashed = (err && err.constructor ? err.constructor.name : "Error") + ": " + (err && err.message);
}
check("openCharEditor(null) 抛错了，「新建角色」弹窗根本打不开", crashed === null, crashed || "");
if (crashed === null) {
  check("新建时弹窗没打开", modalRoot.hidden === false);
  check("新建时该有「创建角色」按钮", count(modalBody, isBtn("创建角色")).n === 1);
  check("新建时不该出现「保存」", count(modalBody, isBtn("保存")).n === 0);
  // 没入库就没有角色 id：导出按钮点了会打 /api/characters/undefined，干脆不摆
  check("新建时不该出现导出按钮",
    count(modalBody, (n) => n.tagName === "BUTTON" && /^导出 /.test(n.textContent || "")).n === 0);
  check("新建时「微调位置」该是灰的",
    count(modalBody, (n) => isBtn("微调位置")(n) && n.disabled === true).n === 1);
}

/* ------------------------------------------------ 2. 编辑：导出按钮要还在 */
reset();
crashed = null;
try {
  openCharEditor({ id: 7, name: "丛雨", avatar_source: "/media/avatars/7-src.png",
    appearance: {}, tags: [], catchphrases: [], likes: [], dislikes: [],
    example_dialogs: [], sticker_prefs: [] }, CTX);
} catch (err) {
  crashed = (err && err.message) || "unknown";
}
check("openCharEditor(已有角色) 抛错", crashed === null, crashed || "");
if (crashed === null) {
  check("编辑时该有「保存」", count(modalBody, isBtn("保存")).n === 1);
  check("编辑时不该出现「创建角色」", count(modalBody, isBtn("创建角色")).n === 0);
  check("编辑时两个导出按钮都要在",
    count(modalBody, (n) => n.tagName === "BUTTON" && /^导出 /.test(n.textContent || "")).n === 2);
  check("留着原图时「微调位置」不该是灰的",
    count(modalBody, (n) => isBtn("微调位置")(n) && n.disabled === true).n === 0);
}

/* ------------------------------------------------ 3. 编辑但没留原图 */
reset();
openCharEditor({ id: 8, name: "Q版角色", avatar_source: null, appearance: {},
  tags: [], catchphrases: [], likes: [], dislikes: [], example_dialogs: [], sticker_prefs: [] }, CTX);
check("没留原图时「微调位置」该是灰的",
  count(modalBody, (n) => isBtn("微调位置")(n) && n.disabled === true).n === 1);

/* ------------------------------------------------ 4. 设置面板：接入方式那段刚改过
   预置平台要把地址填好并锁成只读，「自定义」要放开可填。这段以前只有源码断言，
   真跑一次才知道有没有在渲染期抛错。
   renderSettings 会 await /api/providers（走 ui.js 的 fetch），所以先把 fetch 顶掉。 */
const PROVIDERS = [
  { key: "deepseek", name: "DeepSeek", family: "openai", needs_base_url: true, needs_key: true,
    base_url: "https://api.deepseek.com/v1", hint: "官方地址已预置",
    base_placeholder: "", key_placeholder: "sk-…", model_placeholder: "deepseek-chat" },
  { key: "openai", name: "自定义 / 中转站", family: "openai", needs_base_url: true, needs_key: true,
    base_url: "", hint: "自己填地址", base_placeholder: "https://…", key_placeholder: "", model_placeholder: "" },
  { key: "mock", name: "内置 Mock", family: "mock", needs_base_url: false, needs_key: false,
    base_url: "", hint: "不联网", base_placeholder: "", key_placeholder: "", model_placeholder: "mock" },
];
globalThis.fetch = async (path) => ({
  ok: true,
  status: 200,
  headers: { get: () => "application/json" },
  json: async () => ({ providers: PROVIDERS, settings: {}, models: [], note: "" }),
  text: async () => "",
});

const { renderSettings } = await import(pathToFileURL(join(dir, "panels.js")).href);
/* 设置不再是弹窗，是内嵌在「我」页里的一块表单 —— 渲染目标换成普通容器，
   下面查格子也从 modalBody 换成这个 host。 */
const settingsHost = makeNode("div");
const resetSettings = () => { settingsHost.children.length = 0; };

/* field() 画出来的是 div.field > label + 控件，按 label 文字找格子最可靠。
   接入方式那格是 select，所以三种控件都要认。 */
function fieldInput(root, labelText) {
  let found = null;
  (function walk(node) {
    if (!node || typeof node.tagName !== "string" || found) return;
    if (node.className && String(node.className).split(/\s+/).includes("field")) {
      const lab = (node.children || []).find((c) => c.tagName === "LABEL");
      if (lab && lab.textContent === labelText) {
        found = (node.children || []).find((c) => ["INPUT", "TEXTAREA", "SELECT"].includes(c.tagName));
        return;
      }
    }
    for (const c of node.children || []) walk(c);
  })(root);
  return found;
}

for (const [label, provider, savedBase] of [
  ["预置平台", "deepseek", ""],
  ["自定义", "openai", "http://my-relay:18787/v1"],
  ["内置 Mock", "mock", ""],
]) {
  resetSettings();
  const ctx = { ...CTX, state: { ...CTX.state, settings: {
    llm_provider: provider, llm_base_url: savedBase, llm_model: "m", llm_api_key: "sk-x",
    llm_temperature: 0.9, llm_max_tokens: 800, context_chars: 6000, llm_timeout: 120,
    llm_thinking: "off", sticker_mode: "rich", max_stickers_per_reply: 1,
    sticker_search_provider: "auto", user_name: "我", user_notes: "",
    github_repo: "", github_pack_path: "stickers", github_token: "",
  } } };
  let err = null;
  try {
    await renderSettings(ctx, settingsHost);   // async：先 await /api/providers 再画格子
  } catch (e) {
    err = (e && e.message) || String(e);
  }
  check("设置面板（" + label + "）渲染期抛错", err === null, err || "");
  if (err) continue;
  check("设置面板（" + label + "）没画出东西", settingsHost.children.length > 0);
  const urlBox = fieldInput(settingsHost, "Base URL");
  check("设置面板（" + label + "）找不到 Base URL 那格", !!urlBox);
  if (!urlBox) continue;
  if (label === "预置平台") {
    check("选预置平台没把官方地址填进格子，实际值=" + JSON.stringify(urlBox.value),
      urlBox.value === "https://api.deepseek.com/v1");
    check("预置平台的地址该锁成只读", urlBox.readOnly === true);
  }
  if (label === "自定义") {
    check("自定义没保住用户自己填的地址，实际值=" + JSON.stringify(urlBox.value),
      urlBox.value === "http://my-relay:18787/v1");
    check("自定义那格该能改（readOnly 没被留下）", urlBox.readOnly === false);
  }
  if (label === "内置 Mock") {
    // needs_base_url/needs_key 为 false：那两格该整个收掉
    let row = urlBox;
    for (let i = 0; i < 4 && row; i += 1) { if (row.hidden === true) break; row = row.parentNode; }
    check("内置 Mock 没把 Base URL 格子收掉", !!row && row.hidden === true);
  }
}

/* 在平台和自定义之间来回切一次，不该把用户填的中转站地址弄丢 */
resetSettings();
const switchCtx = { ...CTX, state: { ...CTX.state, settings: {
  llm_provider: "openai", llm_base_url: "http://my-relay:18787/v1", llm_model: "m",
  llm_api_key: "sk-x", llm_temperature: 0.9, llm_max_tokens: 800, context_chars: 6000,
  llm_timeout: 120, llm_thinking: "off", sticker_mode: "rich", max_stickers_per_reply: 1,
  sticker_search_provider: "auto", user_name: "我", user_notes: "",
  github_repo: "", github_pack_path: "stickers", github_token: "",
} } };
await renderSettings(switchCtx, settingsHost);
const access = fieldInput(settingsHost, "接入方式");
const urlBox = fieldInput(settingsHost, "Base URL");
check("找不到「接入方式」下拉", !!access);
if (access && urlBox) {
  const fire = (type) => { for (const fn of access._listeners[type] || []) fn({ target: access }); };
  access.value = "deepseek"; fire("change");
  check("切到平台后没锁地址", urlBox.readOnly === true && urlBox.value === "https://api.deepseek.com/v1");
  access.value = "openai"; fire("change");
  check("切回自定义把中转站地址弄丢了，实际值=" + JSON.stringify(urlBox.value),
    urlBox.value === "http://my-relay:18787/v1");
  check("切回自定义该能改", urlBox.readOnly === false);
}

/* ------------------------------------------------ 5. 飞书那段
   新增的 fieldset 要真的画出来；App Secret 已保存时必须回掩码，且用户留着掩码
   直接点保存不能把真凭据覆盖掉（这个坑以前吃掉过 llm_api_key）。 */
resetSettings();
const fsCtx = { ...CTX, state: { ...CTX.state, characters: [{ id: "hero", name: "亚丝娜", title: "" }], settings: {
  llm_provider: "mock", llm_base_url: "", llm_model: "m", llm_api_key: "", llm_temperature: 0.9,
  llm_max_tokens: 800, context_chars: 6000, llm_timeout: 120, llm_thinking: "off",
  sticker_mode: "rich", max_stickers_per_reply: 1, sticker_search_provider: "auto",
  user_name: "我", user_notes: "", github_repo: "", github_pack_path: "stickers", github_token: "",
  feishu_app_id: "cli_abc", feishu_app_secret: "sec_…****wxyz", feishu_character: "hero",
  feishu_stickers: false, configured_secrets: { feishu_app_secret: true },
} } };
await renderSettings(fsCtx, settingsHost);
const appIdBox = fieldInput(settingsHost, "App ID");
const secretBox = fieldInput(settingsHost, "App Secret");
const charBox = fieldInput(settingsHost, "新会话默认角色");
check("找不到「App ID」格子", !!appIdBox);
check("找不到「App Secret」格子", !!secretBox);
check("找不到「新会话默认角色」下拉", !!charBox);
if (appIdBox) {
  check("App ID 没原样回显（不是密钥，显示成空的会让人以为没保存），实际值=" + appIdBox.value,
    appIdBox.value === "cli_abc");
}
if (secretBox) {
  check("App Secret 该是密码框", secretBox.type === "password");
  check("App Secret 没回显掩码，实际值=" + JSON.stringify(secretBox.value),
    secretBox.value.indexOf("…") >= 0);
}
if (charBox) {
  const opts = (charBox.children || []).map((o) => o.value);
  check("默认角色下拉里没有已建的角色", opts.includes("hero"), opts.join(","));
  check("默认角色下拉没留「自动」这一项", opts.includes(""));
  check("默认角色没选中保存过的那个", String(charBox.value) === "hero");
}
/* check-field 的结构是 label > input + span + span；替身的 textContent 不像真 DOM
   那样汇总子孙，所以得去翻兄弟节点的文本。 */
const stickerBox = (function find(node) {
  if (!node || typeof node.tagName !== "string") return null;
  if (node.tagName === "INPUT" && node.type === "checkbox") {
    const sibs = (node.parentNode && node.parentNode.children) || [];
    if (sibs.some((c) => /表情包图/.test(c.textContent || ""))) return node;
  }
  for (const c of node.children || []) { const hit = find(c); if (hit) return hit; }
  return null;
}(settingsHost));
check("找不到「回复带表情包图」开关", !!stickerBox);
if (stickerBox) check("表情开关没按保存值画（存了 false 却显示开着）", stickerBox.checked === false);

console.log(failed ? "SMOKE: FAIL" : "SMOKE: PASS");
process.exit(failed ? 1 : 0);
