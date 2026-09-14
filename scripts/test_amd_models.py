import json, urllib.request, time

with open('/opt/anime-chat/data/settings.json') as f:
    config = json.load(f)
api_key = config['llm_api_key']
base = 'https://developer.amd.com.cn/radeon/api/v1'

# AMD 平台全部候选模型
test_models = [
    'DeepSeek-V4-Flash',            # DeepSeek V4，最新旗舰
    'DeepSeek-V4-Flash-Vision-Exp', # V4 + 视觉
    'Qwen3.8-Flash-Next',           # 通义千问 3.8 + 视觉
    'MiniCPM5-2B',                  # 小模型
    'MinerU2.5-Pro',                # OCR 专用
]


def test_model(model_name):
    print('=== 测试 ' + model_name + ' ===')
    payload = {
        'model': model_name,
        'messages': [{'role': 'user', 'content': '你好，请用一句话介绍你自己'}],
        'max_tokens': 80,
    }
    req = urllib.request.Request(
        base + '/chat/completions',
        data=json.dumps(payload).encode(),
        method='POST',
        headers={'Authorization': 'Bearer ' + api_key,
                 'Content-Type': 'application/json'})
    start = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=40)
        data = json.loads(r.read().decode('utf-8'))
        elapsed = time.time() - start
        content = data['choices'][0]['message']['content']
        usage = data.get('usage', {})
        print('响应时间: %.1fs' % elapsed)
        print('回复: ' + content[:120])
        print('Tokens: ' + json.dumps(usage))
    except Exception as e:
        print('失败: ' + str(e))
    print()


for m in test_models:
    test_model(m)