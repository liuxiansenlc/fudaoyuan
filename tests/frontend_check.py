# -*- coding: utf-8 -*-
"""
前端改造回归自检（响应式 + 交互）。

目的：前端改动最容易"看着没事、其实某个页面结构坏了"。这里把关键约束固化成断言：
  1. 所有主要页面都能 200 渲染（模板语法没被改坏）
  2. 每个登录后页面都有"移动端导航三件套"（汉堡按钮 / 侧栏 id / 遮罩）
  3. 每张 table.tb 都被 .tbwrap 包裹（窄屏才不会撑破卡片）
  4. 关键列表表带 data-label（窄屏卡片式渲染需要）
  5. CSS 里存在响应式断点、抽屉、卡片表格等规则
  6. JS 里存在抽屉与本提交态函数
  7. 视口 meta、登录页与无障碍基元仍在
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ['WB_INLINE_WORKER'] = '0'

from web import create_app, db

app = create_app()
PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %-46s %s' % ('OK ' if cond else '!!!', name, detail if not cond else ''))


def csrf(c):
    m = re.search(r'name="csrf-token" content="([^"]+)"',
                  c.get('/').get_data(as_text=True))
    return m.group(1) if m else ''


print('=' * 78)
print('前端改造回归自检')
print('=' * 78)

# ---------------- 静态资源 ----------------
print('\n1) 静态资源')
css = open(os.path.join(HERE, 'web/static/css/app.css'), encoding='utf-8').read()
js = open(os.path.join(HERE, 'web/static/js/app.js'), encoding='utf-8').read()

check('CSS 有 900px 断点', '@media (max-width: 900px)' in css)
check('CSS 有 640px 断点', '@media (max-width: 640px)' in css)
check('CSS 抽屉状态选择器 html[data-nav]', 'html[data-nav="open"]' in css)
check('CSS 表格滚动容器 .tbwrap', '.tbwrap' in css)
check('CSS 表格卡片模式 table.tb.resp', 'table.tb.resp' in css)
check('CSS 触控高度令牌 --ctl-h', '--ctl-h' in css)
check('CSS 锚点滚动补偿 scroll-margin-top', 'scroll-margin-top' in css)
check('CSS 按钮加载态 .btn.loading', '.btn.loading' in css)
check('CSS 尊重减少动效偏好', 'prefers-reduced-motion' in css)
check('CSS 暗色主题变量仍完整', 'html[data-theme="dark"]' in css)

check('JS 有 wbNavToggle', 'wbNavToggle' in js)
check('JS 有 wbNavClose', 'wbNavClose' in js)
check('JS 有 wbBtnLoading', 'wbBtnLoading' in js)
check('JS 抽屉支持 ESC 关闭', "e.key === 'Escape'" in js)

# ---------------- 页面渲染 ----------------
print('\n2) 页面渲染与移动端骨架')
with app.test_client() as c:
    c.get('/login')
    tok0 = re.search(r'name="csrf-token" content="([^"]+)"',
                     c.get('/login').get_data(as_text=True)).group(1)
    c.post('/login', data={'username': 'admin', 'password': '123456', '_csrf': tok0})
    tok = csrf(c)

    # 找一个批次和有结论的学生，用于详情页
    with app.app_context():
        b = db.q('SELECT id FROM batches ORDER BY id DESC LIMIT 1', one=True)
        f = db.q("SELECT id FROM files WHERE status='analyzed' ORDER BY id LIMIT 1", one=True)
        p = db.q('SELECT id FROM programs ORDER BY id LIMIT 1', one=True)
    bid = b['id'] if b else None
    fid = f['id'] if f else None
    pid = p['id'] if p else None

    pages = ['/', '/batches', '/review', '/rules', '/refdata', '/settings/model',
             '/system', '/users', '/profile', '/programs']
    if bid:
        pages.append('/batches/%d' % bid)
    if fid:
        pages.append('/review/%d' % fid)
    if pid:
        pages.append('/programs/%d' % pid)

    bad = []
    for path in pages:
        r = c.get(path)
        if r.status_code != 200:
            bad.append('%s→%s' % (path, r.status_code))
        html = r.get_data(as_text=True)
        if r.status_code == 200:
            # 移动端导航三件套
            if 'id="navBtn"' not in html:
                bad.append('%s 缺汉堡按钮' % path)
            if 'id="sidebar"' not in html:
                bad.append('%s 缺侧栏 id' % path)
            if 'id="navScrim"' not in html:
                bad.append('%s 缺遮罩' % path)
            # 表格必须都被 tbwrap 包裹（窄屏不撑破卡片）
            nt, nw = html.count('<table'), html.count('tbwrap')
            if nt != nw:
                bad.append('%s 表格未包裹 (table=%d tbwrap=%d)' % (path, nt, nw))
    check('全部页面 200 且导航骨架齐全', not bad, '；'.join(bad[:6]))
    print('     检查了 %d 个页面' % len(pages))

    # 视口 meta
    r = c.get('/')
    check('含 viewport meta', 'name="viewport"' in r.get_data(as_text=True))
    check('含跳到主内容链接', '跳到主内容' in r.get_data(as_text=True))
    home = r.get_data(as_text=True)
    check('样式/脚本带版本号（防缓存）',
          re.search(r'app\.css\?v=\d+', home) and re.search(r'app\.js\?v=\d+', home))

    # 关键列表的 data-label
    if bid:
        html = c.get('/batches/%d' % bid).get_data(as_text=True)
        check('材料清单含 data-label="学生"', 'data-label="学生"' in html)
        check('材料清单含 data-label="状态"', 'data-label="状态"' in html)
    if fid:
        html = c.get('/review/%d' % fid).get_data(as_text=True)
        check('审核页仍保留跳转脚本 jumpTo', 'function jumpTo' in html)
        check('审核页条目带 data-jump', 'data-jump=' in html)
        check('审核页统计 chip 可点击', "onclick=\"jumpTo('need')\"" in html)

# 匿名态单独用一个客户端（嵌套 with 会共用 cookie，别混在一起测）
with app.test_client() as anon:
    r = anon.get('/login')
    lh = r.get_data(as_text=True)
    check('登录页 200', r.status_code == 200, 'HTTP %s' % r.status_code)
    check('登录页含 viewport', 'name="viewport"' in lh)
    check('登录页不含侧栏（匿名态）', 'id="navBtn"' not in lh)

print('\n' + '=' * 78)
print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败：', FAIL)
sys.exit(1 if FAIL else 0)
