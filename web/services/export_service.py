# -*- coding: utf-8 -*-
"""
标准化 Word 导出。

要解决的真实问题：学生交上来的 docx 里，奖项名和图片经常是**错位、堆叠**的
——好几张图挤在一起，或者图跑到奖项名上面两页，辅导员核对时得来回翻。

导出的目标是重排成：
    板块标题 → 奖项名（带序号） → 紧跟其 ≥1 张证明图（居中、等比缩放）
顺序完全按模板六大板块 + 引擎与人工的配对结论重来，与原 Word 的排版无关。

实现要点：
- **以模板 docx 为基底**再清空段落，这样页面尺寸(A4)、页边距(上下2.54/左右3.17cm)、
  默认字体天然继承，不必用代码重建版式。清空时要保住 `<w:sectPr>`——
  它挂在 body 末尾，删段落时很容易一起删掉，删掉页面设置就全丢了。
- 图片宽度上限 = 可用宽度 14.651cm；同时限高，否则长截图（如 646x1437）会撑破页。
- 只导出「已确定」的条目；待确认 / 缺图统一进文末「待补充清单」，不混进正文。
- 同一奖项可带多张证明：正文里同名奖项重复贴的图会被引擎判成「未认领」，
  但只要它与该条目高度匹配（≥60 分），就一并挂到该条目下。
"""
import os
import re
import zipfile

from docx import Document
from docx.shared import Pt, Cm
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from ..config import WebConfig
from . import sections as SEC

# 版式常量（与模板实测一致）
AVAIL_W_CM = 14.65     # A4 21cm − 左右各 3.17cm
MAX_IMG_H_CM = 20.5    # 留出标题空间，避免长截图撑破页
HEAD_FONT = '黑体'
BODY_FONT = '宋体'
RELATED_MIN_SCORE = 60  # 「同名奖项的第二张证明」的最低匹配度
# 人工否定的条目在 mark 模式下要跟着的这个说明（管理员选了「保留并标注」时才出现）
REJECT_NOTE = '（经审核不符合申报规范，仅作留痕，不计入申报材料）'

# 板块名称也不再各写一份：统一从 sections.CATALOG 派生，
# 否则「导出用一套文案、审核页用另一套文案」会不知不觉漂移。
TPL_SECTIONS = [(k, SEC.with_ordinal(i, SEC.CATALOG_MAP[k]))
                for i, k in enumerate(SEC.TPL_KEYS)]
COMP_SUB = [('国家级', '（一）国家级'), ('省级', '（二）省级'), ('其他', '（三）校级及其他级别')]
EXPORTABLE = {'green', 'blue', 'confirmed', 'manual', 'adopted'}
PENDING = {'amber', 'pending_weak', 'red', 'red_flag', 'declared'}


# ------------------------------------------------------------------ 字体与段落
def _font(run, name, size, bold=False):
    run.font.size = Pt(size)
    run.font.name = name
    run.font.bold = bold
    rPr = run._element.get_or_add_rPr()
    rf = rPr.find(qn('w:rFonts'))
    if rf is None:
        rf = rPr.makeelement(qn('w:rFonts'), {})
        rPr.insert(0, rf)
    for a in ('w:ascii', 'w:hAnsi', 'w:eastAsia'):
        rf.set(qn(a), name)
    return run


def _para(doc, align=None, indent_pt=None, space_after=6, line=360):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    pf = p.paragraph_format
    if indent_pt:
        pf.first_line_indent = Pt(indent_pt)
    pf.space_after = Pt(space_after)
    pPr = p._element.get_or_add_pPr()
    sp = pPr.find(qn('w:spacing'))
    if sp is None:
        sp = pPr.makeelement(qn('w:spacing'), {})
        pPr.append(sp)
    sp.set(qn('w:line'), str(line))       # 360/240 = 1.5 倍行距，与模板一致
    sp.set(qn('w:lineRule'), 'auto')
    return p


# 学生原文里自带的序号（导出时已重新编号，这里要去掉，否则出现「1. 1.省级：…」）
_RE_LEAD_NO = re.compile(
    r'^\s*(?:(?:\d{1,2}\s*[\.、）)])|(?:[（(]\s*\d{1,2}\s*[)）])|(?:[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])'
    r'|(?:[（(]\s*[一二三四五六七八九十]\s*[)）]))+\s*')


def _clean_text(t):
    """去掉自带的序号前缀、收掉换行与多余空格。"""
    t = (t or '').replace('\u3000', ' ')
    t = re.sub(r'\s*\n\s*', ' ', t)
    prev = None
    while prev != t:
        prev = t
        t = _RE_LEAD_NO.sub('', t)
    return re.sub(r'\s{2,}', ' ', t).strip()


def _blank_document(template_path):
    """打开模板并清空正文，保留 sectPr（页面设置挂在它上面）。"""
    doc = Document(template_path)
    body = doc.element.body
    sect = body.find(qn('w:sectPr'))
    for child in list(body):
        if child is not sect:
            body.remove(child)
    return doc


# ------------------------------------------------------------------ 图片
def _img_path_from_url(url):
    name = os.path.basename(url or '')
    if not name:
        return None
    p = os.path.join(WebConfig.IMAGE_DIR, name)
    return p if os.path.exists(p) else None


def _add_image(doc, path):
    """等比缩放 + 居中。长图按高度上限反算宽度，避免撑破页。"""
    w = h = 0
    try:
        from PIL import Image
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        pass
    p = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=10)
    run = p.add_run()
    if w and h:
        ratio = float(h) / float(w)
        wcm = AVAIL_W_CM
        if wcm * ratio > MAX_IMG_H_CM:
            wcm = MAX_IMG_H_CM / ratio
        run.add_picture(path, width=Cm(wcm))
    else:
        run.add_picture(path, width=Cm(AVAIL_W_CM * 0.7))


def _count_drawings(doc):
    return len(doc.element.body.findall('.//' + qn('w:drawing')))


# ------------------------------------------------------------------ 主流程
def build_docx(view, out_path, template=None, include_pending=True,
               attach_related=True, program_name='', reject_mode=None):
    """
    view: view_builder.build() 的产物（已叠加人工判定）
    program_name: 用于标题（不同奖学金标题不同，不能写死"国家奖学金"）
    reject_mode: 'remove' 剔除人工否定项 / 'mark' 保留并标注；不传则用 view 里的
    返回 (out_path, stats)
    """
    template = template or WebConfig.TEMPLATE_DOCX
    if not os.path.exists(template):
        raise FileNotFoundError('模板不存在：%s' % template)

    doc = _blank_document(template)
    # ★ 板块顺序与名称**直接沿用审核页渲染出来的那份**（view['sections']），不再自己重算。
    #   原因：辅导员可以把未认领的图「采纳」到任意板块（例如「技能证书」），
    #   而这个板块可能没写在奖学金的板块方案里。若导出只遍历方案，
    #   这些条目会在 Word 里**凭空消失**（认领了却找不到类目）。
    #   view['sections'] 的顺序已是「方案板块 → 方案外按目录顺序」，与页面完全一致。
    sec_list = [(s['key'], s.get('label') or s['key']) for s in (view.get('sections') or [])]
    if not sec_list:
        sec_list = TPL_SECTIONS
    reject_mode = reject_mode or view.get('reject_mode') or 'remove'
    if reject_mode not in ('remove', 'mark'):
        reject_mode = 'remove'
    student = view.get('student') or {}
    name = (student.get('name') or '学生').strip()
    class_no = (student.get('class_no') or student.get('class') or '').strip()

    # ---- 把数据摊平，并按板块归拢 ----
    sec_map = {s['key']: s for s in view['sections']}
    rows_by_sec = {}
    for key, _ in sec_list:
        s = sec_map.get(key) or {}
        rows_by_sec[key] = [r for r in (s.get('rows') or [])]

    # 「同名奖项的第二张证明」：从该板块未认领的图里挑出来，挂到对应条目
    for key, _ in sec_list:
        s = sec_map.get(key) or {}
        pool = list(s.get('leftover_main') or [])
        if not attach_related or not pool:
            continue
        for r in rows_by_sec[key]:
            r['extra_imgs'] = []
        for c in pool:
            n = c.get('nearest') or {}
            target, best = None, 0.0
            for r in rows_by_sec[key]:
                txt = (r.get('item_text') or '').strip()
                if not txt or not n.get('item'):
                    continue
                # 用尾部片段比对，避开学生长描述里的前缀差异
                if n['item'].endswith(txt[-20:]) and (n.get('score') or 0) > best:
                    target, best = r, float(n.get('score') or 0)
            if target is not None and best >= RELATED_MIN_SCORE:
                target['extra_imgs'].append(c)
                c['_used_by_related'] = True

    # ---- 标题 ----
    title = '%s%s%s申请支撑材料' % (
        ('%s ' % class_no) if class_no else '', name,
        (program_name or '').strip() or '国家奖学金')
    t = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, space_after=14)
    _font(t.add_run(title), HEAD_FONT, 18, bold=True)

    # ---- 各板块（顺序与审核页一致，方案外的类目也会出现在这里，不会丢） ----
    kept = pending = marked = removed = 0
    for key, label in sec_list:
        s = sec_map.get(key) or {}
        rows = _exportable_rows(rows_by_sec[key], reject_mode)
        marked += sum(1 for r in rows if r['status'] == 'rejected')
        removed += sum(1 for r in rows_by_sec[key]
                       if r['status'] == 'rejected' and reject_mode == 'remove')
        self_cards = [c for c in (s.get('leftover_self') or [])
                      if not c.get('_used_by_related')]

        h = _para(doc, space_after=4)
        _font(h.add_run(label), HEAD_FONT, 14)

        if key == '竞赛':
            _write_competition(doc, rows)
        elif key == '科研':
            sub = _para(doc, indent_pt=24.1, space_after=4)
            _font(sub.add_run('（一）校级'), BODY_FONT, 12, bold=True)
            _write_rows(doc, rows)
        elif key in ('体测', '志愿'):
            _write_rows(doc, rows)
            _write_section_level(doc, self_cards)
        else:
            _write_rows(doc, rows)

        if not rows and not self_cards:
            p = _para(doc, indent_pt=24, space_after=6)
            _font(p.add_run('（本板块未提交材料）'), BODY_FONT, 12)

        kept += len(rows) + len(self_cards)
        pending += sum(1 for r in rows_by_sec[key] if r['status'] in PENDING)
        pending += len([c for c in (s.get('leftover_main') or [])
                        if not c.get('_used_by_related')])

    # ---- 人工否定的剔除说明（remove 模式只报数量，不留条目名） ----
    if removed:
        _write_removed_note(doc, removed)

    # ---- 待补充清单 ----
    if include_pending:
        _write_pending(doc, view)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    doc.save(out_path)

    stats = {
        'kept_items': kept,
        'pending': pending,
        'reject_mode': reject_mode,
        'rejected_marked': marked,
        'rejected_removed': removed,
        'images': _count_drawings(doc),
        'bytes': os.path.getsize(out_path),
    }
    return out_path, stats


def _exportable_rows(rows, reject_mode):
    """能写进正文的条目：系统/人工已确定的，加上 mark 模式下的人工否定项。"""
    out = []
    for r in rows:
        if r['status'] in EXPORTABLE:
            out.append(r)
        elif r['status'] == 'rejected' and reject_mode == 'mark':
            out.append(r)
    return out


def _write_removed_note(doc, n):
    """remove 模式：只说明"有几项被人工否掉、未计入"，不列出条目名。"""
    p = _para(doc, indent_pt=24, space_after=4)
    _font(p.add_run('注：另有 %d 项经审核不符合申报规范，未计入上列材料。' % n),
          BODY_FONT, 10.5)


def _write_rows(doc, rows):
    for i, r in enumerate(rows, 1):
        p = _para(doc, indent_pt=24, space_after=4)
        _font(p.add_run('%d. %s' % (i, _clean_text(r['item_text']))), BODY_FONT, 12)
        if r.get('status') == 'rejected':
            q = _para(doc, indent_pt=48, space_after=4)
            _font(q.add_run(REJECT_NOTE), BODY_FONT, 10.5)
        for c in _cells_of(r):
            path = _img_path_from_url(c.get('url'))
            if path:
                _add_image(doc, path)


def _write_competition(doc, rows):
    """竞赛板块按级别分子标题，与模板的（一）国家级 /（二）省级 一致。"""
    buckets = {k: [] for k, _ in COMP_SUB}
    for r in rows:
        lv = ((r.get('img') or {}).get('vlevel') or '') if r.get('img') else ''
        if '国家级' in lv:
            buckets['国家级'].append(r)
        elif '省级' in lv:
            buckets['省级'].append(r)
        else:
            buckets['其他'].append(r)
    for k, label in COMP_SUB:
        items = buckets.get(k) or []
        if not items:
            continue
        sub = _para(doc, indent_pt=24.1, space_after=4)
        _font(sub.add_run(label), BODY_FONT, 12, bold=True)
        for i, r in enumerate(items, 1):
            p = _para(doc, indent_pt=24, space_after=4)
            _font(p.add_run('%d. %s' % (i, _clean_text(r['item_text']))), BODY_FONT, 12)
            if r.get('status') == 'rejected':
                q = _para(doc, indent_pt=48, space_after=4)
                _font(q.add_run(REJECT_NOTE), BODY_FONT, 10.5)
            for c in _cells_of(r):
                path = _img_path_from_url(c.get('url'))
                if path:
                    _add_image(doc, path)


def _cells_of(r):
    out = []
    if r.get('img'):
        out.append(r['img'])
    out.extend(r.get('extra_imgs') or [])
    return out


def _write_section_level(doc, cards):
    """体测 / 志愿者这类：没有文字条目，写一行指标 + 贴整块截图。"""
    for c in cards:
        bits = [x for x in [c.get('award_name'),
                            c.get('metrics_text'),
                            ('年份 %s' % c['year']) if c.get('year') else ''] if x]
        if bits:
            p = _para(doc, indent_pt=24, space_after=4)
            _font(p.add_run('· ' + '，'.join(bits)), BODY_FONT, 12)
        path = _img_path_from_url(c.get('url'))
        if path:
            _add_image(doc, path)


def _write_pending(doc, view):
    """文末「待补充 / 待确认清单」——不能直接采信的东西集中摆出来。"""
    pend_items, pend_imgs = [], []
    for s in view['sections']:
        for row in s['rows']:
            if row['status'] in PENDING:
                pend_items.append(row)
        pend_imgs.extend([c for c in (s.get('leftover_main') or [])
                          if not c.get('_used_by_related')])
    if not pend_items and not pend_imgs:
        return

    h = _para(doc, space_after=4)
    _font(h.add_run('附：待补充 / 待确认清单（不计入上列材料）'), HEAD_FONT, 14)

    if pend_items:
        p = _para(doc, indent_pt=24, space_after=4)
        _font(p.add_run('一、存在反向证据或缺少配图的条目，需补充后重新审核：'),
              BODY_FONT, 12, bold=True)
        for i, row in enumerate(pend_items, 1):
            q = _para(doc, indent_pt=24, space_after=2)
            _font(q.add_run('%d. %s' % (i, _clean_text(row['item_text']))), BODY_FONT, 12)
            why = list(row.get('neg') or [])
            if row.get('pending_reason') and row['pending_reason'] not in why:
                why.append(row['pending_reason'])
            if not why:
                why = ['没有达到配对门槛的证明图']
            a = _para(doc, indent_pt=48, space_after=4)
            _font(a.add_run('原因：%s' % '；'.join(why)), BODY_FONT, 10.5)

    if pend_imgs:
        p = _para(doc, indent_pt=24, space_after=4)
        _font(p.add_run('二、以下图片未认领到具体奖项，请人工确认归属'
                        '（也可能是学生漏写的奖项）：'), BODY_FONT, 12, bold=True)
        for i, c in enumerate(pend_imgs, 1):
            bits = [x for x in [c.get('suggest_award') or c.get('award_name'),
                                c.get('metrics_text'),
                                ('年份 %s' % c['year']) if c.get('year') else '年份不明确'] if x]
            q = _para(doc, indent_pt=24, space_after=2)
            _font(q.add_run('%d. %s' % (i, '，'.join(bits) or c.get('file'))), BODY_FONT, 12)
            path = _img_path_from_url(c.get('url'))
            if path:
                _add_image(doc, path)


# ------------------------------------------------------------------ 批量
def batch_zip(paths, zip_path):
    """把多份 docx 打包成一个 zip（下载用）。"""
    os.makedirs(os.path.dirname(os.path.abspath(zip_path)), exist_ok=True)
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        used = {}
        for p in paths:
            if not os.path.exists(p):
                continue
            base = os.path.basename(p)
            if base in used:
                used[base] += 1
                stem, ext = os.path.splitext(base)
                base = '%s(%d)%s' % (stem, used[base], ext)
            else:
                used[base] = 0
            z.write(p, base)
    return zip_path, os.path.getsize(zip_path)


def safe_filename(name):
    bad = '\\/:*?"<>|\r\n\t'
    out = ''.join('_' if ch in bad else ch for ch in (name or '')).strip().strip('.')
    return re.sub(r'\s+', '', out) or 'material'
