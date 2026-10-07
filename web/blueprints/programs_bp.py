# -*- coding: utf-8 -*-
"""奖学金项目管理：创建 / 编辑 / 归档 / 删除，以及板块方案、规则覆盖、参考材料、模板。"""
import os

from flask import (Blueprint, render_template, request, jsonify, redirect,
                   url_for, flash, abort)

from ..config import WebConfig
from .. import db
from ..security import login_required, admin_required, current_user
from ..services import programs as PG
from ..services import sections as SEC
from ..services import engine_adapter as EA
from ..services import rule_service as RS
from ..blueprints.main import safe_name

bp = Blueprint('programs', __name__)


@bp.route('/programs')
@login_required
def index():
    return render_template('programs.html',
                           rows=PG.list_programs(include_archived=True),
                           presets=SEC.PRESET_NAMES,
                           categories=SEC.CATEGORIES)


@bp.route('/programs/<int:pid>')
@login_required
def detail(pid):
    p = PG.get(pid)
    if not p:
        flash('奖学金项目不存在')
        return redirect(url_for('programs.index'))
    batches = db.q('SELECT * FROM batches WHERE program_id=? ORDER BY id DESC', (pid,))
    for b in batches:
        b['n_files'] = (db.q('SELECT COUNT(*) c FROM files WHERE batch_id=?',
                             (b['id'],), one=True) or {}).get('c', 0)
        b['n_analyzed'] = (db.q("SELECT COUNT(*) c FROM files WHERE batch_id=? AND"
                                " status='analyzed'", (b['id'],), one=True) or {}).get('c', 0)
    ref_rows = db.q('SELECT * FROM refdata WHERE program_id=? ORDER BY kind, id DESC', (pid,))
    global_ref = db.q('SELECT * FROM refdata WHERE program_id IS NULL ORDER BY kind, id DESC')
    ref = EA.load_reference(EA.build_cfg(p), p)
    # 只有"该项目的覆盖项"（与全局不同）才在界面上标出来
    own_rules = PG.program_rules(pid)
    inherited = {k: v for k, v in PG.effective_overrides(pid).items()
                 if k not in own_rules}
    rmode_src, rmode_eff = PG.reject_mode_source(p)
    return render_template('program_detail.html', p=p, batches=batches,
                           ref_rows=ref_rows, global_ref=global_ref, meta=ref['meta'],
                           catalog=SEC.CATALOG, presets=SEC.PRESET_NAMES,
                           categories=SEC.CATEGORIES, own_rules=own_rules,
                           groups=EA.rules_snapshot(p), inherited=inherited,
                           reject_modes=PG.REJECT_MODES,
                           reject_mode_labels=PG.REJECT_MODE_LABELS,
                           rmode_src=rmode_src, rmode_eff=rmode_eff,
                           template_exists=bool(p.get('template_path')
                                                and os.path.exists(p['template_path'])))


# ------------------------------------------------------------------ 写入
@bp.route('/api/programs', methods=['POST'])
@admin_required
def api_create():
    u = current_user()
    d = request.get_json(silent=True) or {}
    try:
        pid = PG.create(
            name=d.get('name'),
            category=d.get('category') or '国家级',
            description=d.get('description') or '',
            preset=d.get('preset'),
            quota=d.get('quota') or 0,
            scheme=d.get('scheme'),
            user_id=u['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, id=pid)


@bp.route('/api/programs/<int:pid>', methods=['POST', 'PUT'])
@admin_required
def api_update(pid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    try:
        PG.update(pid, name=d.get('name'), category=d.get('category'),
                  description=d.get('description'), quota=d.get('quota'),
                  template_path=d.get('template_path'), status=d.get('status'),
                  reject_mode=d.get('reject_mode'),
                  user_id=u['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True)


@bp.route('/api/programs/<int:pid>/scheme', methods=['POST'])
@admin_required
def api_scheme(pid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    items = d.get('scheme')
    if not isinstance(items, list) or not items:
        return jsonify(ok=False, error='板块方案不能为空'), 400
    scheme = SEC.normalize_scheme(items)
    if not scheme:
        return jsonify(ok=False, error='没有有效的板块，请至少选一个'), 400
    PG.update(pid, scheme=scheme, user_id=u['id'])
    return jsonify(ok=True, scheme=scheme)


@bp.route('/api/programs/<int:pid>/export_opts', methods=['POST'])
@admin_required
def api_export_opts(pid):
    """人工否定的条目导出时怎么处理（项目级）。传空串 = 跟随全局默认。"""
    u = current_user()
    d = request.get_json(silent=True) or {}
    mode = (d.get('reject_mode') or '').strip()
    try:
        PG.update(pid, reject_mode=mode, user_id=u['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    src, eff = PG.reject_mode_source(PG.get(pid))
    return jsonify(ok=True, reject_mode=mode, scope=src, effective=eff)


@bp.route('/api/programs/<int:pid>/preset', methods=['POST'])
@admin_required
def api_preset(pid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    name = d.get('preset')
    if name not in SEC.PRESETS:
        return jsonify(ok=False, error='未知预设'), 400
    scheme = SEC.default_scheme(name)
    PG.update(pid, scheme=scheme, user_id=u['id'])
    return jsonify(ok=True, scheme=scheme)


@bp.route('/api/programs/<int:pid>/rules', methods=['POST'])
@admin_required
def api_rules(pid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    items = d.get('items')
    if not isinstance(items, dict):
        return jsonify(ok=False, error='items 必须是 {key: value}'), 400
    base = EA.build_cfg(None)
    valid = base.key_map()
    clean, rejected = {}, []
    for k, v in items.items():
        if k not in valid:
            rejected.append(k)
            continue
        cur = valid[k]
        try:
            if isinstance(cur, bool):
                v = bool(v)
            elif isinstance(cur, int):
                v = int(float(v))
            elif isinstance(cur, float):
                v = float(v)
        except Exception:
            rejected.append(k)
            continue
        clean[k] = v
    n = PG.save_program_rules(pid, clean, user_id=u['id'])
    EA.reload_rules()
    return jsonify(ok=True, saved=n, rejected=rejected)


@bp.route('/api/programs/<int:pid>/rules/reset', methods=['POST'])
@admin_required
def api_rules_reset(pid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    PG.reset_program_rules(pid, d.get('keys'), user_id=u['id'])
    EA.reload_rules()
    return jsonify(ok=True)


@bp.route('/api/programs/<int:pid>/archive', methods=['POST'])
@admin_required
def api_archive(pid):
    PG.archive(pid, current_user()['id'])
    return jsonify(ok=True)


@bp.route('/api/programs/<int:pid>/restore', methods=['POST'])
@admin_required
def api_restore(pid):
    PG.restore(pid, current_user()['id'])
    return jsonify(ok=True)


@bp.route('/api/programs/<int:pid>', methods=['DELETE'])
@admin_required
def api_delete(pid):
    u = current_user()
    force = request.args.get('force') == '1'
    try:
        PG.delete(pid, u['id'], force=force)
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True)


# ------------------------------------------------------------------ 模板与参考材料
@bp.route('/api/programs/<int:pid>/template', methods=['POST'])
@admin_required
def api_template(pid):
    u = current_user()
    f = request.files.get('file')
    if not f or not f.filename:
        return jsonify(ok=False, error='没有收到文件'), 400
    original = safe_name(f.filename)
    if not original.lower().endswith('.docx'):
        return jsonify(ok=False, error='模板必须是 .docx'), 400
    dest = os.path.join(WebConfig.UPLOAD_DIR, 'templates', '%d_%s' % (pid, original))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    f.save(dest)
    PG.update(pid, template_path=dest, user_id=u['id'])
    db.audit(u['id'], 'program.template', pid, original)
    return jsonify(ok=True, path=dest, name=original)


@bp.route('/api/programs/<int:pid>/template', methods=['DELETE'])
@admin_required
def api_template_clear(pid):
    PG.update(pid, template_path='', user_id=current_user()['id'])
    return jsonify(ok=True)


@bp.route('/api/programs/<int:pid>/refdata/<kind>', methods=['POST'])
@login_required
def api_refdata(pid, kind):
    u = current_user()
    if kind not in ('competition', 'ranking', 'other'):
        return jsonify(ok=False, error='未知类型'), 400
    fs = request.files.getlist('files')
    if not fs:
        return jsonify(ok=False, error='没有收到文件'), 400
    use_ai = request.form.get('ai') in ('1', 'true', 'on', 'yes')
    dest_dir = os.path.join(WebConfig.UPLOAD_DIR, 'refdata', str(pid), kind)
    os.makedirs(dest_dir, exist_ok=True)
    saved, classified = [], []
    if use_ai:
        from ..services import refdata_ai as RAI
        from ..services import vision_client as VC
        model_row = VC.active_model()
        for f in fs:
            original = safe_name(f.filename)
            ext = os.path.splitext(original)[1].lower()
            if ext not in WebConfig.ALLOWED_REF:
                return jsonify(ok=False, error='AI 识别只接受 .xls/.xlsx/.csv：%s' % original), 400
            tmp = os.path.join(dest_dir, '_ai_raw_' + original)
            f.save(tmp)
            base_name = 'ai_' + os.path.splitext(original)[0] + '.xlsx'
            try:
                out_kind, stored, n, note = RAI.recognize_and_store(
                    tmp, kind, model_row, dest_dir, base_name)
            except Exception as e:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
                return jsonify(ok=False, error='AI 识别失败：%s（%s）' % (original, e)), 400
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
            PG.upload_refdata(pid, out_kind, original, stored,
                              os.path.getsize(stored), u['id'], note=note)
            saved.append(original)
            classified.append('%s→%s(%d条)' % (original, out_kind, n))
        db.audit(u['id'], 'refdata.ai_upload', str(pid),
                 '；'.join(classified) if classified else ','.join(saved))
        return jsonify(ok=True, saved=saved, ai=True,
                       classified=classified, n=len(saved))
    for f in fs:
        original = safe_name(f.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in WebConfig.ALLOWED_REF:
            return jsonify(ok=False, error='只接受 .xls/.xlsx/.csv：%s' % original), 400
        dest = os.path.join(dest_dir, original)
        f.save(dest)
        PG.upload_refdata(pid, kind, original, dest, os.path.getsize(dest), u['id'])
        saved.append(original)
    return jsonify(ok=True, saved=saved)


@bp.route('/api/programs/refdata/<int:rid>', methods=['DELETE'])
@login_required
def api_refdata_delete(rid):
    try:
        PG.delete_refdata(rid, current_user()['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 404
    return jsonify(ok=True)


@bp.route('/api/programs/refdata/<int:rid>/adopt', methods=['POST'])
@login_required
def api_refdata_adopt(rid):
    """把「全校通用」的参考材料复制一份绑定到当前项目（不改动原件）。"""
    u = current_user()
    d = request.get_json(silent=True) or {}
    pid = int(d.get('program_id') or 0)
    row = db.q('SELECT * FROM refdata WHERE id=?', (rid,), one=True)
    if not row or not pid:
        return jsonify(ok=False, error='参数不完整'), 400
    PG.upload_refdata(pid, row['kind'], row['name'], row['stored_path'], row['size'],
                      u['id'], note='从全校通用材料绑定')
    return jsonify(ok=True)


# ------------------------------------------------------------------ 自定义规则
@bp.route('/api/programs/<int:pid>/nl_rules', methods=['GET'])
@login_required
def api_nl_list(pid):
    return jsonify(ok=True, rules=RS.list_rules(pid), catalog=RS.catalog(),
                   global_rules=RS.list_rules(None))


@bp.route('/api/programs/<int:pid>/nl_rules/compile', methods=['POST'])
@admin_required
def api_nl_compile(pid):
    """中文 → DSL。只编译不落库，先把结果回显给辅导员确认。"""
    d = request.get_json(silent=True) or {}
    nl = (d.get('nl') or '').strip()
    if len(nl) < 4:
        return jsonify(ok=False, error='请把规则写得具体一点'), 400
    dsl, errs, raw = RS.compile_nl(nl, program_id=pid)
    if dsl is None:
        return jsonify(ok=False, error='；'.join(errs), raw=raw[:800]), 400
    return jsonify(ok=True, dsl=dsl, explain=RS.explain_dsl(dsl),
                   preview=RS.preview(dsl, program_id=pid))


@bp.route('/api/programs/<int:pid>/nl_rules', methods=['POST'])
@admin_required
def api_nl_save(pid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    try:
        rid = RS.save(pid, d.get('nl') or '', d.get('dsl') or {},
                      status=d.get('status') or 'draft',
                      priority=int(d.get('priority') or 100),
                      user_id=u['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    EA.reload_rules()
    return jsonify(ok=True, id=rid)


@bp.route('/api/programs/nl_rules/<int:rid>', methods=['POST'])
@admin_required
def api_nl_update(rid):
    u = current_user()
    d = request.get_json(silent=True) or {}
    try:
        RS.update(rid, status=d.get('status'), priority=d.get('priority'),
                  dsl=d.get('dsl'), nl_text=d.get('nl'), user_id=u['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    EA.reload_rules()
    return jsonify(ok=True)


@bp.route('/api/programs/nl_rules/<int:rid>', methods=['DELETE'])
@admin_required
def api_nl_delete(rid):
    try:
        RS.delete(rid, current_user()['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 404
    EA.reload_rules()
    return jsonify(ok=True)


@bp.route('/api/programs/nl_rules/preview', methods=['POST'])
@admin_required
def api_nl_preview():
    d = request.get_json(silent=True) or {}
    return jsonify(ok=True, **RS.preview(d.get('dsl') or {}, program_id=d.get('program_id')))


@bp.route('/api/programs/rule_catalog', methods=['GET'])
@login_required
def api_rule_catalog():
    return jsonify(ok=True, catalog=RS.catalog())
