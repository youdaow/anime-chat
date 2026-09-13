#!/bin/bash
# 配置 AMD DeepSeek（或其他 OpenAI 兼容服务）到 animechat。
#
# 为什么只写 settings.json、不碰 systemd：
#   animechat 的配置优先级是「环境变量 > settings.json」。把 Key 写进 systemd 的
#   Environment= 会**覆盖** settings.json，而且明文躺在系统单元文件里、任何能
#   systemctl show 的人都读得到。所以密钥一律只落 settings.json。
#
# 用法： ssh <你的服务器>; cd /opt/anime-chat; bash config_amd.sh
set -e

DATA_DIR="${ANIMECHAT_DATA_DIR:-/opt/anime-chat/data}"
BASE_URL="https://developer.amd.com.cn/radeon/api/v1"
# AMD 开发者平台当前的免费模型；换模型只改这一行（或跑完去网页「设置」里改）。
MODEL="DeepSeek-V4-Flash"

echo "🔧 配置 AMD DeepSeek  ->  $DATA_DIR/settings.json"
# -s 不回显，粘贴 Key 时不会留在终端历史
read -rsp "请输入 AMD API Key（粘贴后回车）: " API_KEY; echo
if [ -z "$API_KEY" ]; then echo "❌ Key 为空，已取消"; exit 1; fi

python3 - "$DATA_DIR" "$BASE_URL" "$MODEL" "$API_KEY" <<'PY'
import json, os, sys
data_dir, base_url, model, key = sys.argv[1:5]
path = os.path.join(data_dir, "settings.json")
cur = {}
if os.path.isfile(path):
    try:
        cur = json.load(open(path, encoding="utf-8"))
    except ValueError:
        cur = {}
cur.update({"llm_base_url": base_url, "llm_api_key": key, "llm_model": model})
json.dump(cur, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
os.chmod(path, 0o600)          # 只有 root 可读，别把密钥留给同机其他用户
print("✅ 已写入", path)
PY

echo "🔄 重启服务..."
systemctl restart animechat
sleep 3
systemctl is-active animechat >/dev/null && echo "🟢 服务在跑" || { echo "❌ 服务没起来，看日志： journalctl -u animechat -n 30"; exit 1; }
echo "🎉 完成。现在访问本站的人都会共用这把 Key。"