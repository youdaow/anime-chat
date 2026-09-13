"""中文情绪识别（规则 + 词典）。

不是模型推理，是可解释的启发式：命中关键词打分，取最高分。用来做三件事——
1. 模型没主动带表情时，按回复情绪自动补一张表情包；
2. 界面上的情绪徽章；
3. 表情搜索的默认关键词。

命中不了就返回 neutral，绝不硬猜成激烈情绪。
"""

from __future__ import annotations

import re

# 情绪 -> (中文名, 关键词)。关键词按"出现即加分"处理，长词权重更高。
LEXICON: dict[str, tuple[str, list[str]]] = {
    "happy": ("开心", [
        "哈哈", "哈哈哈", "嘿嘿", "嘻嘻", "太开心", "好开心", "真开心", "开心", "高兴", "愉快",
        "太好了", "好耶", "耶", "爽", "舒服", "美滋滋", "棒", "赞", "满意", "笑死", "笑得",
        "感谢", "谢谢", "多谢", "太好了", "成功", "赢了", "过关", "好事", "心情好", "喜欢",
    ]),
    "love": ("喜欢", [
        "爱你", "喜欢你", "好喜欢", "心动", "喜欢", "爱你哦", "贴贴", "抱抱", "抱一下", "比心",
        "亲一下", "mua", "老婆", "老公", "宝贝", "宝贝你", "想你", "惦记", "在乎", "宠",
    ]),
    "tsundere": ("傲娇", [
        "才不是", "才没有", "别误会", "哼", "才不是为你", "顺路", "刚好", "勉为其难", "笨蛋",
        "下不为例", "不是特意", "谁要", "少啰嗦", "真拿你没办法",
    ]),
    "sad": ("难过", [
        "难过", "伤心", "想哭", "哭了", "泪", "呜呜", "嘤嘤", "委屈", "难受", "心里不舒服",
        "失落", "孤单", "寂寞", "没人", "被凶", "好惨", "惨了", "眼泪", "哭死", "emo",
        "舍不得", "心疼", "自责", "对不起", "抱歉", "不好意思",
    ]),
    "angry": ("生气", [
        "生气", "气死", "火大", "恼火", "愤怒", "想揍", "滚", "闭嘴", "别烦", "烦死", "烦人",
        "可恶", "该死", "岂有此理", "受不了", "忍不了", "达咩", "不行!", "胡说", "找打",
    ]),
    "shock": ("震惊", [
        "不会吧", "什么?", "什么？", "惊了", "震惊", "离谱", "我的天", "天呐", "不是吧",
        "真的假的", "居然", "竟然", "裂开", "懵", "呆", "啊这", "挖草", "卧槽", "我勒个",
    ]),
    "awkward": ("无语", [
        "无语", "汗", "流汗", "尴尬", "呃", "额", "这个", "不知道该说", "无言", "吐槽",
        "冷场", "尬", "行吧", "算了", "emm", "emmm", "啊这",
    ]),
    "sleepy": ("困倦", [
        "困", "好困", "想睡", "睡觉", "晚安", "哈欠", "累", "疲惫", "没精神", "撑不住",
        "犯困", "困死", "躺平", "睡着",
    ]),
    "think": ("思考", [
        "让我想想", "想想", "思考", "琢磨", "考虑", "分析", "嗯…", "嗯.", "问题在于",
        "我觉得", "依我看", "首先", "原因", "可能是", "大概", "也许", "观察",
    ]),
    "hype": ("亢奋", [
        "加油", "冲", "冲冲冲", "拼了", "干杯", "庆祝", "撒花", "起飞", "燃", "带劲",
        "吃瓜", "看戏", "围观", "八卦", "开始", "出发", "搞定", "拿下", "赢麻",
    ]),
}

EMOTION_LABELS: dict[str, str] = {k: v[0] for k, v in LEXICON.items()}
EMOTION_KEYS: list[str] = list(LEXICON.keys()) + ["neutral"]

# 否定/转折前缀：命中时把情绪压回去，避免"我才不开心"被判成开心
_NEG_HEADS = ("不", "没", "别", "才不是", "无", "非", "难以", "可不是")
_PUNCT = re.compile(r"[，。！？!?~、,.\s…\-—]+")


def detect(text: str) -> tuple[str, float, dict[str, float]]:
    """返回 (情绪key, 置信度0~1, 各情绪得分)。空文本 -> neutral。"""
    body = (text or "").strip()
    if not body:
        return "neutral", 0.0, {}
    scores: dict[str, float] = {}
    lowered = body.lower()
    for key, (_label, words) in LEXICON.items():
        score = 0.0
        for w in words:
            w_l = w.lower()
            idx = 0
            while True:
                pos = lowered.find(w_l, idx)
                if pos < 0:
                    break
                idx = pos + len(w_l)
                head = lowered[max(0, pos - 3):pos]
                hit = 1.0 + min(len(w_l), 6) * 0.12
                if any(neg in head for neg in _NEG_HEADS):
                    hit *= 0.25  # 被否定，权重打骨折
                score += hit
        if score:
            scores[key] = score
    if not scores:
        return "neutral", 0.0, {}
    # 重复/语气符号轻微放大，让"哈哈哈哈哈哈"更明确
    total = sum(scores.values())
    best = max(scores, key=lambda k: scores[k])
    confidence = scores[best] / total if total else 0.0
    excl = body.count("！") + body.count("!") + body.count("？") + body.count("?")
    if excl >= 2:
        confidence = min(1.0, confidence + 0.08)
    if scores[best] < 1.15:
        return "neutral", round(min(confidence, 0.4), 3), scores
    return best, round(confidence, 3), scores


_LABEL_TO_KEY = {meta[0]: key for key, meta in LEXICON.items()}


def norm(raw: str) -> str | None:
    """把「开心 / happy / 心情：傲娇」这类写法统一成情绪 key；认不出返回 None。"""
    s = (raw or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in EMOTION_KEYS:
        return low
    if s in _LABEL_TO_KEY:
        return _LABEL_TO_KEY[s]
    for key, meta in LEXICON.items():
        if meta[0] and meta[0] in s:
            return key
    for name, key in (("困倦", "sleepy"), ("无语", "awkward"), ("震惊", "shock"), ("思考", "think"),
                      ("亢奋", "hype"), ("喜欢", "love"), ("难过", "sad"), ("平常", "neutral")):
        if name in s:
            return key
    return None


def label(emotion: str) -> str:
    if emotion == "neutral":
        return "平常"
    return EMOTION_LABELS.get(emotion, emotion)


def keywords(emotion: str) -> list[str]:
    """给表情搜索用的关键词。"""
    if emotion in LEXICON:
        name, words = LEXICON[emotion]
        return [name] + [w for w in words if len(w) <= 4][:6]
    return ["表情"]


# ------------------------------------------------- 表情「定义」用的别名层
# LEXICON 判的是「这句回复是什么情绪」，下面这张表判的是「这张图在演什么」。
# 两者不能共用：搜图关键词、远程文件名、标签里出现的是情绪本名（傲娇 / 哭 / tsundere），
# 而 LEXICON 存的是说话时才用的短语（「才不是」「哼」）。拿它去认标签基本全落空，
# 于是联网抓回来的表情一律 neutral —— 角色挑图时永远绕开它（用户报的 bug）。
ALIASES: dict[str, list[str]] = {
    "tsundere": ["傲娇", "傲骄", "嘴硬", "杂鱼", "嘲讽", "嘲笑", "挑衅", "得意", "看不起", "哼",
                 "tsundere", "smug", "tease", "teasing", "mock", "zako", "brat"],
    "happy": ["开心", "高兴", "笑", "哈哈", "乐", "愉快", "爽", "可爱", "嘿嘿",
              "happy", "smile", "laugh", "lol", "joy", "cheer", "yay", "giggle"],
    "love": ["喜欢", "爱", "抱抱", "抱", "贴贴", "比心", "亲亲", "心动", "老婆", "老公", "宝贝",
             "hug", "kiss", "love", "heart", "cuddle", "adore", "smooch"],
    "sad": ["哭", "难过", "伤心", "委屈", "泪", "呜呜", "惨", "失落", "心疼", "emo",
            "sad", "cry", "crying", "tear", "tears", "sob", "upset", "depressed"],
    "angry": ["生气", "怒", "炸毛", "愤怒", "讨厌", "滚", "锤", "抓狂", "火大", "达咩",
              "angry", "rage", "furious", "mad", "smash", "punch", "slap"],
    "shock": ["震惊", "惊了", "吓", "呆住", "呆", "懵", "石化", "离谱", "裂开", "啊这",
              "shock", "surprised", "surprise", "omg", "wow", "stunned", "gasp"],
    "awkward": ["尴尬", "无语", "汗", "流汗", "凝视", "呆滞", "冷场", "无言", "救命",
                "awkward", "sweat", "cringe", "shrug", "unamused", "secondhand"],
    "sleepy": ["困", "睡", "晚安", "哈欠", "累", "躺平", "没精神", "犯困",
               "sleep", "sleepy", "yawn", "tired", "bed", "nap", "drowsy"],
    "think": ["思考", "想想", "琢磨", "疑惑", "疑问", "不懂", "问号", "分析", "沉思",
              "think", "thinking", "consider", "ponder", "wonder", "hmm", "confused"],
    "hype": ["兴奋", "冲", "加油", "跳舞", "撒花", "庆祝", "燃", "吃瓜", "围观", "起哄",
             "hype", "dance", "dancing", "party", "excited", "sparkle", "pumped"],
}

_ALIAS_ORDER: list[str] = list(ALIASES.keys())
_HAS_ASCII = re.compile(r"[a-z]")
_ASCII_WORD = re.compile(r"(?<![a-z])[a-z]{2,16}(?![a-z])")


def match_words(text: str) -> dict[str, float]:
    """扫一段文字（标签、文件名、搜索词都行），返回 {情绪: 命中词数}。

    英文按词边界匹配，免得 sad 命中 satisfied、act 命中 transaction；中文按子串。
    """
    s = (text or "").lower()
    if not s:
        return {}
    ascii_words = set(_ASCII_WORD.findall(s))
    out: dict[str, float] = {}
    for emo, words in ALIASES.items():
        score = 0.0
        for w in words:
            wl = w.lower()
            hit = (wl in ascii_words) if _HAS_ASCII.search(wl) else (wl in s)
            if hit:
                # 按词长加权：「困惑」比单字「困」更具体，不然「困惑」会被判成困倦
                score += len(wl)
        if score:
            out[emo] = score
    return out


def guess(text: str, default: str = "neutral") -> str:
    """给一张图定情绪：命中词最多的那个；一个都不中就返回 default，绝不硬猜。"""
    scores = match_words(text)
    if not scores:
        return default
    return max(scores.items(), key=lambda kv: (kv[1], -_ALIAS_ORDER.index(kv[0])))[0]


def aliases(emotion: str, limit: int = 3) -> list[str]:
    """这张图属于哪个情绪时，顺手该带哪几个中文标签（喂给搜索和提示词）。"""
    if emotion not in ALIASES:
        return []
    out = [label(emotion)] if emotion != "neutral" else []
    return list(dict.fromkeys(out + ALIASES[emotion]))[:limit]
