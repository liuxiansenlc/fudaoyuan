# -*- coding: utf-8 -*-
"""
统一读取层：把"本地 OCR"和"视觉大模型"两种结果，归成同一个结构。
下游（分类 / 配对 / 报表）只认这个结构，不关心来源。

关键设计：
1. 证据形式（doc_kind）与奖项语义（semantic_type）分开。
   - doc_kind：证书 / 名单公示 / 汇总表截图 / 成绩单 / 评分表 —— 都算有效证据
   - semantic_type：奖学金 / 竞赛 / 荣誉 / 技能 / 体测 / 志愿 / 科研
   两者组合才能正确判断"这个板块里出现这份材料是否合理"。
2. VLM 有结果就用 VLM，没有就回退本地 OCR，可以渐进式接入。
"""
import re
import vision as vision_mod

# 本地 OCR 的证书类型 → 统一 doc_kind
OCR_KIND = {
    'scholarship': 'certificate', 'honor': 'certificate', 'competition': 'certificate',
    'research': 'certificate', 'level_exam': 'transcript', 'physical': 'form',
    'volunteer': 'summary', 'roster': 'roster', 'roster_research': 'roster',
    'unknown': 'other',
}
KIND_LABEL = {
    'certificate': '证书', 'roster': '名单公示', 'summary': '汇总表截图',
    'transcript': '成绩单', 'form': '系统截图', 'other': '未识别',
}

# 语义类型关键词
from settings import DEFAULT

# 语义类型关键词表（key → 关键词列表），来自规则包，可由界面增删
SEM_KEYS = [tuple(x) for x in DEFAULT.semantic_keys]


RE_YEAR_RANGE = re.compile(r'(20\d{2})\s*[-—~至]\s*(20\d{2})')


def semantic_type(*texts):
    t = ''.join(x for x in texts if x)
    if not t:
        return None
    for key, words in SEM_KEYS:
        if any(w in t for w in words):
            return key
    return None


def build(rec, ocr, vis, student_name, cfg=None):
    """
    生成统一读取结果。
    rec: 图片记录（尺寸/大小）
    ocr: 本地 OCR 结果（ocr_local.ocr_image_bytes 的返回）
    vis: VLM 结果（vision.get 的返回，可能为 None）
    """
    if vis:
        text = ' '.join(str(x) for x in [
            vis.get('award_name'), vis.get('title'), vis.get('issuer'),
            vis.get('academic_year'), vis.get('award_level'), vis.get('note')] if x)
        kind = vis.get('doc_kind') or 'certificate'
        award_name = vis.get('award_name') or ''
        level = vis.get('level') or vision_mod.judge_level(
            vis.get('issuer', ''), vis.get('title', ''), award_name)
        year = vis.get('academic_year') or ''
        persons = vis.get('persons') or []
        rule = vis.get('person_in_roster')
        # 姓名核对：以结构化的人名列表为准，不要用"姓名是否出现在拼出来的文本里"判断
        # （拼文本时容易漏掉 persons，导致把本人材料误判成他人证书）
        if kind == 'roster' or rule is not None:
            if rule is True or (student_name and student_name in persons):
                person_status = 'roster_in'
            elif rule is False:
                person_status = 'roster_missing'
            elif persons:
                person_status = 'roster_missing'
            else:
                person_status = 'no_name'
        elif persons:
            person_status = 'ok' if (student_name and student_name in persons) else 'name_conflict'
        else:
            person_status = 'no_name'
        return {
            'source': 'vlm',
            'kind': kind, 'kind_label': KIND_LABEL.get(kind, kind),
            'title': vis.get('title') or '',
            'semantic': semantic_type(award_name, vis.get('title'), vis.get('note')),
            'award_name': award_name,
            'award_level': vis.get('award_level') or '',
            'year': year, 'issuer': vis.get('issuer') or '', 'level': level,
            'persons': persons, 'metrics': vis.get('metrics') or {},
            'note': vis.get('note') or '',
            'is_team': vis.get('is_team'), 'team_size': vis.get('team_size'),
            'person_status': person_status,
            'person_detail': vis.get('note') or '',
            'text': text, 'confidence': 1.0, 'raw_text': ocr.get('text', ''),
        }

    # ---- 回退：本地 OCR
    from cert_classify import classify, check_person
    cert = classify(ocr.get('text', ''))
    kind = OCR_KIND.get(cert['type'], 'other')
    status, detail = check_person(cert, student_name)
    return {
        'source': 'ocr',
        'kind': kind, 'kind_label': KIND_LABEL.get(kind, kind),
        'title': '',
        'semantic': semantic_type(ocr.get('text', '')),
        'award_name': '', 'award_level': cert.get('award_level') or '',
        'year': (cert.get('years') or [''])[0],
        'issuer': '', 'level': vision_mod.judge_level(ocr.get('text', '')),
        'persons': cert.get('names') or [], 'metrics': {},
        'note': detail, 'is_team': None, 'team_size': None,
        'person_status': status, 'person_detail': detail,
        'text': ocr.get('text', ''), 'confidence': ocr.get('confidence', 0),
        'raw_text': ocr.get('text', ''), 'cert': cert,
    }


def expand_year_range(text, cfg=None):
    """
    展开学年的简写。学生常写成 "2022-2024学年省政府奖学金"，
    实际是 2022-2023 与 2023-2024 两个学年的两张证书。
    这类条目要拆成多条，否则会判成"缺图"。
    返回拆分后的文本列表；不需要拆时返回 [text]。
    """
    m = RE_YEAR_RANGE.search(text)
    if not m:
        return [text]
    a, b = int(m.group(1)), int(m.group(2))
    _c = cfg or DEFAULT
    if b - a < _c.reading_rules.get('year_range_min_gap', 2) \
            or b - a > _c.reading_rules.get('year_range_max_gap', 5):
        return [text]
    out = []
    for y in range(a, b):
        rng = '%d-%d' % (y, y + 1)
        t = text[:m.start()] + rng + text[m.end():]
        # 去掉重复的"学年学年"之类
        t = re.sub(r'学年\s*学年', '学年', t)
        out.append(t)
    return out


# ------------------------------------------------------------------ 资格判定
def check_cet(metrics, text='', cfg=None):
    """英语四级/六级是否通过：总分 >= 425"""
    score = None
    for k, v in (metrics or {}).items():
        if '总分' in str(k):
            try:
                score = int(float(v))
            except Exception:
                score = None
    if score is None:
        m = re.search(r'总分\s*[:：]?\s*(\d{3})', str(text))
        if m:
            score = int(m.group(1))
    if score is None:
        return None, '未读到总分'
    _pass = (cfg or DEFAULT).reading_rules.get('cet_pass', 425)
    return score >= _pass, '总分 %d，%s（%d 分及格线）' % (
        score, '已通过' if score >= _pass else '未通过', _pass)


def check_mandarin(metrics, text='', cfg=None):
    """普通话等级：二级乙等及以上视为达标"""
    grade = (metrics or {}).get('等级') or ''
    if not grade:
        m = re.search(r'(一级[甲乙]等|二级[甲乙]等|三级[甲乙]等)', str(text))
        grade = m.group(1) if m else ''
    if not grade:
        return None, '未读到等级'
    _okset = (cfg or DEFAULT).reading_rules.get(
        'mandarin_min', ['一级甲等', '一级乙等', '二级甲等', '二级乙等'])
    ok = grade in _okset
    return ok, '等级 %s，%s' % (grade, '达到二级乙等要求' if ok else '未达二级乙等')
