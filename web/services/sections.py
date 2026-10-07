# -*- coding: utf-8 -*-
"""
板块目录与板块方案。

背景：不同奖学金的申报材料板块不一样。
    国家奖学金是模板里的六大板块（奖学金/竞赛/科研/荣誉/体测/志愿）；
    校级奖学金往往不看科研；单项奖学金只看竞赛/科研/荣誉。
如果把这些写死在代码里，加一个奖学金就要改代码。

做法：引擎侧仍然只认一套**规范板块键**（引擎照学生文档的标题分类，与奖学金无关），
    每个奖学金项目只配置「用哪些板块、叫什么名、顺序如何、是否必需」。
这样新增奖学金 = 建一条记录 + 选一套板块方案，完全不碰引擎。

canonical key 必须与引擎 `docx_parser` 分出来的 section 名一致，否则归不到组里。
"""
import json

# 规范板块目录：key → 默认名称
CATALOG = [
    ('奖学金', '曾获奖学金'),
    ('竞赛', '学科竞赛获奖（A类竞赛需注明）'),
    ('科研', '科研创新'),
    ('荣誉', '荣誉情况（校级及以上）'),
    ('体测', '体测'),
    ('志愿', '志愿者时长'),
    ('技能', '技能证书'),
    ('四六级', '四六级'),
    ('实践', '社会实践'),
    ('学生干部任职经历', '任职经历'),
    ('其他', '其他'),
    ('未归类', '未归类材料'),
]
CATALOG_MAP = dict(CATALOG)
TPL_KEYS = ['奖学金', '竞赛', '科研', '荣誉', '体测', '志愿']
EXTRA_KEYS = ['技能', '四六级', '实践', '学生干部任职经历', '其他', '未归类']

# 板块前缀：渲染成「一、曾获奖学金」这种模板样式
_CN = ['一', '二', '三', '四', '五', '六', '七', '八', '九', '十',
       '十一', '十二', '十三', '十四', '十五']


def with_ordinal(idx, label):
    return '%s、%s' % (_CN[idx] if idx < len(_CN) else str(idx + 1), label)


# 预置方案（新建奖学金时可选，之后可自由改）
PRESETS = {
    '国家奖学金': {
        'sections': [('奖学金', True), ('竞赛', True), ('科研', True),
                     ('荣誉', True), ('体测', True), ('志愿', True)],
        'rules': {},
    },
    '国家励志奖学金': {
        'sections': [('奖学金', True), ('竞赛', False), ('科研', False),
                     ('荣誉', True), ('体测', True), ('志愿', True)],
        'rules': {},
    },
    '省政府奖学金': {
        'sections': [('奖学金', True), ('竞赛', True), ('科研', True),
                     ('荣誉', True), ('体测', True), ('志愿', True)],
        'rules': {},
    },
    '校级奖学金': {
        'sections': [('奖学金', True), ('竞赛', False), ('荣誉', True),
                     ('体测', True), ('志愿', False)],
        'rules': {},
    },
    '单项奖学金': {
        'sections': [('竞赛', True), ('科研', True), ('荣誉', True)],
        'rules': {},
    },
    '其他': {
        'sections': [(k, False) for k in TPL_KEYS],
        'rules': {},
    },
}
PRESET_NAMES = list(PRESETS.keys())
CATEGORIES = ['国家级', '省级', '市级', '校级', '院级', '社会类', '其他']


def default_scheme(preset='国家奖学金'):
    """返回 [{'key','label','required'}]。"""
    p = PRESETS.get(preset) or PRESETS['其他']
    return normalize_scheme([{'key': k, 'required': r} for k, r in p['sections']])


def normalize_scheme(items):
    """补齐 label、去重、按目录顺序校正；容忍脏数据。"""
    out, seen = [], set()
    for it in (items or []):
        if isinstance(it, str):
            it = {'key': it}
        k = (it or {}).get('key')
        if not k or k in seen or k not in CATALOG_MAP:
            continue
        seen.add(k)
        out.append({
            'key': k,
            'label': (it.get('label') or CATALOG_MAP[k]).strip(),
            'required': bool(it.get('required', True)),
        })
    return out


def parse_scheme(raw, preset=None):
    """从 DB 里的 JSON 文本解析；为空/损坏时退回预置。"""
    items = None
    if raw:
        try:
            items = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            items = None
    if not items:
        return default_scheme(preset or '国家奖学金')
    return normalize_scheme(items)


def labels(scheme):
    """渲染用：{key: '一、曾获奖学金'}"""
    out = {}
    for i, s in enumerate(scheme):
        out[s['key']] = with_ordinal(i, s['label'])
    return out


def keys(scheme):
    return [s['key'] for s in scheme]


def ordered_keys(scheme, present):
    """
    渲染 / 导出的统一板块顺序：**方案里的板块 → 方案外但目录里有的（按目录顺序）→ 其余**。

    为什么需要它：辅导员可以把一张未认领的图「采纳」到任意板块（例如「技能证书」），
    而这个板块可能没写在当前奖学金的方案里。如果导出只遍历方案，这类条目会
    **在 Word 里凭空消失**（踩过）。所以顺序必须按"方案在前、其余按目录顺序接上"，
    并且**与内容无关**——有没有内容都占同一个位置，类目顺序才不会跳。

    present: 实际有内容的 key 集合；只有这些 key 会被返回。
    """
    present = set(present or [])
    out, seen = [], set()
    for k in keys(scheme) + EXTRA_KEYS:
        if k in seen:
            continue
        seen.add(k)
        if k in present:
            out.append(k)
    for k in present:                 # 目录里没有的脏 key，放最后，不丢
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def full_labels(scheme, ordered):
    """
    给排好序的板块生成「一、xxx」标签，保证**全局序号连续、不重复**。

    规则：方案内的板块沿用方案自己的顺序编号（一、二、三…）；
    方案外的板块（技能、四六级…）接在**方案板块之后**继续编号，
    而不是按"有内容的板块"的全局索引编号 —— 否则会出现
    「四、荣誉情况」和「四、技能证书」两个"四"（踩过：胡微祥缺科研/体测/志愿，
    技能被编号成"四"，和荣誉撞了）。
    """
    own = labels(scheme)          # 方案内：{key: '一、xxx'} 顺序已定
    scheme_keys = keys(scheme)
    base = len(scheme_keys)       # 方案外从「第 N+1 个」起编
    seen = 0
    out = {}
    for k in ordered:
        if k in own:
            out[k] = own[k]
        else:
            out[k] = with_ordinal(base + seen, CATALOG_MAP.get(k, k))
            seen += 1
    return out


def required_keys(scheme):
    return [s['key'] for s in scheme if s.get('required')]


def scheme_to_json(scheme):
    return json.dumps(normalize_scheme(scheme), ensure_ascii=False)
