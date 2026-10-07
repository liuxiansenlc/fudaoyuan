# -*- coding: utf-8 -*-
"""
受限规则 DSL 与求值器。

背景：不同奖学金、不同学院的口径常常是"一句话规则"——
    "省级竞赛不认院级证书"
    "国家级竞赛证书必须能核对到获奖人姓名"
    "名单公示类的必须能在名单里查到申报人"
把这些做成界面上的自然语言输入很好，但**运行期绝不能让大模型参与判定**，
否则同一份材料跑两次可能得出不同结论，也没法复现、没法解释。

所以分两步：
    编译期（在 Web 层，允许调模型）：中文 → 本文档定义的 DSL（JSON）
    运行期（在这里，纯函数）：      DSL × 材料事实 → 效果列表

求值器保证：**零网络、零随机、零时间依赖**；规则按 (priority desc, id asc)
排序，结果可复现；每条命中的规则都会输出"改了什么"，界面可以展开看。

DSL 形状：
    {
      "id": "r_ab12",
      "name": "省级竞赛不认院级证书",
      "priority": 100,                     # 越大越先应用
      "enabled": true,
      "scope": {"sections": ["竞赛"], "levels": ["省级"]},   # 可选，空=不限
      "when": [{"field": "img.level", "op": "eq", "value": "院级"}],
      "then": [{"action": "penalty", "value": -50},
               {"action": "status",  "value": "amber"},
               {"action": "note",    "value": "省级竞赛不接受院级证书"}]
    }
"""
import re

# ------------------------------------------------------------------ 白名单
SECTIONS = ['奖学金', '竞赛', '科研', '荣誉', '体测', '志愿',
            '技能', '四六级', '实践', '学生干部任职经历', '其他', '未归类']
LEVELS = ['国家级', '省级', '市级', '校级', '院级']
KINDS = ['certificate', 'roster', 'summary_table', 'transcript',
         'score_sheet', 'form', 'other']
SEMANTICS = ['scholarship', 'competition', 'research', 'honor', 'skill',
             'physical', 'volunteer', 'level_exam', 'unknown']
PERSON_STATUS = ['ok', 'no_name', 'name_conflict', 'roster_in', 'roster_missing', 'none']
STATUSES = ['green', 'blue', 'amber', 'pending_weak', 'red', 'red_flag']

FIELDS = {
    'item.section':      {'type': 'enum', 'values': SECTIONS, 'label': '条目所属板块'},
    'item.level':        {'type': 'enum', 'values': LEVELS + [''], 'label': '学生申报的级别'},
    'item.comp_std':     {'type': 'text', 'label': '归一后的赛事名'},
    'item.text':         {'type': 'text', 'label': '条目原文'},
    'item.year':         {'type': 'text', 'label': '条目里的年份'},
    'img.kind':          {'type': 'enum', 'values': KINDS, 'label': '证据形式'},
    'img.semantic':      {'type': 'enum', 'values': SEMANTICS, 'label': '材料语义类别'},
    'img.level':         {'type': 'enum', 'values': LEVELS + [''], 'label': '材料落款判定的级别'},
    'img.award_level':   {'type': 'text', 'label': '证书上的奖项等级'},
    'img.award_name':    {'type': 'text', 'label': '证书上的奖项名'},
    'img.year':          {'type': 'text', 'label': '证书上的年份'},
    'img.person_status': {'type': 'enum', 'values': PERSON_STATUS, 'label': '申报人姓名核对结果'},
    'img.is_team':       {'type': 'bool', 'label': '是否团队获奖'},
    'img.metric.*':      {'type': 'metric', 'label': '材料上的量化指标（如 img.metric.总分）'},
    'pair.score':        {'type': 'number', 'label': '当前配对得分'},
}

OPS = ['eq', 'ne', 'in', 'not_in', 'contains', 'not_contains',
       'gte', 'lte', 'gt', 'lt', 'exists', 'missing']

ACTIONS = {
    'penalty':        {'type': 'number',  'label': '扣分（填正数，内部按负分算）'},
    'bonus':          {'type': 'number',  'label': '加分'},
    'status':         {'type': 'enum', 'values': STATUSES, 'label': '强制判定状态'},
    'note':           {'type': 'text',    'label': '给人工看的一句说明'},
    'require_person': {'type': 'bool',    'label': '要求必须能核对到本人姓名'},
}

MAX_RULES = 200
MAX_CONDITIONS = 8
MAX_ACTIONS = 5


# ------------------------------------------------------------------ 上下文
def _g(d, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


# 文本里出现这些词就认为申报的是对应级别（按"从高到低"的顺序取第一个命中）
_LEVEL_HINTS = [
    ('国家级', ['国家级', '国家', '全国', '国赛', '国际', '教育部', '共青团中央']),
    ('省级', ['省级', '省政府', '省教育厅', '省大学生', '全省', '省赛', '浙江省', '赛区', '华东']),
    ('市级', ['市级', '全市', '湖州市']),
    ('院级', ['院级', '学院']),
    ('校级', ['校级', '全校', '湖州师范学院', '湖州师范大学']),
]


def scan_level(text):
    t = (text or '').replace(' ', '')
    if not t:
        return ''
    for lvl, words in _LEVEL_HINTS:
        if any(w in t for w in words):
            return lvl
    return ''


def build_facts(item, img, score=None, status=None):
    """
    把「条目 + 图片 + 得分」摊平成扁平事实表，键与 FIELDS 一一对应。
    取值一律容错：缺失就是空串/None，绝不抛异常。
    """
    isig = (item or {}).get('sig') or {}
    imsig = (img or {}).get('sig') or {}
    cert = (img or {}).get('cert') or {}
    reading = (img or {}).get('reading') or {}
    metrics = {}
    for src in (reading.get('metrics'), (img or {}).get('metrics')):
        if isinstance(src, dict):
            metrics.update(src)

    cand = set()
    for x in (isig.get('levels'), (item or {}).get('level')):
        if isinstance(x, (list, tuple, set)):
            cand |= {str(y) for y in x}
        elif x:
            cand.add(str(x))
    # ★ 必须按固定的级别顺序取，**不能直接遍历 set**：
    #   Python 的字符串哈希每个进程都加盐，set 迭代顺序会变，
    #   同一份材料跑两次可能得出不同的 item.level —— 那就破坏了"结果可复现"。
    item_level = next((lv for lv in LEVELS if lv in cand), '')
    if not item_level:
        # 兜底：学生常把级别直接写在文字里（"…项目省级立项"、"全国总决赛"）
        item_level = scan_level((item or {}).get('text') or '')

    return {
        'item.section': (item or {}).get('section') or '',
        'item.level': item_level,
        'item.comp_std': (isig.get('comp_std') or imsig.get('comp_std') or '') or '',
        'item.text': (item or {}).get('text') or '',
        'item.year': ','.join(str(y) for y in (isig.get('years') or [])) or '',
        'img.kind': (img or {}).get('kind') or '',
        'img.semantic': (img or {}).get('semantic') or '',
        'img.level': (img or {}).get('vlevel') or imsig.get('level') or '',
        'img.award_level': (img or {}).get('award_level') or cert.get('award_level') or '',
        'img.award_name': imsig.get('award_name') or '',
        'img.year': ','.join(str(y) for y in (imsig.get('years') or [])) or '',
        'img.person_status': (img or {}).get('person_status') or 'none',
        'img.is_team': bool((img or {}).get('is_team') or cert.get('is_team')),
        'pair.score': score,
        '_metrics': metrics,
    }


def merge_facts(*parts):
    """
    合并多份事实表，**只取非空值**。

    为什么需要它：build_facts(item, None) 会把 img.* 键填成空串，
    build_facts(None, img) 会把 item.* 键填成空串。
    如果用 dict.update() 直接合，后一份的"空串"会把前一份的真实值覆盖掉
    （踩过：条目的 section 被图片 facts 里的空串盖成 ''，规则 scope 永远匹配不上）。
    """
    out = {}
    for p in parts:
        for k, v in (p or {}).items():
            if v in (None, '', [], {}):
                continue
            out[k] = v
        m = (p or {}).get('_metrics')
        if m:
            out.setdefault('_metrics', {}).update(m)
    return out


# ------------------------------------------------------------------ 校验
def validate(dsl):
    """返回 (ok, errors)。任何越界（发明字段/操作符/动作）都会被拒绝。"""
    errs = []
    if not isinstance(dsl, dict):
        return False, ['规则必须是一个 JSON 对象']
    if not (dsl.get('name') or dsl.get('nl')):
        errs.append('缺少 name（规则名）')

    pr = dsl.get('priority', 100)
    if not isinstance(pr, int) or not (0 <= pr <= 999):
        errs.append('priority 必须是 0~999 的整数')

    scope = dsl.get('scope') or {}
    if not isinstance(scope, dict):
        errs.append('scope 必须是对象')
    else:
        for key, allowed in (('sections', SECTIONS), ('levels', LEVELS)):
            v = scope.get(key)
            if v in (None, [], ''):
                continue
            if not isinstance(v, list):
                errs.append('scope.%s 必须是数组' % key)
                continue
            for x in v:
                if x not in allowed:
                    errs.append('scope.%s 里的 "%s" 不是允许的值' % (key, x))

    when = dsl.get('when')
    if not isinstance(when, list) or not when:
        errs.append('when 至少要有一个条件')
    elif len(when) > MAX_CONDITIONS:
        errs.append('when 最多 %d 个条件' % MAX_CONDITIONS)
    else:
        for i, c in enumerate(when):
            if not isinstance(c, dict):
                errs.append('when[%d] 必须是对象' % i)
                continue
            f = c.get('field')
            op = c.get('op')
            if not _field_ok(f):
                errs.append('when[%d].field "%s" 不在允许的字段里' % (i, f))
            if op not in OPS:
                errs.append('when[%d].op "%s" 不是允许的操作符' % (i, op))
            if op in ('exists', 'missing'):
                continue
            if 'value' not in c:
                errs.append('when[%d] 缺少 value' % i)
                continue
            if op in ('in', 'not_in') and not isinstance(c['value'], list):
                errs.append('when[%d] 用 %s 时 value 必须是数组' % (i, op))
            if op in ('gte', 'lte', 'gt', 'lt'):
                fld = FIELDS.get(f) or FIELDS.get('img.metric.*')
                if not _is_number(c['value']):
                    errs.append('when[%d] 用 %s 时 value 必须是数字' % (i, op))

    then = dsl.get('then')
    if not isinstance(then, list) or not then:
        errs.append('then 至少要有一个动作')
    elif len(then) > MAX_ACTIONS:
        errs.append('then 最多 %d 个动作' % MAX_ACTIONS)
    else:
        for i, a in enumerate(then):
            if not isinstance(a, dict):
                errs.append('then[%d] 必须是对象' % i)
                continue
            act = a.get('action')
            spec = ACTIONS.get(act)
            if not spec:
                errs.append('then[%d].action "%s" 不是允许的动作' % (i, act))
                continue
            if act in ('penalty', 'bonus'):
                if not _is_number(a.get('value')):
                    errs.append('then[%d] %s 的 value 必须是数字' % (i, act))
            elif act == 'status':
                if a.get('value') not in STATUSES:
                    errs.append('then[%d] status 的 value 必须是 %s 之一'
                                % (i, '/'.join(STATUSES)))
            elif act in ('note',):
                if not isinstance(a.get('value'), str) or not a['value'].strip():
                    errs.append('then[%d] note 的 value 必须是非空字符串' % i)
    return (not errs), errs


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _field_ok(f):
    if not isinstance(f, str):
        return False
    if f in FIELDS:
        return True
    if f.startswith('img.metric.') and len(f) > len('img.metric.'):
        return True
    return False


# ------------------------------------------------------------------ 求值
def _cmp(a, b):
    """统一比较：数字按数值，其余按字符串。"""
    if _is_number(a) and _is_number(b):
        return a, b
    if _is_number(a) or _is_number(b):
        try:
            if _is_number(a):
                return float(a), float(b)
            return float(a), float(b)
        except Exception:
            pass
    return str(a if a is not None else ''), str(b if b is not None else '')


def _match_one(facts, cond):
    f = cond.get('field')
    op = cond.get('op')
    val = facts.get(f)
    if f and f.startswith('img.metric.'):
        val = (facts.get('_metrics') or {}).get(f[len('img.metric.'):])
    if op == 'exists':
        return val not in (None, '', [], {})
    if op == 'missing':
        return val in (None, '', [], {})
    if val is None:
        val = ''

    if op == 'in':
        return val in (cond.get('value') or [])
    if op == 'not_in':
        return val not in (cond.get('value') or [])
    if op == 'eq':
        a, b = _cmp(val, cond.get('value'))
        return a == b
    if op == 'ne':
        a, b = _cmp(val, cond.get('value'))
        return a != b
    if op == 'contains':
        return str(cond.get('value') or '') in str(val)
    if op == 'not_contains':
        return str(cond.get('value') or '') not in str(val)
    if op in ('gte', 'lte', 'gt', 'lt'):
        a, b = _cmp(val, cond.get('value'))
        try:
            if isinstance(a, str):
                return False
            return {'gte': a >= b, 'lte': a <= b, 'gt': a > b, 'lt': a < b}[op]
        except Exception:
            return False
    return False


def _scope_ok(scope, facts):
    if not scope:
        return True
    secs = scope.get('sections') or []
    if secs and facts.get('item.section') not in secs:
        return False
    lvls = scope.get('levels') or []
    if lvls and facts.get('item.level') not in lvls and facts.get('img.level') not in lvls:
        return False
    return True


def sort_rules(rules):
    """确定性排序：优先级高的先应用；同优先级按 id 字典序。"""
    out = [r for r in (rules or []) if isinstance(r, dict) and r.get('enabled', True)]
    return sorted(out, key=lambda r: (-int(r.get('priority', 100) or 0), str(r.get('id') or '')))


def evaluate(item, img, rules, score=None):
    """
    纯函数求值（从原始条目/图片对象出发）。
    返回：
      {'score_delta': int, 'set_status': str|None, 'notes': [...],
       'require_person': bool, 'matched': [{'id','name','effects':[...]}]}
    """
    return evaluate_facts(build_facts(item, img, score=score), rules)


def evaluate_facts(facts, rules):
    """
    纯函数求值（从**已摊平的事实表**出发）。

    为什么要两个入口：真实流水线里能拿到 item/img 原始对象，
    而"规则影响预览"只能读已经落盘的 result.json。
    两条路必须走同一个求值器，否则会出现"预览说没影响、实际有影响"。
    """
    eff = {'score_delta': 0, 'set_status': None, 'notes': [],
           'require_person': False, 'matched': []}
    if not rules:
        return eff
    facts = facts or {}
    for r in sort_rules(rules):
        if not _scope_ok(r.get('scope'), facts):
            continue
        conds = r.get('when') or []
        if not conds or not all(_match_one(facts, c) for c in conds):
            continue
        applied = []
        for a in (r.get('then') or []):
            act, val = a.get('action'), a.get('value')
            if act == 'penalty' and _is_number(val):
                eff['score_delta'] -= abs(val)
                applied.append('扣 %g 分' % abs(val))
            elif act == 'bonus' and _is_number(val):
                eff['score_delta'] += abs(val)
                applied.append('加 %g 分' % abs(val))
            elif act == 'status' and val in STATUSES:
                if eff['set_status'] is None:
                    eff['set_status'] = val
                applied.append('判定为 %s' % val)
            elif act == 'note' and isinstance(val, str) and val.strip():
                eff['notes'].append(val.strip())
                applied.append('说明：%s' % val.strip())
            elif act == 'require_person':
                eff['require_person'] = bool(val)
                if val:
                    applied.append('要求核对到本人姓名')
        eff['matched'].append({'id': r.get('id'), 'name': r.get('name') or r.get('nl') or '',
                               'effects': applied})
    return eff


def summarize_matched(matched):
    """给界面/理由列表用的一行摘要。"""
    out = []
    for m in (matched or []):
        nm = m.get('name') or m.get('id') or '规则'
        out.append('规则「%s」：%s' % (nm, '、'.join(m.get('effects') or [])))
    return out
