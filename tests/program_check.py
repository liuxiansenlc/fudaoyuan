# -*- coding: utf-8 -*-
"""
多奖学金隔离验证。

要证明三件事：
  1. 同一个学生材料，在两个板块方案不同的奖学金项目下，**审核页的板块不同**
  2. 项目级的规则覆盖（如校级奖学金体测线 70）只对自己生效，**不影响全局/其他项目**
  3. 导出 Word 按各自项目的板块方案排版

用同一个学生（薛婉婷）分别放进「国家奖学金」和「校级奖学金」两个项目里跑一遍对比。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ['WB_INLINE_WORKER'] = '0'

from web import create_app, db
from web.services import programs as PG
from web.services import sections as SEC
from web.services import engine_adapter as EA
from web.services import view_builder as VB
from web.services import export_service as EX
from web.services import task_service as TS

app = create_app()
SRC = r'D:/浏览器下载/Desktop/2.【班级汇总】国家奖学金各班级申报材料'
DOC = os.path.join(SRC, '20240822班薛婉婷国家奖学金申请支撑材料.docx')

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %-52s %s' % ('OK ' if cond else '!!!', name, detail if not cond else ''))


def make_project(client, tok, name, preset, category):
    j = client.post('/api/programs', json={'name': name, 'preset': preset,
                                           'category': category},
                    headers={'X-CSRF-Token': tok}).get_json()
    return j['id']


def make_batch(client, tok, pid, name):
    r = client.post('/batches', data={'name': name, 'program_id': str(pid), '_csrf': tok},
                    follow_redirects=False)
    return int(re.search(r'/batches/(\d+)', r.headers['Location']).group(1))


def upload(client, tok, bid):
    data = {'_csrf': tok, 'files': [(open(DOC, 'rb'), os.path.basename(DOC))]}
    j = client.post('/api/batches/%d/files' % bid, data=data,
                    content_type='multipart/form-data').get_json()
    assert j['ok'] and j['uploaded'], j
    return j['uploaded'][0]['id']


print('=' * 80)
print('多奖学金隔离验证')
print('=' * 80)

with app.test_client() as c:
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    c.get('/login').get_data(as_text=True)).group(1)
    r = c.post('/login', data={'username': 'admin', 'password': '123456', '_csrf': tok})
    check('登录（默认密码 admin/123456）', r.status_code == 302, r.status_code)
    # 登录成功后 session.clear() 会把 _csrf 也清掉，必须重新取一次
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    c.get('/').get_data(as_text=True)).group(1)

    # ---------------- 1) 两个项目、两套板块方案 ----------------
    print('\n1) 建两个板块方案不同的项目')
    p1 = make_project(c, tok, '国奖（隔离验证）', '国家奖学金', '国家级')
    p2 = make_project(c, tok, '校级（隔离验证）', '校级奖学金', '校级')
    s1 = PG.scheme_of(PG.get(p1))
    s2 = PG.scheme_of(PG.get(p2))
    print('   国奖板块:', SEC.keys(s1))
    print('   校级板块:', SEC.keys(s2))
    check('两个项目板块方案不同', SEC.keys(s1) != SEC.keys(s2))
    check('国奖含科研', '科研' in SEC.keys(s1))
    check('校级不含科研', '科研' not in SEC.keys(s2))

    # ---------------- 2) 同一份材料分别跑 ----------------
    print('\n2) 同一份学生材料分别放进两个项目')
    b1 = make_batch(c, tok, p1, '国奖批次')
    b2 = make_batch(c, tok, p2, '校级批次')
    f1 = upload(c, tok, b1)
    f2 = upload(c, tok, b2)
    print('   批次 %s→文件%s ｜ 批次 %s→文件%s' % (b1, f1, b2, f2))
    for _ in range(2):
        r = c.post('/api/batches/%d/run' % b1, json={'kind': 'analyze_only'},
                   headers={'X-CSRF-Token': tok})
        r2 = c.post('/api/batches/%d/run' % b2, json={'kind': 'analyze_only'},
                    headers={'X-CSRF-Token': tok})
        with app.app_context():
            TS.loop_once(); TS.loop_once()
    st1 = db.q('SELECT status FROM files WHERE id=?', (f1,), one=True)['status']
    st2 = db.q('SELECT status FROM files WHERE id=?', (f2,), one=True)['status']
    check('两份材料都出结论', st1 == 'analyzed' and st2 == 'analyzed', '%s/%s' % (st1, st2))

    # ---------------- 3) 审核页板块应当不同 ----------------
    print('\n3) 审核页按各自项目方案渲染')
    def sec_labels(fid):
        rec = EA.load_result(fid)
        prog = PG.get_by_batch(db.q('SELECT batch_id FROM files WHERE id=?', (fid,), one=True)['batch_id'])
        v = VB.build(rec, file_id=fid, scheme=PG.scheme_of(prog))
        return [s['label'] for s in v['sections'] if s['is_tpl']]

    L1, L2 = sec_labels(f1), sec_labels(f2)
    print('   国奖页板块:', L1)
    print('   校级页板块:', L2)
    check('国奖页含「三、科研创新」', any('科研' in x for x in L1), str(L1))
    check('校级页不含科研', not any('科研' in x for x in L2), str(L2))
    check('两页板块不同', L1 != L2)
    check('国奖板块顺序为模板顺序',
          [x for x in L1] == sorted(L1, key=lambda s: L1.index(s)))

    # ---------------- 4) 项目级规则覆盖 ----------------
    print('\n4) 项目级规则覆盖只对自己生效')
    # 全局体测线保持默认 75
    base = EA.build_cfg(None)
    check('全局体测线 = 75', abs(base.reading_rules['physical_pass'] - 75.0) < 1e-6,
          base.reading_rules['physical_pass'])
    r = c.post('/api/programs/%d/rules' % p2,
               json={'items': {'reading_rules.physical_pass': 70}},
               headers={'X-CSRF-Token': tok})
    check('给校级项目设体测线 70', r.get_json().get('ok'), r.get_json())
    EA.reload_rules()
    c1 = EA.build_cfg(PG.get(p1))
    c2 = EA.build_cfg(PG.get(p2))
    check('国奖仍是 75（未受影响）', abs(c1.reading_rules['physical_pass'] - 75.0) < 1e-6,
          c1.reading_rules['physical_pass'])
    check('校级变为 70', abs(c2.reading_rules['physical_pass'] - 70.0) < 1e-6,
          c2.reading_rules['physical_pass'])
    check('全局默认未被污染',
          abs(EA.build_cfg(None).reading_rules['physical_pass'] - 75.0) < 1e-6)

    # ---------------- 5) 导出按各自方案 ----------------
    print('\n5) 导出 Word 按各自方案排版')
    for label, fid, pid, want, notwant in [
            ('国奖', f1, p1, '三、科研创新', None),
            ('校级', f2, p2, None, '三、科研创新')]:
        rec = EA.load_result(fid)
        prog = PG.get(pid)
        v = VB.build(rec, file_id=fid, scheme=PG.scheme_of(prog))
        out = os.path.join(HERE, 'data', 'exports', '_check_prog_%s.docx' % pid)
        path, st = EX.build_docx(v, out, template=PG.template_of(prog))
        from docx import Document
        txt = '\n'.join(p.text for p in Document(path).paragraphs)
        ok = True
        if want:
            ok = want in txt
        if notwant:
            ok = notwant not in txt
        check('%s导出%s' % (label, ('含' + want) if want else ('不含' + notwant)), ok,
              txt[:160].replace('\n', ' | '))
        print('     %s：保留 %d 条 / %d 图 / %.0f KB'
              % (label, st['kept_items'], st['images'], st['bytes'] / 1024))

    # ---------------- 6) 归档与删除防护 ----------------
    print('\n6) 归档与删除防护')
    r = c.post('/api/programs/%d/archive' % p1, headers={'X-CSRF-Token': tok})
    check('归档成功', r.get_json().get('ok'))
    check('归档后不在默认列表',
          p1 not in [x['id'] for x in PG.list_programs()])
    check('归档后仍在全部列表',
          p1 in [x['id'] for x in PG.list_programs(include_archived=True)])
    r = c.delete('/api/programs/%d' % p2, headers={'X-CSRF-Token': tok})
    j = r.get_json()
    check('有批次时拒删', (not j.get('ok')) and '无法删除' in (j.get('error') or ''), j)
    c.post('/api/programs/%d/restore' % p1, headers={'X-CSRF-Token': tok})

print('\n' + '=' * 80)
print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败：', FAIL)
sys.exit(1 if FAIL else 0)
