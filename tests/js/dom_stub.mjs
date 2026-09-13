/* 最小 DOM 替身，只为在 node 里真的跑一遍弹窗渲染函数。
 *
 * 为什么需要它：这个仓库的前端没有构建、也没有 JS 测试框架，平时只靠
 * test_web_assets.py 那种「源码里有没有某个字符串」的断言。那种断言挡不住
 * 「函数一进来就抛错」——「新建角色」曾被 tweakBtn.disabled = !char.avatar_source
 * 一行当场 TypeError 干掉（char 是 null），而源码断言全绿，因为那行写得没问题，
 * 是它钉住了一个会崩的写法。注释里那句「单测碰不到，浏览器一点就炸」说的就是这事。
 *
 * 这里补一层够用的替身，让 pytest 能真的调用一次 openCharEditor(null, ctx)。
 * 只实现被用到的那几样，别指望它是个浏览器。
 */

function makeNode(tag) {
  const node = {
    tagName: String(tag).toUpperCase(),
    children: [],
    _attrs: {},
    dataset: {},
    style: { setAttribute(k, v) { this[k] = v; } },
    _listeners: {},
    className: "",
    textContent: "",
    innerHTML: "",
    hidden: false,
    disabled: false,
    value: "",
    checked: false,
    open: false,
    readOnly: false,
    parentNode: null,
  };
  node.appendChild = (child) => { child.parentNode = node; node.children.push(child); return child; };
  node.removeChild = (child) => {
    const i = node.children.indexOf(child);
    if (i >= 0) node.children.splice(i, 1);
    return child;
  };
  node.addEventListener = (type, fn) => { (node._listeners[type] = node._listeners[type] || []).push(fn); };
  /* 真浏览器里 value / type / checked / placeholder 这些属性会「反映」到元素属性上
     （setAttribute("value","x") 之后 input.value 就是 "x"）。ui.js 的 el() 走的正是
     setAttribute 这条路，所以替身不反映的话，任何关于输入框初值的断言都只会看到 ""，
     测了等于没测。只补被用到的这几个，别指望它是个浏览器。 */
  const REFLECT = { value: "value", type: "type", checked: "checked", placeholder: "placeholder" };
  node.setAttribute = (k, v) => {
    node._attrs[k] = String(v);
    const prop = REFLECT[k];
    if (!prop) return;
    if (prop === "checked") node.checked = v !== "false";   // el() 把 true 传成 setAttribute("checked","")
    else node[prop] = String(v);
  };
  node.getAttribute = (k) => (k in node._attrs ? node._attrs[k] : null);
  node.replaceWith = (next) => {
    if (!node.parentNode) return;
    const i = node.parentNode.children.indexOf(node);
    if (i >= 0) node.parentNode.children[i] = next;
    next.parentNode = node.parentNode;
  };
  node.remove = () => { if (node.parentNode) node.parentNode.removeChild(node); };
  node.focus = () => {};
  node.querySelector = () => null;
  node.querySelectorAll = () => [];
  Object.defineProperty(node, "firstChild", { get: () => node.children[0] || null });
  Object.defineProperty(node, "childNodes", { get: () => node.children.slice() });
  node.classList = {
    add: (c) => { const s = node.className.split(/\s+/).filter(Boolean); if (!s.includes(c)) s.push(c); node.className = s.join(" "); },
    remove: (c) => { node.className = node.className.split(/\s+/).filter(Boolean).filter((x) => x !== c).join(" "); },
    contains: (c) => node.className.split(/\s+/).includes(c),
    toggle: (c, on) => {
      if (on === undefined) { if (node.classList.contains(c)) node.classList.remove(c); else node.classList.add(c); }
      else if (on) node.classList.add(c);
      else node.classList.remove(c);
    },
  };
  return node;
}

const byId = {};
for (const id of ["modal-root", "modal", "modal-body", "toast-root", "modal-backdrop"]) {
  byId[id] = makeNode("div");
  byId[id].id = id;
}

globalThis.document = {
  createElement: (tag) => makeNode(tag),
  createTextNode: (t) => ({ nodeType: 3, nodeValue: String(t), textContent: String(t) }),
  getElementById: (id) => byId[id] || null,
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
  visibilityState: "visible",
  body: makeNode("body"),
};
globalThis.window = { addEventListener: () => {}, matchMedia: () => ({ matches: false }) };
/* ui.js 的 append() 用 `kids instanceof Node` 区分元素和文本。node 里没有 DOM 的 Node，
   不补一个就当场 ReferenceError；拿 Object 顶上是因为元素对象命中、字符串不命中，
   正好是 append() 想要的效果。 */
globalThis.Node = Object;
globalThis.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
globalThis.fetch = async () => { throw new Error("渲染期不该发请求"); };

export const modalRoot = byId["modal-root"];
export const modalBody = byId["modal-body"];
export { makeNode };
