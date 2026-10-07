# -*- coding: utf-8 -*-
"""规则配置 / 参考数据 / 模型设置 / 系统信息。"""
import os
import json
import uuid

from flask import (Blueprint, render_template, request, jsonify, flash,
                   redirect, url_for)

from ..config import WebConfig
from .. import db
from ..security import (login_required, admin_required, current_user,
                        encrypt_secret, decrypt_secret, mask_secret)
from ..services import engine_adapter as EA
from ..services import programs as PG
from ..services import rule_service as RS
from ..services import privacy as PV
from ..services import vision_client as VC
from ..blueprints.main import safe_name

bp = Blueprint('config', __name__)


# ================================================================== 规则
@bp.route('/rules')
@login_required
def rules():
    groups = EA.rules_snapshot()
    overrides = {r['rule_key']: db.jloads(r['rule_value']) for r in
                 db.q('SELECT rule_key,rule_value FROM rules_overrides')}
    return render_template('rules.html', groups=groups, overrides=overrides,
                           n_over=len(overrides), programs=PG.list_programs())


@bp.route('/api/rules', methods=['GET'])
@login_required
def api_rules_get():
    return jsonify(ok=True, groups=EA.rules_snapshot(),
                   overrides={r['rule_key']: db.jloads(r['rule_value']) for r in
                              db.q('SELECT rule_key,rule_value FROM rules_overrides')})


@bp.route('/api/rules', methods=['PUT'])
@admin_required
def api_rules_put():
    u = current_user()
    d = request.get_json(silent=True) or {}
    items = d.get('items') or {}
    if not isinstance(items, dict):
        return jsonify(ok=False, error='items 必须是 {key: value}'), 400
    base = EA.build_cfg()
    valid = base.key_map()
    clean, rejected = {}, []
    for k, v in items.items():
        if k not in valid:
            rejected.append(k)
            continue
        cur = valid[k]
        try:
            # 类型对齐：原本是数字就转数字，避免 "45" 字符串把数值比较写坏
            if isinstance(cur, bool):
                v = bool(v)
            elif isinstance(cur, int) and not isinstance(cur, bool):
                v = int(float(v))
            elif isinstance(cur, float):
                v = float(v)
        except Exception:
            rejected.append(k)
            continue
        clean[k] = v
    now = db.utcnow()
    for k, v in clean.items():
        db.upsert('rules_overrides',
                  {'rule_key': k, 'rule_value': json.dumps(v, ensure_ascii=False),
                   'updated_by': u['id'], 'updated_at': now},
                  update_cols=['rule_value', 'updated_by', 'updated_at'],
                  key_cols=['rule_key'])
    EA.reload_rules()
    db.audit(u['id'], 'rules.save', '', '%d 项' % len(clean))
    return jsonify(ok=True, saved=len(clean), rejected=rejected)


@bp.route('/api/rules/reset', methods=['POST'])
@admin_required
def api_rules_reset():
    u = current_user()
    d = request.get_json(silent=True) or {}
    keys = d.get('keys')
    if keys:
        for k in keys:
            db.ex('DELETE FROM rules_overrides WHERE rule_key=?', (k,))
    else:
        db.ex('DELETE FROM rules_overrides')
    EA.reload_rules()
    db.audit(u['id'], 'rules.reset', '', str(keys or 'all'))
    return jsonify(ok=True)


# ================================================================== 参考数据
@bp.route('/refdata')
@login_required
def refdata():
    rows = db.q_dict('SELECT * FROM refdata ORDER BY kind, id DESC')
    cfg = EA.build_cfg()
    ref = EA.load_reference(cfg)
    return render_template('refdata.html', rows=rows, meta=ref['meta'])


@bp.route('/api/refdata/<kind>', methods=['POST'])
@login_required
def api_refdata_upload(kind):
    u = current_user()
    if kind not in ('competition', 'ranking'):
        return jsonify(ok=False, error='未知类型'), 400
    fs = request.files.getlist('files')
    if not fs:
        return jsonify(ok=False, error='没有收到文件'), 400
    use_ai = request.form.get('ai') in ('1', 'true', 'on', 'yes')
    saved, classified, errors = [], [], []
    if use_ai:
        from ..services import refdata_ai as RAI
        model_row = VC.active_model()
        if not model_row:
            return jsonify(ok=False, error='AI 识别需要先配置模型：请到「模型设置」填好 '
                                           'base_url / api_key / 模型名并启用，再试一次'), 400
        for f in fs:
            original = safe_name(f.filename)
            ext = os.path.splitext(original)[1].lower()
            if ext not in WebConfig.ALLOW_REF:
                errors.append('%s：只接受 .xls/.xlsx/.csv' % original)
                continue
            dest_dir = os.path.join(WebConfig.UPLOAD_DIR, 'refdata', kind)
            os.makedirs(dest_dir, exist_ok=True)
            tmp = os.path.join(dest_dir, '_ai_raw_' + uuid.uuid4().hex[:8] + '_' + original)
            f.save(tmp)
            # 落盘名加唯一前缀：不同班级的同名文件（如都叫「成绩表.xlsx」）不会互相覆盖
            base_name = 'ai_%s_%s.xlsx' % (uuid.uuid4().hex[:8],
                                           os.path.splitext(original)[0])
            try:
                out_kind, stored, n, note = RAI.recognize_and_store(
                    tmp, kind, model_row, dest_dir, base_name)
            except Exception as e:
                # 单个文件失败不拖垮整批：记下来继续处理其余文件
                errors.append('%s：%s' % (original, e))
                continue
            finally:
                try:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                except Exception:
                    pass
            db.ex('INSERT INTO refdata(kind,name,stored_path,size,note,uploaded_by,created_at)'
                  ' VALUES(?,?,?,?,?,?,?)',
                  (out_kind, original, stored, os.path.getsize(stored), note,
                   u['id'], db.utcnow()))
            saved.append(original)
            classified.append('%s→%s(%d条)' % (original, out_kind, n))
        db.audit(u['id'], 'refdata.ai_upload', kind,
                 '；'.join(classified) if classified else '；'.join(errors))
        if not saved:
            return jsonify(ok=False, ai=True, error='AI 识别失败：' + '；'.join(errors[:3])), 400
        return jsonify(ok=True, saved=saved, ai=True, errors=errors,
                       classified=classified, n=len(saved))
    for f in fs:
        original = safe_name(f.filename)
        ext = os.path.splitext(original)[1].lower()
        if ext not in WebConfig.ALLOW_REF:
            return jsonify(ok=False, error='只接受 .xls/.xlsx/.csv：%s' % original), 400
        dest_dir = os.path.join(WebConfig.UPLOAD_DIR, 'refdata', kind)
        os.makedirs(dest_dir, exist_ok=True)
        # 唯一前缀：避免不同班级的同名文件互相覆盖（覆盖了就会"少一个班的学号"）
        stored = os.path.join(dest_dir, '%s_%s' % (uuid.uuid4().hex[:8], original))
        f.save(stored)
        db.ex('INSERT INTO refdata(kind,name,stored_path,size,uploaded_by,created_at)'
              ' VALUES(?,?,?,?,?,?)',
              (kind, original, stored, os.path.getsize(stored), u['id'], db.utcnow()))
        saved.append(original)
    db.audit(u['id'], 'refdata.upload', kind, ','.join(saved))
    return jsonify(ok=True, saved=saved)


@bp.route('/api/refdata/delete_batch', methods=['POST'])
@login_required
def api_refdata_delete_batch():
    """批量删除参考材料。body: {ids: [refdata_id, ...]}"""
    u = current_user()
    d = request.get_json(silent=True) or {}
    ids = d.get('ids')
    if not isinstance(ids, list) or not ids:
        return jsonify(ok=False, error='没有选中要删除的文件'), 400
    names, missed = [], []
    for rid in ids[:500]:
        try:
            row = PG.delete_refdata(int(rid), u['id'])
            names.append(row.get('name') or str(rid))
        except Exception:
            missed.append(rid)
    return jsonify(ok=True, deleted=len(names), names=names, missed=missed)


@bp.route('/api/refdata/<int:rid>', methods=['DELETE'])
@login_required
def api_refdata_delete(rid):
    u = current_user()
    row = db.q_dict('SELECT * FROM refdata WHERE id=?', (rid,), one=True)
    if not row:
        return jsonify(ok=False, error='不存在'), 404
    try:
        if os.path.exists(row['stored_path']):
            os.remove(row['stored_path'])
    except Exception:
        pass
    db.ex('DELETE FROM refdata WHERE id=?', (rid,))
    db.audit(u['id'], 'refdata.delete', rid, row['name'])
    return jsonify(ok=True)


@bp.route('/api/refdata/preview')
@login_required
def api_refdata_preview():
    cfg = EA.build_cfg()
    return jsonify(ok=True, meta=EA.load_reference(cfg)['meta'])


# ================================================================== 模型
@bp.route('/settings/model')
@admin_required
def model_settings():
    rows = db.q_dict('SELECT * FROM model_configs ORDER BY id DESC')
    for r in rows:
        r['key_mask'] = mask_secret(decrypt_secret(r.get('api_key_enc')))
        r.pop('api_key_enc', None)
        try:
            r['extra_obj'] = json.loads(r.get('extra') or '{}')
        except Exception:
            r['extra_obj'] = {}
    left, calls, images = VC.check_quota()
    pv_ok, pv_msg = PV.available()
    return render_template('model_settings.html', rows=rows, left=left,
                           calls=calls, images=images, privacy_on=PV.enabled(),
                           privacy_ok=pv_ok, privacy_msg=pv_msg)


@bp.route('/api/settings/model', methods=['POST'])
@admin_required
def api_model_save():
    u = current_user()
    d = request.get_json(silent=True) or {}
    name = (d.get('name') or '默认模型').strip()
    base_url = (d.get('base_url') or '').strip()
    model = (d.get('model') or '').strip()
    api_key = (d.get('api_key') or '').strip()
    if not base_url or not model:
        return jsonify(ok=False, error='base_url 与模型名必填'), 400

    mid = d.get('id')
    extra = json.dumps({'json_mode': bool(d.get('json_mode', True))}, ensure_ascii=False)
    if mid:
        row = db.q_dict('SELECT * FROM model_configs WHERE id=?', (mid,), one=True)
        if not row:
            return jsonify(ok=False, error='配置不存在'), 404
        if api_key:
            db.ex('UPDATE model_configs SET name=?,base_url=?,model=?,api_key_enc=?,extra=?,'
                  'updated_at=? WHERE id=?',
                  (name, base_url, model, encrypt_secret(api_key), extra, db.utcnow(), mid))
        else:
            db.ex('UPDATE model_configs SET name=?,base_url=?,model=?,extra=?,updated_at=?'
                  ' WHERE id=?', (name, base_url, model, extra, db.utcnow(), mid))
    else:
        if not api_key:
            return jsonify(ok=False, error='首次配置必须填 api_key'), 400
        mid = db.ex('INSERT INTO model_configs(name,base_url,model,api_key_enc,extra,'
                    'is_active,created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (name, base_url, model, encrypt_secret(api_key), extra, 0,
                     u['id'], db.utcnow()))
    db.audit(u['id'], 'model.save', mid, model)
    return jsonify(ok=True, id=mid)


@bp.route('/api/settings/model/<int:mid>/activate', methods=['POST'])
@admin_required
def api_model_activate(mid):
    u = current_user()
    with db.tx() as conn:
        conn.execute('UPDATE model_configs SET is_active=0')
        conn.execute('UPDATE model_configs SET is_active=1 WHERE id=?', (mid,))
    db.audit(u['id'], 'model.activate', mid)
    return jsonify(ok=True)


@bp.route('/api/settings/model/<int:mid>', methods=['DELETE'])
@admin_required
def api_model_delete(mid):
    u = current_user()
    db.ex('DELETE FROM model_configs WHERE id=?', (mid,))
    db.audit(u['id'], 'model.delete', mid)
    return jsonify(ok=True)


@bp.route('/api/settings/model/test', methods=['POST'])
@admin_required
def api_model_test():
    d = request.get_json(silent=True) or {}
    mid = d.get('id')
    if mid:
        row = db.q_dict('SELECT * FROM model_configs WHERE id=?', (mid,), one=True)
        if not row:
            return jsonify(ok=False, error='配置不存在'), 404
        row = dict(row)
        row['api_key'] = decrypt_secret(row.get('api_key_enc'))
        try:
            row['extra_obj'] = json.loads(row.get('extra') or '{}')
        except Exception:
            row['extra_obj'] = {}
        ok, msg = VC.ping(row)
    else:
        ok, msg = VC.ping()
    return jsonify(ok=ok, message=msg)


# ================================================================== 系统
@bp.route('/system')
@login_required
def system():
    cache_dir = os.path.join(WebConfig.ENGINE_CACHE_DIR, 'vision')
    n_cache = len([f for f in os.listdir(cache_dir) if f.endswith('.json')]) \
        if os.path.isdir(cache_dir) else 0
    _, calls, images = VC.check_quota()
    usage = db.q_dict('SELECT * FROM api_usage ORDER BY day DESC LIMIT 14')
    logs = db.q_dict('SELECT * FROM audit_log ORDER BY id DESC LIMIT 30')
    from ..services import task_service as TS
    return render_template('system.html', n_cache=n_cache, usage=usage, logs=logs,
                           worker=TS.WORKER_ID, inline=WebConfig.INLINE_WORKER,
                           daily_limit=WebConfig.VISION_DAILY_LIMIT,
                           usage_today_images=images, usage_today_calls=calls)


# ================================================================== 全局自定义规则
# 与 /api/programs/<pid>/nl_rules 一一对应，只是作用域为"全校通用"（program_id 为空）。
@bp.route('/api/rules/global/nl_rules', methods=['GET'])
@login_required
def g_nl_list():
    return jsonify(ok=True, rules=RS.list_rules(None), catalog=RS.catalog())


@bp.route('/api/rules/global/nl_rules/compile', methods=['POST'])
@admin_required
def g_nl_compile():
    d = request.get_json(silent=True) or {}
    nl = (d.get('nl') or '').strip()
    if len(nl) < 4:
        return jsonify(ok=False, error='请把规则写得具体一点'), 400
    dsl, errs, raw = RS.compile_nl(nl, program_id=None)
    if dsl is None:
        return jsonify(ok=False, error='；'.join(errs), raw=raw[:800]), 400
    return jsonify(ok=True, dsl=dsl, explain=RS.explain_dsl(dsl),
                   preview=RS.preview(dsl, program_id=None))


@bp.route('/api/rules/global/nl_rules', methods=['POST'])
@admin_required
def g_nl_save():
    u = current_user()
    d = request.get_json(silent=True) or {}
    try:
        rid = RS.save(None, d.get('nl') or '', d.get('dsl') or {},
                      status=d.get('status') or 'draft',
                      priority=int(d.get('priority') or 100), user_id=u['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    EA.reload_rules()
    return jsonify(ok=True, id=rid)


# ================================================================== 隐私遮挡
@bp.route('/api/settings/privacy', methods=['GET', 'POST'])
@admin_required
def api_privacy():
    if request.method == 'GET':
        ok, msg = PV.available()
        return jsonify(ok=True, enabled=PV.enabled(), available=ok, message=msg,
                       patterns=[p[0] for p in PV.PATTERNS])
    d = request.get_json(silent=True) or {}
    on = bool(d.get('enabled'))
    db.set_setting(PV.SETTING_KEY, '1' if on else '0', current_user()['id'])
    db.audit(current_user()['id'], 'privacy.toggle', '', str(on))
    return jsonify(ok=True, enabled=on)


@bp.route('/api/settings/privacy/check', methods=['POST'])
@admin_required
def api_privacy_check():
    """上传一张图，看会被遮掉什么（不落盘、不改动任何东西）。"""
    f = request.files.get('file')
    if not f:
        return jsonify(ok=False, error='没有收到图片'), 400
    r = PV.preview_mask(f.read())
    return jsonify(ok=r.get('ok', False), error=r.get('error', ''), hits=r.get('hits', []))


# ================================================================== 导出选项（全局默认）
@bp.route('/api/settings/export', methods=['GET', 'POST'])
@admin_required
def api_export_settings():
    """
    人工否定的条目导出时怎么处理。这里是**全局默认**；
    单个奖学金项目可在「项目 → 导出设置」里覆盖。
    """
    if request.method == 'GET':
        return jsonify(ok=True, reject_mode=PG.global_reject_mode(),
                       modes=PG.REJECT_MODES)
    d = request.get_json(silent=True) or {}
    try:
        m = PG.set_global_reject_mode((d.get('reject_mode') or '').strip(),
                                      current_user()['id'])
    except ValueError as e:
        return jsonify(ok=False, error=str(e)), 400
    return jsonify(ok=True, reject_mode=m)
