/* 聊天音效：全部用 Web Audio 现场合成，不引入音频文件——零体积、零版权、离线可用，
   也不会因为浏览器没缓存到 mp3 就变成「点了没声」。 */

const KEY = "animechat-sfx";
let ctx = null;
let bus = null;

export function isOn() {
  return localStorage.getItem(KEY) !== "0";   // 默认开
}

export function setOn(v) {
  localStorage.setItem(KEY, v ? "1" : "0");
  if (v) prime();
}

function ensure() {
  if (!isOn()) return null;
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!ctx) {
    ctx = new AC();
    // 限幅器：连发几条时不至于互相削顶炸音
    const comp = ctx.createDynamicsCompressor();
    comp.threshold.value = -18; comp.knee.value = 22; comp.ratio.value = 6;
    comp.attack.value = 0.004; comp.release.value = 0.18;
    bus = ctx.createGain();
    bus.gain.value = 0.9;
    bus.connect(comp).connect(ctx.destination);
  }
  if (ctx.state === "suspended") ctx.resume().catch(() => {});
  return ctx;
}

/* 浏览器在用户手势之前会挂起 AudioContext，所以第一次交互时悄悄解锁一次。 */
export function prime() { ensure(); }

function noise(c, dur) {
  const buf = c.createBuffer(1, Math.max(1, Math.ceil(c.sampleRate * dur)), c.sampleRate);
  const data = buf.getChannelData(0);
  for (let i = 0; i < data.length; i += 1) data[i] = Math.random() * 2 - 1;
  const node = c.createBufferSource();
  node.buffer = buf;
  return node;
}

function tone(c, freq, at, dur, peak, type) {
  const osc = c.createOscillator();
  osc.type = type || "sine";
  osc.frequency.setValueAtTime(freq, at);
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, at);
  g.gain.exponentialRampToValueAtTime(peak, at + 0.014);
  g.gain.exponentialRampToValueAtTime(0.0001, at + dur);
  osc.connect(g).connect(bus);
  osc.start(at);
  osc.stop(at + dur + 0.03);
}

/* 发送：一声很短的「咻」——噪声过带通往上扫，加一点低频垫底，不吵但确定发出去了。 */
export function playSend() {
  const c = ensure();
  if (!c) return;
  const t = c.currentTime;
  const src = noise(c, 0.2);
  const bp = c.createBiquadFilter();
  bp.type = "bandpass"; bp.Q.value = 1.3;
  bp.frequency.setValueAtTime(760, t);
  bp.frequency.exponentialRampToValueAtTime(2500, t + 0.11);
  const g = c.createGain();
  g.gain.setValueAtTime(0.0001, t);
  g.gain.exponentialRampToValueAtTime(0.13, t + 0.018);
  g.gain.exponentialRampToValueAtTime(0.0001, t + 0.19);
  src.connect(bp).connect(g).connect(bus);
  src.start(t);
  src.stop(t + 0.22);
  tone(c, 220, t, 0.09, 0.05, "triangle");
}

/* 收到：B5 → E6 两下轻铃，第二个音叠一层高八度泛音，做出「叮」的金属尾音。 */
export function playReceive() {
  const c = ensure();
  if (!c) return;
  const t = c.currentTime;
  tone(c, 987.77, t, 0.26, 0.1);
  tone(c, 1318.51, t + 0.085, 0.4, 0.085);
  tone(c, 2637.02, t + 0.085, 0.16, 0.022);
}
