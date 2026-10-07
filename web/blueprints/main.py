# -*- coding: utf-8 -*-
"""批次管理 + 多文件上传 + 仪表盘。"""
import os
import re
import hashlib
import zipfile

from flask import (Blueprint, render_template, request, redirect, url_for,
                   jsonify, flash, current_app)

from ..config import WebConfig
from .. import db
from ..security import login_required, current_user
from ..services import engine_adapter as EA
from ..services import task_service as TS
from ..services import programs as PG

bp = Blueprint('main', __name__)


# ------------------------------------------------------------------ 工具
_BAD = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def safe_name(name):
    """保留中文，只去危险字符与路径分隔符。"""
    name = os.path.basename(name or '')
    name = _BAD.sub('_', name).strip().strip('.')
    return name or 'unnamed'


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def batch_stats(batch_id):
    rows = db.q_dict('SELECT * FROM files WHERE batch_id=?', (batch_id,))
    n = len(rows)
    analyzed = sum(1 for r in rows if r['status'] == 'analyzed')
    return {'files': n, 'analyzed': analyzed,
            'n_items': sum(r['n_items'] or 0 for r in rows),
            'images': sum(r['n_images'] or 0 for r in rows),
            'failed': sum(1 for r in rows if r['status'] in ('parse_failed', 'analyze_failed'))}


def overall_stats():
    rows = db.q_dict('SELECT * FROM files')
    st = {'files': len(rows), 'analyzed': sum(1 for r in rows if r['status'] == 'analyzed'),
          'n_items': sum(r['n_items'] or 0 for r in rows),
          'images': sum(r['n_images'] or 0 for r in rows)}
    # 汇总所有已分析结果里的分层数量
    auto = manual = need = miss = 0
    ns, nimg = 0, 0
    for r in rows:
        rec = EA.load_result(r['id'])
        if not rec:
            continue
        ns += 1
        nimg += len(rec.get('images') or [])
        for m in rec.get('matches') or []:
            s = m['status']
            if s in ('green', 'blue'):
                auto += 1
            elif s in ('amber', 'pending_weak'):
                need += 1
            elif s == 'red':
                miss += 1
    st.update(auto=auto, need=need, miss=miss, students=ns, imgs=nimg)
    return st


# ------------------------------------------------------------------ 页面
@bp.route('/')
@login_required
def dashboard():
    batches = db.q_dict('SELECT b.*, p.name AS program_name, p.category AS program_category'
                        ' FROM batches b LEFT JOIN programs p ON b.program_id=p.id'
                        ' ORDER BY b.id DESC LIMIT 12')
    for b in batches:
        b.update(batch_stats(b['id']))
    recent = db.q_dict('SELECT * FROM files ORDER BY id DESC LIMIT 8')
    return render_template('dashboard.html', batches=batches, recent=recent,
                           st=overall_stats(),
                           cache_n=_cache_count())


@bp.route('/batches')
@login_required
def batches():
    rows = db.q_dict('SELECT b.*, p.name AS program_name, p.category AS program_category'
                     ' FROM batches b LEFT JOIN programs p ON b.program_id=p.id'
                     ' ORDER BY b.id DESC')
    for b in rows:
        b.update(batch_stats(b['id']))
    return render_template('batches.html', batches=rows,
                           programs=PG.list_programs())


@bp.route('/batches', methods=['POST'])
@login_required
def create_batch():
    u = current_user()
    name = (request.form.get('name') or '').strip()
    pid = request.form.get('program_id') or ''
    if not name:
        flash('请填写批次名称')
        return redirect(url_for('main.batches'))
    if not pid:
        flash('请选择奖学金项目（不同奖学金的核对口径不同）')
        return redirect(url_for('main.batches'))
    bid = db.ex('INSERT INTO batches(program_id,name,note,status,created_by,created_at)'
                ' VALUES(?,?,?,?,?,?)',
                (int(pid), name, (request.form.get('note') or '').strip(), 'draft',
                 u['id'], db.utcnow()))
    db.audit(u['id'], 'batch.create', bid, '%s @ program %s' % (name, pid))
    return redirect(url_for('main.batch_detail', bid=bid))


@bp.route('/batches/<int:bid>')
@login_required
def batch_detail(bid):
    b = db.q_dict('SELECT * FROM batches WHERE id=?', (bid,), one=True)
    if not b:
        flash('批次不存在')
        return redirect(url_for('main.batches'))
    files = db.q_dict('SELECT * FROM files WHERE batch_id=? ORDER BY id', (bid,))
    tasks = db.q_dict('SELECT * FROM tasks WHERE batch_id=? ORDER BY id DESC LIMIT 12', (bid,))
    program = PG.get(b.get('program_id'))
    ref = EA.load_reference(EA.build_cfg(program), program)
    ref_ready = bool(ref['meta']['competition_n'] or ref['meta']['ranking_n'])
    return render_template('batch_detail.html', b=b, files=files, tasks=tasks,
                           st=batch_stats(bid), program=program, ref=ref,
                           ref_ready=ref_ready)


def _cache_count():
    d = os.path.join(WebConfig.ENGINE_CACHE_DIR, 'vision')
    if not os.path.isdir(d):
        return 0
    return len([f for f in os.listdir(d) if f.endswith('.json')])


# ------------------------------------------------------------------ 上传
@bp.route('/api/batches/<int:bid>/files', methods=['POST'])
@login_required
def upload_files(bid):
    u = current_user()
    b = db.q_dict('SELECT * FROM batches WHERE id=?', (bid,), one=True)
    if not b:
        return jsonify(ok=False, error='批次不存在'), 404

    fs = request.files.getlist('files')
    if not fs:
        return jsonify(ok=False, error='没有收到文件'), 400
    if len(fs) > WebConfig.MAX_FILES_PER_UPLOAD:
        return jsonify(ok=False, error='单次最多 %d 个文件' % WebConfig.MAX_FILES_PER_UPLOAD), 400

    dest_dir = os.path.join(WebConfig.UPLOAD_DIR, str(bid))
    os.makedirs(dest_dir, exist_ok=True)

    ok, skipped, errors = [], [], []
    for f in fs:
        original = safe_name(f.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in WebConfig.ALLOWED_DOCX:
            errors.append({'name': original, 'error': '只接受 .docx 文件'})
            continue
        if db.q('SELECT 1 FROM files WHERE batch_id=? AND orig_name=?', (bid, original)):
            skipped.append({'name': original, 'reason': '同名文件已存在'})
            continue
        stored = os.path.join(dest_dir, '%d_%s' % (int(__import__('time').time() * 1000), original))
        f.save(stored)
        try:
            info = EA.ingest(stored)
            status = 'parsed'
            err = ''
        except Exception as e:
            info = {'student_name': original[:8], 'n_items': 0, 'n_images': 0}
            status = 'parse_failed'
            err = '%s: %s' % (type(e).__name__, e)
        fid = db.ex(
            'INSERT INTO files(batch_id,student_name,orig_name,stored_path,sha256,size,'
            'n_items,n_images,status,error,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
            (bid, info.get('student_name', ''), original, stored, sha256_file(stored),
             os.path.getsize(stored), info.get('n_items', 0), info.get('n_images', 0),
             status, err, db.utcnow()))
        if err:
            errors.append({'name': original, 'error': err})
        else:
            ok.append({'id': fid, 'name': original, 'student': info.get('student_name'),
                       'items': info.get('n_items'), 'images': info.get('n_images')})

    db.ex('UPDATE batches SET updated_at=?, status=? WHERE id=?',
          (db.utcnow(), 'uploaded', bid))
    db.audit(u['id'], 'batch.upload', bid, '%d 个文件' % len(ok))
    return jsonify(ok=True, uploaded=ok, skipped=skipped, errors=errors)


@bp.route('/api/files/<int:fid>', methods=['DELETE'])
@login_required
def delete_file(fid):
    u = current_user()
    row = db.q_dict('SELECT * FROM files WHERE id=?', (fid,), one=True)
    if not row:
        return jsonify(ok=False, error='文件不存在'), 404
    try:
        if row['stored_path'] and os.path.exists(row['stored_path']):
            os.remove(row['stored_path'])
    except Exception:
        pass
    p = EA.result_path(fid)
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass
    db.ex('DELETE FROM files WHERE id=?', (fid,))
    db.ex('DELETE FROM match_states WHERE file_id=?', (fid,))
    db.ex('DELETE FROM orphan_adoptions WHERE file_id=?', (fid,))
    db.audit(u['id'], 'file.delete', fid, row['orig_name'])
    return jsonify(ok=True)


# ------------------------------------------------------------------ 运行
@bp.route('/api/batches/<int:bid>/run', methods=['POST'])
@login_required
def run_batch(bid):
    u = current_user()
    data = request.get_json(silent=True) or {}
    only = data.get('file_id')
    kind = data.get('kind') or 'full'          # full | analyze_only

    # ★ 空批次不该能运行：没有学生材料，跑了也是空转（还会卡在"运行中"）
    if only:
        n_files = db.q('SELECT COUNT(*) c FROM files WHERE id=?', (only,), one=True)['c']
        if not n_files:
            return jsonify(ok=False, error='没有可运行的材料（文件不存在）'), 400
    else:
        n_files = db.q('SELECT COUNT(*) c FROM files WHERE batch_id=?', (bid,), one=True)['c']
        if not n_files:
            return jsonify(ok=False, error='这个批次还没有上传任何学生材料，请先上传再运行'), 400

    running = db.q_dict("SELECT * FROM tasks WHERE batch_id=? AND status IN ('queued','running')",
                        (bid,), one=True)
    if running:
        return jsonify(ok=False, error='该批次已有任务在跑，请等它结束', task_id=running['id']), 409

    # 清掉上次中断遗留的瞬态状态（读取中/配对中），避免清单显示卡住
    if only:
        db.ex("UPDATE files SET status='uploaded' WHERE id=? AND status IN ('reading','analyzing')",
              (only,))
    else:
        db.ex("UPDATE files SET status='uploaded' WHERE batch_id=?"
              " AND status IN ('reading','analyzing')", (bid,))

    if kind == 'analyze_only':
        tid = TS.enqueue('analyze', batch_id=bid, file_id=only,
                         payload={'file_id': only}, user_id=u['id'])
        db.audit(u['id'], 'batch.analyze', bid)
    else:
        tid = TS.enqueue('read_images', batch_id=bid, file_id=only,
                         payload={'file_id': only, 'auto_analyze': True}, user_id=u['id'])
        db.audit(u['id'], 'batch.run', bid)
    return jsonify(ok=True, task_id=tid)


@bp.route('/api/files/<int:fid>/reanalyze', methods=['POST'])
@login_required
def reanalyze(fid):
    u = current_user()
    row = db.q_dict('SELECT * FROM files WHERE id=?', (fid,), one=True)
    if not row:
        return jsonify(ok=False, error='文件不存在'), 404
    tid = TS.enqueue('analyze', batch_id=row['batch_id'], file_id=fid,
                     payload={'file_id': fid}, user_id=u['id'])
    return jsonify(ok=True, task_id=tid)
