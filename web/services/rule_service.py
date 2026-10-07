# -*- coding: utf-8 -*-
"""
自定义规则：自然语言 → 受限 DSL → 存库 → 运行期纯函数求值。

防幻觉的四道保险：
  1. **编译产物必须过白名单**：发明了字段/操作符/动作 → 直接拒绝，不落库。
  2. **运行期零 LLM**：判定只用 rules_engine.evaluate（纯函数，零网络零随机）。
  3. **排序确定**：priority desc, id asc —— 同一份材料跑两次结果必然一致。
  4. **命中可追溯**：每条匹配的规则都产出"改了什么"，审核页可展开查看。

编译器（要调模型，慢且可能失败）和使用者（快且必须确定）彻底分开，
是这个设计能成立的关键。
"""
import os
import json
import uuid

from ..config import WebConfig
from .. import db

HERE = os.path.dirname(os.path.abspath(__file__))


def _RE():
    import sys
    eng = WebConfig.ENGINE_DIR
    if eng not in sys.path:
        sys.path.insert(0, eng)
    import rules_engine
    return rules_engine


# ------------------------------------------------------------------ 目录
def catalog():
    """给界面和编译器用的字段/操作符/动作清单。"""
    RE = _RE()
    return {
        'fields': [{'key': k, **v} for k, v in RE.FIELDS.items()],
        'ops': RE.OPS,
        'actions': [{'key': k, **v} for k, v in RE.ACTIONS.items()],
        'statuses': RE.STATUSES,
        'levels': RE.LEVELS,
        'sections': RE.SECTIONS,
        'person_status': RE.PERSON_STATUS,
        'notes': {
            'img.person_status': {
                'ok': '证书/名单上核对到申报人本人',
                'no_name': '材料上没出现姓名，无法核对',
                'name_conflict': '材料上是别人的名字（硬红线）',
                'roster_in': '名单类材料里查到了申报人',
                'roster_missing': '名单类材料里没查到申报人',
                'none': '未知',
            },
            'img.level': '按落款推断：完整校名→校级；剔掉校名后仍有"XX学院"→院级',
        },
    }


# ------------------------------------------------------------------ 中文回显
_OP_CN = {
    'eq': '等于', 'ne': '不等于', 'in': '属于', 'not_in': '不属于',
    'contains': '包含', 'not_contains': '不包含',
    'gte': '≥', 'lte': '≤', 'gt': '>', 'lt': '<',
    'exists': '有值', 'missing': '无值',
}
_ACT_CN = {'penalty': '扣分', 'bonus': '加分', 'status': '判定为',
           'note': '附加说明', 'require_person': '要求核对本人姓名'}
_ST_CN = {'green': '自动配对', 'blue': '跨位纠正', 'amber': '待确认',
          'pending_weak': '待确认·附件疑似', 'red': '缺图', 'red_flag': '姓名异常'}


def explain_dsl(dsl):
    """把 DSL 反向渲染成中文，给辅导员确认用。"""
    RE = _RE()
    ok, errs = RE.validate(dsl)
    if not ok:
        return '（规则不合法：%s）' % '；'.join(errs)
    parts = []
    sc = dsl.get('scope') or {}
    conds = []
    if sc.get('sections'):
        conds.append('板块是「%s」' % '、'.join(sc['sections']))
    if sc.get('levels'):
        conds.append('级别是「%s」' % '、'.join(sc['levels']))
    for c in dsl.get('when') or []:
        f = c['field']
        label = (RE.FIELDS.get(f) or RE.FIELDS.get('img.metric.*')).get('label', f)
        if f.startswith('img.metric.'):
            label = '材料上的「%s」' % f[len('img.metric.'):]
        v = c.get('value')
        if isinstance(v, list):
            v = '、'.join(str(x) for x in v)
        if c['op'] in ('exists', 'missing'):
            conds.append('%s%s' % (label, _OP_CN[c['op']]))
        else:
            conds.append('%s %s %s' % (label, _OP_CN.get(c['op'], c['op']), v))
    acts = []
    for a in dsl.get('then') or []:
        act = a.get('action')
        if act == 'status':
            acts.append('判定为「%s」' % _ST_CN.get(a.get('value'), a.get('value')))
        elif act == 'penalty':
            acts.append('扣 %g 分' % abs(a.get('value') or 0))
        elif act == 'bonus':
            acts.append('加 %g 分' % abs(a.get('value') or 0))
        elif act == 'note':
            acts.append('附说明：%s' % a.get('value'))
        elif act == 'require_person':
            acts.append('要求必须核对到本人姓名' if a.get('value') else '不要求核对姓名')

    head = '当' + ('且'.join(conds) if conds else '（无前提条件）')
    return '如果 %s，就 %s。' % (head, '，并且'.join(acts))


# ------------------------------------------------------------------ 存取
def list_rules(program_id=None, include_disabled=True):
    if program_id is None:
        rows = db.q('SELECT * FROM rule_nl WHERE program_id IS NULL ORDER BY priority DESC, id')
    else:
        rows = db.q('SELECT * FROM rule_nl WHERE program_id=? ORDER BY priority DESC, id',
                    (program_id,))
    out = []
    for r in rows:
        if not include_disabled and r.get('status') != 'enabled':
            continue
        r['dsl_obj'] = db.jloads(r.get('dsl') or '{}', {})
        r['explain'] = explain_dsl(r['dsl_obj']) if r['dsl_obj'] else ''
        out.append(r)
    return out


def get(rid):
    r = db.q('SELECT * FROM rule_nl WHERE id=?', (rid,), one=True)
    if r:
        r['dsl_obj'] = db.jloads(r.get('dsl') or '{}', {})
        r['explain'] = explain_dsl(r['dsl_obj']) if r['dsl_obj'] else ''
    return r


def save(program_id, nl_text, dsl, status='draft', priority=100,
         compile_note='', user_id=None):
    RE = _RE()
    ok, errs = RE.validate(dsl)
    if not ok:
        raise ValueError('规则不合法：%s' % '；'.join(errs))
    dsl = dict(dsl)
    dsl.setdefault('id', 'r_%s' % uuid.uuid4().hex[:8])
    dsl['enabled'] = (status == 'enabled')
    dsl['priority'] = int(priority)
    rid = db.ex(
        'INSERT INTO rule_nl(program_id,nl_text,dsl,status,priority,compile_note,'
        'created_by,created_at) VALUES(?,?,?,?,?,?,?,?)',
        (program_id, nl_text, json.dumps(dsl, ensure_ascii=False), status,
         int(priority), compile_note, user_id, db.utcnow()))
    db.audit(user_id, 'rule.create', rid, nl_text[:80])
    return rid


def update(rid, status=None, priority=None, dsl=None, nl_text=None, user_id=None):
    r = get(rid)
    if not r:
        raise ValueError('规则不存在')
    if dsl is not None:
        ok, errs = _RE().validate(dsl)
        if not ok:
            raise ValueError('规则不合法：%s' % '；'.join(errs))
    d = r['dsl_obj']
    if status is not None:
        d['enabled'] = (status == 'enabled')
    if priority is not None:
        d['priority'] = int(priority)
    try:
        d['id'] = d.get('id') or ('r_%s' % uuid.uuid4().hex[:8])
    except Exception:
        d['id'] = 'r_%s' % uuid.uuid4().hex[:8]
    db.ex('UPDATE rule_nl SET status=?, priority=?, dsl=?, nl_text=?, updated_at=? WHERE id=?',
          (status if status is not None else r['status'],
           int(priority) if priority is not None else r['priority'],
           json.dumps(d, ensure_ascii=False),
           nl_text if nl_text is not None else r['nl_text'],
           db.utcnow(), rid))
    db.audit(user_id, 'rule.update', rid, '%s' % (status or ''))
    return get(rid)


def delete(rid, user_id=None):
    r = get(rid)
    if not r:
        raise ValueError('规则不存在')
    db.ex('DELETE FROM rule_nl WHERE id=?', (rid,))
    db.audit(user_id, 'rule.delete', rid, r['nl_text'][:80])


def effective_rules(program_id):
    """
    生效规则 = 全局启用的 + 该奖学金项目启用的。
    项目里同 id 的规则以项目版为准（允许项目微调全局规则）。
    """
    glob = {r['dsl_obj'].get('id'): r['dsl_obj']
            for r in list_rules(None, include_disabled=False) if r['dsl_obj']}
    own = {r['dsl_obj'].get('id'): r['dsl_obj']
           for r in list_rules(program_id, include_disabled=False) if r['dsl_obj']} \
        if program_id else {}
    merged = dict(glob)
    merged.update(own)
    return [v for v in merged.values() if v]


# ------------------------------------------------------------------ 编译
COMPILE_SYSTEM = """你是「奖学金材料审核规则」的编译器。把用户的中文规则，翻译成一个**受限 JSON 规则**。

只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。格式：
{
  "name": "短名（不超过 12 字）",
  "priority": 100,
  "scope": {"sections": ["竞赛"], "levels": ["省级"]},
  "when": [{"field": "img.level", "op": "eq", "value": "院级"}],
  "then": [{"action": "penalty", "value": 50}, {"action": "status", "value": "amber"}]
}

可用字段（只能用这些，metric 用 img.metric.<指标名>）：
{fields}

可用操作符：{ops}
    eq/ne 相等不等；in/not_in 属于（value 用数组）；contains/not_contains 文本包含；
    gte/lte/gt/lt 数值比较；exists/missing 有值/无值

可用动作：
{actions}
    penalty 填正数（内部按扣分处理）；status 的取值：{statuses}

约束：
1. **只能用上面列出的字段/操作符/动作**，绝不允许发明新的。
2. scope 可为空对象（表示不限板块/级别）。
3. when 是数组，全部满足才触发（AND）。
4. 语义必须忠实于用户说法，**不要自行加码**。用户没提"缺图"就不要判缺图。
5. 中文里的"不认/不算/不接受"通常表示要判为待确认并扣分；
   "必须/一定要"通常表示要求的条件不满足时判为待确认。
6. 拿不准的，宁可选保守动作（status=amber + note），不要发明规则。
"""

COMPILE_USER = """用户的中文规则：

{nl}

已有的规则（避免重复，可参考风格）：
{existing}
"""


def compile_nl(nl_text, program_id=None, model_row=None):
    """
    中文 → DSL。返回 (dsl, errors, raw)。
    - 只输出 JSON，出错重试时会带上错误信息
    - 编译结果**必须过白名单**，否则不返回 dsl
    """
    from . import vision_client as VC

    RE = _RE()
    model_row = model_row or VC.active_model()
    if not model_row:
        return None, ['尚未配置可用的模型（去「模型设置」里填 base_url / api_key / 模型名）'], ''

    fields = '\n'.join('  %-20s %s' % (k, v.get('label', '')) for k, v in RE.FIELDS.items())
    actions = '\n'.join('  %-16s %s' % (k, v.get('label', '')) for k, v in RE.ACTIONS.items())
    system = (COMPILE_SYSTEM
              .replace('{fields}', fields)
              .replace('{ops}', '、'.join(RE.OPS))
              .replace('{actions}', actions)
              .replace('{statuses}', '、'.join(RE.STATUSES)))

    existing = []
    for r in list_rules(program_id, include_disabled=True)[:8]:
        existing.append('- %s → %s' % (r['nl_text'][:40], json.dumps(r['dsl_obj'],
                                                                    ensure_ascii=False)[:120]))
    user = COMPILE_USER.replace('{nl}', nl_text.strip()).replace(
        '{existing}', '\n'.join(existing) or '（无）')

    correction = ''
    for _ in range(2):
        raw, err = VC.chat_text(model_row, system, user + correction)
        if err:
            return None, [err], raw or ''
        try:
            obj = VC.json_from_text(raw)
        except Exception as e:
            correction = '\n\n上次输出不是合法 JSON（%s），请只输出一个 JSON 对象。' % e
            continue
        ok, errs = RE.validate(obj)
        if ok:
            return obj, [], raw
        correction = ('\n\n上次输出不合规：%s\n请严格使用允许的字段/操作符/动作。'
                      % '；'.join(errs))
    return None, ['两次编译都不合规，请把规则说得更具体一些'], raw


# ------------------------------------------------------------------ 影响预览
def preview(dsl, program_id=None, limit=12):
    """
    拿历史结论试跑这条规则，告诉辅导员"会影响多少条、具体哪几条"。
    这是确认规则是否符合预期最有效的手段——比读 DSL 直观得多。
    """
    RE = _RE()
    ok, errs = RE.validate(dsl)
    if not ok:
        return {'ok': False, 'errors': errs, 'hits': [], 'n': 0}

    r = dict(dsl)
    r.setdefault('id', 'preview')
    r['enabled'] = True
    rules = [r]

    from . import engine_adapter as EA
    if program_id:
        files = db.q("SELECT f.id,f.student_name FROM files f JOIN batches b ON f.batch_id=b.id"
                     " WHERE b.program_id=? AND f.status='analyzed'", (program_id,))
    else:
        files = db.q("SELECT id,student_name FROM files WHERE status='analyzed'")
    hits = []
    for f in files:
        rec = EA.load_result(f['id'])
        if not rec:
            continue
        imap = {im['file']: im for im in (rec.get('images') or [])}
        idmap = {it['id']: it for it in (rec.get('items') or [])}
        for m in (rec.get('matches') or []):
            it = idmap.get(m.get('item_id'))
            if not it:
                continue
            img = imap.get(m.get('img_file')) or {}
            # 用落盘的事实表求值 —— 与真实流水线走同一个求值器。
            # 必须用 merge_facts（只取非空值），否则图片 facts 里的空串会把
            # 条目的 section 覆盖掉，规则 scope 永远匹配不上。
            facts = RE.merge_facts(it.get('facts'), img.get('facts'))
            facts['pair.score'] = m.get('score')
            eff = RE.evaluate_facts(facts, rules)
            if eff['matched']:
                before = m.get('status')
                after = eff['set_status'] or before
                hits.append({
                    'file_id': f['id'], 'student': f['student_name'],
                    'item': (m.get('item_text') or '')[:60],
                    'before': before, 'after': after,
                    'delta': eff['score_delta'],
                    'effects': '；'.join(eff['matched'][0].get('effects') or []),
                })
    return {'ok': True, 'errors': [], 'n': len(hits), 'hits': hits[:limit],
            'explain': explain_dsl(dsl)}
