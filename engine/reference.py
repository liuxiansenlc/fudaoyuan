# -*- coding: utf-8 -*-
"""
参考数据层：成绩排名表 + A类竞赛名单

成绩排名表：17 个班级 xlsx，用于按学号核实绩点、排名、不及格情况。
A类竞赛名单：xls，用于核实学生申报的竞赛是否属于 A 类、以及归一化赛事名称。
"""
import os
import re
import glob
import json

# ------------------------------------------------------------------ 成绩排名表
from settings import DEFAULT

# 路径不再写死：默认取 EngineConfig（其默认值仍来自 config.py），Web 层可按批次覆盖
RANKING_ROOT = None
COMPETITION_XLS = None

# 排名表列名（第 2 行为表头）
RANK_COLS = {
    '学号': 'sid', '姓名': 'name', '总分': 'total', '总应获得学分': 'should_credit',
    '门数': 'n_course', '总学分': 'credit', '获得学分': 'earned_credit',
    '不及格学分': 'fail_credit', '通过率': 'pass_rate',
    '算术平均分': 'avg_score', '算术平均分排名': 'avg_score_rank',
    '学分加权平均分': 'weighted_avg', '学分加权平均分排名': 'weighted_avg_rank',
    '平均绩点': 'gpa', '平均绩点排名': 'gpa_rank',
    '平均学分绩点': 'weighted_gpa', '平均学分绩点排名': 'weighted_gpa_rank',
    '学分绩点和': 'gpa_sum', '学分绩点和排名': 'gpa_sum_rank',
    '不及格门次': 'fail_count', '专业': 'major', '班级': 'class_no', '备注': 'note',
}


def _norm(v):
    if v is None:
        return ''
    return str(v).strip()


def _norm_sid(v):
    """学号归一：去掉 .0 之类的小数尾巴与空格"""
    s = _norm(v)
    if s.endswith('.0'):
        s = s[:-2]
    return s


def load_ranking(root=None, verbose=False, cfg=None):
    root = root or RANKING_ROOT or (cfg or DEFAULT).ranking_root
    """
    返回 {'by_sid': {学号: rec}, 'by_name': {(班级,姓名): rec}, 'files': n}
    root 可以是目录（递归找 *.xlsx），也可以是单个 xlsx 文件路径。
    Web 上传的成绩表存的是单文件路径，这里要兼容两种情况，否则
    传单个文件时 glob 找不到（踩过：上传后"成绩表 0 条学号"）。
    """
    import openpyxl
    if root and os.path.isfile(root):
        files = [root]
    else:
        files = glob.glob(os.path.join(root or '', '**', '*.xlsx'), recursive=True)
    by_sid = {}
    by_name = {}
    dup_sid = []
    for f in sorted(files):
        try:
            wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        except Exception:
            continue
        ws = wb[wb.sheetnames[0]]
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
        if not rows:
            continue
        # 找表头行：包含"学号"和"姓名"的那一行
        hdr_i = None
        for i, r in enumerate(rows[:6]):
            vals = [_norm(c) for c in r]
            if '学号' in vals and '姓名' in vals:
                hdr_i = i
                break
        if hdr_i is None:
            continue
        header = [_norm(c) for c in rows[hdr_i]]
        idx = {}
        for j, h in enumerate(header):
            if h in RANK_COLS:
                idx[RANK_COLS[h]] = j
        if 'sid' not in idx or 'name' not in idx:
            continue
        for r in rows[hdr_i + 1:]:
            sid = _norm_sid(r[idx['sid']]) if idx['sid'] < len(r) else ''
            if not sid or not re.fullmatch(r'\d{8,14}', sid):
                continue
            rec = {'source_file': os.path.basename(f)}
            for k, j in idx.items():
                if j < len(r):
                    rec[k] = _norm(r[j])
            if sid in by_sid and by_sid[sid] != rec:
                dup_sid.append(sid)
            by_sid[sid] = rec
            key = (rec.get('class_no', ''), rec.get('name', ''))
            by_name[key] = rec
    if verbose and dup_sid:
        print('  注意：学号重复出现 %d 个：%s' % (len(set(dup_sid)), sorted(set(dup_sid))[:5]))
    return {'by_sid': by_sid, 'by_name': by_name, 'files': len(files)}


# ------------------------------------------------------------------ A类竞赛名单
def _clean_comp_name(s):
    s = _norm(s)
    s = re.sub(r'\s+', '', s)
    s = s.replace('“', '"').replace('”', '"').replace('"', '')
    return s


def load_competitions(path=None, cfg=None):
    path = path or COMPETITION_XLS or (cfg or DEFAULT).competition_file
    """
    返回 {'items': [{name, name_clean, level, category, raw}], 'names': [...]}
    level: 国家级/省级；category: A+/A/A-

    同时支持 .xls（xlrd）与 .xlsx/.xlsm（openpyxl）。之前只用 xlrd，
    上传 .xlsx 竞赛表会直接报「Excel xlsx file; not supported」——
    辅导员传的表不一定是 .xls，这里按扩展名分派，读法更稳。
    """
    ext = os.path.splitext(path or '')[1].lower()
    sheets = []            # [(sheet_name, [[cell,...] per row])]
    if ext in ('.xlsx', '.xlsm'):
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        for ws in wb.worksheets:
            sheets.append((ws.title, [list(r) for r in ws.iter_rows(values_only=True)]))
        wb.close()
    else:
        import xlrd
        book = xlrd.open_workbook(path)
        for sh in book.sheets():
            sheets.append((sh.name, [sh.row_values(i) for i in range(sh.nrows)]))

    items = []
    for sheet_name, raw_rows in sheets:
        rows = [[_norm(c) for c in r] for r in raw_rows]
        if not rows:
            continue
        hdr_i = None
        for i, r in enumerate(rows[:6]):
            if '竞赛名称' in r:
                hdr_i = i
                break
        if hdr_i is None:
            continue
        header = rows[hdr_i]
        def col(name):
            return header.index(name) if name in header else None
        c_name, c_level, c_cat = col('竞赛名称'), col('级别'), col('竞赛类别')
        if c_name is None:
            continue
        for r in rows[hdr_i + 1:]:
            nm = r[c_name] if c_name < len(r) else ''
            if not nm or len(nm) < 3:
                continue
            items.append({
                'name': nm,
                'name_clean': _clean_comp_name(nm),
                'level': (r[c_level] if c_level is not None and c_level < len(r) else ''),
                'category': (r[c_cat] if c_cat is not None and c_cat < len(r) else ''),
                'sheet': sheet_name,
            })
    # 去重
    seen = set()
    uniq = []
    for it in items:
        k = it['name_clean']
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    return {'items': uniq, 'names': [it['name_clean'] for it in uniq]}


# ------------------------------------------------------------------ 赛事名归一
STOP = set('全国大学生浙江省第届年赛竞赛大赛比赛项目组别分组')


def _tokens(s):
    """把赛事名切成有区分度的词组（2-6 字的中文片段 + 英文缩写）"""
    s = _clean_comp_name(s)
    s = re.sub(r'[（）()【】\[\]、，,。.；;：:·\-—_/]', ' ', s)
    out = []
    for part in s.split():
        if re.fullmatch(r'[A-Za-z][A-Za-z0-9\-]{1,12}', part):
            out.append(part.lower())
            continue
        # 中文：取 2-6 字的连续片段，滑动切片
        zh = re.sub(r'[^\u4e00-\u9fa5]', '', part)
        if len(zh) < 2:
            continue
        for n in (4, 3, 2):
            for i in range(0, len(zh) - n + 1):
                g = zh[i:i + n]
                if g not in STOP:
                    out.append(g)
    return set(out)


def name_similarity(a, b):
    """赛事名相似度 0~1：基于区分度词组的重合（Jaccard 变体）"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    if not inter:
        return 0.0
    # 长词组权重更高
    w = sum(len(x) ** 2 for x in inter)
    denom = min(sum(len(x) ** 2 for x in ta), sum(len(x) ** 2 for x in tb))
    return min(1.0, w / denom) if denom else 0.0


def match_competition(text, comp_names, topk=3, min_sim=None, min_gap=None, cfg=None):
    _c = cfg or DEFAULT
    if min_sim is None:
        min_sim = _c.t('ref_comp_sim')
    if min_gap is None:
        min_gap = _c.t('ref_comp_gap')
    """
    把一段文本匹配到 A类名单里的赛事。

    两道闸门，缺一不可（都踩过坑）：
      1. **相似度下限 0.7**（不是 0.5）：实测"全国大学生数学竞赛"与
         "全国大学生数学建模竞赛"相似度高达 0.55 —— 而前者根本不在 A 类名单里。
         阈值太低会把"不在名单内的赛事"误判成 A 类。
      2. **唯一性**：第一名必须明显高于第二名（差距 ≥ min_gap），
         否则视为"不确定"，宁可不给结论，也不猜。
         比较前先合并互相包含的候选（如"蓝桥杯…大赛"与"蓝桥杯…大赛（软件类）"
         属同一赛事，不算两个独立选项）。

    返回 [(标准名, 相似度, category, level)]，不确定时返回 []。
    """
    scored = []
    for it in comp_names:
        s = name_similarity(text, it['name_clean'])
        if s > _c.t('ref_prefilter'):
            scored.append((it['name'], round(s, 3), it.get('category', ''),
                           it.get('level', ''), it['name_clean']))
    scored.sort(key=lambda x: -x[1])
    scored = [x for x in scored if x[1] >= min_sim]

    # 合并互相包含的候选（同一赛事的不同表述）
    kept = []
    for x in scored:
        if any((x[4] in k[4]) or (k[4] in x[4]) for k in kept):
            continue
        kept.append(x)
    if not kept:
        return []
    if len(kept) >= 2 and (kept[0][1] - kept[1][1]) < min_gap:
        return []          # 有多个同样像的候选 → 不下结论
    return [(x[0], x[1], x[2], x[3]) for x in kept[:topk]]


def find_in_list(text, comp_names, threshold=None, cfg=None):
    """
    宽松查找：只判断"这段文本像不像名单里的某个赛事"，不要求唯一。
    用于回答"这个赛事到底在不在 A 类名单里"。
    返回 (是否命中, 最佳匹配名, 相似度)
    """
    best, bs = None, 0.0
    for it in comp_names:
        s = name_similarity(text, it['name_clean'])
        if s > bs:
            best, bs = it['name'], s
    _th = threshold if threshold is not None else (cfg or DEFAULT).t('ref_find') if cfg else 0.7
    return (bs >= _th), best, round(bs, 3)


if __name__ == '__main__':
    import sys
    r = load_ranking(verbose=True)
    print('成绩表: %d 个文件，%d 条学号记录' % (r['files'], len(r['by_sid'])))
    for sid in list(r['by_sid'])[:3]:
        rec = r['by_sid'][sid]
        print('   ', sid, rec.get('name'), rec.get('class_no'), '绩点', rec.get('weighted_gpa'),
              '排名', rec.get('weighted_gpa_rank'), '不及格学分', rec.get('fail_credit'))
    c = load_competitions()
    print('A类竞赛: %d 条' % len(c['items']))
    for it in c['items'][:3]:
        print('   ', it['category'], it['level'], it['name'][:40].replace('\n', ' '))
    print()
    print('赛事名匹配测试:')
    for probe in ['第十六届蓝桥杯全国软件和信息技术专业人才大赛浙江赛区单片机设计与开发大学组二等奖',
                  '第十二届“大唐杯”全国大学生新一代信息通信技术大赛ICT基础通识赛浙江赛区 三等奖',
                  '第三届“凌特杯”通信系统设计大赛决赛 三等奖']:
        print('  ', probe[:34])
        for nm, sc, cat, lv in match_competition(probe, c['items']):
            print('       %.3f  [%s/%s] %s' % (sc, cat, lv, nm[:44].replace('\n', ' ')))
