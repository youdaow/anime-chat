"""内置假模型：不填任何 Key 也能把整条链路跑通。

它按角色的人设碎片 + 对用户消息的情绪判断拼一句回复，并正确产出 [sticker:] / [emotion:]
标记，所以表情流程、界面、存储、上下文都能在离线状态下验收。
它不是"更聪明的机器人"，界面里也明确标着 Mock。
"""

from __future__ import annotations

import hashlib
import random
import re

from .emotion import detect, label
from .models import Character

GREET = re.compile(r"^(你好|您好|嗨|哈喽|hello|hi|在吗|在不在|早上好|晚上好)", re.I)
BYE = re.compile(r"(晚安|睡了|再见|拜拜|下车了|下了)", re.I)
THANKS = re.compile(r"(谢谢|多谢|感谢|辛苦)", re.I)
QUESTION = re.compile(r"(为什么|怎么|怎样|是什么|能不能|可以吗|吗[?？]?$|[?？])")
SAD = re.compile(r"(难过|崩溃|压力|累了|失眠|难受|哭|烦|焦虑|不想)", re.I)


def _rng(*seeds: str) -> random.Random:
    h = hashlib.sha1("||".join(seeds).encode("utf-8")).hexdigest()
    return random.Random(int(h[:12], 16))


def _core(text: str, limit: int = 18) -> str:
    """从用户消息里抓一个可作为话题中心的片段。"""
    cleaned = re.sub(r"\[[^\]]*\]", "", text or "")
    cleaned = re.sub(r"[，。！？、,.!?\s~…]+", " ", cleaned).strip()
    parts = [p for p in cleaned.split(" ") if len(p) >= 2]
    if not parts:
        return ""
    longest = max(parts, key=len)
    return longest[:limit]


def reply(char: Character, user_text: str, prefs: list[str]) -> str:
    rng = _rng(char.id, user_text)
    emo, conf, _ = detect(user_text)
    lines: list[str] = []

    catch = rng.choice(char.catchphrases) if char.catchphrases else ""
    topic = _core(user_text)
    likes = rng.choice(char.likes) if char.likes else ""
    dislikes = rng.choice(char.dislikes) if char.dislikes else ""

    if GREET.match(user_text.strip()):
        lines.append(rng.choice([
            "来啦来啦，我正找你。",
            "哼，这个点才来。",
            "你好呀，今天过得怎么样？",
            "吾之感官正等着你的一句话。",
        ]) if not catch else catch + "——所以，你来了。")
        chosen_emo = "happy"
    elif BYE.search(user_text):
        lines.append(rng.choice([
            "去吧，别熬太晚。", "晚安，明天记得来找我！", "睡吧，这里我守着。",
        ]) + ("（" + (likes or "明天") + "的事，我们改天再说）" if rng.random() < 0.4 else ""))
        chosen_emo = "sleepy"
    elif THANKS.search(user_text):
        lines.append(rng.choice([
            "谢什么，顺手的事。", "不、不用谢！我只是恰好有余力。", "该谢的是你自己，你去做了。",
        ]))
        chosen_emo = "tsundere" if char.sticker_style != "off" else "happy"
    elif SAD.search(user_text) or emo == "sad":
        anchor = ("我在。" if rng.random() < 0.5 else "先别急着解释，跟我说最难受的那一下。")
        extra = ("要是连话都不想说，我就安静陪着。" if rng.random() < 0.5 else "你已经撑过很多次了，这次也会。")
        lines.append(anchor + " " + extra)
        chosen_emo = "love"
    elif QUESTION.search(user_text):
        head = catch or "让我先想三秒。"
        body = ("关于「" + topic + "」，我的判断是：先做最小的那一步，剩下的会自己长出来。" if topic
                else "这题我不能瞎猜，你说具体一点。")
        lines.append(head + " " + body)
        chosen_emo = "think"
    else:
        opener = catch or rng.choice(["嗯？", "哦？", "诶诶？", "收到。"])
        if topic:
            mid = rng.choice([
                "「" + topic + "」这件事，我记住了。",
                "你一说" + topic + "，我就想起" + (likes or "上次那件事") + "。",
                "别再" + topic + "了，行不行？……开玩笑的，继续说。",
                "关于" + topic + "，我站在你这边，不用理由。",
            ])
        else:
            mid = rng.choice(["再多说一点嘛。", "嗯，我在听。", "所以呢所以呢？"])
        tail = rng.choice([
            "下次遇到这种事，先告诉我。",
            "不许自己扛。",
            "（小声）其实我挺在意你说的这些。",
            "",
        ])
        lines.append((opener + " " + mid + " " + tail).strip())
        chosen_emo = emo if conf > 0.4 else rng.choice(["happy", "neutral", "think"])

    out = "\n".join(lines)
    if char.sticker_style != "off":
        want = 2 if char.sticker_style == "rich" and conf > 0.6 else 1
        pool = prefs or []
        if pool and rng.random() < 0.65:
            mark = rng.choice(pool)
        else:
            mark = label(chosen_emo) if chosen_emo != "neutral" else ""
        if mark:
            out += "\n[sticker:" + mark + "]"
        out += "[emotion:" + chosen_emo + "]"
    else:
        out += "[emotion:" + chosen_emo + "]"
    return out
