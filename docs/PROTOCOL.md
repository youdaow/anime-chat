# 表情包标记协议 (sticker marker protocol)

模型输出的正文里夹两种带方括号的标记，服务端解析后**从文字里摘掉**，改成结构化事件推给前端。
用户永远看不到方括号本身。

## 语法

```text
[sticker:标签]     让角色发一张表情包
[emotion:情绪key]  声明这句话的情绪
```

- 冒号中英文都认（`：` 也行），方括号允许全角 `［表情：傲娇］`。
- 关键词认：`sticker / 贴纸 / 表情 / 表情包 / emoji` 与 `emotion / 情绪 / 心情`。
- 标签内容随便模型怎么写：id、中文名、情绪词、带标点全角都先做归一化再匹配。
- 一条回复最多 `max_stickers_per_reply` 张（默认 2），超出的标记静默丢弃。

## 情绪 key

| key | 界面显示 | 常见触发词 |
| --- | --- | --- |
| neutral | 平静 | 没有明显情绪时 |
| happy | 开心 | 哈哈、开心、太好了、笑死 |
| love | 喜欢 | 喜欢、爱你、心动、好棒 |
| tsundere | 傲娇 | 才不是、哼、别误会、笨蛋 |
| sad | 难过 | 难过、委屈、想哭、难受 |
| angry | 生气 | 生气、火大、气死、讨厌 |
| shock | 震惊 | 不会吧、什么、震惊、离谱 |
| awkward | 无语 | 无语、尴尬、服了、绝了 |
| sleepy | 困倦 | 困、睡觉、好累、哈欠 |
| think | 思考 | 想想、这个嘛、思考、有点深 |
| hype | 亢奋 | 冲、加油、燃、可以的 |

检测是**规则式**的（`src/animechat/emotion.py`），带否定词降权：`我才不开心` 不会被算成 happy。
规则不够用的时候换小模型分类器，接口 `detect(text) -> (key, confidence, scores)` 不用变。

## 选图顺序（StickerLibrary.resolve）

1. 精确 id；
2. 归一化标签命中打分：完全相等 3.0 / 前缀包含 2.0+长度奖励 / 互相包含 1.6 / 字符重叠最多 1.2 / 情绪名相等 1.8，再叠加 收藏 +0.35、被用过最多 +0.25、**非内置 +0.15**（平手时用户自己丢的图优先）；总分低于 0.55 视为没命中；
3. 把标签当情绪名认（`[sticker:傲娇]` 直接等于 `[emotion:tsundere]` 的选图请求）；
4. 只匹配该情绪的表情，按角色 `sticker_prefs` 加权（命中 +2.0、收藏 +0.5），用「会话 + 消息 + 情绪」做种子稳定随机（同一句话重生成得到同一张，重启后也一样）；
5. 一张都没有 → 返回空，前端就不显示图，绝不占位假图。

归一化 `norm()`：NFKC（全角转半角）→ 小写 → 去掉所有标点符号与空白。所以 `Tsundere` / `tsu-ndere` / ` 傲娇！` 会落到同一个键。

## 服务端兜底（auto_attach）

模型一个标记都没发时，用最终文本检测情绪并补图，条件：

- 角色的 `sticker_style != off` 且全局 `sticker_mode != off`；
- `light` 需要置信度 ≥ 0.45，`rich` 需要 ≥ 0.2；
- 总张数不超过配额（模型主动发的优先占额度）。

角色设成 `off` 就彻底只用文字——想对比效果时把这个开关拨一下，其他都不用动。

## 流式解析

模型是分 token 吐字的，标记会被切在任意位置：`[sti`cker:傲`娇]`。
`src/animechat/stickers.py` 的 `MarkerStream` 是个小状态机：

- 遇到配对的完整标记 → 解析、发事件、正文里抹掉；
- 遇到没闭合的 `[` → **扣留**尾巴不发给前端，等下一个 chunk 拼接（上限 80 字符，超过就当作普通文字放行）；
- 结尾的孤立 `[` 在 `flush()` 时放行；
- 认不出的标记（表情库没有对应图）整段丢弃，**不会**漏进正文；
- 中途点「停止」也走 `flush()`，已扣留的部分会落地成文字，消息不会半截消失。

SSE 事件（`POST /api/chat`，前端用 fetch + ReadableStream 手解）：

```text
event: start     data: {"conversation_id":12,"message_id":34}
event: meta      data: {"attempts":[{"model":"qwen3.7-plus","http_status":200}]}   // 只有对面回 hub.attempts 时才有
event: text      data: {"t":"哼，"}
event: sticker   data: {"id":"tsun","url":"/media/builtin/tsun.png","label":"傲娇","emotion":"tsundere","auto":false}
event: emotion   data: {"key":"tsundere","label":"傲娇"}
event: text      data: {"t":"才没有等你！"}
event: done      data: {"message_id":34,"content":"哼，才没有等你！","stickers":["tsun"],"emotion":"tsundere","model":"...","elapsed_ms":1830,"prompt_chars":2415}
event: error     data: {"message":"...","hint":"...","code":"invalid_api_key"}
```

落库的 `content` 是摘掉标记并 strip 过的正文，`stickers` 存表情 id，所以历史里能看到「它当时甩了哪张」。
重放历史时，表情会以文字形式回到提示词里（`[刚刚给对方发了表情包：傲娇]`），模型因此知道刚刚甩过图，不会重复刷屏。

## 让角色更会用图

- 表情标签词表会自动写进系统提示词（`prompt.sticker_vocab()`），模型看到的是真实存在的标签，不是凭空猜；
- 内置 30 张的标签见 `src/animechat/assets/stickers/manifest.json`，那是素材的唯一真源；
- 角色的「偏好表情」会加权，所以同一个情绪在不同角色手里甩出来的图不一样。

