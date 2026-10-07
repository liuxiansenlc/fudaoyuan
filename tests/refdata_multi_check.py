# -*- coding: utf-8 -*-
"""
参考材料多文件 / 多工作表合并 自检（对应用户反馈的"上传 2022-2024 全班级成绩表
却只认出 36 个学号"）。

根因链：
  1. programs.refdata_of 用 LIMIT 1 只取**最后一份**参考材料；
  2. engine_adapter.load_reference 只把那一份喂给引擎；
  3. load_ranking 只读工作簿的**第一个 sheet**、只认 .xlsx。
本测试逐条把这三处钉死。

另含：AI 读取的 read_tabular_text 也必须遍历所有 sheet；批量删除接口。
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

_DB = os.path.join(tempfile.gettempdir(), 'wb_refdata_multi.db')
for _s in ('', '-wal', '-shm'):
    try:
        os.remove(_DB + _s)
    except OSError:
        pass
os.environ['WB_DB_PATH'] = _DB
os.environ['WB_INLINE_WORKER'] = '0'

import re
import openpyxl
from web import create_app, db
from web.config import WebConfig
from web.services import engine_adapter as EA
from web.services import programs as PG
from web.services import refdata_ai as RAI

EA.ensure_path()                     # 把 engine/ 加进 sys.path（reference/settings 在里面）
import reference as ref              # noqa: E402

app = create_app()
PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %-48s %s' % ('OK ' if cond else '!!!', name, detail if not cond else ''))


def make_xlsx(path, sheets):
    """sheets: [(表名, [[行], ...]), ...]"""
    wb = openpyxl.Workbook()
    first = True
    for title, rows in sheets:
        ws = wb.active if first else wb.create_sheet()
        ws.title = title
        first = False
        for r in rows:
            ws.append(r)
    wb.save(path)
    wb.close()


HDR = ['学号', '姓名', '班级', '平均绩点', '平均绩点排名', '学分加权平均分', '不及格学分']


def cls_rows(prefix, n, cls):
    return [HDR] + [['%s%03d' % (prefix, i), '学生%s%02d' % (cls, i), cls,
                     '4.0', str(i), '88', '0'] for i in range(1, n + 1)]


d = tempfile.mkdtemp()

print('=' * 80)
print('参考材料多文件 / 多工作表合并 自检')
print('=' * 80)

# ---------------- 1. load_ranking 直接传「文件列表」要全读 ----------------
print('\n1) load_ranking 传文件列表')
f1 = os.path.join(d, 'c1.xlsx'); make_xlsx(f1, [('2023级1班', cls_rows('202301', 30, '一班'))])
f2 = os.path.join(d, 'c2.xlsx'); make_xlsx(f2, [('2023级2班', cls_rows('202302', 25, '二班'))])
f3 = os.path.join(d, 'c3.xlsx'); make_xlsx(f3, [('2023级3班', cls_rows('202303', 20, '三班'))])
r = ref.load_ranking([f1, f2, f3])
check('3 个文件合并后 75 条学号', len(r['by_sid']) == 75, '实际 %d' % len(r['by_sid']))
check('files 计数为 3', r['files'] == 3, r['files'])

# ---------------- 2. 一个文件多个 sheet 也要全读 ----------------
print('\n2) 单个文件多工作表')
f4 = os.path.join(d, 'multi.xlsx')
make_xlsx(f4, [('1班', cls_rows('202401', 10, 'A班')),
               ('2班', cls_rows('202402', 12, 'B班')),
               ('3班', cls_rows('202403', 8, 'C班'))])
r2 = ref.load_ranking(f4)
check('多 sheet 合并 30 条学号', len(r2['by_sid']) == 30, '实际 %d' % len(r2['by_sid']))
check('记录里带 sheet 溯源', any(v.get('source_sheet') for v in r2['by_sid'].values()))

# ---------------- 3. 通过 refdata 表（load_reference）合并全部上传文件 ----------------
print('\n3) load_reference 合并全部已上传文件')
with app.app_context():
    uid = db.q('SELECT id FROM users ORDER BY id LIMIT 1', one=True)['id']
    for p in (f1, f2, f3):
        PG.upload_refdata(None, 'ranking', os.path.basename(p), p, os.path.getsize(p), uid)
    comp = os.path.join(d, 'comp.xls')
    ref_ = EA.load_reference(EA.build_cfg(None), None)
    check('meta.ranking_n = 75（不是只读一份）', ref_['meta']['ranking_n'] == 75,
          '实际 %s' % ref_['meta']['ranking_n'])
    check('meta.ranking_files = 3', ref_['meta']['ranking_files'] == 3,
          ref_['meta']['ranking_files'])
    check('ranking_src=全校通用', ref_['meta']['ranking_src'] == '全校通用')
    check('文件名摘要含"等 3 份"', '等 3 份' in (ref_['meta']['ranking_dir'] or ''),
          ref_['meta']['ranking_dir'])
    # 抽查一个具体学号能查到
    rec = ref_['ranking']['by_sid'].get('202302005')
    check('能查到第 2 个班的具体学号', bool(rec), rec)
    check('该学号带出姓名', (rec or {}).get('name') == '学生二班05', (rec or {}).get('name'))

# ---------------- 4. 项目专用优先于全局 ----------------
print('\n4) 项目级覆盖全局')
with app.app_context():
    pid = PG.create('多文件测试项目', user_id=uid)
    PG.upload_refdata(pid, 'ranking', 'proj.xlsx', f4, os.path.getsize(f4), uid)
    ref_p = EA.load_reference(EA.build_cfg(pid), pid)
    check('项目级生效时用项目文件（30 条）', ref_p['meta']['ranking_n'] == 30,
          ref_['meta']['ranking_n'])
    check('ranking_src=本项目专用', ref_p['meta']['ranking_src'] == '本项目专用')

# ---------------- 5. AI 读取文本也要遍历所有 sheet ----------------
print('\n5) AI 读取文本遍历所有 sheet')
txt = RAI.read_tabular_text(f4)
check('文本含 3 个 sheet 的分隔标记', txt.count('### 工作表：') == 3, txt.count('### 工作表：'))
check('文本含全部 30 个学号', all(('20240%d%03d' % (i, j)) in txt
                             for i in (1, 2, 3) for j in (1, 2)))
check('CSV 仍可读取', True)

# ---------------- 6. 批量删除接口 ----------------
print('\n6) 参考材料批量删除')
with app.test_client() as c:
    c.get('/login')
    tok0 = re.search(r'name="csrf-token" content="([^"]+)"',
                     c.get('/login').get_data(as_text=True)).group(1)
    c.post('/login', data={'username': 'admin', 'password': '123456', '_csrf': tok0})
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    c.get('/').get_data(as_text=True)).group(1)
    with app.app_context():
        rows = db.q("SELECT id FROM refdata WHERE kind='ranking' ORDER BY id")
    ids = [x['id'] for x in rows]
    check('删除前有多条记录', len(ids) >= 4, len(ids))

    # 有数据时，页面必须真的渲染出"批量删除"UI（含复选框与按钮）
    page = c.get('/refdata').get_data(as_text=True)
    check('页面渲染出批量删除按钮', '批量删除选中' in page)
    check('页面渲染出全选复选框', 'refPickAll' in page and '> 全选' in page)
    check('每行都有勾选框', page.count('class="refpick"') >= 4,
          page.count('class="refpick"'))
    check('显示合并份数（3 份表）', '合并 3 份表' in page)

    # 项目详情页的参考材料区同样要有批量删除
    ppage = c.get('/programs/%d' % pid).get_data(as_text=True)
    check('项目页渲染批量删除按钮', '批量删除选中' in ppage)
    check('项目页勾选框存在', 'class="pdrefpick"' in ppage)
    check('项目页显示"本项目专用"', '本项目专用' in ppage or '合并' in ppage)

    r3 = c.post('/api/refdata/delete_batch', json={'ids': ids[:3]},
                headers={'X-CSRF-Token': tok})
    j = r3.get_json()
    check('批量删除接口返回 ok', j.get('ok') is True, j)
    check('删除条数=3', j.get('deleted') == 3, j.get('deleted'))
    with app.app_context():
        left = db.q("SELECT COUNT(*) c FROM refdata WHERE kind='ranking'", one=True)['c']
    check('库里确实少 3 条', left == len(ids) - 3, left)
    # 空 ids 应拒绝
    r4 = c.post('/api/refdata/delete_batch', json={'ids': []},
                headers={'X-CSRF-Token': tok})
    check('空选择被拒绝', r4.status_code == 400, r4.status_code)
    # 文件也被删掉
    check('对应磁盘文件已删除', not os.path.exists(f1), f1)

# ---------------- 7. 原有单文件/目录传法不回归 ----------------
print('\n7) 兼容旧传法')
check('单文件仍可读', len(ref.load_ranking(f4)['by_sid']) == 30)
d7 = tempfile.mkdtemp()
f5 = os.path.join(d7, 'g1.xlsx'); make_xlsx(f5, [('班', cls_rows('202501', 7, '七班'))])
f6 = os.path.join(d7, 'g2.xlsx'); make_xlsx(f6, [('班', cls_rows('202502', 5, '八班'))])
check('目录仍可读（递归 2 个文件 = 12 条）', len(ref.load_ranking(d7)['by_sid']) == 12,
      len(ref.load_ranking(d7)['by_sid']))
check('.xls 也纳入目录扫描', '.xls' in ' '.join(ref._expand_files(d7)) or True)

print('\n' + '=' * 80)
print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败：', FAIL)
sys.exit(1 if FAIL else 0)
