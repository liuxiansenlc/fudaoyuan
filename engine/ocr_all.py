# -*- coding: utf-8 -*-
"""
批量 OCR：把所有学生材料里的图片识别一遍并写入缓存。
只跑一次，之后所有分析都读缓存，迭代几乎零成本。
"""
import os
import sys
import glob
import json
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docx_parser import parse_docx          # noqa: E402
from ocr_local import ocr_image_bytes       # noqa: E402

from config import STUDENT_DIR as ROOT


def main():
    files = [p for p in sorted(glob.glob(os.path.join(ROOT, '*.docx')))
             if '模板' not in p and not os.path.basename(p).startswith('~$')]
    t0 = time.time()
    total = 0
    cached = 0
    for p in files:
        try:
            r = parse_docx(p)
        except Exception as e:
            print('[跳过] %s: %s' % (os.path.basename(p), e))
            continue
        s = r['student']
        n_new = 0
        for m, rec in r['media'].items():
            total += 1
            o = ocr_image_bytes(rec['data'], rec['sha256'])
            if o['cached']:
                cached += 1
            else:
                n_new += 1
        print('  %-9s %-4s 图%3d  本次识别%3d' % (s['class_no'], s['name'], len(r['media']), n_new))
    print('-' * 52)
    print('图片总数 %d，命中缓存 %d，本次新识别 %d，耗时 %.0f 秒' % (total, cached, total - cached, time.time() - t0))


if __name__ == '__main__':
    main()
