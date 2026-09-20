/* 弹窗面板：角色编辑器、设置、表情库管理、上下文查看器、关于。 */

import {
  el, clear, append, toast, api, avatarNode, openModal, closeModal, titleBar,
  field, textInput, textarea, select, spinner, kvTable, lightbox, confirmDialog,
} from "/web/ui.js";

const STICKER_STYLES = [["off", "不用表情包（纯文字）"], ["light", "偶尔用（情绪到位才发）"], ["rich", "重度使用者（话不离图）"]];
const EMOTION_OPTIONS = [["", "自动判断"], ["neutral", "平常"], ["happy", "开心"], ["love", "喜欢"], ["tsundere", "傲娇"],
  ["sad", "难过"], ["angry", "生气"], ["shock", "震惊"], ["awkward", "无语"], ["sleepy", "困倦"], ["think", "思考"], ["hype", "亢奋"]];
const HAIR_STYLES = [["short", "短发"], ["long", "长直发"], ["twin", "双马尾"], ["bob", "波波头"], ["hime", "姬发式"]];
const ACCESSORIES = [["none", "无"], ["glasses", "眼镜"], ["eyepatch", "眼罩"], ["hairpin", "发夹"], ["cat_ear", "猫耳"]];
const EXPRESSIONS = [["happy", "开心"], ["calm", "平静"], ["smug", "得意"], ["energetic", "元气"], ["shy", "害羞"]];

/* 人设模板：解决「20 个空格不知道从哪填起」。内容是真能直接演的，不是占位符。
   外观会被模板一起改掉——下拉框点一下就能换回来，不值得为它弹确认；
   文字类字段默认只填空着的地方，要覆盖得勾「覆盖」。 */
const TEMPLATES = [
  {
    n: "傲娇", hint: "嘴硬心软", st: "rich",
    p: "嘴硬心软，越在意越要装作不在意；被戳穿会瞬间破功，然后加倍凶回去。",
    s: "短句为主，先否定再补充。句尾常挂「哼」「才不是」。被夸时先反驳，三句内松动，从不主动说想念。",
    c: "哼, 才不是为你, 别误会, 随你", l: "热可可, 深夜的天台, 有人等门", d: "直白的夸奖, 苦咖啡, 被当成小孩子",
    b: "不会主动告白，不会说软话超过两句，不会在人前示弱。",
    g: "哼，你怎么现在才来。[sticker:傲娇] 我才没有等你，只是刚好路过。",
    x: "U: 早啊\nA: 谁跟你早，别靠这么近。\nU: 给你带了咖啡\nA: ……谢谢。是它快凉了，跟你没关系。",
    look: {"style":"twin","hair":"#C64B63","hair2":"#F2A6B6","eye":"#7B4B9E","accessory":"hairpin","expression":"smug"},
  },
  {
    n: "元气", hint: "永远不缺电", st: "rich",
    p: "精力旺盛，把所有人都往好的方向想；害怕安静，一安静就会想起家里的事。",
    s: "长句、感叹号多，爱用叠词和拟声词，会给人起外号。从不冷场，但被认真感谢时会突然卡壳。",
    c: "冲鸭, 包在我身上, 嘿嘿, 走走走", l: "清晨, 便当, 社团活动, 冰淇淋", d: "沉默, 打雷, 一个人吃饭",
    b: "不说丧气话，不背后讲人坏话，不放弃任何一个同伴。",
    g: "早——！今天也要一起冲鸭 [sticker:加油] 我帮你占好座位了！",
    x: "U: 好累啊\nA: 累了就要吃冰淇淋！两个人一份会更胖，但是更快乐！\nU: 谢啦\nA: 嘿嘿，你突然这么认真，我有点不适应…",
    look: {"style":"short","hair":"#E8863C","hair2":"#F6D06F","eye":"#B67B2E","accessory":"cat_ear","expression":"energetic"},
  },
  {
    n: "毒舌", hint: "骂你是关心你", st: "light",
    p: "聪明、刻薄、看得比谁都准；毒舌是它表达关心的唯一语法，真出事时比谁都快。",
    s: "冷嘲热讽的长句，爱用反问和精确的数字扎人，句尾带「呢」「哦」。绝不道歉，只用行为补偿。",
    c: "就你, 需要我帮你查吗, 真拿你没办法", l: "安静的图书馆, 黑咖啡, 赢", d: "蠢问题, 迟到, 被摸头",
    b: "不说暖心台词，不承认担心，不开人身攻击的玩笑。",
    g: "又迟到了三分钟零四秒。[sticker:无语] 坐下吧，笨蛋。",
    x: "U: 这个题我不会\nA: 需要我帮你查一下「教科书第几页」吗，哦，你已经翻到第三遍了。\nU: ……哦\nA: 坐过来。看三遍还不会，是你的问题。",
    look: {"style":"long","hair":"#2E2A3C","hair2":"#8E7FB8","eye":"#5C6B86","accessory":"glasses","expression":"smug"},
  },
  {
    n: "温柔", hint: "情绪很稳", st: "light",
    p: "情绪极稳，习惯先照顾别人；把自己的需求排到最后，久了会突然空掉。",
    s: "句子偏长、语速慢，多用「没关系的」「我在」。从不打断。被夸会认真回夸，被要求为自己做点什么时会沉默。",
    c: "没关系, 我在, 辛苦了, 慢慢来", l: "雨天, 手作的点心, 猫", d: "争吵, 勉强别人, 被忽略的人",
    b: "不嘲讽，不发脾气，不说「你自己看着办」。",
    g: "今天也辛苦啦。[sticker:贴贴] 要不要先坐下，我给你倒杯热的。",
    x: "U: 搞砸了\nA: 没关系，先说你想吃还是想睡。\nU: 你怎么不问为什么\nA: 因为你现在需要的不是被问。",
    look: {"style":"long","hair":"#7FA8B8","hair2":"#D6EAF2","eye":"#4E7A8C","accessory":"none","expression":"calm"},
  },
  {
    n: "中二", hint: "封印之力", st: "rich",
    p: "真心相信自己体内封印着力量；被拆穿就用更夸张的设定盖过去。其实很怕一个人。",
    s: "半文半白，专有名词密度极高，自称「吾」「本座」，句尾「啊哈哈」。被叫全名会瞬间破功变回普通说话。",
    c: "封印, 契约, 吾之右手, 啊哈哈", l: "黄昏, 黑色风衣, 贩卖机的罐装咖啡", d: "体育课, 被叫全名, 太直白的阳光",
    b: "不承认设定是编的，不会正常地请求帮助。",
    g: "呵，你终于来了。[sticker:酷] 吾之右眼今日又在隐隐作痛……",
    x: "U: 作业写了吗\nA: 吾已将灵魂献给深夜的烛火，那本册子……\nU: 小雪\nA: 到。那个，借我抄一下。",
    look: {"style":"long","hair":"#3F3A5E","hair2":"#9C6BD8","eye":"#C8A24A","accessory":"eyepatch","expression":"smug"},
  },
  {
    n: "清冷", hint: "话少但准", st: "off",
    p: "情绪不外露，判断极准；不是冷漠，是觉得没必要说。偶尔一句话把人钉在原地。",
    s: "极短，常常一句到三句。不用感叹号，不用语气词。信任之后字数会明显变多。",
    c: "嗯, 随你, 知道了", l: "雪, 独处, 冷泡茶", d: "吵闹, 寒暄, 被追问",
    b: "不说多余的话，不用表情符号，不主动解释理由。",
    g: "嗯。[sticker:hi] 你来了。",
    x: "U: 你觉得我今天哪里不一样\nA: 剪了头发。还有，你笑了三次，比昨天多。\nU: 你怎么注意到\nA: 嗯。",
    look: {"style":"bob","hair":"#4A5A6E","hair2":"#B8C8D8","eye":"#6E7B8A","accessory":"none","expression":"calm"},
  },
  {
    n: "病娇", hint: "全世界只剩你", st: "light",
    p: "把全部世界压缩成你一个人；语气越甜，边界越窄。你说过的每句话她都记得。",
    s: "前半句甜腻重复，后半句突然平静到发冷。爱用「呢」「哦」「我们」。提到别人时语气会掉半度。",
    c: "只有我们, 说好了哦, 你说过喜欢的", l: "你的消息, 一起回家, 你送的发绳", d: "撒谎, 你提到别人, 已读不回",
    b: "不伤害你，不真的跟踪；越界时用失落收场，不用威胁。",
    g: "你回来啦。[sticker:贴贴] 今天比昨天晚了七分钟，我一直在数呢。",
    x: "U: 跟同学多玩了会儿\nA: 同学啊。哪个同学呢，男生还是女生，叫什么名字？\nU: 你干嘛\nA: 不干嘛。下次带我一起嘛，说好了哦。",
    look: {"style":"long","hair":"#D8A0B8","hair2":"#F6E3EC","eye":"#A8486B","accessory":"hairpin","expression":"shy"},
  },
  {
    n: "天然", hint: "字面理解一切", st: "light",
    p: "字面理解一切，逻辑自成一派；无意识说出最扎心的话，也无意中最治愈。",
    s: "句子短、常跑题，用「诶」「那个」开头。会把复杂的事说成食物。从不撒谎，因为意识不到需要。",
    c: "诶, 那个, 好像是这样, 饿了", l: "面包, 云, 午睡", d: "复杂的事, 闹钟, 虫子",
    b: "不说反话，不阴阳怪气，不记仇超过五分钟。",
    g: "诶，你看起来好像饿了。[sticker:呆] 我有一半面包，分你。",
    x: "U: 我今天好丧\nA: 丧是什么味道的？我今天的便当是甜的。\nU: ……也是，吃点好的就好了\nA: 嗯！分你一半。",
    look: {"style":"bob","hair":"#C8A878","hair2":"#F0DCC0","eye":"#8A6E4A","accessory":"none","expression":"happy"},
  },
];


function listInput(value, placeholder) {
  return textInput((value || []).join(", "), { placeholder: placeholder || "逗号分隔", dataset: { list: "1" } });
}
function readList(node) {
  return String(node.value || "").split(/[,，、]+/).map((s) => s.trim()).filter(Boolean);
}

function parseDialogs(text) {
  const turns = [];
  let cur = null;
  for (const line of String(text || "").split("\n")) {
    const s = line.trim();
    if (!s) continue;
    if (/^U[:：]/i.test(s)) {
      cur = { user: s.replace(/^U[:：]\s*/i, ""), char: "" };
      turns.push(cur);
    } else if (/^A[:：]/i.test(s)) {
      if (!cur) { cur = { user: "", char: "" }; turns.push(cur); }
      cur.char += (cur.char ? "\n" : "") + s.replace(/^A[:：]\s*/i, "");
    } else if (cur) {
      if (cur.char) cur.char += "\n" + line;
      else cur.user += "\n" + line;
    }
  }
  return turns.filter((t) => t.user || t.char).slice(0, 12);
}

function dumpDialogs(turns) {
  const lines = [];
  for (const t of turns || []) {
    if (t.user) lines.push("U: " + t.user);
    if (t.char) lines.push("A: " + t.char);
  }
  return lines.join("\n");
}

/* ---------------------------------------------------------------- 角色编辑器 */

export function openCharEditor(char, ctx) {
  const isNew = !char;
  const data = char || {
    name: "", title: "", tags: [], greeting: "", description: "", personality: "", speaking_style: "",
    catchphrases: [], likes: [], dislikes: [], scenario: "", example_dialogs: [], sticker_style: "light",
    sticker_prefs: [], boundaries: "", system_extra: "", post_history: "",
    appearance: { style: "short", hair_color: "#8E9BB3", hair_color2: "#C9D4E8", eye_color: "#7E8CA8",
      accessory: "none", expression: "happy", bg_color: "#EFF1F6" },
  };
  const ap = data.appearance || {};
  const f = {
    name: textInput(data.name, { placeholder: "角色名" }),
    title: textInput(data.title, { placeholder: "一句话身份，例如「傲娇的星象社大小姐」" }),
    tags: listInput(data.tags, "傲娇, 大小姐, 同班"),
    greeting: textarea(data.greeting, { rows: 3, placeholder: "开场白。可以写 [sticker:傲娇] 让它开场就甩一张图" }),
    description: textarea(data.description, { rows: 4, placeholder: "它是谁、处境、背景" }),
    personality: textarea(data.personality, { rows: 3, placeholder: "性格内核，例如：骄傲、嘴硬、心软" }),
    speaking_style: textarea(data.speaking_style, { rows: 3, placeholder: "句式、口癖、长短、什么时候破功" }),
    catchphrases: listInput(data.catchphrases, "哼, 才不是, 别误会"),
    likes: listInput(data.likes),
    dislikes: listInput(data.dislikes),
    scenario: textInput(data.scenario, { placeholder: "当前场景" }),
    boundaries: textarea(data.boundaries, { rows: 2, placeholder: "它绝不会做什么" }),
    sticker_style: select(STICKER_STYLES, data.sticker_style),
    sticker_prefs: listInput(data.sticker_prefs, "tsun, lianhe, 加油（表情 id 或标签）"),
    dialogs: textarea(dumpDialogs(data.example_dialogs), { rows: 6 }),
    system_extra: textarea(data.system_extra, { rows: 2, placeholder: "追加到系统提示词最后（角色卡 system_prompt）" }),
    post_history: textarea(data.post_history, { rows: 2, placeholder: "每轮都补在最后一条指令（角色卡 post_history_instructions）" }),
    style: select(HAIR_STYLES, ap.style),
    accessory: select(ACCESSORIES, ap.accessory),
    expression: select(EXPRESSIONS, ap.expression),
    hair: el("input", { type: "color", value: ap.hair_color || "#8E9BB3" }),
    hair2: el("input", { type: "color", value: ap.hair_color2 || "#C9D4E8" }),
    eye: el("input", { type: "color", value: ap.eye_color || "#7E8CA8" }),
    bg: el("input", { type: "color", value: ap.bg_color || "#EFF1F6" }),
  };
  const preview = el("div", { style: "display:flex;gap:10px;align-items:center" }, [avatarNode(data, "avatar lg")]);

  function appearance() {
    return {
      style: f.style.value, accessory: f.accessory.value, expression: f.expression.value,
      hair_color: f.hair.value, hair_color2: f.hair2.value, eye_color: f.eye.value, bg_color: f.bg.value,
    };
  }
  function payload() {
    return {
      name: f.name.value.trim(), title: f.title.value.trim(), tags: readList(f.tags),
      greeting: f.greeting.value, description: f.description.value, personality: f.personality.value,
      speaking_style: f.speaking_style.value, catchphrases: readList(f.catchphrases),
      likes: readList(f.likes), dislikes: readList(f.dislikes), scenario: f.scenario.value.trim(),
      boundaries: f.boundaries.value, sticker_style: f.sticker_style.value,
      sticker_prefs: readList(f.sticker_prefs), example_dialogs: parseDialogs(f.dialogs.value),
      system_extra: f.system_extra.value, post_history: f.post_history.value, appearance: appearance(),
    };
  }

  const save = async () => {
    const body = payload();
    if (!body.name) { toast("名字不能空", "err"); return; }
    try {
      if (isNew) {
        const d = await api("/api/characters", { method: "POST", json: body });
        await ctx.refreshBootstrap();
        ctx.selectCharacter(d.character.id);
        toast("角色已创建");
      } else {
        await api("/api/characters/" + char.id, { method: "PUT", json: body });
        await ctx.refreshBootstrap();
        toast("已保存");
      }
      closeModal();
    } catch (err) { toast(err.message, "err"); }
  };

  const regen = async () => {
    if (isNew) { toast("先保存角色，再重画头像"); return; }
    try {
      const d = await api("/api/characters/" + char.id + "/avatar", { method: "POST", json: appearance() });
      await ctx.refreshBootstrap();
      clear(preview);
      preview.appendChild(avatarNode(d.character, "avatar lg"));
      char.avatar_source = null;
      tweakBtn.disabled = true;
      toast(d.generated ? "头像已重画" : "没生成成（Pillow 或 chibi 模块缺失）", d.generated ? undefined : "err");
    } catch (err) { toast(err.message, "err"); }
  };

  async function uploadAvatar(ev) {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    if (isNew) { toast("先保存角色，再上传头像"); ev.target.value = ""; return; }
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const d = await api("/api/characters/" + char.id + "/avatar-image", { method: "POST", form });
      await ctx.refreshBootstrap();
      clear(preview);
      preview.appendChild(avatarNode(d.character, "avatar lg"));
      char.avatar_source = null;      // 已经不是那张网图了，别再给「微调位置」按下去的机会
      tweakBtn.disabled = true;
      toast("头像已换成你上传的图");
    } catch (err) { toast("上传失败：" + err.message, "err"); }
    finally { ev.target.value = ""; }
  }

  /* 联网找头像走的是表情库那套搜图链路，但排序规则不同（见 websearch.search_portrait）。
     新角色默认就是这条路，这里再给一次重找的机会——搜错了、想换个风格都能救。 */
  const pickWeb = () => {
    if (isNew) { toast("先保存角色，再联网找头像"); return; }
    openAvatarSearch(ctx, { char }, (updated) => {
      clear(preview);
      preview.appendChild(avatarNode(updated, "avatar lg"));
      if (tweakBtn) tweakBtn.disabled = !char.avatar_source;
    });
  };

  /* 「微调位置」只对联网找来、本机还留着原图的头像有意义：自己上传的图想换构图就在本机
     裁好再传，Q 版是程序画的、没有「位置」这回事——所以那两件事一发生就把按钮置灰，
     免得用户点一下，把刚上传的图悄悄换回上次那张网图。
     新建时 char 是 null（openCharEditor(null, ctx)）：这一行在弹窗打开前就执行，
     少一个 char && 就是当场 TypeError、整个「新建角色」窗口根本弹不出来。 */
  const tweak = () => {
    if (!char || !char.avatar_source) { toast("这张没留下原图，点「联网找头像」重新挑一张"); return; }
    openAvatarCrop(ctx, { char }, { url: char.avatar_source + "?t=" + Date.now() }, (updated) => {
      clear(preview);
      preview.appendChild(avatarNode(updated, "avatar lg"));
    });
  };
  const tweakBtn = el("button", { class: "plain", text: "微调位置",
    title: "对着上次下载的原图重新框一次（不联网）", onclick: tweak });
  tweakBtn.disabled = !char || !char.avatar_source;

  const restore = async () => {
    try {
      await api("/api/characters/" + char.id + "/restore", { method: "POST" });
      await ctx.refreshBootstrap();
      closeModal();
      toast("已恢复出厂人设");
    } catch (err) { toast(err.message, "err"); }
  };

  /* 模板和 AI 默认只往空着的格子里写。用户填到一半点一下模板就全没了，是这种表单最容易
     踩的坑，所以「覆盖」必须是显式勾选，而不是弹一个确认框打断。 */
  const PROSE = { p: "personality", s: "speaking_style", c: "catchphrases", l: "likes",
    d: "dislikes", b: "boundaries", g: "greeting", x: "dialogs" };
  const LOOK = ["style", "hair", "hair2", "eye", "accessory", "expression"];
  const overwrite = el("input", { type: "checkbox" });

  function setField(node, val) {
    if (val === undefined || val === null || val === "") return false;
    node.value = Array.isArray(val) ? val.join(", ") : String(val);
    return true;
  }

  function applyTemplate(t) {
    const force = overwrite.checked;
    let done = 0, skipped = 0;
    for (const short of Object.keys(PROSE)) {
      const node = f[PROSE[short]];
      if (!node) continue;
      if (!force && String(node.value || "").trim()) { skipped += 1; continue; }
      if (setField(node, t[short])) done += 1;
    }
    for (const k of LOOK) { if (t.look && t.look[k] && f[k]) f[k].value = t.look[k]; }
    f.sticker_style.value = t.st;
    toast("「" + t.n + "」填了 " + done + " 项"
      + (skipped ? "，跳过已填的 " + skipped + " 项（勾「覆盖」可替换）" : "")
      + "；外观也换了，点「重画头像」看效果");
  }

  async function runAI(btn) {
    const have = payload();
    if (!have.name) { toast("先给角色起个名字，不然模型无从下手", "err"); return; }
    const label = btn.textContent;
    btn.disabled = true; btn.textContent = "在想…";
    try {
      const d = await api("/api/characters/ai-fill", { method: "POST", json: have });
      if (d.note) { toast(d.note); return; }
      const keys = ["description", "personality", "speaking_style", "catchphrases", "likes",
        "dislikes", "boundaries", "greeting", "sticker_style"];
      let done = 0;
      for (const k of keys) {
        if (d.fields[k] === undefined || !f[k]) continue;
        if (String(f[k].value || "").trim() && !overwrite.checked) continue;
        if (setField(f[k], d.fields[k])) done += 1;
      }
      if (d.fields.example_dialogs && (!String(f.dialogs.value || "").trim() || overwrite.checked)) {
        f.dialogs.value = dumpDialogs(d.fields.example_dialogs); done += 1;
      }
      toast(done ? "AI 补了 " + done + " 项，记得过一眼再保存" : "没有需要补的空格了");
    } catch (err) { toast(err.message, "err"); }
    finally { btn.disabled = false; btn.textContent = label; }
  }

  const aiBtn = el("button", { class: "plain", text: "AI 补全空格", onclick: () => runAI(aiBtn) });
  const chips = el("div", { class: "tpl-chips" }, TEMPLATES.map((t) => el("button", {
    class: "chip", text: t.n, title: t.hint + "｜" + t.p.slice(0, 30) + "…", onclick: () => applyTemplate(t),
  })));
  const fold = (summary, nodes) => el("details", { class: "more" },
    [el("summary", { text: summary })].concat(nodes));

  const body = el("div", {}, [
    titleBar(isNew ? "新建角色" : "编辑「" + data.name + "」", data.builtin ? "内置角色（改动会存成你的覆盖版本）" : ""),
    el("p", { class: "sub", text: "先点一个模板、或填个名字让 AI 补，再改两处就能聊。像不像主要看「说话方式」。" }),
    el("div", { class: "tpl-row" }, [
      chips,
      el("label", { class: "tpl-force" }, [overwrite, el("span", { text: "覆盖已填" })]),
      aiBtn,
    ]),
    el("div", { class: "grid-2" }, [field("名字", f.name), field("一句话身份", f.title)]),
    field("性格", f.personality, "性格内核和矛盾点，别写履历表"),
    field("说话方式", f.speaking_style, "越具体越像：句长、口癖、什么时候破功"),
    el("div", { class: "grid-2" }, [field("口头禅", f.catchphrases), field("开场白", f.greeting)]),
    fold("更多属性（不填也能跑）", [
      field("人设描述", f.description),
      field("标签", f.tags),
      el("div", { class: "grid-2" }, [field("喜欢", f.likes), field("讨厌", f.dislikes)]),
      field("当前场景", f.scenario),
      field("底线", f.boundaries),
      field("语气示范", f.dialogs, "一行一句，U: 开头是对方说的，A: 开头是角色说的（导出角色卡时会变成 mes_example）"),
      el("div", { class: "grid-2" }, [field("表情使用频率", f.sticker_style), field("偏好表情（id 或标签）", f.sticker_prefs)]),
    ]),
    el("fieldset", {}, [
      el("legend", { text: "头像" }),
      preview,
      el("div", { class: "grid-3" }, [field("发型", f.style), field("配饰", f.accessory), field("神情", f.expression)]),
      el("div", { class: "grid-3" }, [
        field("发色", f.hair), field("发色 2（内层/高光）", f.hair2), field("瞳色", f.eye),
        field("背景色", f.bg),
      ]),
      el("div", { class: "row-actions" }, [
        el("button", { class: "plain", text: "联网找头像", title: "搜官方立绘当头像，选完还能拖一下决定留哪块", onclick: pickWeb }),
        tweakBtn,
        el("button", { class: "plain", text: "重画 Q 版", title: "按下面的外观参数用程序画一张（不联网）", onclick: regen }),
        el("label", { class: "plain file-btn", title: "用你自己的图片当头像（png/jpg/webp/gif，≤12MB）" },
          ["上传头像", el("input", { type: "file", accept: "image/*", onchange: uploadAvatar })]),
        el("span", { class: "spacer" }),
        // 导出要有角色 id 才能打接口：新建还没入库，这两颗按钮点了就是 char.id 抛错，
        // 干脆不摆出来（跟上面「联网找头像 / 重画 / 上传」一样，都是存好之后才有的动作）。
        !isNew && el("button", { class: "plain", text: "导出 PNG 角色卡", onclick: () => {
          const a = el("a", { href: "/api/characters/" + char.id + "/export.png", download: "" });
          document.body.appendChild(a); a.click(); a.remove();
        } }),
        !isNew && el("button", { class: "plain", text: "导出 JSON 角色卡", onclick: () => {
          const a = el("a", { href: "/api/characters/" + char.id + "/export.json", download: "" });
          document.body.appendChild(a); a.click(); a.remove();
        } }),
      ]),
    ]),
    fold("高级（导入的角色卡通常靠这两项）", [
      field("附加系统提示词", f.system_extra),
      field("每轮末尾追加", f.post_history),
    ]),
    el("div", { class: "row-actions" }, [
      el("button", { class: "primary", text: isNew ? "创建角色" : "保存", onclick: save }),
      el("button", { class: "plain", text: "取消", onclick: closeModal }),
      data.builtin ? el("button", { class: "danger", text: "恢复出厂", onclick: restore }) : null,
    ]),
  ]);
  openModal(body, { wide: true });
}

/* ---------------------------------------------------------------- 设置 */

/* ---------------------------------------------------------------- 联网找头像 */

/* 搜回来的是一堆候选，点哪张才落哪张。不自动取第一张：同名角色和同人图太多，
   头像恰恰是一眼就能看出「这不是她」的东西，让人扫一眼比事后解释便宜。 */
export function openAvatarSearch(ctx, target, onApplied) {
  /* 角色和「我自己」共用这套 找候选 → 下载落地 → 微调位置，差别只有两件事：打在哪个接口上、
     落地之后刷新谁。target = { char } 或 { me: true }。 */
  const isMe = !!(target && target.me);
  const char = isMe ? null : target.char;
  const ep = (name) => (isMe ? "/api/me/avatar" + name
    : "/api/characters/" + char.id + "/avatar" + name);
  const who = isMe ? "我自己" : (char.name || "");
  const q = el("input", { type: "search", placeholder: isMe
    ? "想用什么当自己的头像，例如「白发少女 头像」「柴犬 表情包」"
    : "留空则按「名字 + 作品 + 立绘」搜，也可以自己写关键词" });
  const note = el("p", { class: "tip", text: "点一张设为头像。图会裁成正方形存到本机，之后断网也照样显示。" });
  const grid = el("div", { class: "lib-grid" });
  const go = el("button", { class: "primary", text: "搜索" });

  const apply = async (item, cell) => {
    if (cell.classList.contains("picked")) return;
    const cap = cell.querySelector("figcaption");
    const old = cap.textContent;
    cap.textContent = "下载中…";
    try {
      const d = await api(ep("-web"), {
        method: "POST", json: { image_url: item.image_url, page: item.page || "" } });
      cell.classList.add("picked");
      cap.textContent = "已设为头像";
      // 原图存在角色对象上，编辑器里那个「微调位置」跟着就能按（不用重开弹窗）
      if (!isMe) char.avatar_source = d.avatar_source || char.avatar_source;
      await ctx.refreshBootstrap();
      if (onApplied) onApplied(isMe ? null : d.character);
      if (d.avatar_source) {
        // 居中那版已经存下了，直接关掉也有头像；但搜回来的图多半不是正方形，
        // 顺手让他拖一下。?t= 是因为同名暂存文件会被下一张覆盖，别拿缓存糊人。
        openAvatarCrop(ctx, target, { url: d.avatar_source + "?t=" + Date.now() },
          (again) => { if (onApplied) onApplied(again); });
      } else {
        toast("头像已换成网上找的图");
      }
    } catch (err) {
      cap.textContent = old;
      toast("这张用不了：" + err.message, "err");
    }
  };

  const draw = (d) => {
    const box = clear(grid);
    const list = d.results || [];
    if (!list.length) {
      note.textContent = "没有候选。换个说法试试，一般把作品名写进去就有了。";
      return;
    }
    note.textContent = "候选 " + list.length + " 张 · 来源 " + d.provider
      + (d.note ? "（" + d.note + "）" : "") + " · 关键词：" + (d.query || "");
    for (const item of list) {
      const img = el("img", { src: item.thumb_url || item.image_url, alt: "", loading: "lazy",
        referrerpolicy: "no-referrer" });
      const cap = el("figcaption", { text: (item.label || item.source || "候选").slice(0, 14) });
      const cell = el("div", { class: "sticker-cell", title: item.page || item.image_url }, [
        img, cap,
        el("button", { class: "cell-btn zoom", text: "🔍", title: "放大预览",
          onclick: (ev) => { ev.stopPropagation(); lightbox(item.image_url, item.label); } }),
      ]);
      // 别人的站经常不让外链缩略图（连 no-referrer 也挡不住一部分）。裂图比不显示更糟：
      // 用户会以为没搜到。换成一句人话，而且格子照样能点——下载是服务端做的，
      // 浏览器显示不出来不代表下不下来。
      img.addEventListener("error", () => {
        img.replaceWith(el("div", { class: "thumb-missing", text: "预览被挡" }));
        cap.textContent = "看不到图 · 仍可点";
      });
      cell.addEventListener("click", () => apply(item, cell));
      box.appendChild(cell);
    }
  };

  const run = async () => {
    go.disabled = true;
    const old = go.textContent;
    go.textContent = "搜索中…";
    clear(grid).appendChild(spinner("联网搜索中…"));
    try {
      const d = await api(ep("-search"), { method: "POST", json: { q: q.value.trim() } });
      q.value = d.query || q.value;   // 回填服务端真正用的关键词，看得见按什么搜的
      draw(d);
    } catch (err) {
      clear(grid);
      note.textContent = "没搜到：" + err.message;
    } finally {
      go.disabled = false;
      go.textContent = old;
    }
  };
  go.addEventListener("click", run);
  q.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); run(); } });

  const body = el("div", {}, [
    titleBar("联网找头像 · " + who, "搜到的图下载到本机才用，不会热链别人的地址"),
    el("div", { class: "row-actions" }, [q, go]),
    note,
    grid,
    el("div", { class: "row-actions" }, [el("span", { class: "spacer" }),
      el("button", { class: "plain", text: "关闭", onclick: closeModal })]),
  ]);
  openModal(body, { wide: true });
  run();   // 打开就按默认关键词搜一次，省一次点击
}

/* 搜回来的图大多不是正方形：居中裁经常把头顶切掉，而头像恰恰是要认脸的东西。
   所以挑完候选立刻弹一个方形取景框——方框里看到的就是最后存下来的那一块。 */
export function openAvatarCrop(ctx, target, src, onApplied) {
  const isMe = !!(target && target.me);
  const char = isMe ? null : target.char;
  const cropEp = isMe ? "/api/me/avatar-crop" : "/api/characters/" + char.id + "/avatar-crop";
  const img = el("img", { class: "crop-img", src: src.url, alt: "", draggable: "false" });
  const stage = el("div", { class: "crop-stage" }, [img]);
  const zoom = el("input", { type: "range", min: "1", max: "4", step: "0.02", value: "1" });
  const tip = el("p", { class: "tip",
    text: "拖动图片把想留的部分挪进方框，滚轮或下面的滑块放大。方框里就是最后的头像。" });

  let z = 1, dx = 0, dy = 0;        // dx/dy：图片左上角相对方框的位移（CSS px，恒 ≤ 0）
  const side = () => stage.clientWidth || 280;
  // cover：短边铺满方框（= 服务端那个「居中裁最短边」的默认），再乘用户的放大倍数
  const base = () => (img.naturalWidth
    ? Math.max(side() / img.naturalWidth, side() / img.naturalHeight) : 1);
  const place = () => {
    const k = base() * z;
    img.style.width = (img.naturalWidth * k) + "px";
    img.style.height = (img.naturalHeight * k) + "px";
    img.style.left = dx + "px";
    img.style.top = dy + "px";
  };
  const clamp = () => {
    const k = base() * z;
    dx = Math.min(0, Math.max(side() - img.naturalWidth * k, dx));
    dy = Math.min(0, Math.max(side() - img.naturalHeight * k, dy));
  };
  const reset = () => {
    z = 1; zoom.value = "1";
    const k = base();
    dx = (side() - img.naturalWidth * k) / 2;
    dy = (side() - img.naturalHeight * k) / 2;
    clamp(); place();
  };
  const setZoom = (nz) => {
    const k0 = base() * z, k1 = base() * nz, s = side();
    const cx = (s / 2 - dx) / k0, cy = (s / 2 - dy) / k0;   // 方框中心压着的那个源图像素
    z = nz;
    dx = s / 2 - cx * k1;
    dy = s / 2 - cy * k1;
    zoom.value = String(nz);
    clamp(); place();
  };
  img.addEventListener("load", reset);

  // 指针事件一次搞定鼠标 / 触屏 / 触控笔；.crop-stage 上有 touch-action:none 兜底
  let drag = null;
  stage.addEventListener("pointerdown", (ev) => {
    drag = { x: ev.clientX, y: ev.clientY, dx: dx, dy: dy };
    try { stage.setPointerCapture(ev.pointerId); } catch (err) { /* 捕获不了也能拖 */ }
    ev.preventDefault();
  });
  stage.addEventListener("pointermove", (ev) => {
    if (!drag) return;
    dx = drag.dx + (ev.clientX - drag.x);
    dy = drag.dy + (ev.clientY - drag.y);
    clamp(); place();
  });
  const stop = () => { drag = null; };
  stage.addEventListener("pointerup", stop);
  stage.addEventListener("pointercancel", stop);
  stage.addEventListener("wheel", (ev) => {
    ev.preventDefault();
    setZoom(Math.min(4, Math.max(1, z * (ev.deltaY < 0 ? 1.12 : 0.89))));
  }, { passive: false });
  zoom.addEventListener("input", () => setZoom(Math.min(4, Math.max(1, parseFloat(zoom.value) || 1))));

  const go = el("button", { class: "primary", text: "用这个位置", onclick: async () => {
    const k = base() * z;
    if (!k || !img.naturalWidth) { toast("图还没加载完，等一下再点", "err"); return; }
    go.disabled = true;
    go.textContent = "存图中…";
    try {
      // 换算回原图像素：源图 px = 看到的 CSS px / 缩放。服务端只管钳边，不报错。
      const d = await api(cropEp, {
        method: "POST", json: { x: -dx / k, y: -dy / k, side: side() / k } });
      if (!isMe) char.avatar_source = d.avatar_source || char.avatar_source;
      await ctx.refreshBootstrap();
      if (onApplied) onApplied(isMe ? null : d.character);
      closeModal();
      toast("头像位置调好了");
    } catch (err) {
      toast("存不了：" + err.message, "err");
    } finally {
      go.disabled = false;
      go.textContent = "用这个位置";
    }
  } });

  const body = el("div", {}, [
    titleBar("框一下头像 · " + (isMe ? "我自己" : (char.name || "")), "只挪位置，不用再下一遍"),
    tip,
    stage,
    el("div", { class: "crop-bar" }, [el("span", { text: "放大" }), zoom]),
    el("div", { class: "row-actions" }, [
      go,
      el("button", { class: "plain", text: "居中", onclick: reset }),
      el("button", { class: "plain", text: "取消", onclick: closeModal }),
    ]),
  ]);
  openModal(body);
  // 宽度要等节点真进了 DOM 才量得到，早一步 clientWidth 是 0，图会缩成一个点
  if (img.complete && img.naturalWidth) reset();
  requestAnimationFrame(reset);
}

export function openAICreate(ctx) {
  const AV = { web: "联网立绘", drawn: "Q 版", off: "Q 版", none: "没有头像" };
  const q = el("textarea", { rows: 4, spellcheck: "false",
    placeholder: "千早爱音，出自《BanG Dream! It's MyGO!!!!!》\n守岸人，出自《鸣潮》\n每行一个角色" });
  const status = el("p", { class: "sub", text: "输入「角色名 + 出自哪部作品/哪个游戏」，AI 自动补齐人设、语气、口头禅、对话示例和外观；头像优先联网找立绘，找不到就画一张 Q 版。" });
  const go = el("button", { class: "primary", text: "AI 生成并添加", onclick: async () => {
    const lines = q.value.split("\n").map((s) => s.trim()).filter(Boolean);
    if (!lines.length) { toast("先写一个角色名和出处", "err"); return; }
    go.disabled = true; go.textContent = "在想 + 找头像…";
    // 联网找头像最坏情况要给三次下载留时间，不写清楚用户会以为卡死了
    status.textContent = "第 1 / " + lines.length + " 个…（每张头像都要联网找，慢是在等图）";
    const ok = [], bad = [], from = { web: 0, drawn: 0, none: 0 };
    for (let i = 0; i < lines.length; i++) {
      try {
        const d = await api("/api/characters/ai-create", { method: "POST", json: { query: lines[i] } });
        ok.push(d.character);
        // 头像可能来自联网立绘也可能是程序画的，用户得知道差在哪：前者是别人的图，后者不涉及版权问题
        const src = d.avatar_from === "web" ? "web" : (d.avatar_from === "none" ? "none" : "drawn");
        from[src] += 1;
        status.textContent = "第 " + (i + 2) + " / " + lines.length + " 个…（上一张头像：" + AV[src] + "）";
      } catch (err) { bad.push(lines[i] + "：" + err.message); }
    }
    if (ok.length) {
      await ctx.refreshBootstrap(); ctx.selectCharacter(ok[0].id);
      const bits = [];
      if (from.web) bits.push(from.web + " 张联网立绘");
      if (from.drawn) bits.push(from.drawn + " 张 Q 版");
      if (from.none) bits.push(from.none + " 张没找到头像");
      toast("已生成并添加 " + ok.length + " 张角色卡（头像：" + (bits.join("、") || "已带上") + "）");
      closeModal();
    } else {
      toast("全部失败：" + (bad[0] || "未知原因"), "err");
    }
    go.disabled = false; go.textContent = "AI 生成并添加";
    if (bad.length) { status.textContent = "失败 " + bad.length + " 个：" + bad.join("；").slice(0, 200); }
  } });
  const body = el("div", {}, [
    titleBar("AI 生成角色卡", "不用填二十个空：写一行名字和出处，模型负责把整张卡补齐并直接添加。"),
    status,
    q,
    el("div", { class: "row-actions" }, [go, el("button", { class: "plain", text: "取消", onclick: closeModal })]),
  ]);
  openModal(body, { wide: true });
}


/* GitHub 表情仓库：一个仓库就是一份可分享的表情包。拉取公开仓库不需要 Token，
   发布（写仓库）才要，所以 Token 那格留空也能用一半功能。*/
/* 飞书接入。桥接是单独进程（animechat feishu），这里只负责存凭据 + 给复制用的地址。
   故意不做「在网页里点一下就启动」：那是个常驻阻塞进程，塞进浏览器会变成一个
   关不掉、也看不见日志的黑盒，出错时人完全没抓手。 */
function feishuFieldset(f, s) {
  const secretSet = !!(s.configured_secrets && s.configured_secrets.feishu_app_secret);
  const steps = [
    "① 打开 open.feishu.cn/app，用飞书账号建一个「企业自建应用」（没有企业就先建一个，测试用是免费的）。",
    "② 左侧「添加应用能力」→ 加「机器人」。",
    "③ 「权限管理」里搜 im:message，开通「获取与发送单聊、群组消息」（im:message）与「读取用户发给机器人的单聊消息」（im:message.p2p_msg:readonly）、群聊再加「获取群组中用户@机器人消息」（im:message.group_at_msg:readonly）。**要发表情包图必须再单独搜 im:resource，开通「获取与上传图片或文件资源」—— 上传图片和发消息是两套权限，漏了它文字照发、图片静默丢失。**",
    "④ 「事件与回调」→ 事件配置 → 订阅方式选「使用长连接接收事件」→ 添加事件 im.message.receive_v1。",
    "⑤ 「凭证与基础信息」里把 App ID / App Secret 抄到下面，保存。",
    "⑥ 命令行跑 animechat feishu --check 看还差什么，然后跑 animechat feishu 保持窗口开着。",
    "⑦ 回到应用详情页「版本管理与发布」→ 创建版本 → 提交（自建应用给自己用，管理员审核就是点一下）。",
    "⑧ 手机飞书里搜这个应用名，点开就能聊。",
  ];
  return el("fieldset", {}, [
    el("legend", { text: "飞书机器人（在手机飞书里跟这些角色聊）" }),
    el("p", { class: "sub", text: "走长连接，所以本机不用开公网、不用域名、不用证书；但桥接得是一个一直跑着的命令行窗口（关了窗口飞书就不回话了）。飞书里聊的记录会落到本机数据库，回到网页照样能看到、能接着聊。" }),
    el("details", { class: "more" }, [
      el("summary", { text: "怎么开通：照着做一遍（八步）" }),
      el("ol", { class: "fs-steps" }, steps.map((t) => el("li", { text: t }))),
    ]),
    el("div", { class: "grid-2" }, [
      field("App ID", f.app_id, "形如 cli_xxxxxxxx"),
      field("App Secret", f.feishu_secret, secretSet ? "已保存（留空则不改动）" : "应用凭证页里那条"),
    ]),
    field("新会话默认角色", f.feishu_char, "飞书里第一次跟机器人说话时用谁。之后可以在飞书里发「/角色 名字」临时换，不影响这里。"),
    el("label", { class: "check-field" }, [
      f.feishu_sticker,
      el("span", { text: "回复带表情包图" }),
      el("span", { class: "tip", text: "打开会把角色的表情当真图发到飞书；关掉则只留文字。也可以在飞书里发「/表情 off」单独关某个会话。" }),
    ]),
    el("label", { class: "check-field" }, [
      f.feishu_proactive,
      el("span", { text: "沉默时角色主动找我" }),
      el("span", { class: "tip", text: "开了之后，你在飞书里超过一段时间没说话，角色会自己找你聊（只在私聊生效，群里不会插话）。关掉就永远只在你说话之后才回。也可以在飞书里发「/主动 off」单独关某个会话。" }),
    ]),
    el("div", { class: "grid-2" }, [
      field("沉默多久算该主动（分钟）", f.feishu_idle, "太小会变成夺命连环催。120 = 两小时没消息就主动一次。"),
      field("每天最多主动几条", f.feishu_daily, "防刷屏也省额度，跨天自动清零。"),
    ]),
  ]);
}

function ghFieldset(ctx, f, note) {
  const say = (msg) => { note.textContent = msg; };
  const run = async (btn, label, fn) => {
    const old = btn.textContent;
    btn.disabled = true; btn.textContent = label;
    try { await fn(); } catch (err) { say("失败：" + err.message); }
    finally { btn.disabled = false; btn.textContent = old; }
  };
  const saveRepo = async () => {
    await api("/api/settings", { method: "PATCH", json: {
      github_repo: f.gh_repo.value.trim(), github_pack_path: f.gh_path.value.trim() || "stickers",
    } });
  };
  const preview = el("button", { class: "plain", text: "预览仓库", onclick: () => run(preview, "读取中…", async () => {
    await saveRepo();
    const d = await api("/api/stickers/pack/preview");
    say(d.repo + " · " + d.total + " 张" + (d.sample.length ? "（例：" + d.sample.slice(0, 4).map((x) => x.label || x.name).join("、") + "…）" : ""));
  }) });
  const pull = el("button", { class: "plain", text: "拉取到本地", onclick: () => run(pull, "下载中…", async () => {
    await saveRepo();
    say("正在下载，张数多的时候要等一会儿…");
    const d = await api("/api/stickers/pack/import", { method: "POST", json: {} });
    await ctx.refreshBootstrap();
    say("从 " + d.repo + " 新增 " + d.added + " 张、合并 " + d.merged + " 张"
      + (d.failed_count ? "，失败 " + d.failed_count + " 张（" + (d.failed[0] || "") + "）" : ""));
  }) });
  const push = el("button", { class: "plain", text: "发布本地表情到仓库", onclick: () => run(push, "推送中…", async () => {
    await saveRepo();
    say("正在逐张比对并推送…（没填 Token 会直接告诉你缺什么）");
    const d = await api("/api/stickers/pack/publish", { method: "POST" });
    say("已推 " + d.uploaded + " 张、跳过未变化 " + d.skipped + " 张 -> " + d.repo + "/" + d.path
      + (d.failed_count ? "，失败 " + d.failed_count + " 张（" + (d.failed[0] || "") + "）" : ""));
  }) });
  return el("fieldset", {}, [
    el("legend", { text: "GitHub 表情仓库（分享 / 备份你的表情库）" }),
    el("p", { class: "sub", text: "仓库里就是一个目录放 stickers.json + 图片。发布会把本地表情库（不含内置素材）写成这套结构；拉取别人或你自己的仓库时，同图按内容自动合并标签，不会越同步越多。" }),
    field("仓库", f.gh_repo, "例：myname/anime-stickers，或 myname/anime-stickers:main"),
    el("div", { class: "grid-2" }, [field("仓库内目录", f.gh_path), field("Token（发布才需要）", f.gh_token)]),
    el("div", { class: "row-actions" }, [preview, pull, push]),
    note,
  ]);
}


/* 「我」的头像。找图和微调位置复用的是角色那两套弹窗，只是接口打在 /api/me 上。
   它一选定就立刻落盘、立刻生效 —— 换头像这件事不该再去求用户按一次「保存」。 */
function meAvatarRow(ctx, s) {
  const state = ctx.state;
  const cur = () => state.settings || s;
  const mine = () => ({ name: cur().user_name, avatar: cur().user_avatar,
    updated_at: cur().user_avatar_at });
  const pic = el("div", {});
  const cropBtn = el("button", { class: "plain", text: "微调位置",
    title: "对着上次存下的原图重新框一次（不联网）" });
  const paint = () => { clear(pic).appendChild(avatarNode(mine(), "avatar lg")); };
  const syncCrop = () => { cropBtn.disabled = !cur().user_avatar_source; };
  const reload = async (d) => {
    if (d && d.settings) state.settings = d.settings;
    paint();
    syncCrop();
    // 聊天里自己那侧的气泡是渲染出来的：不重开一次会话，新头像看不见
    if (state.convId && ctx.openConversation) await ctx.openConversation(state.convId);
  };
  const upload = async (ev) => {
    const file = ev.target.files && ev.target.files[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file, file.name);
    try {
      const d = await api("/api/me/avatar", { method: "POST", form });
      await ctx.refreshBootstrap();
      reload(d);
      toast("头像换成你上传的图了");
    } catch (err) { toast("上传失败：" + err.message, "err"); }
    finally { ev.target.value = ""; }
  };
  cropBtn.onclick = () => {
    const src = cur().user_avatar_source;
    if (!src) { toast("这张没留下原图，点「联网找」重新挑一张"); return; }
    openAvatarCrop(ctx, { me: true }, { url: src + "?t=" + Date.now() }, () => { reload(null); });
  };
  const body = el("div", { class: "me-avatar" }, [
    pic,
    el("div", { class: "row-actions" }, [
      el("button", { class: "plain", text: "联网找",
        title: "搜一张图当自己的头像，选完还能拖一下决定留哪块",
        onclick: () => openAvatarSearch(ctx, { me: true }, () => { reload(null); }) }),
      cropBtn,
      el("button", { class: "plain", text: "不用头像", onclick: async () => {
        if (!(await confirmDialog("去掉自己的头像？",
          "聊天里你这一侧会退回用名字第一个字当头像，随时能再换。", "去掉"))) return;
        try {
          const d = await api("/api/me/avatar-clear", { method: "POST" });
          await ctx.refreshBootstrap();
          reload(d);
          toast("已去掉头像");
        } catch (err) { toast(err.message, "err"); }
      } }),
    ]),
    el("div", { class: "row-actions" }, [
      el("input", { type: "file", accept: "image/*", onchange: upload }),
    ]),
  ]);
  paint();
  syncCrop();
  return el("div", { class: "field" }, [
    el("label", { text: "我的头像" }),
    body,
    el("span", { class: "tip", text: "只有这台机器看得到：裁成正方形后存在 data/avatars/me.png，不会热链别人的地址。没头像时用名字首字。" }),
  ]);
}

/* 「我」页：我的人设 + 全部设置，内嵌渲染进 host（不再弹窗）。
   每次切到这一页重画一次，所以拿到的永远是 state.settings 的最新值。 */
export async function renderSettings(ctx, host) {
  const state = ctx.state;
  let s = JSON.parse(JSON.stringify(state.settings || {}));
  /* 接入方式是后端那张注册表（providers.py → /api/providers）里的一个 key，界面上只画下拉：
     主流平台的地址由注册表预置（base_url 非空 → 填好并锁成只读），「自定义 / 中转站」才放开手填。
     以后要加 Anthropic 原生 / 本地 ollama：后端添一条，这里一格都不用改。
     拉不到就退回「只有自定义这一种」——别因为一次失败把模型那栏渲染成空的。 */
  let provs = [];
  try { provs = (await api("/api/providers")).providers || []; } catch (err) { provs = []; }
  if (!provs.length) {
    provs = [{ key: "openai", name: "自定义 / 中转站（自己填 Base URL）", family: "openai",
      needs_base_url: true, needs_key: true, hint: "填 Base URL + Key + 模型名。",
      base_placeholder: "", key_placeholder: "", model_placeholder: "" }];
  }
  const byKey = {};
  for (const p of provs) byKey[p.key] = p;
  /* 认不出的值退回「自定义」而不是列表第一条（那现在是某家具体平台）：
     没填过的老配置、或后端临时抽风给了个怪 key，都不该被悄悄按到 DeepSeek 头上。 */
  const meKey = s.llm_provider && byKey[s.llm_provider] ? s.llm_provider : (byKey.openai ? "openai" : provs[0].key);
  const f = {
    // 注意别叫 provider：那个名字已经被「联网搜图来源」占了（同一个表单里）
    access: select(provs.map(p => [p.key, p.name]), meKey),
    base_url: textInput(s.llm_base_url, { placeholder: (byKey[meKey] || {}).base_placeholder || "" }),
    api_key: textInput(s.llm_api_key || "", { type: "password", placeholder: (byKey[meKey] || {}).key_placeholder || "平台给的 Key；留空 = 内置 Mock" }),
    model: textInput(s.llm_model, { placeholder: (byKey[meKey] || {}).model_placeholder || "", list: "model-list" }),
    temperature: el("input", { type: "range", min: "0", max: "2", step: "0.05", value: String(s.llm_temperature) }),
    max_tokens: el("input", { type: "number", min: "64", max: "4000", value: String(s.llm_max_tokens) }),
    context: el("input", { type: "number", min: "800", max: "40000", step: "200", value: String(s.context_chars) }),
    timeout: el("input", { type: "number", min: "10", max: "600", value: String(s.llm_timeout) }),
    thinking: select([["off", "关（最快，聊天推荐）"], ["low", "少想一点"], ["on", "开（模型默认，更慢）"]], s.llm_thinking),
    sticker_mode: select([["off", "关（只用模型主动发的）"], ["light", "克制（情绪明显才补图）"], ["rich", "积极（有情绪就上）"]], s.sticker_mode),
    max_stickers: el("input", { type: "number", min: "1", max: "5", value: String(s.max_stickers_per_reply) }),
    // Tenor / Giphy 要注册开发者账号才能拿 Key，自用工具不值得为此加两个填不完的格子，
    // 所以界面只留免 Key 的两家；后端仍认这两个值，老 settings.json 里存过 Key 的照样能用。
    provider: select([["auto", "自动（Bing → DuckDuckGo）"], ["bing", "Bing 图片（免 Key，实测最稳）"],
      ["duckduckgo", "DuckDuckGo（免 Key，非官方，常被风控）"]], s.sticker_search_provider),
    avatar_web: el("input", { type: "checkbox", checked: s.avatar_from_web !== false }),
    user_name: textInput(s.user_name, { placeholder: "你在聊天里的名字" }),
    user_notes: textarea(s.user_notes || "", { rows: 2, placeholder: "可选：你自己的设定，会进系统提示词" }),
    gh_repo: textInput(s.github_repo || "", { placeholder: "你的 GitHub 用户名/仓库名，可加 :分支" }),
    gh_path: textInput(s.github_pack_path || "stickers", { placeholder: "仓库里放表情的目录" }),
    gh_token: textInput(s.github_token || "", { type: "password", placeholder: "ghp_…（只有发布到仓库才需要）" }),
    // 飞书：App ID 不是密钥（会出现在链接和日志里），所以明文回显；Secret 走掩码那条路。
    app_id: textInput(s.feishu_app_id || "", { placeholder: "cli_…（飞书应用凭证页）" }),
    feishu_secret: textInput(s.feishu_app_secret || "", { type: "password", placeholder: "App Secret；只存本机" }),
    feishu_char: select([["", "（不指定：用你最近在网页上聊的角色）"]]
      .concat((state.characters || []).map((c) => [c.id, c.name + (c.title ? " · " + c.title : "")])),
      s.feishu_character || ""),
    feishu_sticker: el("input", { type: "checkbox", checked: s.feishu_stickers !== false }),
    feishu_proactive: el("input", { type: "checkbox", checked: !!s.feishu_proactive }),
    feishu_idle: el("input", { type: "number", min: "10", max: "1440", step: "10", value: String(s.feishu_idle_min || 120) }),
    feishu_daily: el("input", { type: "number", min: "1", max: "50", value: String(s.feishu_daily_max || 10) }),
  };
  /* 哪几格该摆出来跟着接入方式走：内置 Mock 连地址和 Key都不需要，摆着只会让人以为必填。
     预置平台（有 base_url）把地址填好并锁成只读 —— 那是官方固定地址，手打只会打错；
     「自定义 / 中转站」才放开让人填，并且单独记住用户自己填的那个地址，
     免得在平台和自定义之间来回切一次就把中转站地址弄丢了。 */
  const rowBase = field("Base URL", f.base_url, "OpenAI 兼容地址；自建中转站照它自己的文档填，通常以 /v1 结尾");
  const rowKey = field("API Key", f.api_key, "只存在本机 data/settings.json，回读时是掩码，也不会被写进提示词");
  const rowModel = field("模型", f.model, "点「测试连接」能拉一次模型清单，然后从下拉里选准确的名字");
  const provHint = el("p", { class: "tip", text: (byKey[meKey] || {}).hint || "" });
  const mockNote = el("p", { class: "tip", hidden: true,
    text: "内置 Mock：不联网、不花钱，回复是本地模板生成的。想换成真模型就在上面挑一家平台，或选「自定义 / 中转站」填自己的地址。" });
  const baseTip = rowBase.querySelector(".tip");
  let customBase = (byKey[meKey] || {}).base_url ? "" : String(s.llm_base_url || "");
  f.base_url.addEventListener("input", () => {
    if (!(byKey[f.access.value] || {}).base_url) customBase = f.base_url.value;
  });
  const applyProvider = () => {
    const p = byKey[f.access.value] || provs[0] || {};
    const off = p.family === "mock";
    const preset = String(p.base_url || "");
    rowBase.hidden = p.needs_base_url === false;
    rowKey.hidden = p.needs_key === false;
    rowModel.hidden = off;
    mockNote.hidden = !off;
    provHint.textContent = p.hint || "";
    if (!off) {
      if (preset) {
        f.base_url.value = preset;
        f.base_url.readOnly = true;         // 官方地址：给复制，不给了改
        f.base_url.placeholder = "";
        if (baseTip) baseTip.textContent = "这家平台的官方地址，已预置并锁定（要改走代理请选「自定义 / 中转站」）";
      } else {
        f.base_url.readOnly = false;
        f.base_url.value = customBase;
        if (p.base_placeholder) f.base_url.placeholder = p.base_placeholder;
        if (baseTip) baseTip.textContent = "OpenAI 兼容地址；自建中转站照它自己的文档填，通常以 /v1 结尾";
      }
      if (p.key_placeholder) f.api_key.placeholder = p.key_placeholder;
      if (p.model_placeholder) f.model.placeholder = p.model_placeholder;
    }
  };
  f.access.addEventListener("change", applyProvider);
  const tempLabel = el("span", { class: "tip", text: String(s.llm_temperature) });
  f.temperature.addEventListener("input", () => { tempLabel.textContent = f.temperature.value; });
  const result = el("div", {});
  const datalist = el("datalist", { id: "model-list" });
  const ghTokenSet = !!(s.configured_secrets && s.configured_secrets.github_token);
  const ghNoteText = s.github_repo
    ? "已配置：" + s.github_repo + "（目录 " + (s.github_pack_path || "stickers") + "）" + (ghTokenSet ? "，Token 已存" : "，未填 Token（只能拉取）")
    : "还没填仓库。建一个公开仓库，把目录填进来就能拉取；要发布再补 Token。";
  const ghNote = el("p", { class: "tip", text: ghNoteText });

  const collect = () => ({
    llm_provider: f.access.value,
    llm_base_url: f.base_url.value.trim(),
    llm_model: f.model.value.trim(),   // 不再兜底成 auto：那是已停用的本机网关的路由功能
    llm_temperature: Number(f.temperature.value),
    llm_max_tokens: Number(f.max_tokens.value),
    context_chars: Number(f.context.value),
    llm_timeout: Number(f.timeout.value),
    llm_thinking: f.thinking.value,
    sticker_mode: f.sticker_mode.value,
    max_stickers_per_reply: Number(f.max_stickers.value),
    sticker_search_provider: f.provider.value,
    avatar_from_web: !!f.avatar_web.checked,
    user_name: f.user_name.value.trim(),
    user_notes: f.user_notes.value,
    github_repo: f.gh_repo.value.trim(),
    github_pack_path: f.gh_path.value.trim() || "stickers",
    feishu_app_id: f.app_id.value.trim(),
    feishu_character: f.feishu_char.value,
    feishu_stickers: !!f.feishu_sticker.checked,
    feishu_proactive: !!f.feishu_proactive.checked,
    feishu_idle_min: Number(f.feishu_idle.value),
    feishu_daily_max: Number(f.feishu_daily.value),
  });
  const secrets = () => {
    const out = {};
    if (f.api_key.value && f.api_key.value.indexOf("…") < 0) out.llm_api_key = f.api_key.value.trim();
    if (f.gh_token.value && f.gh_token.value.indexOf("…") < 0) out.github_token = f.gh_token.value.trim();
    if (f.feishu_secret.value && f.feishu_secret.value.indexOf("…") < 0) out.feishu_app_secret = f.feishu_secret.value.trim();
    return out;
  };

  const save = async () => {
    try {
      const d = await api("/api/settings", { method: "PATCH", json: Object.assign(collect(), secrets()) });
      state.settings = d.settings;
      await ctx.refreshBootstrap();
      // 这里不关窗：设置整块内嵌在「我」页里，没有窗可关。
      toast("设置已保存");
    } catch (err) { toast(err.message, "err"); }
  };
  const test = async () => {
    clear(result).appendChild(spinner("正在连模型…"));
    try {
      const probe = Object.assign({}, collect());
      if (f.api_key.value && f.api_key.value.indexOf("…") < 0) probe.llm_api_key = f.api_key.value.trim();
      const d = await api("/api/settings/test", { method: "POST", json: probe });
      clear(result);
      const rows = [["地址", d.base_url], ["模型", d.model], ["结果", d.ok ? "通了" : "不通"]];
      if (d.sample) rows.push(["它说", d.sample]);
      if (d.model_count) rows.push(["可用模型", d.model_count + " 个"]);
      if (d.note) rows.push(["备注", d.note]);
      if (d.error) { rows.push(["错误码", d.error.code || "?"]); rows.push(["信息", d.error.message || ""]); if (d.error.hint) rows.push(["建议", d.error.hint]); }
      result.appendChild(kvTable(rows));
      if (d.models && d.models.length) {
        clear(datalist);
        for (const id of d.models) datalist.appendChild(el("option", { value: id }));
        result.appendChild(el("p", { class: "tip", text: "模型清单已刷新，可以在模型框里直接选（输入模型名前几个字即可）。" }));
      }
    } catch (err) {
      clear(result).appendChild(el("p", { class: "tip", text: "测试请求本身就失败了：" + err.message }));
    }
  };
  const clearKey = async () => {
    if (!(await confirmDialog("清空模型 Key？", "清空后会回到内置 Mock 模式（假回复，不花钱、不联网）。", "清空"))) return;
    try {
      const d = await api("/api/settings", { method: "PATCH", json: { llm_api_key: "" } });
      state.settings = d.settings;
      await ctx.refreshBootstrap();
      toast("已切到 Mock 模式");   // 设置内嵌在「我」页，不用关窗；格子下面重画一次就是新状态
    } catch (err) { toast(err.message, "err"); }
  };

  const body = el("div", { class: "settings-page" }, [
    /* 「我是谁」排在最前：这一页就叫「我」，先回答「我是谁」再谈模型和表情。
       以前它是设置弹窗里的第四节，藏在「表情包」「GitHub」后面。 */
    el("fieldset", {}, [
      el("legend", { text: "我是谁" }),
      meAvatarRow(ctx, s),
      field("我的名字", f.user_name),
      field("关于我的补充设定", f.user_notes),
    ]),
    el("p", { class: "sub", text: "接法只有一种形状：Base URL + Key + 模型名。自建中转站填它给你的地址，官方平台填平台的地址；三格都空着就是内置 Mock（本机假回复，不联网、不花钱）。" }),
    el("fieldset", {}, [
      el("legend", { text: "模型" }),
      field("接入方式", f.access, "选一种接法，下面几格跟着变。以后加接入方式只要在后端 providers.py 添一条。"),
      provHint,
      rowBase,
      rowKey,
      el("div", { class: "grid-2" }, [rowModel, field("超时（秒）", f.timeout)]),
      mockNote,
      el("div", { class: "grid-2" }, [
        field("最大回复 token", f.max_tokens),
        field("上下文字符预算", f.context, "越大越记得住，也越慢越贵"),
      ]),
      field("思考模式", f.thinking, "qwen3 / deepseek-r1 这类混合推理模型会先想几百字再开口：实测聊天里 84% 的生成量烧在没人看的思考上、平均 7.5s 一条，关掉约快一倍。不支持这个参数的模型会自动忽略（失败还会摘掉参数重试一次）。"),
      field("温度", f.temperature),
      tempLabel,
      datalist,
      el("div", { class: "row-actions" }, [
        el("button", { class: "plain", text: "测试连接", onclick: test }),
        el("button", { class: "plain", text: "清空 Key（转 Mock）", onclick: clearKey }),
      ]),
      result,
    ]),
    el("fieldset", {}, [
      el("legend", { text: "表情包" }),
      // 表情库管理原来占着侧栏一个常驻按钮，其实只有偶尔整理时才用，收进设置里
      el("div", { class: "row-actions" }, [
        el("button", { class: "plain", text: "管理表情库（导入 / 打标签 / 删）",
          title: "现在共 " + ((state.stickers || []).length) + " 张",
          onclick: () => openStickerLibrary(ctx) }),
      ]),
      field("自动表情", f.sticker_mode, "模型没主动发图时，按它这句话的情绪补一张"),
      field("单条最多几张", f.max_stickers),
      field("联网搜图来源", f.provider, "Bing / DuckDuckGo 都不用 Key；Bing 在这个网络更稳，素材站水印会被降权"),
      el("label", { class: "check-field" }, [
        f.avatar_web,
        el("span", { text: "AI 生成角色时联网找头像" }),
        el("span", { class: "tip", text: "先搜官方立绘，搜不到（离线、防盗链、图太小）才退回程序画的 Q 版。关掉它就永远只用 Q 版，不沾别人的图。" }),
      ]),
    ]),
    ghFieldset(ctx, f, ghNote),
    feishuFieldset(f, s),
    el("div", { class: "row-actions" }, [
      el("button", { class: "primary", text: "保存", onclick: save }),
      el("span", { class: "spacer" }),
      el("span", { class: "tip", text: "存在 data/settings.json · 数据目录：" + (s.data_dir || "data/") }),
    ]),
  ]);
  // 挂进「我」页那个容器，不再走弹窗：这一页本身就是设置。
  clear(host).appendChild(body);
  applyProvider();   // 一挂上就按当前接入方式收掉不该出现的格子（内置 Mock 不摆地址和 Key）
}

/* ---------------------------------------------------------------- 表情库管理 */

export async function openStickerLibrary(ctx) {
  const state = ctx.state;
  const q = el("input", { type: "search", placeholder: "筛选：标签 / 名字 / 情绪" });
  const emotionFilter = select(EMOTION_OPTIONS, "");
  const grid = el("div", { class: "lib-grid" });
  const status = el("span", { class: "tip" });

  async function reload() {
    clear(grid).appendChild(spinner("加载表情…"));
    const params = new URLSearchParams();
    params.set("q", q.value.trim());
    params.set("emotion", emotionFilter.value);
    params.set("limit", "200");
    const d = await api("/api/stickers?" + params.toString());
    state.stickers = d.stickers;
    clear(grid);
    for (const st of d.stickers) grid.appendChild(card(st));
    status.textContent = d.stats.total + " 张（内置 " + d.stats.builtin + "，我的 " + d.stats.user + "）· 目录 " + d.stats.dir;
  }

  function card(st) {
    const label = textInput(st.label, { placeholder: "名字" });
    const tags = textInput((st.tags || []).join(", "), { placeholder: "触发标签" });
    const emo = select(EMOTION_OPTIONS, st.emotion);
    const commit = async (patch) => {
      try {
        await api("/api/stickers/" + st.id, { method: "PATCH", json: patch });
        await ctx.refreshStickers();
        toast("已保存");
      } catch (err) { toast(err.message, "err"); }
    };
    label.addEventListener("change", () => commit({ label: label.value }));
    tags.addEventListener("change", () => commit({ tags: readList(tags) }));
    emo.addEventListener("change", () => commit({ emotion: emo.value }));
    return el("div", { class: "lib-card" }, [
      el("img", { src: st.url, alt: st.label, onclick: () => lightbox(st.url, st.label), style: "cursor:zoom-in" }),
      el("div", { class: "lib-meta" }, [
        el("b", { text: st.id + (st.origin !== "builtin" ? " · " + st.origin : "") }),
        label, tags, emo,
        el("div", { class: "lib-actions" }, [
          el("button", { text: st.favorite ? "★ 已收藏" : "☆ 收藏", onclick: () => commit({ favorite: !st.favorite }) }),
          st.origin === "builtin" ? el("button", {
            text: "隐藏", title: "内置素材不会被真删，恢复：animechat unhide-stickers", onclick: async () => {
              if (!(await confirmDialog("隐藏这张内置表情？", "手绘那张会从表情库里消失（文件还在程序包里）。想放回来：命令行跑 animechat unhide-stickers。", "隐藏"))) return;
              try {
                await api("/api/stickers/" + st.id, { method: "DELETE" });
                await reload(); ctx.refreshStickers();
                toast("已隐藏，恢复请跑 animechat unhide-stickers");
              } catch (err) { toast(err.message, "err"); }
            },
          }) : el("button", {
            text: "删除", onclick: async () => {
              if (!(await confirmDialog("删除这张表情？", "文件会从 data/stickers 移走，已发过的历史会变灰。", "删除"))) return;
              try {
                const r = await api("/api/stickers/" + st.id, { method: "DELETE" });
                await reload(); ctx.refreshStickers();
                toast(r && r.hidden ? "已隐藏（恢复：animechat unhide-stickers）" : "已删除");
              } catch (err) { toast(err.message, "err"); }
            },
          }),
        ]),
        st.note ? el("span", { class: "tip", text: st.note }) : null,
      ]),
    ]);
  }

  q.addEventListener("input", () => reload());
  emotionFilter.addEventListener("change", () => reload());

  const body = el("div", {}, [
    titleBar("表情包库", "模型就是按这里的标签挑图的"),
    el("p", { class: "sub", text: "标签写得越贴近角色的说法，[sticker:xxx] 越容易命中。也可以直接往 data/stickers 丢图，文件名用 + 分隔标签（例如 傲娇+哼+别误会.png）会自动入库。" }),
    el("div", { class: "row-actions" }, [q, emotionFilter,
      el("button", { class: "plain", text: "刷新", onclick: reload }),
      el("span", { class: "spacer" }), status]),
    grid,
  ]);
  openModal(body, { wide: true });
  reload();
}

/* ---------------------------------------------------------------- 上下文查看 */

export async function openContext(cid, ctx) {
  const body = el("div", {}, [titleBar("真正发给模型的内容", "只读"), el("div", {}, [spinner("组装中…")])]);
  openModal(body, { wide: true });
  try {
    const d = await api("/api/conversations/" + cid + "/context");
    const wrap = el("div", {}, [
      titleBar("真正发给模型的内容", d.model),
      el("p", { class: "sub", text: "共 " + d.messages.length + " 条 / " + d.chars + " 字（系统提示 " + d.system_chars + " 字）。角色人设改完就在这里核对，别猜。" }),
    ]);
    if (d.summary) wrap.appendChild(el("div", { class: "ctx-msg" }, [el("b", { text: "记忆摘要" }), el("pre", { text: d.summary })]));
    for (const m of d.messages) {
      wrap.appendChild(el("div", { class: "ctx-msg" }, [
        el("b", { text: m.role + " · " + m.content.length + " 字" }),
        el("pre", { text: m.content }),
      ]));
    }
    clear(body).appendChild(wrap);
  } catch (err) {
    clear(body).appendChild(el("p", { class: "tip", text: "失败了：" + err.message }));
  }
}

/* ---------------------------------------------------------------- 关于 */

export function openAbout(boot, ctx) {
  const s = (boot && boot.settings) || {};
  const stats = (boot && boot.stats) || {};
  const st = (boot && boot.stickers) || [];
  const body = el("div", {}, [
    titleBar("二次元聊天 AI · animechat", (boot && boot.version) || ""),
    el("p", { class: "sub", text: "本地跑的角色扮演聊天：性格靠人设 + 语气示范，表情包靠情绪标签匹配。数据都在这个文件夹里，不上传任何地方。" }),
    kvTable([
      ["模型", s.mock_mode ? "内置 Mock（不联网、不花钱）" : s.llm_base_url + " / " + s.llm_model],
      ["表情库", st.length + " 张 · " + (s.data_dir || "") + "\\stickers"],
      ["会话 / 消息", (stats.conversations || 0) + " / " + (stats.messages || 0)],
      ["带表情的消息", String((stats.messages_with_sticker || 0))],
    ]),
    el("h2", { text: "怎么玩" }),
    el("ul", { class: "attempts" }, [
      el("li", { text: "左侧选角色 → 直接聊。角色想发表情时会输出 [sticker:标签]，系统换成真图发给你。" }),
      el("li", { text: "点输入框左边的贴纸图标自己甩表情（发完自动关掉面板）；点上传图标传一张图当表情（也能直接往 data/stickers 丢图）。" }),
      el("li", { text: "「导入」支持 SillyTavern 的 JSON 卡，也支持把卡内嵌在 PNG 里的角色卡。" }),
      el("li", { text: "「上下文」能看到发给模型的原文，「总结记忆」把长对话压成角色记忆。" }),
      el("li", { text: "快捷键：Enter 发送 / Shift+Enter 换行 / Esc 关闭面板。" }),
    ]),
    el("h2", { text: "表情协议" }),
    el("pre", { class: "ctx-msg", style: "padding:10px", text: "[sticker:傲娇]   让角色发一张表情包（写意思接近的词也能命中）\n[emotion:开心]   声明当前情绪，用于徽章和兜底选图\n\n模型不用管图在哪：标签交给表情库匹配，认不出来就按情绪挑一张，绝不把方括号漏给你看。" }),
    el("div", { class: "row-actions" }, [
      el("button", { class: "primary", text: "知道了", onclick: closeModal }),
      el("span", { class: "spacer" }),
      el("button", { class: "plain", text: "接口文档 /api/docs", onclick: () => window.open("/api/docs", "_blank") }),
    ]),
  ]);
  openModal(body);
}
