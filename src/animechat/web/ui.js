/* DOM 小工具。所有来自模型/用户的文字一律走 textContent，不拼 innerHTML，避免注入。 */

export function el(tag, props, kids) {
  const node = document.createElement(tag);
  if (props) {
    for (const key of Object.keys(props)) {
      const val = props[key];
      if (val === null || val === undefined || val === false) continue;
      if (key === "class") node.className = val;
      else if (key === "text") node.textContent = String(val);
      else if (key === "html") node.innerHTML = val; // 只用于我自己写的静态片段
      else if (key === "dataset") Object.assign(node.dataset, val);
      else if (key === "style") node.setAttribute("style", val);
      else if (key.startsWith("on") && typeof val === "function") node.addEventListener(key.slice(2), val);
      else if (val === true) node.setAttribute(key, "");
      else node.setAttribute(key, String(val));
    }
  }
  append(node, kids);
  return node;
}

export function append(node, kids) {
  if (kids === null || kids === undefined || kids === false) return node;
  if (Array.isArray(kids)) { for (const k of kids) append(node, k); return node; }
  node.appendChild(kids instanceof Node ? kids : document.createTextNode(String(kids)));
  return node;
}

export function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); return node; }
export const qs = (id) => document.getElementById(id);

export function toast(message, kind) {
  const root = qs("toast-root");
  const node = el("div", { class: "toast" + (kind === "err" ? " err" : ""), text: String(message) });
  root.appendChild(node);
  setTimeout(() => {
    node.style.transition = "opacity .25s, transform .25s";
    node.style.opacity = "0";
    node.style.transform = "translateY(6px)";
    setTimeout(() => node.remove(), 260);
  }, kind === "err" ? 5200 : 2600);
  return node;
}

let modalCloser = null;
const modalLayers = [];

export function openModal(content, opts) {
  const root = qs("modal-root");
  const box = qs("modal");
  const body = qs("modal-body");
  if (root.hidden) {
    modalLayers.length = 0;            // 新起一个弹窗，清掉上次可能残留的层
  } else {
    // 已经有一层（比如填了一半的设置表单）：先记下来，关掉上层时原样还回去。
    // 否则确认框会把下面的表单一起销毁，用户填的 Key 就没了。
    modalLayers.push({ kids: Array.from(body.childNodes), closer: modalCloser, cls: box.className });
  }
  clear(body);
  body.appendChild(content);
  root.hidden = false;
  box.className = "modal" + (opts && opts.wide ? " wide" : "");
  modalCloser = (opts && opts.onClose) || null;
  return body;
}

export function closeModal() {
  const root = qs("modal-root");
  if (root.hidden) return;
  const box = qs("modal");
  const body = qs("modal-body");
  const dismissed = modalCloser;
  const prev = modalLayers.pop();
  if (prev) {
    clear(body);
    for (const n of prev.kids) body.appendChild(n);   // 节点连着事件监听一起搬回来
    box.className = prev.cls;
    modalCloser = prev.closer;
    if (dismissed) dismissed();
    return;
  }
  root.hidden = true;
  clear(body);
  modalCloser = null;
  if (dismissed) dismissed();
}

/** 点遮罩 / 按 Esc：整条层叠一起关掉，而不是退回上一层。 */
export function dismissModals() {
  const root = qs("modal-root");
  if (root.hidden) return;
  const stack = modalLayers.slice();
  modalLayers.length = 0;
  root.hidden = true;
  clear(qs("modal-body"));
  const closers = [modalCloser].concat(stack.map((l) => l.closer));
  modalCloser = null;
  for (const fn of closers) { if (fn) fn(); }
}

export function confirmDialog(title, message, okText) {
  return new Promise((resolve) => {
    const buttons = el("div", { class: "row-actions" }, [
      el("button", { class: "plain", text: "取消", onclick: () => { closeModal(); resolve(false); } }),
      el("span", { class: "spacer" }),
      el("button", { class: "danger", text: okText || "确定", onclick: () => { closeModal(); resolve(true); } }),
    ]);
    const body = el("div", {}, [el("h2", { text: title }), el("p", { class: "sub", text: message }), buttons]);
    openModal(body);
  });
}

export function titleBar(title, sub, onClose) {
  return el("h2", {}, [
    el("span", { text: title }),
    sub ? el("span", { class: "pill", text: sub }) : null,
    el("button", { class: "x", text: "×", title: "关闭", onclick: onClose || closeModal }),
  ]);
}

export async function api(path, opts) {
  const opt = opts || {};
  const init = { method: opt.method || "GET", headers: {} };
  if (opt.json !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opt.json);
  }
  if (opt.form instanceof FormData) init.body = opt.form;
  if (opt.signal) init.signal = opt.signal;
  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    if (err && err.name === "AbortError") throw err;
    throw new Error("请求失败：" + err.message);
  }
  if (!res.ok) {
    let message = "HTTP " + res.status;
    let payload = null;
    try {
      payload = await res.json();
      const err = payload && payload.error;
      // FastAPI 的 HTTPException 直接把文案放在 detail；OpenAI/网关错误则包在
      // error.detail / error.message 里。两种都接住，否则用户只会看到泛化的 "HTTP 400"。
      message = (err && (err.detail || err.message || (err.error && err.error.message))) ||
                (payload && (payload.detail || payload.message)) || message;
      if (typeof message !== "string") message = JSON.stringify(message);
    } catch (ignored) { /* 非 JSON 错误体，保留状态码 */ }
    const wrapped = new Error(message);
    wrapped.status = res.status;
    wrapped.payload = payload;
    throw wrapped;
  }
  const type = res.headers.get("content-type") || "";
  if (type.indexOf("json") < 0) return res.text();
  return res.json();
}

export function avatarNode(char, cls) {
  const size = cls || "avatar";
  if (char && char.avatar) {
    // 素材是按 max-age=3600 缓存的，换完头像 URL 一个字没变，浏览器会接着给你看旧图。
    // 拿 updated_at 当指纹：改头像必然写卡、必然刷新时间戳，于是必然回源。
    const stamp = (char.avatar.indexOf("?") < 0 && char.updated_at)
      ? "?v=" + Math.round(char.updated_at) : "";
    const img = el("img", { class: size, src: char.avatar + stamp, alt: char.name || "", loading: "lazy" });
    img.addEventListener("error", () => img.replaceWith(letterAvatar(char, size)));
    return img;
  }
  return letterAvatar(char, size);
}

export function letterAvatar(char, cls) {
  const name = (char && char.name) || "角";
  return el("div", { class: (cls || "avatar") + " avatar-fallback", text: name.slice(0, 1) });
}

export function fmtTime(ts) {
  const d = new Date((ts || 0) * 1000);
  const nowD = new Date();
  const hm = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  if (d.toDateString() === nowD.toDateString()) return hm;
  return (d.getMonth() + 1) + "/" + d.getDate() + " " + hm;
}

export function fmtAgo(ts) {
  const diff = Date.now() / 1000 - (ts || 0);
  if (diff < 90) return "刚刚";
  if (diff < 3600) return Math.round(diff / 60) + " 分钟前";
  if (diff < 86400) return Math.round(diff / 3600) + " 小时前";
  if (diff < 86400 * 8) return Math.round(diff / 86400) + " 天前";
  return fmtTime(ts).split(" ")[0];
}

export function lightbox(url, label) {
  const box = el("div", { class: "lightbox", title: "点击关闭" }, [
    el("img", { src: url, alt: label || "" }),
  ]);
  box.addEventListener("click", () => box.remove());
  document.body.appendChild(box);
}

export function spinner(text) {
  return el("div", { class: "picker-loading" }, [
    el("div", { class: "typing" }, [el("i"), el("i"), el("i")]),
    el("div", { text: text || "加载中" }),
  ]);
}

export function field(label, input, tip) {
  return el("div", { class: "field" }, [
    el("label", { text: label }),
    input,
    tip ? el("span", { class: "tip", text: tip }) : null,
  ]);
}

export function textInput(value, attrs) {
  return el("input", Object.assign({ type: "text", value: value === null || value === undefined ? "" : value }, attrs || {}));
}

export function textarea(value, attrs) {
  const node = el("textarea", Object.assign({ rows: 3 }, attrs || {}));
  node.value = value || "";
  return node;
}

export function select(options, value, attrs) {
  const node = el("select", attrs || {});
  for (const opt of options) {
    node.appendChild(el("option", { value: opt[0], text: opt[1], selected: String(opt[0]) === String(value) }));
  }
  node.value = value;
  return node;
}

export function kvTable(rows) {
  const dl = el("dl", { class: "kv" });
  for (const pair of rows) {
    dl.appendChild(el("dt", { text: pair[0] }));
    dl.appendChild(el("dd", { text: pair[1] === null || pair[1] === undefined ? "—" : String(pair[1]) }));
  }
  return dl;
}
