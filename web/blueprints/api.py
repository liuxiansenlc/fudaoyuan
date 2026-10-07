# -*- coding: utf-8 -*-
"""异步接口：任务进度轮询、人工审核动作、未认领图采纳。"""
import os

from flask import Blueprint, jsonify, request, render_template

from ..config import WebConfig
from .. import db
from ..security import login_required, admin_required, current_user
from ..services import task_service as TS
from ..services import engine_adapter as EA

bp = Blueprint('api', __name__, url_prefix='/api')


def _task_json(t):
    """任务 → 前端 JSON。paused 存在 payload 里（部署零迁移）。"""
    p = db.jloads(t.get('payload')) if t.get('payload') else {}
    return {
        'id': t['id'], 'kind': t['kind'], 'status': t['status'],
        'phase': t['phase'], 'message': t['message'], 'error': t['error'],
        'done': t['progress_done'], 'total': t['progress_total'],
        'percent': (round(100.0 * t['progress_done'] / t['progress_total'], 1)
                    if t['progress_total'] else (100 if t['status'] == 'done' else 0)),
        'paused': bool((p or {}).get('paused')),
        'started_at': t.get('started_at'), 'created_at': t.get('created_at'),
        'worker': TS.worker_status(),
    }


# ------------------------------------------------------------------ 任务
@bp.route('/tasks/<int:tid>')
@login_required
def task_status(tid):
    t = TS.get(tid)
    if not t:
        return jsonify(ok=False, error='任务不存在'), 404
    return jsonify(ok=True, task=_task_json(t))


@bp.route('/batches/<int:bid>/tasks')
@login_required
def batch_tasks(bid):
    rows = db.q_dict('SELECT * FROM tasks WHERE batch_id=? ORDER BY id DESC LIMIT 20', (bid,))
    out = [_task_json(t) for t in rows]
    return jsonify(ok=True, tasks=out, files=_files_brief(bid))


@bp.route('/worker/status')
@login_required
def worker_status():
    """worker 是否活着 —— 界面用来判断"任务到底有没有人在处理"。"""
    return jsonify(ok=True, worker=TS.worker_status())


@bp.route('/batches/<int:bid>/files_table')
@login_required
def batch_files_table(bid):
    """返回材料清单表格的 HTML，供"运行中实时刷新清单状态"用。"""
    files = db.q_dict('SELECT * FROM files WHERE batch_id=? ORDER BY id', (bid,))
    html = render_template('_file_table.html', files=files)
    return jsonify(ok=True, html=html, count=len(files))


def _files_brief(bid):
    rows = db.q_dict('SELECT id,student_name,orig_name,status,n_items,n_images,error'
                     ' FROM files WHERE batch_id=? ORDER BY id', (bid,))
    out = []
    for r in rows:
        rec_p = EA.result_path(r['id'])
        import os
        r = dict(r)
        r['has_result'] = os.path.exists(rec_p)
        out.append(r)
    return out


@bp.route('/tasks/<int:tid>/cancel', methods=['POST'])
@login_required
def task_cancel(tid):
    TS.cancel(tid)
    return jsonify(ok=True)


@bp.route('/tasks/<int:tid>/pause', methods=['POST'])
@login_required
def task_pause(tid):
    ok = TS.pause(tid)
    if not ok:
        return jsonify(ok=False, error='任务不在运行中，无法暂停'), 400
    return jsonify(ok=True, paused=True)


@bp.route('/tasks/<int:tid>/resume', methods=['POST'])
@login_required
def task_resume(tid):
    ok = TS.resume(tid)
    if not ok:
        return jsonify(ok=False, error='任务不在运行中，无法继续'), 400
    return jsonify(ok=True, paused=False)


# ------------------------------------------------------------------ 审核动作
@bp.route('/files/<int:fid>/actions', methods=['POST'])
@login_required
def act(fid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    decision = d.get('decision')
    items = d.get('items') or []
    if isinstance(items, (int, str)):
        items = [items]
    if decision not in ('confirm', 'reject', 'reassign', 'reset'):
        return jsonify(ok=False, error='未知动作'), 400
    if not items:
        return jsonify(ok=False, error='没有选中条目'), 400

    n = 0
    with db.tx() as conn:
        for iid in items:
            iid = int(iid)
            if decision == 'reset':
                conn.execute('DELETE FROM match_states WHERE file_id=? AND item_id=?',
                             (fid, iid))
            else:
                vals = {'file_id': fid, 'item_id': iid, 'decision': decision,
                        'img_sha': d.get('img_sha') or '', 'img_file': d.get('img_file') or '',
                        'note': d.get('note') or '', 'is_manual': 1,
                        'updated_by': u['id'], 'updated_at': db.utcnow()}
                sql, args = db.upsert_sql('match_states', vals,
                                          update_cols=['decision', 'img_sha', 'img_file',
                                                       'note', 'is_manual', 'updated_by',
                                                       'updated_at'],
                                          key_cols=['file_id', 'item_id'])
                conn.execute(sql, args)
            n += 1
    db.audit(u['id'], 'review.' + decision, fid, '%d 条' % n)
    return jsonify(ok=True, n=n)


@bp.route('/files/<int:fid>/orphan/adopt', methods=['POST'])
@login_required
def adopt(fid):
    """
    把未认领图片采纳为一条奖项（学生漏写时人工补）。

    板块必须是规范板块键之一（`sections.CATALOG`），因为导出与页面渲染都靠它归类；
    允许选方案外的板块（例如「技能证书」）—— 这类板块会排在方案板块之后导出，
    不会丢。空值时退回图片自身被识别到的板块。
    """
    from ..services import sections as SEC
    u = current_user()
    d = request.get_json(silent=True) or {}
    sha = d.get('img_sha')
    name = (d.get('award_name') or '').strip()
    section = (d.get('section') or '').strip()
    if not sha or not name:
        return jsonify(ok=False, error='缺少图片或奖项名'), 400
    if section and section not in SEC.CATALOG_MAP:
        return jsonify(ok=False, error='未知板块：%s' % section), 400
    if len(name) > 200:
        name = name[:200]
    db.upsert('orphan_adoptions',
              {'file_id': fid, 'img_sha': sha, 'award_name': name, 'section': section,
               'adopted_by': u['id'], 'created_at': db.utcnow()},
              update_cols=['award_name', 'section', 'adopted_by', 'created_at'],
              key_cols=['file_id', 'img_sha'])
    db.audit(u['id'], 'review.adopt', fid, '%s → %s' % (name, section or '（按图归类）'))
    return jsonify(ok=True, section=section, name=name)


@bp.route('/files/<int:fid>/orphan/adopt', methods=['DELETE'])
@login_required
def unadopt(fid):
    u = current_user()
    sha = request.args.get('img_sha') or ''
    db.ex('DELETE FROM orphan_adoptions WHERE file_id=? AND img_sha=?', (fid, sha))
    db.audit(u['id'], 'review.unadopt', fid, sha)
    return jsonify(ok=True)


@bp.route('/files/<int:fid>/decisions')
@login_required
def decisions(fid):
    rows = db.q_dict('SELECT * FROM match_states WHERE file_id=?', (fid,))
    ad = db.q_dict('SELECT * FROM orphan_adoptions WHERE file_id=?', (fid,))
    return jsonify(ok=True, states=rows, adoptions=ad)


@bp.route('/files/<int:fid>/reject_mode', methods=['POST'])
@admin_required
def set_reject_mode(fid):
    """
    人工否定的条目在导出 Word 时怎么处理（剔除 / 保留并标注）。
    有绑定奖学金项目就存到项目上（只影响这个奖学金），否则改全局默认。
    """
    from ..services import programs as PG
    u = current_user()
    row = db.q_dict('SELECT * FROM files WHERE id=?', (fid,), one=True)
    if not row:
        return jsonify(ok=False, error='文件不存在'), 404
    mode = ((request.get_json(silent=True) or {}).get('mode') or '').strip()
    program = PG.get_by_batch(row['batch_id'])
    try:
        if program:
            PG.update(program['id'], reject_mode=mode, user_id=u['id'])
        else:
            PG.set_global_reject_mode(mode, u['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, mode=mode, scope=('project' if program else 'global'))


# ------------------------------------------------------------------ 导出
@bp.route('/files/<int:fid>/export', methods=['POST'])
@login_required
def export_file(fid):
    u = current_user()
    row = db.q_dict('SELECT * FROM files WHERE id=?', (fid,), one=True)
    if not row:
        return jsonify(ok=False, error='文件不存在'), 404
    tid = TS.enqueue('export', batch_id=row['batch_id'], file_id=fid,
                     payload={'kind': 'file', 'file_id': fid}, user_id=u['id'])
    db.audit(u['id'], 'export.file', fid, row['orig_name'])
    return jsonify(ok=True, task_id=tid)


@bp.route('/batches/<int:bid>/export', methods=['POST'])
@login_required
def export_batch(bid):
    u = current_user()
    n = db.q('SELECT COUNT(*) c FROM files WHERE batch_id=? AND status=?',
             (bid, 'analyzed'), one=True)['c']
    if not n:
        return jsonify(ok=False, error='该批次还没有出结论的材料'), 400
    tid = TS.enqueue('export', batch_id=bid, payload={'kind': 'batch'}, user_id=u['id'])
    db.audit(u['id'], 'export.batch', bid, '%d 份' % n)
    return jsonify(ok=True, task_id=tid, n=n)


@bp.route('/exports/<int:tid>/download')
@login_required
def export_download(tid):
    """下载导出产物。只允许导出目录内的文件（防路径穿越）。"""
    from flask import send_file
    t = TS.get(tid)
    if not t:
        return jsonify(ok=False, error='任务不存在'), 404
    payload = db.jloads(t['payload'])
    path = payload.get('result')
    if not path or not os.path.exists(path):
        return jsonify(ok=False, error='导出文件不存在或已清理'), 404
    root = os.path.realpath(os.path.join(os.path.dirname(WebConfig.DB_PATH), 'exports'))
    real = os.path.realpath(path)
    if not real.startswith(root):
        return jsonify(ok=False, error='路径不合法'), 403
    return send_file(real, as_attachment=True, download_name=os.path.basename(real))
