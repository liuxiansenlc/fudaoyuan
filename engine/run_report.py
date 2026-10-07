# -*- coding: utf-8 -*-

"""

主流程：跑完一套材料，输出配对结果与问题清单。



用法：

    python run_report.py            # 跑默认目录下所有学生材料

    python run_report.py 黄俊杰      # 只跑某个人

"""

import os

import re

import sys

import glob

import json



sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))



from docx_parser import parse_docx, triage_media          # noqa: E402

from ocr_local import ocr_image_bytes, is_blank_or_icon   # noqa: E402

from cert_classify import classify, check_person, section_matches  # noqa: E402

import reference                                          # noqa: E402

import matching                                           # noqa: E402

import vision as vision_mod                               # noqa: E402

import reading as reading_mod                             # noqa: E402



from config import (STUDENT_DIR as ROOT, OUT_DIR, MATCH_THRESHOLD,

                    AUTO_CONFIRM_SCORE, ALLOW_OCR_FALLBACK, PHYSICAL_PASS)
from settings import DEFAULT



TYPE_LABEL = {

    'scholarship': '奖学金证书', 'honor': '荣誉证书', 'competition': '竞赛获奖',

    'level_exam': '等级考试', 'physical': '体测', 'volunteer': '志愿服务',

    'research': '科研/大创', 'roster_research': '项目名单截图', 'roster': '名单截图',

    'unknown': '未识别',

}





def build_images(r, ref=None, cfg=None):

    """把解析出的图片，经「视觉大模型」读取后变成可比对的结构。



    读取已全面转为视觉大模型。本地 OCR 只在显式打开回退开关(ALLOW_OCR_FALLBACK)

    时才参与，且仅用于"视觉模型没有结果的图"。默认不回退：没有视觉结果的图会被

    明确标成"待视觉复核"，而不是用可能读错的本地结果悄悄顶替。

    """

    imgs = []

    ignored = []

    sname = r['student'].get('name')

    for m, rec in r['media'].items():

        cat, why = triage_media(rec)

        vis = vision_mod.get(rec['sha256'])

        if vis:

            # 已有视觉读取结果 —— 完全不需要本地 OCR

            o = {'text': '', 'trust': '', 'confidence': 1.0,

                 'total_chars': 0, 'lines': [], 'need_cloud': False}

        elif ALLOW_OCR_FALLBACK:

            o = ocr_image_bytes(rec['data'], rec['sha256'])

            blank, bwhy = is_blank_or_icon(rec, o)

            if blank or (o['total_chars'] <= 2):

                ignored.append({'media': m, 'reason': bwhy or '无有效文字',

                                'triage': cat, 'size': '%sx%s' % (rec['w'], rec['h'])})

                continue

        else:

            ignored.append({'media': m, 'reason': '视觉模型尚未读取此图（本地 OCR 兜底已停用）',

                            'triage': cat, 'size': '%sx%s' % (rec['w'], rec['h'])})

            continue



        rd = reading_mod.build(rec, o, vis, sname)

        if o['text']:

            cert = classify(o['text'])

        else:

            # 视觉模型结果里没有 cert_classify 的结构，用语义类型兜出来给台账显示用

            cert = {'type': rd['semantic'] or 'unknown',

                    'all_types': [rd['semantic']] if rd['semantic'] else [],

                    'award_level': rd['award_level'], 'years': [], 'names': rd['persons'],

                    'is_roster': rd['kind'] == 'roster', 'roster_size': len(rd['persons'])}

        sig = matching.award_signature(rd['text'], None, (ref or REF)['comp_items'], cfg)

        sig['level'] = rd['level']

        sig['award_name'] = rd['award_name']

        sig['award_level_vlm'] = rd['award_level']

        # 视觉模型直接读出的等级也要并入比对集合，

        # 否则像"优胜奖"这种容易读丢的等级会漏判

        if rd['award_level']:

            sig['levels'] = set(sig['levels']) | {rd['award_level']}



        refs = rec['refs']

        blk = r['occurrences'][refs[0]]['block_idx'] if refs else -1

        bsec = None

        if 0 <= blk < len(r['blocks']):

            bsec = r['blocks'][blk].get('section')



        imgs.append({

            'id': len(imgs),

            'media': m,

            'file': m.split('/')[-1],

            'sha256': rec['sha256'],

            'size': '%sx%s' % (rec['w'], rec['h']),

            'w': rec['w'], 'h': rec['h'],

            'section': bsec,

            'triage': cat, 'triage_reason': why,

            'ocr': o['text'],

            'ocr_trust': '视觉' if rd['source'] == 'vlm' else 'OCR',

            'ocr_conf': rd['confidence'],

            'need_cloud': o['need_cloud'],

            'source': rd['source'],

            'reading': rd,

            'kind': rd['kind'], 'kind_label': rd['kind_label'],

            'semantic': rd['semantic'], 'vlevel': rd['level'],

            'cert': cert,

            'person_status': rd['person_status'],

            'person_detail': rd['person_detail'],

            'sig': sig,

            'orders': list(refs),

            'block_idx': blk,

        })

    return imgs, ignored





# ---------------------------------------------------------------- 条目前处理

# 学生把奖项写成两段（"1.2022-2024学年" / "校级优秀学生"）时，

# 后一段以这些词结尾，说明它还没写完

RE_DANGLING = re.compile(r'(学年|年度|学期|中获得|荣获|获得)[；;，,]?$')

# 模板占位符：学生直接留下来没删

RE_PLACEHOLDER = re.compile(r'^[【\[（(]?(插入|请|此处|填写|填写相关|上传)[^】\]）)]{0,20}[】\]）)]?$')

# "一段多奖"的奖项分隔符

RE_SPLIT_AWARD = re.compile(r'[，,；;、]')

# 条目开头的序号：1. / 1、 / （1） / ①

RE_LEAD_NO = re.compile(

    r'^\s*(?:\d{1,2}\s*[\.、）)]|[（(]\s*\d{1,2}\s*[)）]'

    r'|[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s*')

# 判断一个片段是否像"一条奖项"

AWARD_HINT = ['优秀', '先进', '文明', '团员', '团干', '干部', '学生', '寝室',

              '标兵', '模范', '奖', '称号', '荣誉', '奖学金']

# 引号/括号内的顿号不能被当成奖项分隔符

RE_QUOTED = re.compile(
    u'[《【（(][^》】）)]*[》】）)]'
    u'|[“”‘’' + chr(39) + chr(34) + ']'
    u'[^“”‘’' + chr(39) + chr(34) + ']*'
    u'[“”‘’' + chr(39) + chr(34) + ']')





def merge_dangling(raw):

    """把被断成两行的奖项合并回去。



    实测有学生把"1.2022-2024学年"和"校级优秀学生"写成两个段落，

    解析出来就成了两条互不相干的条目，奖项名整条丢失。

    合并条件收紧一些，避免把本来独立的两条误并：

      - 当前段以"学年/年度/学期/获得…"结尾

      - 当前段很短（<= 20 字）

      - 下一段与它同板块，且不是新的序号条目

    """

    out = []

    i = 0

    n = len(raw)

    while i < n:

        d = dict(raw[i])

        t = (d.get('text') or '').strip()

        merged_any = False

        while (i + 1 < n and RE_DANGLING.search(t) and len(t) <= 20):

            nxt = raw[i + 1]

            nt = (nxt.get('text') or '').strip()

            if nxt.get('section') != d.get('section') or not nt:

                break

            if RE_LEAD_NO.match(nt):      # 下一段自己带序号 → 是另一条

                break

            t = t + nt

            merged_any = True

            i += 1

        d['text'] = t

        if merged_any:

            d['split_reason'] = list(d.get('split_reason') or []) + ['断行合并']

        out.append(d)

        i += 1

    return out





def split_multi_award(text):

    """把"一段多奖"拆开。



    实测有学生写成：`1. 2022-2023学年校级优秀学生，校级文明修身先进个人，

    校级优秀共青团员，寝室获校级四星级文明寝室` —— 一段话里塞了 4 个奖项、

    后面跟着 4 张证书。不拆的话，这 4 张图会全部被判成"无归属"。



    注意：引号/括号里的顿号不能当分隔符，否则

    "电子商务'创新、创意、及创业'挑战赛"会被从中间劈开。

    """

    body = RE_LEAD_NO.sub('', text).strip()

    if not body:

        return None

    stash = []



    def _mask(m):

        stash.append(m.group(0))

        return '\x00%d\x00' % (len(stash) - 1)



    masked = RE_QUOTED.sub(_mask, body)

    parts = [p.strip() for p in RE_SPLIT_AWARD.split(masked) if p.strip()]

    parts = [re.sub(r'\x00(\d+)\x00',

                    lambda m: stash[int(m.group(1))], p) for p in parts]

    parts = [RE_LEAD_NO.sub('', p).strip() for p in parts]

    if len(parts) < 2:

        return None

    keep = [p for p in parts if len(p) >= 4 and any(h in p for h in AWARD_HINT)]

    return keep if len(keep) >= 2 else None





def build_items(r, comp_items, cfg=None):

    raw = []

    for it in r['items']:

        d = dict(it)

        d['parent_text'] = it['text']

        d['split_reason'] = []

        raw.append(d)

    # 1) 先合并被断成两行的奖项

    raw = merge_dangling(raw)

    raw = [d for d in raw if not RE_PLACEHOLDER.match(d['text'].strip())]

    # 2) 拆"一段多奖"，3) 展开"2022-2024学年"简写

    expanded = []

    for d in raw:

        p = split_multi_award(d['text']) or [d['text']]

        for x in p:

            for e in reading_mod.expand_year_range(x):

                n = dict(d)

                n['text'] = e

                if x != d['text']:

                    n['split_reason'] = list(d['split_reason']) + ['一段多奖']

                if e != x:

                    n['split_reason'] = list(n['split_reason']) + ['学年简写展开']

                expanded.append(n)



    items = []

    for i, d in enumerate(expanded):

        d['id'] = i

        d['sig'] = matching.award_signature(d['text'], None, comp_items, cfg)

        items.append(d)



    # 计算每条奖项在文档里"紧邻"的图片（用于判断是否发生跨位纠正）

    occ = r['occurrences']

    for d in items:

        nxt = None

        for it2 in items:

            if it2['section'] == d['section'] and it2['block_idx'] > d['block_idx']:

                if nxt is None or it2['block_idx'] < nxt:

                    nxt = it2['block_idx']

        lo, hi = d['block_idx'], (nxt if nxt is not None else 10 ** 9)

        d['adjacent_orders'] = [o['order'] for o in occ if lo <= o['block_idx'] < hi]

    return items





def check_eligibility(r, imgs, ref=None, cfg=None):

    """

    资格核验：把成绩库的硬数据和材料里读到的硬指标凑到一起。

    覆盖最初提出的"绩点/奖项名错误"这类核对。

    """

    s = r['student']

    res = {'identity': [], 'items': []}

    name, cls = s.get('name'), s.get('class_no')

    rec = None

    if name:

        for (c, n), v in (ref or REF)['ranking']['by_name'].items():

            if n == name:

                rec = v

                break

    if rec:

        if cls and rec.get('class_no') and cls != rec['class_no']:

            res['identity'].append(('warn', '文件名的班级(%s)与成绩表登记的班级(%s)不一致' % (cls, rec['class_no'])))

        gpa = rec.get('weighted_gpa') or rec.get('gpa') or '-'

        rank = rec.get('weighted_gpa_rank') or '-'

        res['items'].append(('ok', '平均学分绩点 %s，班级排名 %s' % (gpa, rank)))

        fail = rec.get('fail_credit') or '0'

        failn = rec.get('fail_count') or '0'

        try:

            ok_fail = float(fail) == 0 and float(failn) == 0

        except Exception:

            ok_fail = None

        if ok_fail is True:

            res['items'].append(('ok', '无不及格课程（国奖硬性要求）'))

        elif ok_fail is False:

            res['items'].append(('bad', '存在不及格：不及格学分 %s，不及格门次 %s' % (fail, failn)))

    else:

        res['identity'].append(('warn', '在成绩排名表中未找到该学生，无法核实绩点'))



    # 材料里能读到的硬指标

    for im in imgs:

        rd = im.get('reading') or {}

        m = rd.get('metrics') or {}

        txt = (rd.get('award_name') or '') + (rd.get('title') or '')

        if '四级' in txt or '六级' in txt or 'CET' in txt.upper():

            ok, why = reading_mod.check_cet(m, rd.get('raw_text', ''))

            if ok is not None:

                res['items'].append(('ok' if ok else 'bad', '英语等级：%s' % why))

        if '普通话' in txt:

            ok, why = reading_mod.check_mandarin(m, rd.get('raw_text', ''))

            if ok is not None:

                res['items'].append(('ok' if ok else 'bad', '普通话：%s' % why))

        if '体质' in txt or '体测' in txt or im.get('section') == '体测':

            sc = m.get('总分') or m.get('体测成绩') or m.get('体测总分')

            if sc is not None:

                try:

                    good = float(sc) >= (cfg or DEFAULT).reading_rules.get(
                        'physical_pass', PHYSICAL_PASS)

                except Exception:

                    good = None

                if good is not None:

                    res['items'].append(('ok' if good else 'warn',

                                         '体测总分 %s（要求 %g 分以上）' % (sc, PHYSICAL_PASS)))

        if '志愿者' in txt or '志愿' in txt or im.get('section') == '志愿':

            h = m.get('志愿时长') or m.get('时长')

            if h is not None:

                res['items'].append(('info', '志愿服务时长 %s 小时（按评审办法核对下限）' % h))

    return res






# 语义类型 → 材料类别（给"AI 判断"用）
SEM_CAT = {
    'scholarship': '奖学金类', 'competition': '学科竞赛类', 'research': '科研创新类',
    'honor': '荣誉类', 'physical': '体质健康（体测）', 'volunteer': '志愿服务时长',
    'level_exam': '外语/计算机等级等职业技能证书',
}


def judge_material(im):
    """对"没有认领到具体条目"的图片，用视觉模型读到的内容给出判断。"""
    rd = im.get('reading') or {}
    name = rd.get('award_name') or rd.get('title') or ''
    bits = [name] if name else []
    if rd.get('award_level'):
        bits.append(str(rd['award_level']))
    if im.get('vlevel'):
        bits.append(str(im['vlevel']))
    yr = rd.get('academic_year') or rd.get('year')
    if yr:
        bits.append(str(yr))
    desc = '、'.join(bits)
    met = rd.get('metrics') or {}
    if met:
        desc += '（' + '、'.join('%s %s' % (k, v) for k, v in met.items()) + '）'
    if not desc:
        desc = '未能读出具体名称'
    cat = SEM_CAT.get(im.get('semantic')) or SEM_CAT.get(
        {'技能': 'level_exam', '体测': 'physical', '志愿': 'volunteer',
         '四六级': 'level_exam'}.get(im.get('section'), ''), '')
    tail = '学生正文里没有写对应的奖项条目'
    if cat:
        tail += '；按内容属于「%s」' % cat
    return '%s —— %s。' % (desc, tail)



# 明显的"表格/名单/汇总"类材料不适合当奖项名建议
SUG_BLACK = ('汇总表', '名单', '公示', '表格', '截图', '明细', '立项', '统计表')
# 尾部冗余词，去掉后更像一个"奖项名"
RE_SUG_TAIL = re.compile(r'(荣誉证书|获奖证书|奖状|证书|成绩报告单|成绩单|合格证书|'
                         r'考试成绩报告单|证书查询结果页面)$')


def _suggest_award_name(im, rd):
    """
    给"学生懒得写奖项"的证明材料提一个建议奖项名，例如 {全国大学英语四级考试}。

    只在**像证书/成绩单**、且名字里没有"汇总表/名单"这类词时才给，
    避免把"学生成绩汇总表"当奖项名建议出去。
    """
    if im.get('kind') not in ('certificate', 'transcript'):
        return ''
    name = (rd.get('award_name') or '').strip()
    if not name:
        return ''
    name = re.sub(r'\s+', '', name)
    if any(w in name for w in SUG_BLACK):
        return ''
    name = RE_SUG_TAIL.sub('', name)
    name = re.sub(r'^第[一二三四五六七八九十百\d]+[届次]', '', name)
    name = name.strip().strip('，、：: ')
    return name if len(name) >= 4 else ''


def annotate_materials(items, imgs, res, ctx, cfg=None):
    """给未参与配对的图片，算"最接近的条目"并生成 AI 判断。"""
    used = {m['img_id'] for m in res['matches']}
    for im in imgs:
        if im['id'] in used:
            continue
        best = None
        for it in items:
            sc, rs = matching.score_pair(it, im, ctx, cfg)
            if best is None or sc > best[0]:
                best = (sc, it, rs)
        if best:
            im['nearest'] = {
                'item': best[1].get('text') or '',
                'section': best[1].get('section') or '',
                'score': round(best[0], 1),
                'pos': [r[0] for r in best[2] if r[1] > 0][:5],
                'neg': [r[0] for r in best[2] if r[1] < 0][:4],
            }
        im['ai_judgment'] = judge_material(im)
        rd = im.get('reading') or {}
        sug = _suggest_award_name(im, rd)
        if sug:
            im['suggest_award'] = sug
            _nsc = float((im.get('nearest') or {}).get('score') or 0)
            # 证书上能核对到本人姓名、且跟任何已写条目都对不上
            # → 基本就是"学生懒得写这条"，给一个醒目的建议奖项名
            im['suggest_ok'] = (im.get('person_status') in ('ok', 'roster_in')
                                and _nsc < 40)
    return imgs


def analyze_file(path, comp_items, ref=None, cfg=None):
    """
    单个学生的完整流水线。

    实际实现已搬到 `pipeline.analyze()`（拆成可独立调用的步骤，便于 Web 层
    把"读图"这一步异步化、报进度、中断重跑）。这里保留同名函数作为兼容入口，
    返回契约不变：(r, items, imgs, ignored, res, labeled)。
    """
    from pipeline import analyze
    return analyze(path, comp_items, ref=ref, cfg=cfg)



def render_console(r, items, imgs, ignored, res, labeled):

    s = r['student']

    L = []

    L.append('=' * 78)

    L.append('%s  %s   条目 %d / 图片 %d（忽略 %d 张无效图）'

             % (s.get('class_no'), s.get('name'), len(items), len(imgs), len(ignored)))

    for w in s.get('identity_warnings') or []:

        L.append('  ⚠ 身份：%s' % w)

    el = (res or {}).get('eligibility') or {}

    for lv, msg in (el.get('identity') or []):

        L.append('  ⚠ %s' % msg)

    if el.get('items'):

        L.append('  ── 资格核验 ──')

        for lv, msg in el['items']:

            L.append('     [%s] %s' % ({'ok': '通过', 'bad': '异常', 'warn': '注意'}.get(lv, lv), msg))

    if items:

        L.append('-' * 78)

    by_item = {x['item_id']: x for x in labeled}

    for it in items:

        x = by_item.get(it['id'])

        if not x:

            continue

        tag = {'green': '[绿]', 'blue': '[蓝]', 'amber': '[黄]',

               'red': '[红缺图]', 'red_flag': '[红!!]',

               'declared': '[灰声明无材料]'}.get(x['status'], '[?]')

        L.append('%s %s' % (tag, it['text'][:60]))

        if x['img']:

            im = x['img']

            L.append('      → %s  %s  %s  (%.0f分, %s)'

                     % (im['file'], im['size'], TYPE_LABEL.get(im['cert']['type'], '?'),

                        x['score'], im['ocr_trust']))

            if im['person_status'] not in ('ok', 'roster_in'):

                L.append('        姓名: %s' % im['person_detail'])

            if im['need_cloud']:

                L.append('        (本地识别置信度偏低，建议云端复核)')

            for alt in x['alternatives']:

                L.append('        备选: %s %s' % (alt['file'], TYPE_LABEL.get(alt['cert']['type'])))

        else:

            _sm = res.get('score_matrix') or []

            hi = (sorted(_sm[it['id']], reverse=True)[:1]

                  if (_sm and 0 <= it['id'] < len(_sm)) else [])

            L.append('      → 未找到匹配图片（最高候选仅 %.0f 分）' % (hi[0] if hi else 0))

    # 无归属图片（排除体测/志愿这类整块截图）

    orphan = [imgs[i] for i in res.get('orphan_images', res['unmatched_images'])]

    if orphan:

        L.append('-' * 78)

        L.append('⚠ 无归属图片 %d 张（竞赛/科研/荣誉板块里没主的图）：' % len(orphan))

        for im in orphan:

            L.append('   %s  %s  %s | %s'

                     % (im['file'], im['size'], TYPE_LABEL.get(im['cert']['type'], '?'),

                        im['ocr'].replace('\n', ' ')[:56]))

    slevel = res.get('section_level_images') or []

    if slevel:

        L.append('板块级材料 %d 张（体测/志愿等整块截图，正常）：%s'

                 % (len(slevel), ', '.join(imgs[i]['file'] for i in slevel)))

    if ignored:

        L.append('-' * 78)

        L.append('已忽略的无效图 %d 张：%s'

                 % (len(ignored), ', '.join('%s(%s)' % (x['media'].split("/")[-1], x['reason'])

                                            for x in ignored)))

    return '\n'.join(L)





def main():

    global REF

    print('加载参考数据 …')

    ranking = reference.load_ranking()

    comps = reference.load_competitions()

    REF = {'ranking': ranking, 'comp_items': comps['items']}

    print('  成绩表 %d 条学号记录 / A类竞赛 %d 条' % (len(ranking['by_sid']), len(comps['items'])))

    print()



    files = [p for p in sorted(glob.glob(os.path.join(ROOT, '*.docx')))

             if '模板' not in p and not os.path.basename(p).startswith('~$')]

    if len(sys.argv) > 1:

        key = sys.argv[1]

        files = [p for p in files if key in p]



    os.makedirs(OUT_DIR, exist_ok=True)

    all_out = []

    for p in files:

        r, items, imgs, ignored, res, labeled = analyze_file(p, comps['items'], ref=REF)

        txt = render_console(r, items, imgs, ignored, res, labeled)

        print(txt)

        print()

        all_out.append({

            'file': p,

            'student': r['student'],

            'items': [{'id': i['id'], 'section': i['section'], 'sublevel': i['sublevel'],

                       'text': i['text']} for i in items],

            'images': [{k: v for k, v in im.items() if k not in ('sig', 'ocr')}

                       | {'ocr_text': im['ocr']} for im in imgs],

            'ignored': ignored,

            'matches': [{'item_id': x['item_id'],

                         'item_text': x['item']['text'],

                         'img_file': x['img']['file'] if x['img'] else None,

                         'score': x['score'],

                         'status': x['status'],

                         'reasons': x['reasons'],

                         'pending_reason': x.get('pending_reason') or '',

                         'alternatives': [a['file'] for a in x.get('alternatives', [])]}

                        for x in labeled],

            'eligibility': res.get('eligibility') or {},

            'orphan_images': [imgs[i]['file'] for i in (res.get('orphan_images') or [])],

            'section_level_images': [imgs[i]['file'] for i in (res.get('section_level_images') or [])],

        })



    with open(os.path.join(OUT_DIR, 'result.json'), 'w', encoding='utf-8') as f:

        json.dump(all_out, f, ensure_ascii=False, indent=1, default=str)

    print('结果已写入 %s' % os.path.join(OUT_DIR, 'result.json'))





REF = {}

IMG_CACHE = []



if __name__ == '__main__':

    main()

