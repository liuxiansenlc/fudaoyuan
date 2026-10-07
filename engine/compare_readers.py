# -*- coding: utf-8 -*-
"""
生成"本地 OCR vs 视觉大模型"逐张对比报告。
用来直观评估：换成视觉模型后，识图能力提升了多少。
"""
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docx_parser import parse_docx          # noqa: E402
from ocr_local import ocr_image_bytes       # noqa: E402
import vision as vision_mod                 # noqa: E402
from config import OUT_DIR                  # noqa: E402

STUDENT = '胡珍华'
ROOT = r'D:/浏览器下载/Desktop/2.【班级汇总】国家奖学金各班级申报材料'


def main():
    p = [x for x in glob.glob(os.path.join(ROOT, '*.docx'))
         if STUDENT in x and not os.path.basename(x).startswith('~$')][0]
    r = parse_docx(p)
    rows = []
    for o in r['occurrences']:
        rec = r['media'][o['media']]
        ocr = ocr_image_bytes(rec['data'], rec['sha256'])
        vis = vision_mod.get(rec['sha256'])
        rows.append((o['order'] + 1, o['media'].split('/')[-1],
                     '%sx%s' % (rec['w'], rec['h']), ocr, vis))

    L = []
    L.append('# 本地 OCR 与视觉大模型 识图对比 · %s' % STUDENT)
    L.append('')
    L.append('同样的 32 张图片，左边是本地 OCR（RapidOCR，离线 CPU）读出来的原文，')
    L.append('右边是视觉大模型读出的结论。')
    L.append('')
    n_ocr_ok = sum(1 for _, _, _, o, v in rows if o['trust'] == 'high')
    n_vis = sum(1 for _, _, _, _, v in rows if v)
    L.append('- 图片总数：**%d**' % len(rows))
    L.append('- 本地 OCR 判为"高可信"的：**%d** 张（%.0f%%）' % (n_ocr_ok, 100 * n_ocr_ok / len(rows)))
    L.append('- 已有视觉模型结论的：**%d** 张' % n_vis)
    L.append('')
    L.append('---')
    L.append('')
    for order, fname, size, ocr, vis in rows:
        L.append('## %02d · %s · %s' % (order, fname, size))
        L.append('')
        L.append('**本地 OCR**（可信度 %s / %.2f）' % (ocr['trust'], ocr['confidence']))
        L.append('')
        L.append('```')
        L.append((ocr['text'] or '(无文字)')[:400])
        L.append('```')
        L.append('')
        if vis:
            L.append('**视觉大模型**')
            L.append('')
            L.append('| 字段 | 结果 |')
            L.append('|---|---|')
            for k, label in (('doc_kind', '证据形式'), ('title', '抬头'),
                             ('award_name', '奖项名'), ('award_level', '等级'),
                             ('academic_year', '学年'), ('issuer', '落款'),
                             ('level', '级别')):
                if vis.get(k):
                    L.append('| %s | %s |' % (label, vis[k]))
            if vis.get('persons'):
                L.append('| 人名 | %s |' % '、'.join(vis['persons'][:12]))
            if vis.get('metrics'):
                L.append('| 指标 | %s |' % '，'.join('%s=%s' % (k, v) for k, v in vis['metrics'].items()))
            if vis.get('note'):
                L.append('| 备注 | %s |' % vis['note'])
            L.append('')
        L.append('---')
        L.append('')

    dst = os.path.join(OUT_DIR, '识图对比_胡珍华.md')
    with open(dst, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))
    print('已生成 %s' % dst)
    print('  图片 %d 张，本地OCR高可信 %d 张，视觉模型已读 %d 张' % (len(rows), n_ocr_ok, n_vis))


if __name__ == '__main__':
    main()
