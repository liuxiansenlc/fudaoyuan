# -*- coding: utf-8 -*-
"""
任务队列。

为什么用「DB 任务表 + 独立 worker 进程」而不是 Celery/Redis：
    本系统只有"读图"这一件事耗时（要调几十上百次网络接口），
    并发量是个位数辅导员。再加一个 Redis 服务，宝塔上要多维护一个东西，
    收益却不明显。DB 表 + 原子 UPDATE 认领，够用、可断点续跑、可看进度。

任务类型：
    read_images  读图（调视觉模型，写回 cache/vision）
    analyze      配对出结论（纯本地，秒级）
    export       导出标准化 Word（阶段 2）

进度约定：
    progress_total = 本任务要处理的总数
    progress_done  = 已完成
    phase          = 人类可读的当前阶段（前端直接显示）
"""
import os
import sys
import json
import time
import socket
import uuid
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

from ..config import WebConfig
from .. import db
from ..security import record_usage as _record_usage

WORKER_ID = '%s-%s' % (socket.gethostname(), uuid.uuid4().hex[:6])

# 任务处理器注册表：kind -> callable(task, api) -> None
HANDLERS = {}


def handler(kind):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


# ------------------------------------------------------------------ 入队
def enqueue(kind, batch_id=None, file_id=None, payload=None, user_id=None):
    tid = db.ex(
        'INSERT INTO tasks(kind,batch_id,file_id,payload,status,created_by,created_at)'
        ' VALUES(?,?,?,?,?,?,?)',
        (kind, batch_id, file_id, json.dumps(payload or {}, ensure_ascii=False),
         'queued', user_id, db.utcnow()))
    return tid


def get(task_id):
    return db.q_dict('SELECT * FROM tasks WHERE id=?', (task_id,), one=True)


def bump(task_id, done=None, total=None, phase=None, message=None):
    sets, args = [], []
    if done is not None:
        sets.append('progress_done=?'); args.append(done)
    if total is not None:
        sets.append('progress_total=?'); args.append(total)
    if phase is not None:
        sets.append('phase=?'); args.append(phase)
    if message is not None:
        sets.append('message=?'); args.append(message)
    if not sets:
        return
    args.append(task_id)
    db.ex('UPDATE tasks SET %s WHERE id=?' % ','.join(sets), tuple(args))


def finish(task_id, status='done', error=''):
    db.ex('UPDATE tasks SET status=?, error=?, finished_at=?, claimed_by=? WHERE id=?',
          (status, error[:2000], db.utcnow(), '', task_id))


def cancel(task_id):
    db.ex("UPDATE tasks SET status='canceled', finished_at=? WHERE id=? AND status IN ('queued',)",
          (db.utcnow(), task_id))


def claim_next(worker_id=WORKER_ID):
    """原子认领一个排队任务；没有则返回 None。"""
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM tasks WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        if not row:
            return None
        cur = conn.execute(
            "UPDATE tasks SET status='running', started_at=?, claimed_by=?"
            " WHERE id=? AND status='queued'",
            (db.utcnow(), worker_id, row['id']))
        if cur.rowcount != 1:
            return None
        return dict(row)


def requeue_stale(minutes=20):
    """worker 崩了会留下 running 僵尸任务，超时放回队列。"""
    # 截止时间在 Python 侧算好再传参，绕开 sqlite datetime()/MySQL NOW() 的差异
    db.ex("UPDATE tasks SET status='queued', claimed_by='', started_at=NULL,"
          " message='（上次执行中断，已重新排队）'"
          " WHERE status='running' AND started_at < ?",
          (db.utcnow_minus(minutes),))


# ------------------------------------------------------------------ 执行
def run_task(task):
    kind = task['kind']
    fn = HANDLERS.get(kind)
    if not fn:
        finish(task['id'], 'failed', '未知任务类型：%s' % kind)
        return
    try:
        fn(task, Sys())
        if get(task['id'])['status'] == 'running':
            finish(task['id'], 'done')
    except Exception as e:
        traceback.print_exc()
        finish(task['id'], 'failed', '%s: %s' % (type(e).__name__, e))


class Sys(object):
    """处理器拿它来上报进度、判断是否被取消。"""
    def set_total(self, n, phase=None, message=None):
        bump(_cur, total=n, phase=phase, message=message)

    def step(self, n=1, phase=None, message=None):
        t = get(_cur)
        bump(_cur, done=(t['progress_done'] + n), phase=phase, message=message)

    def phase(self, phase, message=None):
        bump(_cur, phase=phase, message=message)

    def canceled(self):
        return get(_cur)['status'] == 'canceled'


_cur = None


def loop_once(worker_id=WORKER_ID):
    global _cur
    t = claim_next(worker_id)
    if not t:
        return False
    _cur = t['id']
    run_task(t)
    _cur = None
    return True


def loop_forever(sleep=2.0, worker_id=WORKER_ID):
    requeue_stale()
    while True:
        try:
            if not loop_once(worker_id):
                time.sleep(sleep)
        except KeyboardInterrupt:
            return
        except Exception:
            traceback.print_exc()
            time.sleep(sleep)


# ------------------------------------------------------------------ 内置处理器
@handler('read_images')
def _h_read_images(task, api):
    """读图：把批次里所有没读过的图送去视觉模型，写回缓存。"""
    from . import engine_adapter as EA, vision_client as VC

    from . import programs as PG
    program = PG.get_by_batch(task['batch_id'])
    cfg = EA.build_cfg(program)
    files = db.q_dict('SELECT * FROM files WHERE batch_id=?', (task['batch_id'],))
    payload = db.jloads(task['payload'])
    only_file = payload.get('file_id')

    model_row = VC.active_model()
    if not model_row:
        raise RuntimeError('尚未配置可用的视觉模型（请先在「模型设置」里填 base_url / api_key / 模型名）')

    # 1) 汇总所有待读图（跨文件按 sha256 去重，已缓存的直接跳过）
    api.phase('扫描待读图片')
    seen, todo = set(), []
    for f in files:
        if only_file and f['id'] != only_file:
            continue
        try:
            pr = EA.read_progress(f['stored_path'], cfg)
        except Exception as e:
            db.ex("UPDATE files SET error=?, status='parse_failed' WHERE id=?",
                  (str(e), f['id']))
            continue
        for item in pr['todo']:
            if item['triage'] == 'noise':
                continue
            if item['sha256'] in seen:
                continue
            seen.add(item['sha256'])
            todo.append({'file_id': f['id'], 'docx': f['stored_path'],
                         'student': f['student_name'], **item})

    api.set_total(len(todo), phase='准备读图',
                  message='共 %d 张待读（已命中缓存的自动跳过）' % len(todo))
    if not todo:
        api.phase('无需读图', '所有图片都已在缓存中')
        return

    quota_left, _, _ = VC.check_quota()
    if quota_left <= 0:
        raise RuntimeError('已达今日读图上限（%d 张），请调高上限或明天再跑'
                           % WebConfig.VISION_DAILY_LIMIT)
    if len(todo) > quota_left:
        todo = todo[:quota_left]

    done = [0]
    failed = []
    mask_log = []
    lock = threading.Lock()

    from . import privacy as PV
    masking = PV.enabled()

    def work(item):
        try:
            data = EA.read_image_bytes(item['docx'], item['media'])
        except Exception as e:
            return item, None, '取图失败：%s' % e, []
        hits = []
        if masking:
            # 只改"送去模型"的那份字节，落盘的原始图片不动
            data, hits = PV.mask_image(data)
        obj, err = VC.read_image(model_row, data, item['student'])
        return item, obj, err, hits

    with ThreadPoolExecutor(max_workers=int(WebConfig.VISION_CONCURRENCY)) as pool:
        futs = {pool.submit(work, it): it for it in todo}
        for fu in as_completed(futs):
            item, obj, err, hits = fu.result()
            with lock:
                done[0] += 1
                if obj:
                    if hits:
                        # 标记这条读数是基于遮挡后的图 —— 以后排查"结果对不上"时能立刻定位
                        obj['masked'] = True
                        obj['mask_hits'] = hits
                        mask_log.extend([{'sha256': item['sha256'], 'file': item['file'],
                                          **h} for h in hits])
                    VC.write_cache(item['sha256'], obj)
                    _record_usage(model_row['model'], images=1, calls=1)
                    api.step(1, phase='读图中',
                             message='%s · %s' % (item['student'], item['file']))
                else:
                    failed.append({'file': item['file'], 'student': item['student'],
                                   'sha256': item['sha256'], 'error': err})
                    api.step(1, phase='读图中（有失败）',
                             message='失败：%s · %s' % (item['student'], item['file']))

    if failed:
        p = os.path.join(WebConfig.LOG_DIR, 'read_failed_%s.json' % task['id'])
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(failed, f, ensure_ascii=False, indent=1)
        bump(task['id'], message='完成 %d 张，失败 %d 张（见日志）' % (done[0] - len(failed), len(failed)))

    if mask_log:
        p = os.path.join(WebConfig.LOG_DIR, 'masked_%s.json' % task['id'])
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(mask_log, f, ensure_ascii=False, indent=1)
        bump(task['id'], message='已遮挡敏感信息 %d 处（详见日志）' % len(mask_log))

    # 读图完成 → 自动接着出结论（用户点一次「运行」就够）
    if payload.get('auto_analyze'):
        enqueue('analyze', batch_id=task['batch_id'], file_id=only_file,
                payload={'file_id': only_file}, user_id=task.get('created_by'))


@handler('analyze')
def _h_analyze(task, api):
    """配对出结论：纯本地，读缓存 → 匹配 → 落 result.json。"""
    from . import engine_adapter as EA

    from . import programs as PG
    program = PG.get_by_batch(task['batch_id'])
    cfg = EA.build_cfg(program)
    payload = db.jloads(task['payload'])
    only_file = payload.get('file_id')

    api.phase('加载参考数据')
    ref = EA.load_reference(cfg, program)
    files = [f for f in db.q_dict('SELECT * FROM files WHERE batch_id=? ORDER BY id',
                                  (task['batch_id'],))
             if not only_file or f['id'] == only_file]
    api.set_total(len(files), phase='配对中',
                  message='A类竞赛 %d 条 / 成绩表 %d 条学号'
                          % (ref['meta']['competition_n'], ref['meta']['ranking_n']))

    ok, bad = 0, 0
    for f in files:
        if api.canceled():
            return
        try:
            rec = EA.analyze_one(f['stored_path'], ref, cfg, file_id=f['id'])
            EA.save_result(f['id'], rec)
            n_auto = sum(1 for m in rec['matches']
                         if m['status'] in ('green', 'blue'))
            db.ex("UPDATE files SET status='analyzed', n_items=?, n_images=?,"
                  " analyzed_at=?, error='' WHERE id=?",
                  (len(rec['items']), len(rec['images']), db.utcnow(), f['id']))
            ok += 1
            api.step(1, phase='配对中',
                     message='%s · 自动配对 %d/%d' % (f['student_name'], n_auto,
                                                    len(rec['matches'])))
        except Exception as e:
            bad += 1
            db.ex("UPDATE files SET status='analyze_failed', error=? WHERE id=?",
                  ('%s: %s' % (type(e).__name__, e), f['id']))
            api.step(1, phase='配对中（有失败）', message='失败：%s' % f['student_name'])
    api.phase('完成', '成功 %d 份，失败 %d 份' % (ok, bad))


@handler('export')
def _h_export(task, api):
    """导出标准化 Word：单个学生出 docx，整批次打包 zip。"""
    from . import engine_adapter as EA, export_service as EX
    from . import view_builder as VB, programs as PG

    program = PG.get_by_batch(task['batch_id'])
    scheme = PG.scheme_of(program)
    template = PG.template_of(program)
    reject_mode = PG.reject_mode_of(program)
    program_name = (program or {}).get('name') or '国家奖学金'
    payload = db.jloads(task['payload'])
    kind = payload.get('kind') or ('file' if payload.get('file_id') else 'batch')

    if kind == 'file':
        files = [f for f in db.q_dict('SELECT * FROM files WHERE id=?', (payload['file_id'],))]
    else:
        files = db.q_dict("SELECT * FROM files WHERE batch_id=? AND status='analyzed'"
                          " ORDER BY id", (task['batch_id'],))
    files = [f for f in files if f]
    if not files:
        raise RuntimeError('没有可导出的材料（需要先出结论）')

    api.set_total(len(files), phase='导出中', message='共 %d 份' % len(files))
    made, failed = [], []
    for f in files:
        rec = EA.load_result(f['id'])
        if not rec:
            failed.append((f['student_name'], '没有分析结果'))
            api.step(1, message='跳过 %s' % f['student_name'])
            continue
        try:
            view = VB.build(rec, file_id=f['id'], scheme=scheme,
                            reject_mode=reject_mode)
            cls = (rec['student'].get('class_no') or rec['student'].get('class') or '').strip()
            fn = EX.safe_filename('%s_%s_%s申报材料（标准化）.docx'
                                  % (cls, f['student_name'] or f['id'], program_name))
            out = os.path.join(os.path.dirname(WebConfig.DB_PATH), 'exports', fn)
            out, st = EX.build_docx(view, out, template=template,
                                    program_name=program_name,
                                    reject_mode=reject_mode)
            made.append(out)
            api.step(1, message='%s · 保留 %d 项 / %d 图%s'
                     % (f['student_name'], st['kept_items'], st['images'],
                        ('，人工否定 %d 项已标注' % st['rejected_marked'])
                        if st.get('rejected_marked') else
                        ('，人工否定 %d 项已剔除' % st['rejected_removed'])
                        if st.get('rejected_removed') else ''))
        except Exception as e:
            failed.append((f['student_name'], '%s: %s' % (type(e).__name__, e)))
            api.step(1, message='失败：%s' % f['student_name'])

    # 单个学生 → 直接给 docx；整批次 → 打包 zip
    if kind == 'file' and len(made) == 1:
        final = made[0]
    else:
        b = db.q_dict('SELECT * FROM batches WHERE id=?', (task['batch_id'],), one=True)
        bname = EX.safe_filename((b or {}).get('name') or 'batch')
        stamp = time.strftime('%Y%m%d')
        final, _sz = EX.batch_zip(
            made, os.path.join(os.path.dirname(WebConfig.DB_PATH), 'exports',
                               '%s_标准化材料_%s.zip' % (bname, stamp)))

    payload2 = dict(payload)
    payload2.update({'result': final, 'made': len(made),
                     'failed': [{'student': a, 'error': b} for a, b in failed]})
    db.ex('UPDATE tasks SET payload=? WHERE id=?',
          (json.dumps(payload2, ensure_ascii=False), task['id']))
    bump(task['id'], phase='完成',
         message='导出 %d 份，失败 %d 份' % (len(made), len(failed)))


# ------------------------------------------------------------------ 内嵌 worker
_inline_started = False


def start_inline_worker(app):
    """开发/小规模部署：在 Web 进程里起一个后台线程跑任务。

    生产环境（宝塔）建议关掉，改用 `python worker.py` 独立进程，
    这样 gunicorn 重启不会打断正在跑的读图任务。
    """
    global _inline_started
    if _inline_started or not WebConfig.INLINE_WORKER:
        return
    _inline_started = True

    def run():
        with app.app_context():
            loop_forever(sleep=1.5, worker_id='inline-' + WORKER_ID)

    t = threading.Thread(target=run, name='inline-worker', daemon=True)
    t.start()
