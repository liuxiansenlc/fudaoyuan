# -*- coding: utf-8 -*-
"""
证书分类与字段抽取

把本地 OCR 的纯文本，变成结构化的"这张证书是什么"。
分类主要靠证书版式关键词，比"理解图片"便宜得多，且完全在本地完成。

重要设计（来自真实数据）：
1. 证件有两大类版式：
   - 个人证书类：只有一个人名（"XXX同学：荣获……"）
   - 名单公示类：一张截图里有几十个"姓名/学号"，如大创项目公示表
   两类图片的"姓名核对"规则完全不同：前者要求姓名唯一且匹配；
   后者只要求申报人姓名出现在名单里。绝不能混用。
2. 分类结果要和"板块"交叉使用：板块决定期望的证书类型，
   类型不符是最强异常信号（如竞赛板块里混入奖学金证书）。
"""
import re

# ------------------------------------------------------------ 证书版式关键词
PATTERNS = [
    ('scholarship', ['奖学金证书', '奖学金', '励志奖学金', '国家奖学金']),
    ('honor', ['荣誉证书', '优秀学生', '优秀共青团员', '优秀团干部', '先进个人',
               '三好学生', '文明寝室', '文明修身', '标兵', '优秀干部', '优秀毕业生',
               '优秀志愿者', '社会实践先进']),
    ('competition', ['获奖证书', '竞赛', '大赛', '比赛', '杯', '程序设计', '挑战赛',
                     '选拔赛', '总决赛', '赛区', '一等奖', '二等奖', '三等奖',
                     '金奖', '银奖', '铜奖', '优秀奖']),
    ('level_exam', ['等级考试', 'CET', '英语四级', '英语六级', '四级考试', '六级考试',
                    '计算机等级', '普通话']),
    ('physical', ['体质健康', '体测', '体能测试', '国家学生体质健康标准']),
    ('volunteer', ['志愿者', '志愿服务', '志愿时长', '社会实践', '义工']),
    ('research', ['创新创业', '训练计划', '大创', '论文', '专利', '软件著作权',
                  '科研', '立项', '结题']),
]

# 奖项等级（含常见错字/形近干扰）
RE_AWARD_LEVEL = re.compile(
    r'(特等奖|一等奖|二等奖|三等奖|优秀奖|优胜奖|入围奖|参与奖|成功参赛奖|金奖|银奖|铜奖|'
    r'第一名|第二名|第三名|冠军|亚军|季军|一等奖|二等奖)')
LEVEL_CANON = {
    '特等奖': '特等奖', '一等奖': '一等奖', '二等奖': '二等奖', '三等奖': '三等奖',
    '优秀奖': '优秀奖', '优胜奖': '优胜奖', '入围奖': '入围奖',
    '参与奖': '参与奖', '成功参赛奖': '成功参赛奖', '金奖': '金奖', '银奖': '银奖',
    '铜奖': '铜奖', '冠军': '第一名', '亚军': '第二名', '季军': '第三名',
    '第一名': '第一名', '第二名': '第二名', '第三名': '第三名',
}

RE_YEAR = re.compile(r'(20\d{2})\s*[-—~至]\s*(20\d{2})|(20\d{2})')
RE_RANK = re.compile(r'[（(]\s*(\d+)\s*/\s*(\d+)\s*[)）]')
# 姓名/学号（名单公示表版式）
RE_NAME_ID = re.compile(r'([\u4e00-\u9fa5]{2,4})\s*[/／]\s*(\d{8,12})')
# 学号（单独出现，2021103103 这种 10 位）
RE_ID = re.compile(r'(20\d{8})')
# 个人证书版式里的姓名
RE_NAME_TONGXUE = re.compile(r'([\u4e00-\u9fa5]{2,4})\s*(?:同学|同志)')

LEVEL_WORDS = ['国家级', '省级', '市级', '校级', '院级']

NON_CERT_HINT = ['费用报销', '预算', '通知', '会议', '日程', '名单公示']


def _has_any(text, words):
    return [w for w in words if w in text]


def classify(text, item_section_hint=None):
    """
    返回结构化结果:
    {
      'type': 主类型, 'all_types': [...], 'matched_keywords': [...],
      'award_level': 等级, 'years': [...], 'names': [...], 'ids': [...],
      'is_roster': bool, 'roster_size': n, 'confidence_hint': str
    }
    """
    t = re.sub(r'\s+', '', text)
    scores = {}
    hits = {}
    for typ, words in PATTERNS:
        h = _has_any(t, words)
        if h:
            # 用命中数量 + 关键词长度加权，避免"奖"这种泛词占优
            scores[typ] = sum(len(w) for w in h)
            hits[typ] = h

    # 名单公示类特征：大量 姓名/学号 对
    roster = RE_NAME_ID.findall(t)
    ids = RE_ID.findall(t)
    is_roster = len(roster) >= 3 or (len(ids) >= 3 and len(roster) >= 1)

    # 奖项等级：OCR 常有形近错误，做一次宽松归并
    lv = None
    m = RE_AWARD_LEVEL.search(t)
    if m:
        lv = LEVEL_CANON.get(m.group(1))
    if lv is None:
        # 常见 OCR 把"一等奖"读成"上等奖""—等奖"
        m2 = re.search(r'([上—\-一])\s*等奖', t)
        if m2:
            lv = '一等奖'

    years = []
    for a, b, c in RE_YEAR.findall(t):
        for y in (a, b, c):
            if y and y not in years:
                years.append(y)

    names = sorted(extract_name_candidates(t))
    ids = sorted(set(ids))

    # 主类型选择
    typ = None
    if is_roster and scores.get('research'):
        typ = 'roster_research' if '创新创业' in t or '训练计划' in t else 'roster'
    elif is_roster:
        typ = 'roster'
    elif scores:
        # 证书类优先：奖学金/荣誉/竞赛，其次等级考试/体测/志愿
        order = ['level_exam', 'physical', 'volunteer', 'scholarship', 'honor', 'competition', 'research']
        ranked = sorted(scores.items(), key=lambda kv: -kv[1])
        typ = ranked[0][0]
        # 若文本里明确出现"证书"字样且是奖学金/荣誉，优先归到该证书类
        for pref in ['scholarship', 'honor']:
            if pref in scores and ('证书' in t):
                typ = pref
                break

    return {
        'type': typ or 'unknown',
        'all_types': sorted(scores, key=lambda k: -scores[k]),
        'matched_keywords': hits,
        'award_level': lv,
        'years': years,
        'names': names,
        'ids': ids,
        'is_roster': is_roster,
        'roster_size': len(roster),
        'n_ids': len(ids),
        'text_norm': t,
    }


# 姓名抽取时最常见的前缀噪声字（OCR 把"获奖证书黄俊杰同学"读成 4 字名）
_NAME_NOISE = set('证书获荣得予兹授特此发以资鼓励同志学生表彰评为优秀')


def extract_name_candidates(text_norm, student_name=None):
    """
    抽取证书上的"人名"候选。
    注意：这里只能给出候选，不能直接当作"证书署名"下结论 ——
    OCR 会把"获奖证书黄俊杰同学"里的"书黄俊杰"当成名字。
    真正的判定以"申报人姓名是否出现在全文"为准（见 check_person）。
    """
    out = set()
    for m in re.finditer(r'[\u4e00-\u9fa5]{2,6}?(?:同学|同志)', text_norm):
        seg = m.group(0)[:-2]
        # 从右往左剥掉噪声前缀字，留下最长的像人名的部分
        while seg and seg[0] in _NAME_NOISE:
            seg = seg[1:]
        if 2 <= len(seg) <= 4:
            out.add(seg)
            for n in (2, 3):
                if len(seg) > n:
                    out.add(seg[-n:])
    for nm, _sid in RE_NAME_ID.findall(text_norm):
        if 2 <= len(nm) <= 4:
            out.add(nm)
    return out


def check_person(info, student_name, student_class_no=None):
    """
    姓名核对。返回 (status, detail)

    判定顺序很关键（踩过坑）：
    先看"申报人姓名是否出现在证书全文里"，而不是先抽名字再比。
    因为 OCR 常常把"获奖证书黄俊杰同学"里的名字前面粘上模式词，
    先抽名字会抽出"书黄俊杰"，把正确配对误判成"别人证书"。
    """
    text = info.get('text_norm') or ''
    if not student_name:
        return 'no_name', '无法得知申报人姓名'

    if info.get('is_roster'):
        if student_name in text:
            return 'roster_in', '申报人出现在名单中'
        return 'roster_missing', '名单类截图，但未找到申报人「%s」' % student_name

    if student_name in text:
        return 'ok', '姓名匹配'

    cands = extract_name_candidates(text, student_name)
    if not cands:
        return 'no_name', '未能读出证书上的姓名'
    return 'name_conflict', '证书上姓名为「%s」，与申报人「%s」不符' % ('、'.join(sorted(cands)[:3]), student_name)


# ------------------------------------------------------------ 板块期望类型
SECTION_EXPECT = {
    '奖学金': {'scholarship'},
    '竞赛': {'competition'},
    '科研': {'research', 'roster_research', 'roster'},
    '荣誉': {'honor'},
    '体测': {'physical'},
    '志愿': {'volunteer'},
    '技能': {'level_exam'},
    '实践': {'volunteer', 'honor'},
    '四六级': {'level_exam'},
}


def section_matches(section, cert_type):
    """板块与证书类型是否相符"""
    exp = SECTION_EXPECT.get(section)
    if not exp:
        return None
    if cert_type in ('unknown',):
        return None
    return cert_type in exp
