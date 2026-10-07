# -*- coding: utf-8 -*-
"""
视觉大模型客户端（OpenAI 兼容接口）。

为什么放在 Web 层而不是引擎里：
    引擎要保持"离线、确定性、可单测"。只要联网的东西全部集中在这里，
    引擎就永远只读 cache/vision/<sha256>.json，跑起来结果可复现。

三条铁律：
1. **绝不把半成品写进缓存。** 只有通过校验的结果才 put，否则记 failed 让人重试；
   否则引擎会拿到一个字段残缺的"读图结果"，反而比没读更危险。
2. **非法 JSON 要带着错误信息重试一次**，而不是直接丢掉。
3. **成本要能被闸住**：命中 sha 缓存直接跳过；送模型前等比缩到长边 1600。
"""
import os
import io
import json
import time
import base64
import threading

import requests

from ..config import WebConfig
from ..security import decrypt_secret, record_usage, usage_today
from . import engine_adapter as EA

_lock = threading.Lock()

DOC_KINDS = {'certificate', 'roster', 'summary_table', 'transcript',
             'score_sheet', 'form', 'other'}
LEVELS = {'国家级', '省级', '校级', '院级'}

SYSTEM_PROMPT = """你是一位严谨的奖学金申报材料审核员，专门从证书、奖状、公示名单、成绩单、汇总表截图里提取结构化信息。

要求：
1. 只输出一个 JSON 对象，不要任何解释文字、不要 markdown 代码块。
2. 看不清或不存在的信息填 null，**绝不猜测**。
3. 人名列表只写图上真实出现的人名，不要联想。
4. 若图上有身份证号、手机号等个人敏感信息，**不要输出这些号码本身**。
5. note 字段用于写"需要人工注意的地方"（例如：截图未显示姓名无法核对身份、落款单位模糊、该字段为手写）。

字段含义：
{schema}

级别（level）判定口径：
- 落款是教育部/共青团中央/全国性组织，或明确写"全国" → 国家级
- 落款是浙江省教育厅/省级组织，或写"浙江赛区" → 省级
- 落款是"湖州师范学院" → 校级；落款是某个二级学院（如信息工程学院）→ 院级
"""

USER_PROMPT = """请阅读这张图片，按上述字段输出 JSON。
提示：这份材料是学生「{student}」的奖学金申报支撑材料。请特别确认：图上是否出现了申报人本人的姓名（写进 persons），以及奖项名称、等级、年份、落款单位。"""


class VisionError(Exception):
    pass


# ------------------------------------------------------------------ 图片预处理
def prepare_image(data, max_side=None):
    """等比缩放到长边 max_side，输出 JPEG 字节。失败则原样返回。"""
    max_side = max_side or WebConfig.VISION_MAX_SIDE
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        if im.mode in ('RGBA', 'P', 'LA'):
            bg = Image.new('RGB', im.size, (255, 255, 255))
            im = im.convert('RGBA')
            bg.paste(im, mask=im.split()[-1])
            im = bg
        elif im.mode != 'RGB':
            im = im.convert('RGB')
        w, h = im.size
        if max(w, h) > max_side:
            k = max_side / float(max(w, h))
            im = im.resize((max(1, int(w * k)), max(1, int(h * k))), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=88)
        return buf.getvalue(), 'image/jpeg'
    except Exception:
        return data, 'image/png'


# ------------------------------------------------------------------ 模型配置
def active_model():
    from .. import db
    row = db.q_dict('SELECT * FROM model_configs WHERE is_active=1 ORDER BY id DESC LIMIT 1',
                    one=True)
    if not row:
        return None
    row = dict(row)
    row['api_key'] = decrypt_secret(row.get('api_key_enc'))
    try:
        row['extra_obj'] = json.loads(row.get('extra') or '{}')
    except Exception:
        row['extra_obj'] = {}
    return row


# ------------------------------------------------------------------ 校验
def _normalize(obj):
    """把模型返回的东西收拾成引擎认识的形状。越界一律置空，不猜。"""
    out = {}
    kind = str(obj.get('doc_kind') or '').strip().lower()
    out['doc_kind'] = kind if kind in DOC_KINDS else 'other'

    for k in ('title', 'award_name', 'award_level', 'issuer', 'note'):
        v = obj.get(k)
        out[k] = str(v).strip() if v not in (None, '', 'null') else None

    yr = obj.get('academic_year')
    out['academic_year'] = str(yr).strip() if yr not in (None, '', 'null') else None

    lv = str(obj.get('level') or '').strip()
    out['level'] = lv if lv in LEVELS else None

    persons = obj.get('persons')
    if isinstance(persons, str):
        persons = [p for p in persons.replace('、', ',').replace('，', ',').split(',')]
    if not isinstance(persons, list):
        persons = []
    seen, pl = set(), []
    for p in persons:
        p = str(p).strip()
        if p and p not in seen and p.lower() != 'null':
            seen.add(p)
            pl.append(p)
    out['persons'] = pl

    pir = obj.get('person_in_roster')
    out['person_in_roster'] = pir if isinstance(pir, bool) else None

    out['is_team'] = bool(obj.get('is_team')) if isinstance(obj.get('is_team'), bool) else None
    ts = obj.get('team_size')
    try:
        out['team_size'] = int(ts) if ts not in (None, '', 'null') else None
    except Exception:
        out['team_size'] = None

    m = obj.get('metrics')
    if isinstance(m, dict):
        clean = {}
        for k, v in m.items():
            k = str(k).strip()
            if not k:
                continue
            if isinstance(v, (int, float)):
                clean[k] = v
            else:
                sv = str(v).strip()
                try:
                    clean[k] = int(sv) if sv.isdigit() else float(sv)
                except Exception:
                    clean[k] = sv
        out['metrics'] = clean
    else:
        out['metrics'] = {}

    # 敏感字段即便模型给出了也丢弃
    for bad in ('id_card', 'id_number', 'phone', 'mobile', 'bank_card'):
        out.pop(bad, None)
    return out


def _json_from_text(text):
    """模型偶尔会用 ```json 包起来，或者前后带解释，这里尽力抠出来。"""
    t = (text or '').strip()
    if not t:
        raise VisionError('模型返回为空')
    if t.startswith('```'):
        t = t.split('```')[1] if len(t.split('```')) > 1 else t
        if t.lower().startswith('json'):
            t = t[4:]
        t = t.strip()
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find('{'), t.rfind('}')
    if 0 <= i < j:
        return json.loads(t[i:j + 1])
    raise VisionError('返回内容不是合法 JSON')


# ------------------------------------------------------------------ 调用
def _call(model_row, image_bytes, student, correction=None, timeout=120):
    url = model_row['base_url'].rstrip('/') + '/chat/completions'
    data, mime = prepare_image(image_bytes)
    b64 = base64.b64encode(data).decode('ascii')

    schema_txt = json.dumps(EA._mods()[3].SCHEMA, ensure_ascii=False, indent=1)
    system = SYSTEM_PROMPT.replace('{schema}', schema_txt)
    user_text = USER_PROMPT.replace('{student}', student or '（未知）')
    if correction:
        user_text += ('\n\n上一次你的输出无法解析，错误是：%s\n'
                      '请严格只输出一个合法 JSON 对象，不要任何多余文字。' % correction)

    body = {
        'model': model_row['model'],
        'temperature': 0,
        'messages': [
            {'role': 'system', 'content': system},
            {'role': 'user', 'content': [
                {'type': 'text', 'text': user_text},
                {'type': 'image_url',
                 'image_url': {'url': 'data:%s;base64,%s' % (mime, b64)}},
            ]},
        ],
    }
    use_json_mode = model_row.get('extra_obj', {}).get('json_mode', True)
    if use_json_mode:
        body['response_format'] = {'type': 'json_object'}

    headers = {'Authorization': 'Bearer %s' % model_row['api_key'],
               'Content-Type': 'application/json'}

    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
                continue
            raise VisionError('请求失败：%s' % e)
        if resp.status_code == 400 and use_json_mode:
            # 有些兼容接口不认 response_format，降级重试
            body.pop('response_format', None)
            use_json_mode = False
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(2.0)
            continue
        if resp.status_code != 200:
            raise VisionError('接口返回 %s：%s' % (resp.status_code, resp.text[:200]))
        try:
            payload = resp.json()
            content = payload['choices'][0]['message']['content']
        except Exception as e:
            raise VisionError('响应结构异常：%s' % e)
        return content
    raise VisionError('重试后仍失败')


def read_image(model_row, image_bytes, student=''):
    """
    读一张图 → 结构化 dict（已归一）。
    返回 (obj, error)：成功时 error 为 None；失败时 obj 为 None。
    """
    correction = None
    for _ in range(2):
        try:
            content = _call(model_row, image_bytes, student, correction=correction)
        except VisionError as e:
            return None, str(e)
        try:
            raw = _json_from_text(content)
            obj = _normalize(raw)
            if not obj.get('doc_kind'):
                raise VisionError('缺少 doc_kind')
            return obj, None
        except Exception as e:
            correction = str(e)
            continue
    return None, '模型两次返回都不合规：%s' % correction


def write_cache(sha256, obj):
    """通过校验的结果才写缓存 —— 这是"引擎读到的都是可信结果"的保证。"""
    _, _, _, vision, _ = EA._mods()
    with _lock:
        return vision.put(sha256, obj,
                          cache_dir=os.path.join(WebConfig.ENGINE_CACHE_DIR, 'vision'))


def has_cache(sha256):
    _, _, _, vision, _ = EA._mods()
    return bool(vision.get(sha256,
                           cache_dir=os.path.join(WebConfig.ENGINE_CACHE_DIR, 'vision')))


def check_quota():
    calls, images = usage_today()
    left = int(WebConfig.VISION_DAILY_LIMIT) - images
    return left, calls, images


def ping(model_row=None):
    """连通性测试：让模型回一个固定的 JSON。"""
    model_row = model_row or active_model()
    if not model_row:
        return False, '尚未配置模型'
    url = model_row['base_url'].rstrip('/') + '/chat/completions'
    headers = {'Authorization': 'Bearer %s' % model_row['api_key'],
               'Content-Type': 'application/json'}
    body = {'model': model_row['model'], 'temperature': 0,
            'messages': [{'role': 'user',
                          'content': '只输出这个 JSON：{"ok":true}'}]}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=30)
    except Exception as e:
        return False, '请求失败：%s' % e
    if r.status_code != 200:
        return False, 'HTTP %s：%s' % (r.status_code, r.text[:200])
    try:
        txt = r.json()['choices'][0]['message']['content']
    except Exception:
        return False, '响应结构异常'
    return True, '连通正常：%s' % txt.strip()[:60]


# ------------------------------------------------------------------ 纯文本调用
# 规则编译只需要文本能力，不需要视觉。复用同一份模型配置（base_url / api_key），
# 这样"界面上只配一次模型"。
def chat_text(model_row, system, user, timeout=90, json_mode=True):
    """
    纯文本对话。返回 (text, error)；成功时 error 为 None。
    """
    if not model_row:
        return None, '尚未配置模型'
    url = model_row['base_url'].rstrip('/') + '/chat/completions'
    body = {
        'model': model_row['model'],
        'temperature': 0,
        'messages': [{'role': 'system', 'content': system},
                     {'role': 'user', 'content': user}],
    }
    if json_mode and (model_row.get('extra_obj') or {}).get('json_mode', True):
        body['response_format'] = {'type': 'json_object'}
    headers = {'Authorization': 'Bearer %s' % model_row['api_key'],
               'Content-Type': 'application/json'}
    for attempt in range(2):
        try:
            resp = requests.post(url, headers=headers, json=body, timeout=timeout)
        except Exception as e:
            if attempt == 0:
                time.sleep(1.5)
                continue
            return None, '请求失败：%s' % e
        if resp.status_code == 400 and 'response_format' in body:
            body.pop('response_format', None)
            continue
        if resp.status_code in (429, 500, 502, 503, 504) and attempt == 0:
            time.sleep(2.0)
            continue
        if resp.status_code != 200:
            return None, '接口返回 %s：%s' % (resp.status_code, resp.text[:200])
        try:
            return resp.json()['choices'][0]['message']['content'], None
        except Exception as e:
            return None, '响应结构异常：%s' % e
    return None, '重试后仍失败'


def json_from_text(text):
    """从模型输出里抠出 JSON（对外暴露，规则编译要用）。"""
    return _json_from_text(text)
