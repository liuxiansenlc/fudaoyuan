# -*- coding: utf-8 -*-
"""审核页面 + 图片服务。"""
import os

from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, send_from_directory, abort, jsonify)

from ..config import WebConfig
from .. import db
from ..security import login_required, current_user
from ..services import engine_adapter as EA
from ..services import view_builder as VB
from ..services import programs as PG

bp = Blueprint('review', __name__)


@bp.route('/review')
@login_required
def index():
    """所有已出结论的材料，按批次分组。"""
    batches = db.q_dict('SELECT * FROM batches ORDER BY id DESC')
    out = []
    for b in batches:
        fs = db.q_dict("SELECT * FROM files WHERE batch_id=? AND status='analyzed'"
                       " ORDER BY id", (b['id'],))
        if fs:
            out.append({'batch': b, 'files': fs})
    return render_template('review_index.html', groups=out)


@bp.route('/review/<int:fid>')
@login_required
def detail(fid):
    row = db.q_dict('SELECT * FROM files WHERE id=?', (fid,), one=True)
    if not row:
        flash('文件不存在')
        return redirect(url_for('review.index'))
    record = EA.load_result(fid)
    if not record:
        flash('这份材料还没有出结论，请先在批次页运行')
        return redirect(url_for('main.batch_detail', bid=row['batch_id']))

    program = PG.get_by_batch(row['batch_id'])
    reject_mode = PG.reject_mode_of(program)
    view = VB.build(record, file_id=fid, scheme=PG.scheme_of(program),
                    reject_mode=reject_mode)

    # 同一批次里的其他学生，供左侧导航
    peers = db.q_dict("SELECT id,student_name,status FROM files WHERE batch_id=?"
                      " ORDER BY id", (row['batch_id'],))
    batch = db.q_dict('SELECT * FROM batches WHERE id=?', (row['batch_id'],), one=True)
    rmode_src, _ = PG.reject_mode_source(program)
    return render_template('review.html', v=view, f=row, peers=peers,
                           batch=batch, program=program,
                           adopt_sections=_adopt_sections(program),
                           reject_modes=PG.REJECT_MODES,
                           reject_mode_labels=PG.REJECT_MODE_LABELS,
                           reject_mode_src=rmode_src)


def _adopt_sections(program):
    """
    采纳弹窗里的板块下拉：**按该奖学金的板块方案**列（方案内在前、方案外接在后面），
    而不是写死国家奖学金那六项。否则在别的奖学金下，辅导员选的板块可能
    不在方案里，导出时就会找不到对应类目。
    """
    from ..services import sections as SEC
    scheme = PG.scheme_of(program)
    all_keys = [k for k, _ in SEC.CATALOG if k != '未归类']
    ordered = SEC.ordered_keys(scheme, set(all_keys))
    labels = SEC.full_labels(scheme, ordered)
    in_scheme = set(SEC.keys(scheme))
    return [{'key': k, 'label': labels[k], 'in_scheme': k in in_scheme}
            for k in ordered]


@bp.route('/img/<path:name>')
@login_required
def image(name):
    """按 sha256 命名的图片副本，天然跨文件去重。"""
    name = os.path.basename(name)
    p = os.path.join(WebConfig.IMAGE_DIR, name)
    if not os.path.exists(p):
        abort(404)
    return send_from_directory(WebConfig.IMAGE_DIR, name, max_age=31536000)


@bp.route('/api/files/<int:fid>/export_json')
@login_required
def export_json(fid):
    rec = EA.load_result(fid)
    if not rec:
        return jsonify(ok=False, error='无结果'), 404
    return jsonify(ok=True, data=rec)
