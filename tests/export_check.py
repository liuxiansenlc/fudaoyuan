# -*- coding: utf-8 -*-
"""
导出校验：实际生成标准化 docx，再把它的段落结构 dump 出来核对。
重点确认三件事：
  1. 板块顺序与模板一致
  2. 每个奖项名**紧跟**它的证明图（不是堆在一起）
  3. 待确认 / 缺图没有混进正文，而是落在文末清单里
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ['WB_INLINE_WORKER'] = '0'

from docx import Document
from docx.oxml.ns import qn

from web import create_app, db
from web.services import engine_adapter as EA
from web.services import view_builder as VB
from web.services import export_service as EX

app = create_app()
TARGETS = sys.argv[1:] or ['黄俊杰', '胡微祥', '薛婉婷', '陈奕璋']
LIMIT = 400


def dump(path):
    d = Document(path)
    out = []
    for p in d.paragraphs:
        imgs = len(p._element.findall('.//' + qn('w:drawing')))
        t = p.text.strip()
        if not t and not imgs:
            continue
        if imgs:
            out.append('        [图片 x%d]' % imgs)
        else:
            sz = None
            for r in p.runs:
                if r.font.size:
                    sz = r.font.size.pt
                    break
            out.append('%-6s| %s' % (('%gpt' % sz) if sz else '', t[:70]))
    return out


with app.app_context():
    total_ok = True
    for name in TARGETS:
        row = db.q_dict("SELECT * FROM files WHERE student_name=? AND status='analyzed'",
                        (name,), one=True)
        if not row:
            print('!! 找不到 %s' % name)
            continue
        rec = EA.load_result(row['id'])
        view = VB.build(rec, file_id=row['id'])
        cls = (rec['student'].get('class_no') or '').strip()
        out = os.path.join(HERE, 'data', 'exports', '_check_%s.docx' % name)
        path, st = EX.build_docx(view, out)
        lines = dump(path)
        print('=' * 80)
        print('%s  →  %s' % (name, os.path.basename(path)))
        print('  保留条目 %d | 图片 %d 张 | 文末待处理 %d | %.0f KB'
              % (st['kept_items'], st['images'], st['pending'], st['bytes'] / 1024))
        print('-' * 80)
        for ln in lines[:LIMIT]:
            print(ln)
        if len(lines) > LIMIT:
            print('   … 还有 %d 行' % (len(lines) - limit))
        print()

        # --- 自动断言 ---
        text = '\n'.join(lines)
        # 1) 板块顺序 —— 以「本次导出实际渲染出来的板块」为准。
        #    项目可能只看部分板块（没内容的板块不渲染），也可能有方案外类目，
        #    所以不能拿默认方案的全部 6 个板块去 find（会 -1）。直接比 view['sections']。
        order = [s['label'] for s in view['sections']]
        pos = [text.find(l) for l in order]
        seq_ok = all(p >= 0 for p in pos) and pos == sorted(pos)
        if not seq_ok:
            print('   （板块顺序不符：%s）' % [(l, p) for l, p in zip(order, pos) if p < 0])
        # 2) 正文里每条奖项后是否紧跟图片（只看"附："之前，文末清单本就没有配图）
        cut = next((i for i, l in enumerate(lines) if '附：' in l), len(lines))
        body_lines = lines[:cut]
        paired = miss = 0
        for i, ln in enumerate(body_lines):
            body = ln.split('| ', 1)[-1] if '| ' in ln else ln
            if len(body) > 1 and body[0].isdigit() and body[1:3] in ('. ', '、'):
                nxt = body_lines[i + 1] if i + 1 < len(body_lines) else ''
                if nxt.strip().startswith('[图片'):
                    paired += 1
                else:
                    miss += 1
        print('  断言：板块顺序 %s | 奖项紧邻图片 %d 条，未紧邻 %d 条'
              % ('OK' if seq_ok else '失败', paired, miss))
        total_ok = total_ok and seq_ok

    print('=' * 80)
    print('整体：', '通过' if total_ok else '有失败项')
