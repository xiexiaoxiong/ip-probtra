import os
import json
from pathlib import Path
from urllib import request, error

env = {}
for line in Path('/Users/xiexiaoxiong/Documents/patent/IP-protral/.env.local').read_text().splitlines():
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    k, v = line.split('=', 1)
    env[k.strip()] = v.strip().strip('"').strip("'")

coze_url = env.get('COZE_SEARCH_API_URL', 'https://66vpykvvz2.coze.site/run')
coze_token = env.get('COZE_SEARCH_API_TOKEN', '')

cases = [
    ('direct-coze-generic', coze_url, {
        'Authorization': f'Bearer {coze_token}',
        'Content-Type': 'application/json',
    }, {'keywords': ['玩具水枪']}),
    ('local-direct-search-generic', 'http://127.0.0.1:5105/api/direct_search', {
        'Content-Type': 'application/json',
    }, {'keywords': ['玩具水枪']}),
    ('local-run-current-session', 'http://127.0.0.1:5105/run', {
        'Content-Type': 'application/json',
    }, {
        'patent_record_id': 64,
        'analysis_session_id': 'analysis_1781104362260_g2r02c_after_coze_token',
        'input_keywords': [
            '斯皮拉同款玩具水枪',
            '管壁集成支承水枪',
            '线性导向水枪',
            '动密封水枪',
            '阀杆贯穿水枪',
            '水弹水枪',
            '高射程水枪',
            '一体式支承水枪',
            '低阻水枪'
        ]
    }),
    ('local-run-historical-control', 'http://127.0.0.1:5105/run', {
        'Content-Type': 'application/json',
    }, {
        'patent_record_id': 63,
        'analysis_session_id': 'analysis_1779810821813_ymhzv2_after_coze_token',
        'input_keywords': [
            '斯皮拉同款玩具水枪',
            '穿壁密封水弹枪',
            '阀杆穿壁水弹枪',
            '线性导向水弹枪',
            '动密封水弹枪',
            '快喷水弹枪',
            '外置驱动水弹枪',
            '低阻水弹枪',
            '一体支承水弹枪'
        ]
    }),
]

def post(url, headers, payload):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = request.Request(url, data=data, headers=headers, method='POST')
    with request.urlopen(req, timeout=180) as resp:
        return resp.status, resp.read().decode('utf-8', errors='ignore')

for name, url, headers, payload in cases:
    print(f'\n### {name} ###')
    try:
        status, text = post(url, headers, payload)
        print('status:', status)
        print(text[:4000])
    except error.HTTPError as e:
        print('status:', e.code)
        print(e.read().decode('utf-8', errors='ignore')[:4000])
    except Exception as e:
        print('error:', repr(e))
