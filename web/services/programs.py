# -*- coding: utf-8 -*-
"""
奖学金项目管理（多奖学金的落点）。

一个「奖学金项目」= 一套申报口径，包含：
  - 板块方案   这个奖学金要看哪些板块、顺序、名称、是否必需
  - 规则覆盖   只对这个奖学金生效的阈值（如校级奖学金体测线不同）
  - 模板 docx  导出标准化材料时用哪套版式
  - 参考材料   A 类竞赛表 / 成绩排名表等，可绑定到具体项目

设计取舍：**引擎完全不动**。引擎仍按学生文档里的标题分类到一套「规范板块键」，
不同奖学金只在「用哪些键、怎么展示、阈值多少」上有差别。
这样新增一个奖学金 = 建一条记录 + 选一套板块方案，不用改代码。
"""
import os
import json

from ..config import WebConfig
from .. import db
from . import sections as SEC

# ------------------------------------------------------------------ 导出：人工否定的条目
# 辅导员点了「否定」的条目，导出标准化材料时有两种口径，由管理员选：
#   remove  直接剔除（默认）—— 该条不进 Word，也不进待补充清单（否定是结论，不是"待补"）
#   mark    保留并标注「不符合申报规范」—— 供留痕，评审组能看到被否掉的是什么
REJECT_MODES = [
    ('remove', '直接剔除（不写入 Word）'),
    ('mark', '保留，并标注「不符合申报规范」'),
]
REJECT_MODE_KEYS = [k for k, _ in REJECT_MODES]
REJECT_MODE_LABELS = dict(REJECT_MODES)
REJECT_MODE_SETTING = 'reject_export_mode'      # 全局默认，存在 app_settings
REJECT_MODE_DEFAULT = 'remove'


def global_reject_mode():
    try:
        v = db.get_setting(REJECT_MODE_SETTING, REJECT_MODE_DEFAULT)
    except Exception:
        v = REJECT_MODE_DEFAULT
    return v if v in REJECT_MODE_LABELS else REJECT_MODE_DEFAULT


def set_global_reject_mode(mode, user_id=None):
    if mode not in REJECT_MODE_LABELS:
        raise ValueError('未知的处理方式：%s' % mode)
    db.set_setting(REJECT_MODE_SETTING, mode, user_id)
    db.audit(user_id, 'export.reject_mode', '', mode)
    return mode


def reject_mode_of(program):
    """项目自带优先；项目没设（空串）就跟随全局默认。"""
    m = ((program or {}).get('reject_mode') or '').strip()
    return m if m in REJECT_MODE_LABELS else global_reject_mode()


def reject_mode_source(program):
    """给界面显示用：这个值是从哪来的。"""
    m = ((program or {}).get('reject_mode') or '').strip()
    return ('项目' if m in REJECT_MODE_LABELS else '全局'), reject_mode_of(program)


# ------------------------------------------------------------------ 查询
def list_programs(include_archived=False):
    where = '' if include_archived else " WHERE status<>'archived'"
    rows = db.q('SELECT * FROM programs%s ORDER BY sort_order, id' % where)
    for r in rows:
        r['scheme'] = SEC.parse_scheme(r.get('section_scheme'))
        r['n_batches'] = (db.q('SELECT COUNT(*) c FROM batches WHERE program_id=?',
                               (r['id'],), one=True) or {}).get('c', 0)
        r['n_files'] = (db.q('SELECT COUNT(*) c FROM files f JOIN batches b'
                             ' ON f.batch_id=b.id WHERE b.program_id=?',
                             (r['id'],), one=True) or {}).get('c', 0)
        r['n_ref'] = (db.q('SELECT COUNT(*) c FROM refdata WHERE program_id=?',
                           (r['id'],), one=True) or {}).get('c', 0)
    return rows


def get(pid):
    if not pid:
        return None
    r = db.q('SELECT * FROM programs WHERE id=?', (pid,), one=True)
    if not r:
        return None
    r['scheme'] = SEC.parse_scheme(r.get('section_scheme'))
    try:
        r['rules'] = json.loads(r.get('rules_overrides') or '{}')
    except Exception:
        r['rules'] = {}
    return r


def get_by_batch(batch_id):
    b = db.q('SELECT program_id FROM batches WHERE id=?', (batch_id,), one=True)
    return get((b or {}).get('program_id'))


def scheme_of(program):
    if not program:
        return SEC.default_scheme()
    return SEC.parse_scheme(program.get('section_scheme'))


def template_of(program):
    """导出用模板：项目自带优先，否则用默认模板。"""
    if program:
        p = (program.get('template_path') or '').strip()
        if p and os.path.exists(p):
            return p
    return WebConfig.TEMPLATE_DOCX


# ------------------------------------------------------------------ 写入
def create(name, category='国家级', description='', preset=None,
           scheme=None, rules=None, template_path='', quota=0, user_id=None):
    name = (name or '').strip()
    if not name:
        raise ValueError('奖学金名称不能为空')
    scheme = scheme or SEC.default_scheme(preset or '国家奖学金')
    rules = rules or (SEC.PRESETS.get(preset or '', {}) or {}).get('rules', {}) or {}
    pid = db.ex(
        'INSERT INTO programs(name,category,description,section_scheme,rules_overrides,'
        'template_path,quota,status,sort_order,created_by,created_at)'
        ' VALUES(?,?,?,?,?,?,?,?,?,?,?)',
        (name, category, description, SEC.scheme_to_json(scheme),
         json.dumps(rules, ensure_ascii=False), template_path, int(quota or 0),
         'active', 100, user_id, db.utcnow()))
    db.audit(user_id, 'program.create', pid, name)
    return pid


def update(pid, **kw):
    p = get(pid)
    if not p:
        raise ValueError('奖学金项目不存在')
    fields, args = [], []
    for col, val in (
            ('name', (kw.get('name') or p['name']).strip()),
            ('category', kw.get('category') or p['category']),
            ('description', kw.get('description', p.get('description') or '')),
            ('quota', int(kw.get('quota') if kw.get('quota') is not None else (p.get('quota') or 0))),
            ('template_path', kw.get('template_path', p.get('template_path') or '')),
            ('status', kw.get('status') or p.get('status') or 'active'),
    ):
        fields.append('%s=?' % col)
        args.append(val)
    if kw.get('scheme') is not None:
        fields.append('section_scheme=?')
        args.append(SEC.scheme_to_json(kw['scheme']))
    if kw.get('rules') is not None:
        fields.append('rules_overrides=?')
        args.append(json.dumps(kw['rules'], ensure_ascii=False))
    if kw.get('reject_mode') is not None:
        m = (kw.get('reject_mode') or '').strip()
        if m and m not in REJECT_MODE_LABELS:
            raise ValueError('未知的人工否定处理方式：%s' % m)
        fields.append('reject_mode=?')
        args.append(m)
    fields.append('updated_at=?')
    args.append(db.utcnow())
    args.append(pid)
    db.ex('UPDATE programs SET %s WHERE id=?' % ', '.join(fields), tuple(args))
    db.audit(kw.get('user_id'), 'program.update', pid, kw.get('name') or '')
    return get(pid)


def archive(pid, user_id=None):
    db.ex("UPDATE programs SET status='archived', updated_at=? WHERE id=?",
          (db.utcnow(), pid))
    db.audit(user_id, 'program.archive', pid)


def restore(pid, user_id=None):
    db.ex("UPDATE programs SET status='active', updated_at=? WHERE id=?",
          (db.utcnow(), pid))
    db.audit(user_id, 'program.restore', pid)


def delete(pid, user_id=None, force=False):
    """删除。有批次/材料时默认拒绝，避免把已审的材料一起删掉。"""
    n_b = (db.q('SELECT COUNT(*) c FROM batches WHERE program_id=?', (pid,), one=True) or {}).get('c', 0)
    n_r = (db.q('SELECT COUNT(*) c FROM refdata WHERE program_id=?', (pid,), one=True) or {}).get('c', 0)
    if not force and (n_b or n_r):
        raise ValueError('该项目下还有 %d 个批次、%d 份参考材料，无法删除。'
                         '可以先归档，或加 force 强制解绑。' % (n_b, n_r))
    if force:
        db.ex('UPDATE batches SET program_id=NULL WHERE program_id=?', (pid,))
        db.ex('UPDATE refdata SET program_id=NULL WHERE program_id=?', (pid,))
        db.ex('DELETE FROM program_rules WHERE program_id=?', (pid,))
    db.ex('DELETE FROM programs WHERE id=?', (pid,))
    db.audit(user_id, 'program.delete', pid, 'force=%s' % force)


# ------------------------------------------------------------------ 规则
def program_rules(pid):
    """该项目的规则覆盖：{'reading_rules.physical_pass': 70, ...}"""
    if not pid:
        return {}
    out = {}
    for r in db.q('SELECT rule_key,rule_value FROM program_rules WHERE program_id=?', (pid,)):
        try:
            out[r['rule_key']] = json.loads(r['rule_value'])
        except Exception:
            pass
    return out


def save_program_rules(pid, flat, user_id=None):
    """逐条 upsert（db.upsert 已抹平 SQLite/MySQL 的方言差异）。"""
    now = db.utcnow()
    saved = 0
    for k, v in (flat or {}).items():
        db.upsert('program_rules',
                  {'program_id': pid, 'rule_key': k,
                   'rule_value': json.dumps(v, ensure_ascii=False),
                   'updated_by': user_id, 'updated_at': now},
                  update_cols=['rule_value', 'updated_by', 'updated_at'],
                  key_cols=['program_id', 'rule_key'])
        saved += 1
    db.audit(user_id, 'program.rules', pid, '%d 项' % saved)
    return saved


def reset_program_rules(pid, keys=None, user_id=None):
    if keys:
        for k in keys:
            db.ex('DELETE FROM program_rules WHERE program_id=? AND rule_key=?', (pid, k))
    else:
        db.ex('DELETE FROM program_rules WHERE program_id=?', (pid,))
    db.audit(user_id, 'program.rules_reset', pid, str(keys or 'all'))


def effective_overrides(pid):
    """
    生效的规则覆盖 = 全局覆盖 ⊕ 项目覆盖（项目优先）。
    返回可交给 EngineConfig.with_overrides() 的扁平字典。
    """
    flat = {}
    for r in db.q('SELECT rule_key,rule_value FROM rules_overrides'):
        try:
            flat[r['rule_key']] = json.loads(r['rule_value'])
        except Exception:
            pass
    flat.update(program_rules(pid))
    return flat


# ------------------------------------------------------------------ 参考材料
def refdata_of(pid, kind=None):
    """
    取参考材料。项目自带的优先；项目没有的，回退到全局（program_id 为空）的，
    这样"全校通用的 A 类竞赛表"只传一次，各奖学金都能用。
    """
    out = {}
    for k in (['competition', 'ranking'] if not kind else [kind]):
        row = None
        if pid:
            row = db.q('SELECT * FROM refdata WHERE program_id=? AND kind=?'
                       ' ORDER BY id DESC LIMIT 1', (pid, k), one=True)
        if not row:
            row = db.q('SELECT * FROM refdata WHERE (program_id IS NULL) AND kind=?'
                       ' ORDER BY id DESC LIMIT 1', (k,), one=True)
        if row:
            out[k] = row
    return out


def upload_refdata(pid, kind, name, stored_path, size, user_id=None, note=''):
    rid = db.ex('INSERT INTO refdata(program_id,kind,name,stored_path,size,note,'
                'uploaded_by,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (pid, kind, name, stored_path, size, note, user_id, db.utcnow()))
    db.audit(user_id, 'refdata.upload', rid, '%s/%s' % (kind, name))
    return rid


def delete_refdata(rid, user_id=None):
    row = db.q('SELECT * FROM refdata WHERE id=?', (rid,), one=True)
    if not row:
        raise ValueError('不存在')
    try:
        if row['stored_path'] and os.path.exists(row['stored_path']):
            os.remove(row['stored_path'])
    except Exception:
        pass
    db.ex('DELETE FROM refdata WHERE id=?', (rid,))
    db.audit(user_id, 'refdata.delete', rid, row['name'])
    return row
