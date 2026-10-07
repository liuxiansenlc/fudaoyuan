# -*- coding: utf-8 -*-
"""把审核页渲染结果转成纯文本，逐段核对信息是否完整、有没有重复块。"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ['WB_INLINE_WORKER'] = '0'

from web import create_app
from web import db

app = create_app()
TARGET = sys.argv[1] if len(sys.argv) > 1 else '黄俊杰'


def clean(h):
    h = re.sub(r'<svg.*?</svg>', '', h, flags=re.S)
    h = re.sub(r'<img[^>]*>', '[图]', h)
    h = re.sub(r'<(script|style).*?</\1>', '', h, flags=re.S)
    h = re.sub(r'<br\s*/?>', '\n', h)
    h = re.sub(r'<[^>]+>', ' ', h)
    h = h.replace('&quot;', '"').replace('&amp;', '&').replace('&#39;', "'")
    h = h.replace('&lt;', '<').replace('&gt;', '>')
    h = re.sub(r'[ \t\u00a0]+', ' ', h)
    h = re.sub(r'\n\s*\n+', '\n', h)
    return h.strip()


with app.test_client() as c:
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    c.get('/login').get_data(as_text=True)).group(1)
    c.post('/login', data={'username': 'admin', 'password': '123456', '_csrf': tok})
    row = db.q_dict("SELECT * FROM files WHERE student_name=? AND status='analyzed'",
                    (TARGET,), one=True)
    if not row:
        print('找不到', TARGET)
        raise SystemExit(1)
    h = c.get('/review/%d' % row['id']).get_data(as_text=True)
    print('=' * 78)
    print('学生：%s  |  页面 %d KB' % (TARGET, len(h.encode()) // 1024))
    print('=' * 78)
    txt = clean(h)
    # 去掉侧栏与顶栏
    i = txt.find('系统自动判定覆盖率')
    print(txt[i - 400 if i > 400 else 0: i + 4200])
    print()
    print('---- 计数核对 ----')
    for kw in ['未认领', '建议补写奖项名', 'AI 判断：', '反证：', '待确认原因：',
               '人工确认', '人工采纳', '缺图', '未提交', '年份不明确']:
        print('  %-14s %d' % (kw, h.count(kw)))
