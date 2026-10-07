# -*- coding: utf-8 -*-
"""
运行进度 / 暂停继续 / 清单实时刷新 的自检。

覆盖：
  1. /api/tasks/<id> 返回 paused / started_at / percent（前端倒计时与状态依赖这些字段）
  2. 暂停 / 继续 接口（状态存 payload，部署零迁移）
  3. Sys.wait_if_paused()：暂停时阻塞、继续后放行、取消后返回 False
  4. /api/batches/<id>/files_table 返回清单 HTML（运行中实时刷新用）
  5. 材料清单模板能渲染"读取中/配对中"瞬态状态
不联网、不跑真实读图，全部确定性。
"""
import os
import re
import sys
import time
import tempfile
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

# 用独立临时库：绝不碰 data/app.db（本机可能正跑着服务，共库会互相抢任务）
_DB = os.path.join(tempfile.gettempdir(), 'wb_progress_check.db')
for _suffix in ('', '-wal', '-shm'):
    try:
        os.remove(_DB + _suffix)
    except OSError:
        pass
os.environ['WB_DB_PATH'] = _DB
os.environ['WB_INLINE_WORKER'] = '0'          # 关掉内嵌 worker，手动控制，避免时序干扰

from web import create_app
from web import db
from web.config import WebConfig
from web.services import task_service as TS

app = create_app()


def _csrf(c):
    h = c.get('/').get_data(as_text=True)
    m = re.search(r'name="csrf-token" content="([^"]+)"', h)
    return m.group(1) if m else ''


def main():
    with app.test_client() as c:
        c.get('/login')
        tok0 = re.search(r'name="csrf-token" content="([^"]+)"',
                         c.get('/login').get_data(as_text=True)).group(1)
        r = c.post('/login', data={'username': 'admin', 'password': '123456', '_csrf': tok0})
        assert r.status_code in (200, 302), '登录失败'
        tok = _csrf(c)
        assert tok, '拿不到 CSRF'

        # 造一个批次 + 两份材料（直接入库，确定性）
        with app.app_context():
            bid = db.ex("INSERT INTO batches(program_id,name,note,status,created_by,created_at)"
                        " VALUES(NULL,?,?,?,?,?)",
                        ('进度自检批次', '', 'draft', 1, db.utcnow()))
            fid = db.ex("INSERT INTO files(batch_id,student_name,orig_name,stored_path,sha256,"
                        "size,n_items,n_images,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (bid, '张三', 't.docx', '/tmp/t.docx', '', 0, 0, 0, 'uploaded',
                         db.utcnow()))

        # 1) 清单表格接口
        r = c.get('/api/batches/%d/files_table' % bid)
        j = r.get_json()
        assert j['ok'] and j['count'] == 1 and '<table' in j['html'], 'files_table 异常'
        assert '张三' in j['html'], '清单里应包含学生名'
        print('1) files_table OK（%d 份，含学生名）' % j['count'])

        # 2) 瞬态状态能渲染
        with app.app_context():
            db.ex("UPDATE files SET status='analyzing' WHERE id=?", (fid,))
        html = c.get('/api/batches/%d/files_table' % bid).get_json()['html']
        assert '配对中' in html, 'analyzing 状态未渲染'
        with app.app_context():
            db.ex("UPDATE files SET status='reading' WHERE id=?", (fid,))
        html = c.get('/api/batches/%d/files_table' % bid).get_json()['html']
        assert '读取中' in html, 'reading 状态未渲染'
        with app.app_context():
            db.ex("UPDATE files SET status='uploaded' WHERE id=?", (fid,))
        print('2) 瞬态状态（读取中/配对中）渲染 OK')

        # 3) 任务状态字段
        with app.app_context():
            tid = TS.enqueue('analyze', batch_id=bid, payload={}, user_id=1)
        t = c.get('/api/tasks/%d' % tid).get_json()['task']
        for k in ('paused', 'started_at', 'percent', 'done', 'total', 'phase'):
            assert k in t, '任务状态缺少字段 %s' % k
        assert t['paused'] is False
        print('3) 任务状态字段 OK → paused=%s started_at=%s percent=%s'
              % (t['paused'], t['started_at'], t['percent']))

        # 4) 暂停 / 继续 接口
        r = c.post('/api/tasks/%d/pause' % tid, headers={'X-CSRF-Token': tok})
        assert r.get_json()['ok'], '暂停失败'
        t = c.get('/api/tasks/%d' % tid).get_json()['task']
        assert t['paused'] is True, '暂停后 paused 应为 True'
        r = c.post('/api/tasks/%d/resume' % tid, headers={'X-CSRF-Token': tok})
        assert r.get_json()['ok'], '继续失败'
        t = c.get('/api/tasks/%d' % tid).get_json()['task']
        assert t['paused'] is False, '继续后 paused 应为 False'
        print('4) 暂停 / 继续 接口 OK')

        # 5) wait_if_paused：暂停期间阻塞 → 主线程「继续」后放行
        with app.app_context():
            sys5 = TS.Sys(tid)
            TS.pause(tid)
            assert sys5.paused() is True
            assert sys5.canceled() is False
            box = {}

            def _waiter():
                box['ok'] = sys5.wait_if_paused()
                box['t'] = time.time()

            th = threading.Thread(target=_waiter, daemon=True)
            t0 = time.time()
            th.start()
            time.sleep(1.0)
            assert th.is_alive(), '暂停期间 wait_if_paused 必须阻塞、不应提前返回'
            TS.resume(tid)                      # 主线程负责「继续」
            th.join(timeout=10)
            assert not th.is_alive(), '继续后 wait_if_paused 应尽快返回'
            assert box.get('ok') is True, '继续后应返回 True'
            dt = box['t'] - t0
            assert dt >= 0.5, '应确实阻塞了一段时间（实测 %.2fs）' % dt
            print('5) wait_if_paused 暂停→继续 OK（阻塞 %.2fs）' % dt)

        # 6) wait_if_paused：取消时应返回 False
        with app.app_context():
            sys6 = TS.Sys(tid)
            TS.pause(tid)
            TS.cancel(tid)
            assert sys6.wait_if_paused() is False, '取消后应返回 False'
            print('6) wait_if_paused 取消→False OK')

        # 6b) 安全阀：暂停无人点「继续」时，超时自动继续（防止 worker 被永久占住）
        with app.app_context():
            tid2 = TS.enqueue('analyze', batch_id=bid, payload={}, user_id=1)
            sys2 = TS.Sys(tid2)
            TS.pause(tid2)
            old = WebConfig.TASK_MAX_PAUSE_SECONDS
            WebConfig.TASK_MAX_PAUSE_SECONDS = 2      # 临时把上限压到 2 秒便于测试
            try:
                t0 = time.time()
                ok = sys2.wait_if_paused()
                dt = time.time() - t0
                assert ok is True, '超时应自动继续并返回 True'
                assert dt >= 1.8, '应至少等满上限（实测 %.2fs）' % dt
                assert sys2.paused() is False, '超时后应已清除暂停标记'
                print('6b) 暂停超时自动继续 OK（%.2fs 后放行）' % dt)
            finally:
                WebConfig.TASK_MAX_PAUSE_SECONDS = old
                db.ex('DELETE FROM tasks WHERE id=?', (tid2,))

        # 7) 已结束任务不能再暂停
        r = c.post('/api/tasks/%d/pause' % tid, headers={'X-CSRF-Token': tok})
        assert r.status_code == 400, '已取消任务不应能暂停'
        print('7) 已结束任务拒绝暂停 OK（HTTP %d）' % r.status_code)

        # 清理
        with app.app_context():
            db.ex('DELETE FROM tasks WHERE id=?', (tid,))
            db.ex('DELETE FROM files WHERE batch_id=?', (bid,))
            db.ex('DELETE FROM batches WHERE id=?', (bid,))

    print('\n全部通过')


if __name__ == '__main__':
    main()
