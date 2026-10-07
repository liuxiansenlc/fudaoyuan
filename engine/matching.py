# -*- coding: utf-8 -*-
"""
配对引擎：把奖项条目和证书图片对上号。

三层信号，逐层可解释：
  1. 板块先验 —— 板块决定"这里应该出现什么类型的证书"，类型不符是最强异常信号
  2. 内容匹配 —— 赛事名（归一到 A类名单标准名）、奖项短语、等级、年份
  3. 姓名核对 —— 个人证书要求姓名匹配；名单类截图要求申报人出现在名单里

分配用匈牙利算法做全局最优，而不是逐条找最像的：
贪心会抢图（A 抢走了 B 唯一能配的图），全局最优能避免。

支持"不匹配"：每条奖项可以配一个虚拟的"我没找到图"，代价是阈值 T。
这样得分很低的组合不会被强行配在一起。
"""
import re

from reference import name_similarity, match_competition
from reading import semantic_type
from settings import DEFAULT
import rules_engine

# ------------------------------------------------------------ 配置（全部来自 settings.DEFAULT）
# 这些常量名保持不变，只是取值来源从"写死在代码里"改成"规则包"。
# 函数都接受可选 cfg（默认 DEFAULT），Web 层可注入按批次覆盖后的配置。
_V = DEFAULT.vocab

SCHOLARSHIP_PHRASES = _V['scholarship_phrases']
HONOR_PHRASES = _V['honor_phrases']
SKILL_PHRASES = _V['skill_phrases']
PHYSICAL_PHRASES = _V['physical_phrases']
VOLUNTEER_PHRASES = _V['volunteer_phrases']
RESEARCH_PHRASES = _V['research_phrases']

RE_AWARD_LEVEL = re.compile(DEFAULT.regex['award_level'])
RE_YEAR_RANGE = re.compile(DEFAULT.regex['year_range'])
RE_YEAR = re.compile(DEFAULT.regex['year'])
RE_COMP_NAME = re.compile(DEFAULT.regex['comp_name'])
RE_COMP_SHORT = re.compile(DEFAULT.regex['comp_short'])   # 与 RE_COMP_NAME 同处定义，修掉"定义晚于引用"
ITEM_LEVEL = re.compile(DEFAULT.regex['item_level'])

SECTION_EXPECT = {k: set(v) for k, v in DEFAULT.sections['expect'].items()}
SECTION_KINDS = {k: set(v) for k, v in DEFAULT.sections['kinds'].items()}

# 注意：这里是"展示名表"（key → 中文名），与 reading.SEM_KEYS（key → 关键词列表）不同
SEM_KEYS = [tuple(x) for x in DEFAULT.sem_labels]
SEM_LABEL = dict(SEM_KEYS)
SEM_LABEL.update(DEFAULT.sem_label_extra)


def _phrases(text, vocab):
    t = re.sub(r'\s+', '', text)
    return {p for p in vocab if p in t}


def _levels(text):
    t = re.sub(r'\s+', '', text)
    out = set(RE_AWARD_LEVEL.findall(t))
    if not out:
        m = re.search(r'([上—\-一])\s*等奖', t)
        if m:
            out.add('一等奖')
    return out


def _years(text):
    t = re.sub(r'\s+', '', text)
    ys = set()
    for a, b in RE_YEAR_RANGE.findall(t):
        ys.add(a)
        ys.add(b)
        ys.add('%s-%s' % (a, b))
    for y in RE_YEAR.findall(t):
        ys.add(y)
    return ys


def _comp_names(text):
    t = re.sub(r'\s+', '', text)
    out = set()
    for n in RE_COMP_NAME.findall(t) + RE_COMP_SHORT.findall(t):
        n = _clean_comp_name(n)
        if len(n) >= 3:
            out.add(n)
    return out


STOP_LCS = set(_V['stop_lcs'])

# 这些词出现在任何同类材料里，不能当作"同一个奖项"的证据
GENERIC_MASK = _V['generic_mask']


def _mask_generic(t):
    for w in GENERIC_MASK:
        t = t.replace(w, '·' * len(w))
    return t


def _lcs(a, b):
    """最长公共子串（连续）。用于处理学生只写赛事简称的情况。"""
    if not a or not b:
        return ''
    lb = len(b)
    prev = [0] * (lb + 1)
    best = end = 0
    for i in range(1, len(a) + 1):
        cur = [0] * (lb + 1)
        ai = a[i - 1]
        for j in range(1, lb + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best, end = cur[j], i
        prev = cur
    return a[end - best:end]


def award_signature(text, comps, comp_items, cfg=None):
    """把一段文本提炼成"奖项特征"：赛事标准名、短语、等级、年份"""
    _c = cfg or DEFAULT
    _vc = _c.vocab
    sig = {
        'scholarship': _phrases(text, _vc['scholarship_phrases']),
        'honor': _phrases(text, _vc['honor_phrases']),
        'skill': _phrases(text, _vc['skill_phrases']),
        'physical': _phrases(text, _vc['physical_phrases']),
        'volunteer': _phrases(text, _vc['volunteer_phrases']),
        'research': _phrases(text, _vc['research_phrases']),
        'levels': _levels(text),
        'years': _years(text),
        'comp_raw': _comp_names(text),
        'comp_std': [],
    }
    # 赛事名归一到 A类名单标准名
    if comp_items:
        m = match_competition(text, comp_items, topk=2,
                              min_sim=_c.t('ref_comp_sim'),
                              min_gap=_c.t('ref_comp_gap'))
        sig['comp_std'] = [(x[0], x[1], x[2]) for x in m if x[1] >= _c.t('comp_std_min')]
    return sig


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(set(a) | set(b))


# 赛事名抽取时最常见的引导词（"关于公布…"、"由…承办的…"）
_LEAD_NOISE = _V['lead_noise']


def _strip_lead(s):
    changed = True
    while changed and len(s) > 8:
        changed = False
        for w in _LEAD_NOISE:
            if s.startswith(w) and len(s) - len(w) >= 8:
                s = s[len(w):]
                changed = True
    return s


def _comp_sim(A, B, cfg=None):
    """
    赛事名集合的相似度。
    不能只做集合相等判断：正则抽出来的名字常带前缀污染
    （条目里是"湖州师范学院第十四届电子设计竞赛"，
      图片里是"关于公布湖州师范学院第十四届电子设计竞赛"），
    完全相等的判断会把同一场赛事判成"对不上"。
    """
    from difflib import SequenceMatcher
    best = 0.0
    for a in A:
        a2 = _strip_lead(a)
        for b in B:
            b2 = _strip_lead(b)
            if len(a2) >= 6 and len(b2) >= 6 and (a2 in b2 or b2 in a2):
                return 1.0
            best = max(best, SequenceMatcher(None, a2, b2).ratio())
    return best if best >= (cfg or DEFAULT).t('comp_sim_min') else 0.0


def _fuzzy_contain(phrase, text):
    """
    短语在长文本里的"最相似片段"占比。
    用于处理学生表述与证书表述的措辞差异，例如
    学生写"文明修身先进个人"，证书上印的是"文明修身学生先进"。
    """
    from difflib import SequenceMatcher
    if not phrase or not text:
        return 0.0
    m = SequenceMatcher(None, phrase, text).find_longest_match(0, len(phrase), 0, len(text))
    return m.size / len(phrase)


def _coverage(phrase, text):
    """
    phrase 的字形片段有多大比例出现在 text 里。
    比最长公共子串更稳：能区分"优秀学生"和"优秀学生干部"——
    后者只有 3/5 的片段命中，不会误配到"校级优秀学生"。
    """
    if not phrase or not text:
        return 0.0
    p = re.sub(r'\s+', '', phrase)
    t = re.sub(r'\s+', '', text)
    if p in t:
        return 1.0
    grams = [p[i:i + 2] for i in range(len(p) - 1)]
    if not grams:
        return 0.0
    hit = sum(1 for g in grams if g in t)
    return hit / len(grams)


# ------------------------------------------------------------ 打分
def score_pair(item, img, ctx, cfg=None):
    """
    给一条奖项 × 一张图片打分（0~100），并给出可读的理由列表。
    权重经过标定，满分恰好 100：类型22 + 证据形式6 + 名称38 + 等级12 + 级别10 + 年份8 + 姓名6 (+15 三重命中)
    权重取自规则包（settings.DEFAULT.weights），可由界面调整。
    """
    W = (cfg or DEFAULT).weights
    reasons = []
    itxt = re.sub(r'\s+', '', item['text'])

    # ---------- 1. 类型先验（22）
    # 优先用"条目自身说了它是什么"来比对，而不是只看板块。
    # 实测有学生把"英语四级""普通话"写进「荣誉情况」板块，
    # 只看板块会把正确配对按"类型不符"扣分。
    img_sem = img.get('semantic')
    if img_sem is None:
        ctypes = set(img.get('cert', {}).get('all_types') or [])
        for cand in ('scholarship', 'honor', 'competition', 'level_exam',
                     'physical', 'volunteer', 'research'):
            if cand in ctypes:
                img_sem = cand
                break
    item_sem = semantic_type(item['text'])
    if item_sem and img_sem:
        if item_sem == img_sem:
            reasons.append(('奖项类型一致（%s）' % SEM_LABEL.get(item_sem, item_sem), W['type_match']))
        else:
            reasons.append(('奖项类型不符（申报%s / 材料%s）'
                            % (SEM_LABEL.get(item_sem, item_sem),
                               SEM_LABEL.get(img_sem, img_sem)), W['type_mismatch']))
    else:
        exp_sem = SECTION_EXPECT.get(item['section'])
        sem = img_sem
        if exp_sem and sem:
            if sem in exp_sem:
                reasons.append(('奖项类型与板块相符（%s）' % SEM_LABEL.get(sem, sem), W['section_match']))
            else:
                reasons.append(('奖项类型与板块不符（%s板块里出现%s）'
                                % (item['section'], SEM_LABEL.get(sem, sem)), W['section_mismatch']))
    pts = sum(x[1] for x in reasons)

    # ---------- 2. 证据形式是否适用（6）
    kind = img.get('kind')
    if kind and kind != 'other':
        exp_kind = SECTION_KINDS.get(item['section'])
        if exp_kind and kind in exp_kind:
            reasons.append(('证据形式适用（%s）' % img.get('kind_label', kind), W['kind_match']))
        elif exp_kind:
            reasons.append(('证据形式与板块惯例不同（%s）' % img.get('kind_label', kind), W['kind_mismatch']))
    pts = sum(x[1] for x in reasons)

    # ---------- 3. 名称证据（0~38）：赛事名归一 > 视觉奖项名 > 短语重合
    ia, ic = item['sig'], img['sig']
    name_pts = 0.0
    if ia.get('comp_std') and ic.get('comp_std'):
        ia_names = {n for n, _, _ in ia['comp_std']}
        ic_names = {n for n, _, _ in ic['comp_std']}
        same = ia_names & ic_names
        if same:
            name_pts = W['comp_std_equal']
            reasons.append(('赛事名归一后一致：%s' % sorted(same)[0][:24], W['comp_std_equal']))
        else:
            name_pts = W['comp_name_diff']
            reasons.append(('赛事名指向不同赛事（%s vs %s）'
                            % (sorted(ia_names)[0][:14], sorted(ic_names)[0][:14]), W['comp_name_diff']))
    if name_pts == 0 and ia.get('comp_raw') and ic.get('comp_raw'):
        sim = _comp_sim(ia['comp_raw'], ic['comp_raw'], cfg)
        if sim > 0:
            name_pts = round(W['comp_raw_sim_scale'] * sim, 1)
            reasons.append(('赛事名文字重合 %.0f%%' % (sim * 100), name_pts))
        else:
            name_pts = W['comp_raw_none']
            reasons.append(('赛事名对不上', W['comp_raw_none']))
    if name_pts == 0:
        an = ic.get('award_name') or ''
        if an:
            cov = _coverage(an, itxt)
            if cov >= W['award_name_coverage_min']:
                name_pts = round(W['award_name_scale'] * cov, 1)
                reasons.append(('证书奖项名「%s」与学生描述吻合' % an, name_pts))
    if name_pts == 0:
        # 学生普遍只写简称（"百度之星""蓝桥杯""天梯赛"），而证书上印的是全称。
        # 用最长公共子串兜底，能救回"只靠简称描述"的情况；
        # 但要排掉"大学生""竞赛""三等奖"这类通用词，避免无脑匹配。
        an = ic.get('award_name') or ''
        seg = _lcs(_mask_generic(an), _mask_generic(itxt)) if an else ''
        seg = seg.replace('·', '').strip()
        if len(seg) >= W['lcs_min_seg'] and seg not in STOP_LCS:
            name_pts = min(W['lcs_cap'], W['lcs_per_char'] * len(seg))
            reasons.append(('证书奖项名与学生描述共有「%s」' % seg, name_pts))
    if name_pts == 0:
        best_j, best_k = 0.0, None
        for key, label in SEM_KEYS:
            j = _jaccard(ia.get(key) or set(), ic.get(key) or set())
            if j > best_j:
                best_j, best_k = j, label
        if best_j > 0:
            name_pts = round(W['phrase_jaccard_scale'] * best_j, 1)
            reasons.append(('%s表述一致' % best_k, name_pts))
        else:
            for key, label in SEM_KEYS:
                for p in (ia.get(key) or set()):
                    r = _coverage(p, ic.get('text') or '')
                    if r >= W['phrase_fuzzy_min']:
                        name_pts = round(W['phrase_fuzzy_scale'] * r, 1)
                        reasons.append(('%s表述近似（%s）' % (label, p), name_pts))
                        break
                if name_pts:
                    break
    pts = sum(x[1] for x in reasons)

    # ---------- 4. 奖项等级（+12 / -20）
    if ia['levels'] and ic['levels']:
        if ia['levels'] & ic['levels']:
            reasons.append(('奖项等级一致（%s）' % '/'.join(sorted(ia['levels'] & ic['levels'])), W['award_level_match']))
        else:
            reasons.append(('奖项等级冲突（申报 %s / 证书 %s）'
                            % ('/'.join(sorted(ia['levels'])), '/'.join(sorted(ic['levels']))), W['award_level_mismatch']))
    pts = sum(x[1] for x in reasons)

    # ---------- 5. 级别（+10 / -12）：国家级/省级/校级/院级
    ilv = item.get('level') or (ITEM_LEVEL.search(item['text']).group(1)
                                if ITEM_LEVEL.search(item['text']) else None)
    vlv = img.get('vlevel')
    if ilv and vlv:
        if ilv == vlv:
            reasons.append(('级别一致（%s）' % ilv, W['gov_level_match']))
        else:
            reasons.append(('级别不符（申报%s / 落款%s）' % (ilv, vlv), W['gov_level_mismatch']))
    pts = sum(x[1] for x in reasons)

    # ---------- 6. 年份（+8 / 相邻 +3 / 相差大 -6）
    iy = {y for y in ia['years'] if len(y) == 4}
    cy = {y for y in ic['years'] if len(y) == 4}
    img_year = img.get('reading', {}).get('year') or ''
    if not cy and img_year:
        cy = set(re.findall(r'20\d{2}', str(img_year)))
    if iy and cy:
        if iy & cy:
            reasons.append(('年份一致（%s）' % '/'.join(sorted(iy & cy)), W['year_match']))
        elif min((abs(int(a) - int(b)) for a in iy for b in cy), default=99) <= 1:
            reasons.append(('年份相邻，可能学年跨年', W['year_adjacent']))
        else:
            reasons.append(('年份相差较大', W['year_far']))
    pts = sum(x[1] for x in reasons)

    # ---------- 7. 姓名（+6 / -40）
    st = img.get('person_status')
    if st in ('ok', 'roster_in'):
        reasons.append(('申报人姓名在材料中', W['name_ok']))
    elif st == 'roster_missing':
        reasons.append(('名单类材料里未找到申报人', W['roster_missing']))
    elif st == 'name_conflict':
        reasons.append(('材料上的姓名与申报人不符', W['name_conflict']))
    # ---------- 8. 多重信号同时命中（+15）
    # 奖项等级、级别、申报人姓名三项同时命中，巧合概率很低；
    # 此时即便赛事名因简称/全称差异对不上，也应视为强候选。
    if (ia['levels'] and ia['levels'] & ic['levels']
            and ilv and vlv and ilv == vlv
            and st in ('ok', 'roster_in')):
        reasons.append(('等级、级别、姓名三项同时一致', W['triple_bonus']))
    pts = sum(x[1] for x in reasons)

    # ---------- 9. 自定义规则（受限 DSL，纯函数求值，零网络零随机）
    # 规则命中的加减分并入理由列表，这样它会出现在界面的"命中/反证"里，
    # 人工能看到"这条为什么被扣分"。
    if cfg is not None:
        _rules = getattr(cfg, 'nl_rules', None)
        if _rules:
            _eff = rules_engine.evaluate(item, img, _rules, score=pts)
            for _m in _eff['matched']:
                _nm = _m.get('name') or _m.get('id') or '规则'
                _txt = '；'.join(_m.get('effects') or [])
                reasons.append(('规则「%s」：%s' % (_nm, _txt), 0))
            if _eff['score_delta']:
                # 逐条命中按 0 分记（只给人看），加减分合并成一条，避免权重重复计入
                reasons.append(('规则加减分合计', _eff['score_delta']))
                pts += _eff['score_delta']

    return max(W['score_min'], min(W['score_max'], pts)), reasons


# ------------------------------------------------------------ 匈牙利算法
def hungarian_max(cost_matrix):
    """
    求最小代价完美匹配（经典 e-maxx 实现，O(n^3)）。
    cost_matrix 必须是方阵。返回 p，p[j] = 分配给列 j 的行号（1-based）。
    """
    n = len(cost_matrix)
    INF = float('inf')
    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)
    way = [0] * (n + 1)
    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [INF] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = INF
            j1 = -1
            for j in range(1, n + 1):
                if not used[j]:
                    cur = cost_matrix[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(0, n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    return p


def assign(items, images, ctx, threshold=None, cfg=None):
    """
    全局最优分配，允许"不匹配"。
    返回 {matches: [(item_id, img_id, score, reasons)], unmatched_items, unmatched_images,
          score_matrix}
    """
    _c = cfg or DEFAULT
    if threshold is None:
        threshold = _c.t('match')
    n, m = len(items), len(images)
    if n == 0 or m == 0:
        return {
            'matches': [],
            'unmatched_items': [it['id'] for it in items],
            'unmatched_images': [im['id'] for im in images],
            'score_matrix': {},
        }

    score = [[0.0] * m for _ in range(n)]
    reason = {}
    for i, it in enumerate(items):
        for j, im in enumerate(images):
            s, rs = score_pair(it, im, ctx, cfg=cfg)
            score[i][j] = s
            reason[(i, j)] = rs

    size = n + m
    # 把"不匹配"建成虚拟行/虚拟列，代价 = 收益的负值。
    # 关键：放弃一对配对，会同时释放"条目侧的虚拟列"和"图片侧的虚拟行"两个槽，
    # 所以两个槽各记 threshold/2，合起来才正好等于阈值 T。
    # （这里曾经写成两个槽各记 T，导致实际门槛变成 2T，90 分的正确配对被人为拒掉。）
    half = threshold / 2.0
    a = [[0.0] * size for _ in range(size)]
    for i in range(n):
        for j in range(m):
            a[i][j] = -score[i][j]
        # 行 i 的虚拟列 m+i：这条奖项选择"不匹配"
        a[i][m + i] = -half
    for j in range(m):
        # 虚拟行 n+j 的列 j：这张图选择"不匹配"
        a[n + j][j] = -half
    for i in range(n):
        for j in range(m, size):
            if j != m + i:
                a[i][j] = 0.0
    for i in range(n, size):
        for j in range(m, size):
            a[i][j] = 0.0
        for j in range(m):
            if i - n != j:
                a[i][j] = 0.0

    p = hungarian_max(a)
    col2row = {j: p[j] for j in range(1, size + 1)}

    matches = []
    matched_items = set()
    matched_images = set()
    for j in range(1, size + 1):
        row = col2row.get(j, 0)
        if row == 0:
            continue
        r, c = row - 1, j - 1
        if r < n and c < m:
            if score[r][c] >= threshold:
                matches.append({
                    'item_id': items[r]['id'],
                    'img_id': images[c]['id'],
                    'score': round(score[r][c], 1),
                    'reasons': reason[(r, c)],
                })
                matched_items.add(items[r]['id'])
                matched_images.add(images[c]['id'])

    return {
        'matches': sorted(matches, key=lambda x: -x['score']),
        'unmatched_items': [it['id'] for it in items if it['id'] not in matched_items],
        'unmatched_images': [im['id'] for im in images if im['id'] not in matched_images],
        'score_matrix': score,
    }


def speculative_pairs(items, images, result, ctx, min_score=None, cfg=None):
    """
    给"没配上图"的条目，挂上文档里紧邻它、且无人认领的图片，标为待确认。

    为什么需要这一步：
    学生的图片是随文档流插入的，**位置本身是弱信号，但比"完全不管"强**。
    实测有学生把证明材料以截图形式贴在条目下方，内容又长又杂，
    内容相似度够不到阈值 —— 直接判"缺图 + 这张图未认领"，
    辅导员就得自己把两件事对起来，等于没帮上忙。
    挂上去标"待确认"，至少让人一眼看到"这条底下有张图，你自己判断"。
    """
    if min_score is None:
        min_score = (cfg or DEFAULT).t('speculative_min_score')
    imap = {i['id']: i for i in items}
    gmap = {i['id']: i for i in images}
    orphan = set(result['unmatched_images'])
    out = []
    for iid in result['unmatched_items']:
        it = imap[iid]
        if '无材料' in it['text']:
            continue
        cands = [gmap[i] for i in (it.get('adjacent_img_ids') or []) if i in orphan]
        if not cands:
            continue
        best, bs, br = None, -1e9, []
        for im in cands:
            s, rs = score_pair(it, im, ctx, cfg=cfg)
            if s > bs:
                best, bs, br = im, s, rs
        if best is None:
            continue
        # 挂载门槛：
        #   - 分数够高 → 直接挂（图很可能就是它的证明，只是内容没读全）
        #   - 位置旁边只有这一张图 → 也挂（那明显是学生贴在这条下面的，
        #     哪怕内容读不出、判不准，也要摆出来让人看，而不是丢进"未认领"）
        #   - 旁边一堆图且全都得分很低 → 不挂，留给"未认领图片"区
        if bs < min_score and len(cands) > 1:
            continue
        out.append({
            'item_id': iid, 'img_id': best['id'],
            'score': round(max(0.0, bs), 1), 'reasons': br, 'speculative': True,
        })
        orphan.discard(best['id'])
    return out


def label_matches(result, items, images, cfg=None):
    """
    给每个配对打上状态标签（分层标注）。
    green        自动配对   —— 无任何反证、证据充分
    pending_weak 待确认     —— 图在，但内容对不上，需人工判断
    blue         跨位纠正   —— 文档原本相邻的图不是它，系统在别处找到了
    amber        待确认     —— 有多个候选或存在反证
    amber        无归属图 / 缺图 / 人名异常
    """
    T = (cfg or DEFAULT).thresholds
    imap = {i['id']: i for i in items}
    gmap = {i['id']: i for i in images}
    by_item = {}
    for mm in result['matches']:
        by_item.setdefault(mm['item_id'], []).append(mm)

    out = []
    for iid, mlist in by_item.items():
        mlist.sort(key=lambda x: -x['score'])
        best = mlist[0]
        item = imap[iid]
        img = gmap[best['img_id']]
        # 是否有第二候选分数接近
        alt = [m for m in mlist[1:] if m['score'] >= best['score'] - T['alternative_gap']]
        # 文档里紧邻该条目的那张图是不是它
        adjacent = item.get('adjacent_img_ids') or []
        displaced = bool(adjacent) and best['img_id'] not in adjacent
        # 有没有"反证"：任何扣分的理由
        negatives = [r for r in best['reasons'] if r[1] < 0]
        person_ok = img.get('person_status') in ('ok', 'roster_in')

        if img.get('person_status') == 'name_conflict':
            # 证书上明确是别人的名字 —— 硬红线
            status = 'red_flag'
        elif best.get('speculative'):
            # 图是"挂"上去的（位置相邻但内容对不上），一定交人工判断
            status = 'pending_weak'
        elif best['score'] >= T['auto_confirm'] and not alt and not negatives:
            status = 'green'
        elif (best['score'] >= T['green_person_ok'] and not alt and not negatives and person_ok):
            # 没有反证、且申报人姓名能对上 —— 即便得分不顶尖也足够确信
            status = 'green'
        elif displaced:
            status = 'blue'
        else:
            status = 'amber'
        # ---- 自定义规则：可强制状态、可要求核对姓名、可附说明
        rule_hits, rule_notes = [], []
        if cfg is not None:
            _rules = getattr(cfg, 'nl_rules', None)
            if _rules:
                _eff = rules_engine.evaluate(item, img, _rules, score=best['score'])
                rule_hits = rules_engine.summarize_matched(_eff['matched'])
                rule_notes = list(_eff['notes'])
                if _eff['set_status']:
                    status = _eff['set_status']
                if _eff['require_person'] and not person_ok and status in ('green', 'blue'):
                    status = 'amber'
                    rule_notes.append('规则要求核对到本人姓名，但材料上核不到')
        if rule_hits:
            # 规则命中也要出现在理由列表里（权重 0，只是给人看）
            best = dict(best)
            best['reasons'] = list(best['reasons']) + [('规则：' + h, 0) for h in rule_hits]

        # 待确认的条目一定要有"为什么不能自动确认"的说法，方便人工判断
        why_pending = ''
        if status in ('amber', 'pending_weak'):
            if best.get('speculative'):
                why_pending = ('条目位置下面挂着这张图，但内容对不上'
                               + ('（' + '；'.join(r[0] for r in negatives) + '）' if negatives else ''))
            elif negatives:
                why_pending = '；'.join(r[0] for r in negatives)
            elif alt:
                why_pending = ('有 %d 张候选分数接近（%s），系统无法唯一确定'
                               % (len(alt) + 1,
                                  '、'.join(sorted({x.get('file', '') for x in
                                                    [gmap[m['img_id']] for m in alt] + [img]}))))
            elif best['score'] < T['pending_below']:
                why_pending = ('证据不足，仅得 %.0f 分：没有命中赛事名、等级等关键字段'
                               % best['score'])
            else:
                why_pending = '申报人姓名无法在材料上核对'
            if rule_notes:
                why_pending = '；'.join(rule_notes) + ('；' + why_pending if why_pending else '')
        out.append({
            'item_id': iid, 'item': item, 'img': img,
            'score': best['score'], 'reasons': best['reasons'],
            'status': status, 'alternatives': [gmap[m['img_id']] for m in alt],
            'displaced': displaced, 'negatives': negatives,
            'adjacent': [gmap[i] for i in adjacent if i in gmap],
            'pending_reason': why_pending,
            'rule_hits': rule_hits,
        })

    for iid in result['unmatched_items']:
        it = imap[iid]
        # 学生自己在条目里写了"（无材料）"——这是主动声明，不是异常
        declared = ('无材料' in it['text']) or ('无证明材料' in it['text'])
        out.append({'item_id': iid, 'item': it, 'img': None,
                    'score': 0, 'reasons': [],
                    'status': 'declared' if declared else 'red',
                    'displaced': False, 'negatives': [],
                    'alternatives': [], 'adjacent': []})
    return out

# （RE_COMP_SHORT 已上移到常量区，避免"定义晚于引用"）')


def _clean_comp_name(n):
    n = re.sub(r'^(?:20\d{2}(?:[-–—~至]20\d{2})?)?学年', '', n)
    n = re.sub(r'^学年', '', n)
    n = re.sub(r'^(?:第)?[0-9一二三四五六七八九十百]{1,4}届', '', n)
    n = re.sub(r'^20\d{2}年?', '', n)
    n = re.sub(r'^届', '', n)
    return n
