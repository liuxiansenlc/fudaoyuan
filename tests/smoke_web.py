# -*- coding: utf-8 -*-
"""端到端冒烟：登录 → 建批次 → 上传 13 份 docx → 出结论 → 审核页。"""
import os
import re
import sys
import io
import json

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

os.environ['WB_INLINE_WORKER'] = '1'

from web import create_app
from web import db

app = create_app()
SRC = r'D:/浏览器下载/Desktop/2.【班级汇总】国家奖学金各班级申报材料'

with app.test_client() as c:
    # 1. 未登录跳转
    r = c.get('/')
    print('1) 未登录 GET / →', r.status_code, r.headers.get('Location'))

    r = c.get('/login')
    print('2) GET /login →', r.status_code, '页面字节', len(r.data))

    # 2. 取 CSRF（登录页不含表单 token，但 base 里有 meta）
    html = r.get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', html)
    print('   CSRF token:', (m.group(1)[:12] + '…') if m else '无')

    # 3. 登录（先 GET 建立 session，带上 CSRF）
    tok = m.group(1) if m else ''
    r = c.post('/login', data={'username': 'admin', 'password': '123456',
                               '_csrf': tok}, follow_redirects=False)
    print('3) POST /login →', r.status_code, r.headers.get('Location'))
    if r.status_code >= 400:
        print(r.get_data(as_text=True)[:600])

    # 4. 各页面（登录后 token 会重置，重新取一次）
    for path in ['/', '/batches', '/review', '/rules', '/refdata',
                 '/settings/model', '/system', '/users']:
        rr = c.get(path)
        flag = 'OK ' if rr.status_code in (200, 302) else 'ERR'
        print('4) %s %s → %s (%d KB)' % (flag, path, rr.status_code,
                                         len(rr.data) // 1024))
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    c.get('/').get_data(as_text=True)).group(1)

    # 5. 建两个奖学金项目（板块方案不同），验证多奖学金隔离
    print('5) 建奖学金项目：')
    pids = {}
    for name, preset, cat in [('国家奖学金（冒烟）', '国家奖学金', '国家级'),
                              ('校级奖学金（冒烟）', '校级奖学金', '校级')]:
        r = c.post('/api/programs', json={'name': name, 'preset': preset, 'category': cat},
                   headers={'X-CSRF-Token': tok})
        j = r.get_json()
        pids[name] = j.get('id')
        print('   %-18s → %s' % (name, j))
    pid = pids['国家奖学金（冒烟）']

    # 5b. 建批次（必须绑定项目）
    r = c.post('/batches', data={'name': '冒烟测试批次', 'note': 'smoke',
                                'program_id': str(pid), '_csrf': tok},
               follow_redirects=False)
    print('5b) POST /batches →', r.status_code, r.headers.get('Location'))
    bid = int(re.search(r'/batches/(\d+)', r.headers.get('Location', '/batches/0')).group(1))
    print('   批次 id =', bid)

    # 6. 上传 13 份 docx
    files = sorted(f for f in os.listdir(SRC)
                   if f.endswith('.docx') and not f.startswith('~$') and '模板' not in f)
    print('6) 待上传 %d 份' % len(files))
    data = {'_csrf': tok,
            'files': [(open(os.path.join(SRC, f), 'rb'), f) for f in files]}
    r = c.post('/api/batches/%d/files' % bid, data=data,
               content_type='multipart/form-data')
    j = r.get_json()
    print('   上传结果 ok=%s 成功 %d 跳过 %d 失败 %d'
          % (j.get('ok'), len(j.get('uploaded', [])), len(j.get('skipped', [])),
             len(j.get('errors', []))))
    for e in (j.get('errors') or [])[:5]:
        print('     ! ', e)
    for u in (j.get('uploaded') or [])[:20]:
        print('     · %s  条目 %s  图 %s' % (u['student'], u['items'], u['images']))

    # 7. 只出结论（读图全部命中缓存；没有模型配置也不影响）
    r = c.post('/api/batches/%d/run' % bid, json={'kind': 'analyze_only'},
               headers={'X-CSRF-Token': tok})
    j = r.get_json()
    print('7) 触发 analyze →', j)
    tid = j.get('task_id')

    # 8. 手动跑一次任务（不依赖后台线程时机）
    from web.services import task_service as TS
    with app.app_context():
        TS.loop_once()

    r = c.get('/api/tasks/%s' % tid)
    print('8) 任务状态 →', json.dumps(r.get_json().get('task'), ensure_ascii=False))

    rows = db.q_dict('SELECT id,student_name,status,n_items,n_images,error FROM files WHERE batch_id=?', (bid,))
    print('9) 文件状态：')
    for x in rows:
        print('   %-6s %-14s 条%-3s 图%-3s %s' % (x['id'], x['student_name'], x['n_items'],
                                                  x['n_images'], x['status']))

    # 10. 审核页
    ok_ids = [x['id'] for x in rows if x['status'] == 'analyzed']
    print('10) 已出结论 %d 份' % len(ok_ids))
    if ok_ids:
        rr = c.get('/review/%d' % ok_ids[0])
        h = rr.get_data(as_text=True)
        print('    /review/%d → %s (%d KB)' % (ok_ids[0], rr.status_code, len(h) // 1024))
        for kw in ['模板板块', '未认领', 'AI 判断', '反证', '建议补写奖项名',
                   '待确认原因', 'or 待确认', '详情']:
            print('      %-16s %d 处' % (kw, h.count(kw)))
        imgs = re.findall(r'src="(/img/[^"]+)"', h)
        print('      引用图片 %d 张' % len(set(imgs)))
        miss = [u for u in set(imgs)
                if not os.path.exists(os.path.join(HERE, 'data', 'images',
                                                   os.path.basename(u)))]
        print('      缺失图片 %d 张' % len(miss))
        if miss[:3]:
            print('      ', miss[:3])

    # 11. 人工动作
    if ok_ids:
        fid = ok_ids[0]
        rec = json.load(open(os.path.join(HERE, 'data', 'results', '%d.json' % fid), encoding='utf-8'))
        attn = [m['item_id'] for m in rec['matches'] if m['status'] in ('amber', 'pending_weak')]
        if attn:
            r = c.post('/api/files/%d/actions' % fid,
                       json={'decision': 'confirm', 'items': attn[:2]},
                       headers={'X-CSRF-Token': tok})
            print('11) 确认动作 →', r.get_json())
            r = c.get('/review/%d' % fid)
            print('    刷新后「人工确认」出现 %d 次' % r.get_data(as_text=True).count('人工确认'))
        orph = rec.get('orphan_images') or []
        if orph:
            im = [i for i in rec['images'] if i['file'] == orph[0]][0]
            r = c.post('/api/files/%d/orphan/adopt' % fid,
                       json={'img_sha': im['sha256'], 'award_name': '冒烟测试采纳',
                             'section': '技能'},
                       headers={'X-CSRF-Token': tok})
            print('11b) 采纳动作 →', r.get_json())
            h = c.get('/review/%d' % fid).get_data(as_text=True)
            print('    页面出现「人工采纳」%d 次' % h.count('人工采纳'))

    # 12. 详情页全覆盖（这类页面只有带参数时才渲染，最容易漏测）
    print()
    print('12) 详情页覆盖：')
    pages = ['/', '/batches', '/rules', '/refdata', '/settings/model',
             '/system', '/users', '/review', '/batches/%d' % bid]
    if ok_ids:
        pages += ['/review/%d' % i for i in ok_ids[:3]]
    bad = 0
    for path in pages:
        rr = c.get(path)
        if rr.status_code != 200:
            bad += 1
            print('   !! %s → %s' % (path, rr.status_code))
        else:
            print('   OK %-22s %6d B' % (path, len(rr.data)))
    print('   失败 %d 个' % bad)

    # 13. 导出
    print('13) 导出标准化 Word：')
    r = c.post('/api/files/%d/export' % ok_ids[0], headers={'X-CSRF-Token': tok})
    j = r.get_json()
    print('   触发 →', j)
    if j.get('ok'):
        with app.app_context():
            TS.loop_once()
        r = c.get('/api/tasks/%s' % j['task_id'])
        t = r.get_json()['task']
        print('   任务：%s | %s' % (t['status'], t['message'] or t['error']))
        r = c.get('/api/exports/%s/download' % j['task_id'])
        print('   下载 → HTTP %s，%d KB，%s'
              % (r.status_code, len(r.data) // 1024, r.headers.get('Content-Type')))

    # 14. 奖学金项目管理页面
    print('14) 项目管理页面：')
    for path in ['/programs', '/programs/%d' % pid]:
        rr = c.get(path)
        print('   %s %s → %s (%d B)' % ('OK ' if rr.status_code == 200 else 'ERR',
                                        path, rr.status_code, len(rr.data)))
    h = c.get('/programs/%d' % pid).get_data(as_text=True)
    print('   板块方案编辑器:', 'schemeList' in h, '| 规则覆盖:', '规则覆盖' in h,
          '| 参考材料:', '核对参考材料' in h)

    # 15. 自助改密码（改完改回来，避免影响后续）
    print('15) 自助改密码：')
    r = c.post('/api/profile/password', json={'old': '123456', 'new': '1234567'},
               headers={'X-CSRF-Token': tok})
    print('   改密码 →', r.get_json())
    r = c.post('/api/profile/password', json={'old': '1234567', 'new': '123456'},
               headers={'X-CSRF-Token': tok})
    print('   改回 →', r.get_json())
    print('   /profile → %s' % c.get('/profile').status_code)

print('\n冒烟完成')
