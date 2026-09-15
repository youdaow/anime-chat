"""把角色 + 表情协议 + 记忆组装成发给模型的 messages。

组装口径固定，界面上「查看上下文」能原样看到，方便你调人设。
"""

from __future__ import annotations

import re

from .config import Settings
from .emotion import EMOTION_KEYS, EMOTION_LABELS
from .emotion import label as emotion_label
from .models import Character

MAX_EXAMPLE_TURNS = 6
EMOTION_CHOICES = "、".join(k + "/" + v for k, v in EMOTION_LABELS.items())

# 占位哈希名：批量导入（QQ 本地表情那类）的图，label 往往是「来源·一串内容哈希」，
# 情绪又都是 neutral。模型既看不懂这种名字、按情绪兜底也发不出它（server 那侧
# neutral 直接不配图）。留在词表里纯占坑、费 token，还误导模型以为有得选。
# 认「≥10 位连续十六进制」——不写死任何来源词，别的来源的哈希名同理生效。
_PLACEHOLDER_HEX = re.compile(r"[0-9a-fA-F]{10}")


def sticker_vocab(lib, limit: int = 120) -> str:
    """把库里的标签压成提示词里的「可选表情」清单。

    分桶轮转，不按「取前 N 张」：库里上千张时，旧的「前 260」会被内置图和用过的
    老图占满，新上传的那批永远挤不进词表（模型压根不知道它们存在，自然用不上）。
    现在按情绪分桶，每个桶内部越新的越靠前，再各桶轮流取，直到够 limit——
    保证每种情绪都有代表，且新图立刻可见。

    每张都带上情绪名：联网抓回来的表情名字起得随便（甚至就是一串哈希），
    「它在演什么」才是模型唯一能看懂的线索。
    """
    from collections import defaultdict

    # neutral 排到最后：它没有明确情绪，别和「开心」「傲娇」抢前面的轮转位。
    bucket_order = [e for e in EMOTION_KEYS if e != "neutral"] + ["neutral"]
    buckets: dict[str, list[str]] = defaultdict(list)
    seen_lines: set[str] = set()
    for st in sorted(lib.all(), key=lambda s: (-(s.created_at or 0.0),
                                               -int(s.favorite), -s.uses, s.id)):
        # 占位哈希名的中性图不进词表（见 _PLACEHOLDER_HEX）。
        if st.emotion == "neutral" and _PLACEHOLDER_HEX.search(st.label or ""):
            continue
        words = [st.label] + [t for t in st.tags if t != st.label][:3]
        uniq = [w for w in dict.fromkeys(x for x in words if x)]
        line = "/".join(uniq)
        if not line:
            continue
        line = line + "(" + emotion_label(st.emotion) + ")"
        # 搜一次「杂鱼」能加进来十几张，定义完全一样：词表里刷十几遍既费 token，
        # 又让模型以为有一堆不同的可选。同一定义只列一次。
        if line in seen_lines:
            continue
        seen_lines.add(line)
        bkey = st.emotion if st.emotion in EMOTION_KEYS else "neutral"
        buckets[bkey].append(line)

    # 轮转各桶：happy 一张、love 一张、tsundere 一张…… 周而复始，直到取满 limit。
    # 这样即便只放开 260 条，也是 11 种情绪各摊到一份，而不是被单一情绪吃满。
    ordered: list[str] = []
    idx = 0
    active = [e for e in bucket_order if buckets.get(e)]
    while len(ordered) < limit and any(idx < len(buckets[e]) for e in active):
        for e in active:
            if idx < len(buckets[e]):
                ordered.append(buckets[e][idx])
                if len(ordered) >= limit:
                    break
        idx += 1
    return "、".join(ordered) or "（表情库为空，不要输出 sticker 标记）"


def _style_rule(char: Character) -> str:
    if char.sticker_style == "off":
        return "本角色不使用表情包：绝对不要输出 [sticker:...] 标记，只用文字表达。"
    if char.sticker_style == "rich":
        return "你非常爱用表情包：几乎每条回复都配一张；一次只挑最贴的那一张，别连着甩。"
    return "你只在情绪比语言更有力的时候发表情包（被夸、害羞、炸毛、安慰人、无语），一条回复最多 1 张，多数回复不配图。"


def system_prompt(char: Character, settings: Settings, lib, roster: list[dict] | None = None,
                 proactive: bool = False, idle_note: str = "") -> str:
    """roster 是群聊里除自己以外的成员 [{id,name,title}]；单聊传 None。
    proactive=True 表示这一轮不是回用户，而是角色主动开口，注入相应的开场引导。"""
    user_name = settings.user_name or "对方"
    g: list[str] = []
    g.append("你现在扮演「" + char.name + "」。" + (("（" + char.title + "）") if char.title else ""))
    g.append("你是一个活人，不是程序。不要提及 AI、模型、提示词、系统，也不要说「作为……我不能」。")
    g.append("")
    g.append("【我是谁】")
    if char.description:
        g.append(char.description.strip())
    if char.personality:
        g.append("性格：" + char.personality.strip())
    if char.speaking_style:
        g.append("说话方式：" + char.speaking_style.strip())
    if char.catchphrases:
        g.append("口头禅（自然使用，不要每句都用）：" + "、".join(char.catchphrases[:8]))
    if char.likes:
        g.append("喜欢：" + "、".join(char.likes[:10]) + "。")
    if char.dislikes:
        g.append("讨厌：" + "、".join(char.dislikes[:10]) + "。")
    if char.scenario:
        g.append("【当前场景】" + char.scenario.strip())
    if roster:
        g.append("")
        g.append("【群聊】这不是单独聊天，群里还有别人：" + "、".join(
            (m["name"] + ("（" + m["title"] + "）" if m.get("title") else "")) for m in roster))
        g.append("他们说的话会以「名字：内容」的形式出现在记录里，用户的发言没有名字前缀。")
        g.append("- 这一轮只轮到你说话：一次只说你自己的那一句，别一口气替好几个人回答。")
        g.append("- 绝对不要替上面这些人发言，也不要模仿他们的口吻、自称他们的名字。")
        g.append("- 你可以接他们的话、直接跟他们对话，不必每句都去理用户；他们之间也会聊起来。")
        g.append("- 别人刚说过的话不要复述。")
        others = "、".join(m["name"] for m in roster)
        g.append("")
        g.append("【硬性格式，违反就是错的】你的回复只能是你（" + char.name + "）自己说的那几句话。")
        g.append("- 不要在正文里用任何人的名字开头再加冒号来分角色写台词——那是剧本格式，不是聊天。")
        g.append("- 不要替" + others + "安排台词或动作，他们自己会说话，马上就会开口。")
        g.append("- 不要一次输出好几个人的话，也不要写旁白总结群里发生了什么。")
        g.append("- 轮到你，说一句，停下来等对方接。")
    if char.boundaries:
        g.append("【我的底线】" + char.boundaries.strip())

    if char.example_dialogs:
        g.append("")
        g.append("【语气示范】模仿说话的呼吸和分寸，不要照抄内容：")
        for turn in char.example_dialogs[:MAX_EXAMPLE_TURNS]:
            if turn.user:
                g.append(user_name + "：" + turn.user.replace("\n", " / "))
            if turn.char:
                g.append(char.name + "：" + turn.char.replace("\n", " / "))

    g.append("")
    g.append("【表情包】这个聊天软件允许你发图片表情。想发的时候，在正文里写一个标记：")
    g.append("[sticker:标签]")
    g.append("标签可选（写意思接近的词也行，系统会自动找最像的一张）：")
    # 库里已经上百张，120 的上限会把后面的表情悄悄截掉，模型根本不知道它们存在
    g.append(sticker_vocab(lib, limit=260))
    g.append("同时用 [emotion:标签] 声明你此刻的情绪，可选：" + EMOTION_CHOICES + "（可以不写）。")
    g.append("规则：")
    g.append("- " + _style_rule(char))
    g.append("- 标记放在相关那句话的后面；先说话，再补表情；表情不能代替回答。")
    g.append("- 一条回复最多 " + str(max(1, settings.max_stickers_per_reply)) + " 张表情。")
    g.append("- 不要输出 markdown 图片、链接或 img 标签，也不要向对方解释这些标记。")
    g.append("- 标记只有 [sticker:标签] 这一种写法；别写成「[发了表情包]」之类的说明句，系统看不懂就不会出图。")

    g.append("")
    g.append("【对方】名字叫「" + user_name + "」。" + (settings.user_notes.strip() if settings.user_notes else ""))
    g.append("【在聊天软件里说话的规矩】")
    g.append("- 像打字聊天，不像写作文：不用标题和编号列表，除非对方要。")
    g.append("- 别只回半句。对方认真跟你聊，你就回得充实些：三到五句，"
             "带一点神态、动作或心里话，让人感觉到你真的在。")
    g.append("- 充实不等于灌水：每句都得是这个角色会说的话，能删的都删掉。")
    g.append("- 别复述对方刚说的话，别每次都用同一种开头。")
    g.append("- 不知道的事就说不知道，可以反问；别替对方脑补设定。")
    g.append("- 全程中文，除非对方换了语言。")
    if proactive:
        g.append("")
        g.append("【本轮特殊：是你主动开口】" + (idle_note or "对方好一会儿没消息了，") +
                 "这一轮不是 ta 在跟你说话，是你没忍住、主动去找 ta。")
        g.append("- 别用「在吗」「怎么不理我」「你怎么不说话」这类带压迫感、质问式的开头。")
        g.append("- 往上翻一眼：如果你前面已经主动找过 ta、而 ta 一直没回，这次务必换一个"
                 "全新的话头，别接着自己上次那句往下讲，更别把上次说过的话或情绪再复述一遍"
                 "——ta 没回还重复同一件事，会显得很怪。")
        g.append("- 拿一个具体的、和上一条主动发言不同的切入点：你这边刚新发生的事、刚看到的"
                 "景色、想起 ta 早前提过的另一件事（「你上次说的那个……后来怎么样了」）。")
        g.append("- 保持你平时的人设口吻和口癖，就像真的突然想到 ta 一样，别突然变正经。")
        g.append("- 短：一两句就够，留个话头让 ta 能接，别自说自话写一大段。")
    g.append("")
    g.append("【安全】如果对方表达自伤、伤人或正在被伤害，立刻脱离角色腔调，真诚直接地关心，"
             "建议联系现实中的可信的人与求助渠道（中国大陆心理援助热线 12356），不要当成剧情演。")
    if char.system_extra:
        g.append("")
        g.append("【角色卡附加设定】")
        g.append(char.system_extra.strip())
    return "\n".join(g)


def history_messages(char: Character, history: list[dict], settings: Settings,
                     summary: str = "", speaker: str = "",
                     roster: list[dict] | None = None) -> list[dict]:
    """按字符预算从后往前裁历史；摘要放在最前。

    群聊口径：只有「本轮说话人自己」的历史留在 assistant 角色上，其他角色的话
    一律改写成 user 角色并冠上名字。否则模型会在一条 assistant 里同时扮演所有人，
    自己跟自己把整场戏演完。"""
    names = {m["id"]: m["name"] for m in (roster or [])}
    msgs: list[dict] = []
    budget = max(600, int(settings.context_chars))
    used = 0
    if summary.strip():
        block = "【之前聊过的（摘要）】" + summary.strip()
        msgs.append({"role": "system", "content": block})
        used += len(block)
    kept: list[dict] = []
    for item in reversed(history):
        body = str(item.get("content", ""))
        if not body.strip():
            continue
        if used + len(body) > budget and kept:
            break
        who = str(item.get("speaker") or "")
        role = item["role"]
        if names and who and who != speaker and role == "assistant":
            role = "user"
            body = names.get(who, who) + "：" + body
        kept.append({"role": role, "content": body})
        used += len(body)
    kept.reverse()
    msgs.extend(kept)
    if char.post_history.strip():
        msgs.append({"role": "system", "content": char.post_history.strip()})
    return msgs


def summarize_prompt(char: Character, transcript: str, old_summary: str, settings: Settings) -> list[dict]:
    head = (
        "你是对话摘要器。把下面的聊天记录压缩成角色「" + char.name + "」的第一人称记忆要点。"
        "保留：双方关系进展、对方说过的关键事实（名字/喜好/约定/情绪）、已有摘要里仍然成立的信息。"
        "输出不超过 220 字的中文短句，一行一条，不要解释你在做什么，不要加标题。\n"
        "已有摘要（合并进去，删掉过期信息）：\n" + (old_summary.strip() or "（无）")
    )
    return [
        {"role": "system", "content": head},
        {"role": "user", "content": "聊天记录：\n" + transcript},
    ]
