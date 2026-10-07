# -*- coding: utf-8 -*-
"""
批量视觉读取落盘器。

我（视觉模型）逐张读图后，把结果写进 engine/_batch_in.json：
    { "万亿航__image1.png": {字段...}, ... }
本脚本按"学生名 + 文件名"反查该图片的 sha256，写入 cache/vision/<sha256>.json。

这样即使中途中断，已读的部分也已经落盘，不会白读。
"""
import os
import sys
import json
import glob

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from docx_parser import parse_docx      # noqa: E402
import vision                           # noqa: E402
from config import STUDENT_DIR as ROOT  # noqa: E402


def build_map():
    """导出名 -> sha256。导出名形如 '黄俊杰__image3.jpeg'。"""
    m = {}
    for p in glob.glob(os.path.join(ROOT, '*.docx')):
        bn = os.path.basename(p)
        if bn.startswith('~$') or '模板' in bn:
            continue
        try:
            r = parse_docx(p)
        except Exception:
            continue
        nm = r['student'].get('name')
        if not nm:
            continue
        for path, rec in r['media'].items():
            m['%s__%s' % (nm, path.split('/')[-1])] = rec['sha256']
    return m


def main():
    src = os.path.join(HERE, '_batch_in.json')
    if not os.path.exists(src):
        print('未找到 _batch_in.json')
        return
    data = json.load(open(src, encoding='utf-8'))
    mp = build_map()
    ok, miss = 0, []
    for key, val in data.items():
        sha = mp.get(key)
        if not sha:
            miss.append(key)
            continue
        vision.put(sha, val)
        ok += 1
    print('写入 %d 条；未匹配 %d 条 %s' % (ok, len(miss), miss[:6]))
    print('缓存总数：%d' % vision.count())


if __name__ == '__main__':
    main()
