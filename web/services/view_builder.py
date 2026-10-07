# -*- coding: utf-8 -*-
"""
把引擎产出的「学生记录」翻译成「模板页面视图模型」。

放在这里而不是模板里，是因为这套分组/归拢逻辑有点绕（模板板块 + 材料卡 +
人工改判叠加），写在 Jinja 里会很难维护。模板只负责画。
"""
from .. import db
from . import sections as SEC

# 板块顺序/名称由「奖学金项目」配置（见 services/sections.py）。
# 这里保留一份「国家奖学金模板」的默认值，供没绑定项目的老数据与测试使用。
TPL_SECTIONS = [(k, SEC.with_ordinal(i, SEC.CATALOG_MAP[k]))
                for i, k in enumerate(SEC.TPL_KEYS)]
EXTRA_SECTIONS = [(k, SEC.CATALOG_MAP[k]) for k in SEC.EXTRA_KEYS]
SEC_LABEL = dict(TPL_SECTIONS + EXTRA_SECTIONS)

STATUS = {
    'green':        ('自动配对', 'ok', '系统高置信配对，且没有任何反向证据'),
    'blue':         ('跨位纠正', 'blue', '系统改了原文位置，务必看一眼'),
    'amber':        ('待确认', 'warn', '存在反向证据，需人工判断'),
    'pending_weak': ('待确认·附件疑似', 'warn', '条目下只有它，但内容对不上'),
    'red':          ('缺图', 'bad', '没有任何图片达到配对门槛'),
    'red_flag':     ('姓名异常', 'bad', '材料上的姓名与申报人不符'),
    'declared':     ('声明无材料', 'muted', '学生自己写了「（无材料）」'),
    # 人工判定后的状态
    'confirmed':    ('人工确认', 'ok', ''),
    'rejected':     ('人工否定', 'bad',
                     '辅导员已否定该条，不计入导出材料；可随时改回'),
    'manual':       ('人工改派', 'blue', ''),
    'adopted':      ('人工采纳', 'ok', ''),
}

# 这些板块本来就以"整块一张截图"的形式提交，不存在"学生漏写某条奖项"的问题。
# 其余板块里出现没主的图，才需要提醒"学生可能是懒得写奖项"。
SELF_CONTAINED_SECTIONS = {'体测', '志愿'}

SEM_CLASS = {
    'scholarship': '奖学金类', 'competition': '学科竞赛类', 'research': '科研创新类',
    'honor': '荣誉类', 'skill': '技能证书类', 'physical': '体质健康',
    'volunteer': '志愿服务', 'level_exam': '外语/等级考试',
}


def _img_cell(im, tags=None):
    rd = (im.get('reading') or {})
    return {
        'file': im.get('file'), 'sha': im.get('sha256'), 'url': im.get('url'),
        'size': im.get('size'), 'kind_label': im.get('kind_label'),
        'semantic': im.get('semantic'),
        'award_name': rd.get('award_name') or rd.get('title'),
        'award_level': rd.get('award_level'),
        'year': rd.get('academic_year') or rd.get('year'),
        'issuer': rd.get('issuer'), 'vlevel': im.get('vlevel'),
        'persons': rd.get('persons') or [],
        'metrics': rd.get('metrics') or {},
        'note': rd.get('note'),
        'source': im.get('source'),
        'person_status': im.get('person_status'),
        'person_detail': im.get('person_detail'),
        'oriented': im.get('oriented'),
        'tags': tags or [],
        # 摘要一行（缩略图下方）
        'summary': ' · '.join([x for x in [
            rd.get('award_name') or rd.get('title'),
            rd.get('award_level'),
            str(rd.get('academic_year') or rd.get('year') or '')] if x]),
    }


def _metrics_text(m):
    return '、'.join('%s=%s' % (k, v) for k, v in (m or {}).items())


def build(record, file_id=None, scheme=None, reject_mode='remove'):
    """
    record: engine_adapter.analyze_one 产出的记录
    scheme: 该奖学金项目的板块方案（[{key,label,required}]）；不传则用国奖模板默认值
    reject_mode: 人工否定的条目在导出时怎么处理（'remove' / 'mark'），只用于界面提示
    """
    file_id = file_id or record.get('file_id')
    scheme = SEC.normalize_scheme(scheme) or SEC.default_scheme()
    scheme_labels = SEC.labels(scheme)
    scheme_key_set = set(SEC.keys(scheme))
    reject_mode = reject_mode if reject_mode in ('remove', 'mark') else 'remove'
    images = record.get('images') or []
    imap = {im['file']: im for im in images}
    imap_sha = {im.get('sha256'): im for im in images}
    items = record.get('items') or []

    # ---- 人工改判覆盖引擎结论 ----
    manual = {}
    if file_id:
        for r in db.q_dict('SELECT * FROM match_states WHERE file_id=?', (file_id,)):
            manual[r['item_id']] = r

    adoptions = []
    if file_id:
        adoptions = db.q_dict('SELECT * FROM orphan_adoptions WHERE file_id=?', (file_id,))

    def _jump_of(st):
        """这个状态属于顶部哪个统计 chip —— 让 chip 可点击跳转（见 review.html）。"""
        if st in ('amber', 'pending_weak'):
            return ['need', 'attn']
        if st in ('red', 'red_flag', 'declared'):
            return ['miss', 'attn']
        if st == 'rejected':
            return ['rej', 'attn']
        return ['auto']

    # ---- 逐条配对行 ----
    rows = []
    isec = {it['id']: it['section'] for it in items}
    for m in record.get('matches') or []:
        iid = m['item_id']
        st = m['status']
        img = imap.get(m.get('img_file')) if m.get('img_file') else None
        man = manual.get(iid)
        man_note = ''
        if man:
            if man['decision'] == 'confirm':
                st = 'confirmed'
            elif man['decision'] == 'reject':
                # ★ 人工否定 = 一条独立的结论，**不是"缺图"**。
                #   所以保留这张证明图：辅导员要知道自己否的是哪张，
                #   而且随时可以「改回通过」，图不能丢。
                st = 'rejected'
            elif man['decision'] == 'reassign':
                st = 'manual'
                img = imap_sha.get(man['img_sha']) or imap.get(man['img_file']) or img
            man_note = man.get('note') or ''

        reasons = m.get('reasons') or []
        pos = [r[0] for r in reasons if r[1] >= 0]
        neg = [r[0] for r in reasons if r[1] < 0]
        label, cls, hint = STATUS.get(st, ('?', 'muted', ''))

        spec = None
        if m.get('score') is None:
            spec = {'manual': bool(man)}
        if man and man['decision'] == 'reassign' and img:
            spec = {'manual': True}

        rows.append({
            'item_id': iid,
            'item_text': m.get('item_text') or '',
            'section': isec.get(iid),
            'status': st, 'label': label, 'cls': cls, 'hint': hint,
            'score': m.get('score'),
            'pos': pos, 'neg': neg,
            'pending_reason': m.get('pending_reason') or '',
            'rule_hits': m.get('rule_hits') or [],
            'alternatives': m.get('alternatives') or [],
            'img': _img_cell(img) if img else None,
            'manual': bool(man), 'manual_note': man_note,
            'jump': _jump_of(st),
            'is_ok': st in ('green', 'blue', 'confirmed', 'manual'),
        })

    # ---- 人工采纳：把未认领图变成条目 ----
    adopted_sha = set()
    for a in adoptions:
        im = imap_sha.get(a['img_sha'])
        if not im:
            continue
        adopted_sha.add(a['img_sha'])
        sec = a.get('section') or im.get('section') or '未归类'
        rows.append({
            'item_id': None,
            'item_text': a['award_name'],
            'section': sec,
            'status': 'adopted', 'label': STATUS['adopted'][0],
            'cls': 'ok', 'hint': '由辅导员从「未认领图片」采纳而来',
            'score': None, 'pos': ['人工采纳'], 'neg': [], 'pending_reason': '',
            'alternatives': [], 'img': _img_cell(im), 'manual': True,
            'manual_note': '', 'jump': ['auto', 'adopted'],
            'adopt_sha': a['img_sha'], 'adopt_section': sec,
            'in_scheme': sec in scheme_key_set,
            'is_ok': True,
        })

    # ---- 没有配上的图（未认领 / 板块级） ----
    used = {r['img']['file'] for r in rows if r['img']}
    sec_has_items = set()
    for it in items:
        sec_has_items.add(it.get('section'))
    leftover = []
    for im in images:
        if im['file'] in used or im.get('sha256') in adopted_sha:
            continue
        rd = im.get('reading') or {}
        sec = im.get('section') or ''
        tags = []
        # 三档区分，避免把"学生漏写奖项"和"这类材料本来就是整块截图"混为一谈
        if sec in SELF_CONTAINED_SECTIONS:
            tags.append(('muted', '板块级材料'))
        elif sec not in sec_has_items:
            tags.append(('warn', '本板块无文字条目'))
        else:
            tags.append(('warn', '未认领到具体奖项'))
        if not (rd.get('academic_year') or rd.get('year')):
            tags.append(('warn', '年份不明确'))
        if im.get('suggest_ok'):
            tags.append(('ok', '证书可核对本人'))
        if im.get('person_status') == 'no_name':
            tags.append(('muted', '图上未显示姓名'))
        cell = _img_cell(im, tags)
        cell.update({
            'ai_judgment': im.get('ai_judgment'),
            'suggest_ok': im.get('suggest_ok'),
            'suggest_award': im.get('suggest_award'),
            'nearest': im.get('nearest') or {},
            'metrics_text': _metrics_text(rd.get('metrics')),
            'sem_label': SEM_CLASS.get(im.get('semantic'), ''),
            'section': im.get('section'),
            'self_contained': (im.get('section') or '') in SELF_CONTAINED_SECTIONS,
            'jump': ['left'],
        })
        leftover.append(cell)

    # ---- 按板块归拢 ----
    buckets = {}
    for r in rows:
        buckets.setdefault(r['section'] or '未归类', []).append(r)
    lb = {}
    for c in leftover:
        key = c.get('section') or '未归类'
        lb.setdefault(key, []).append(c)

    # 顺序：方案里的板块 → 方案外但目录里有的（按目录顺序）→ 其余。
    # 必须用 SEC.ordered_keys 统一口径，否则「人工采纳到方案外板块」的条目
    # 在导出时找不到对应类目（会整条丢掉）。
    keys = SEC.ordered_keys(scheme, set(buckets) | set(lb))
    labels_all = SEC.full_labels(scheme, keys)

    sections = []
    for k in keys:
        sec_rows = buckets.get(k) or []
        auto = sum(1 for r in sec_rows if r['is_ok'])
        left_all = sorted(lb.get(k) or [], key=lambda x: x['file'])
        left_main = [c for c in left_all if not c['self_contained']]
        left_self = [c for c in left_all if c['self_contained']]
        sections.append({
            'key': k,
            'label': labels_all.get(k) or SEC.CATALOG_MAP.get(k, k),
            'rows': sec_rows, 'leftover': left_all,
            'leftover_main': left_main, 'leftover_self': left_self,
            'n_rows': len(sec_rows), 'n_auto': auto,
            'n_left': len(left_all), 'n_left_main': len(left_main),
            'n_left_self': len(left_self),
            'n_rejected': sum(1 for r in sec_rows if r['status'] == 'rejected'),
            'ok': bool(sec_rows or left_all),
            'is_tpl': k in scheme_key_set,
        })

    # ---- 板块覆盖情况（中性标注，缺了不影响其他板块） ----
    si, mi = {}, {}
    for it in items:
        si[it['section']] = si.get(it['section'], 0) + 1
    for im in images:
        mi[im.get('section')] = mi.get(im.get('section'), 0) + 1
    coverage = [{'key': s['key'], 'label': scheme_labels.get(s['key'], s['label']),
                 'required': bool(s.get('required')),
                 'items': si.get(s['key'], 0), 'images': mi.get(s['key'], 0),
                 'has': bool(si.get(s['key'], 0) or mi.get(s['key'], 0))}
                for s in scheme]

    # ---- 统计 ----
    def cnt(*sts):
        return sum(1 for r in rows if r['status'] in sts)
    stats = {
        'n_items': len(rows),
        'auto': cnt('green', 'blue', 'confirmed', 'manual', 'adopted'),
        'need': cnt('amber', 'pending_weak'),
        'miss': cnt('red', 'red_flag'),
        'declared': cnt('declared'),
        'rejected': cnt('rejected'),
        'adopted': cnt('adopted'),
        'left': len(leftover),
        'images': len(images),
    }
    stats['rate'] = round(100.0 * stats['auto'] / stats['n_items'], 0) if stats['n_items'] else 0

    # 改派时可选的图片候选（全部图片，带一句摘要，方便在下拉里认）
    cand_json = [
        {'sha': im.get('sha256'), 'file': im.get('file'),
         'label': '%s | %s' % (im.get('file'),
                               ((im.get('reading') or {}).get('award_name')
                                or (im.get('reading') or {}).get('title')
                                or im.get('kind_label') or '未识别'))}
        for im in images
    ]

    return {
        'file_id': file_id,
        'student': record.get('student') or {},
        'scheme': scheme,
        'reject_mode': reject_mode,
        'sections': sections,
        'coverage': coverage,
        'stats': stats,
        'eligibility': record.get('eligibility') or {},
        'all_images': [_img_cell(im) for im in images],
        'adoptions': adoptions,
        'cand_json': cand_json,
    }
