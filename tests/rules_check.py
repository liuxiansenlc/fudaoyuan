# -*- coding: utf-8 -*-
"""
规则 DSL 单测（离线，不需要大模型）。

分两层验证：
  A. 纯函数层：白名单校验、求值、排序确定性、中文回显
  B. 引擎集成：把规则挂到 cfg.nl_rules，跑真实材料，看状态与得分真的变了

第 B 层是关键——它证明"规则确实影响判定"，而不只是"存下来了"。
"""
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'engine'))
os.environ['WB_INLINE_WORKER'] = '0'

import rules_engine as RE          # noqa: E402
from web.services import rule_service as RS   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %-56s %s' % ('OK ' if cond else '!!!', name, detail if not cond else ''))


print('=' * 82)
print('规则 DSL 单测')
print('=' * 82)

# ---------------- A. 白名单校验 ----------------
print('\nA) 白名单校验（防幻觉的关键）')
good = {
    'id': 'r_1', 'name': '省级竞赛不认院级证书', 'priority': 120, 'enabled': True,
    'scope': {'sections': ['竞赛'], 'levels': ['省级']},
    'when': [{'field': 'img.level', 'op': 'eq', 'value': '院级'}],
    'then': [{'action': 'penalty', 'value': 50},
             {'action': 'status', 'value': 'amber'},
             {'action': 'note', 'value': '省级竞赛不接受院级立项'}],
}
ok, errs = RE.validate(good)
check('合法规则通过', ok, str(errs))

ok, errs = RE.validate({**good, 'when': [{'field': 'img.发明字段', 'op': 'eq', 'value': 1}]})
check('发明字段被拒', not ok and any('不在允许的字段' in e for e in errs), str(errs))

ok, errs = RE.validate({**good, 'when': [{'field': 'img.level', 'op': 'magic', 'value': 1}]})
check('发明操作符被拒', not ok and any('不是允许的操作符' in e for e in errs), str(errs))

ok, errs = RE.validate({**good, 'then': [{'action': 'delete_files'}]})
check('发明动作被拒', not ok and any('不是允许的动作' in e for e in errs), str(errs))

ok, errs = RE.validate({**good, 'scope': {'sections': ['不存在的板块']}})
check('越界板块被拒', not ok and any('不是允许的值' in e for e in errs), str(errs))

ok, errs = RE.validate({**good, 'then': [{'action': 'status', 'value': 'purple'}]})
check('非法状态值被拒', not ok and any('status' in e and '必须是' in e for e in errs), str(errs))

ok, errs = RE.validate({**good, 'when': []})
check('空条件被拒', not ok and any('至少要有一个条件' in e for e in errs), str(errs))

ok, errs = RE.validate(good | {'when': [{'field': 'img.metric.总分', 'op': 'gte',
                                        'value': 425}]})
check('动态指标字段 img.metric.* 可用', ok, str(errs))

# ---------------- B. 求值 ----------------
print('\nB) 求值（纯函数、确定性）')
item = {'section': '竞赛', 'text': '2025年大创省级立项', 'level': '省级', 'sig': {'levels': ['省级']}}
img = {'kind': 'roster', 'semantic': 'competition', 'vlevel': '院级', 'person_status': 'ok',
       'sig': {'award_name': '大学生创新创业训练计划项目'}, 'cert': {}, 'reading': {}}

eff = RE.evaluate(item, img, [good], score=88)
check('命中并扣分', eff['score_delta'] == -50, str(eff['score_delta']))
check('命中并改状态', eff['set_status'] == 'amber', str(eff['set_status']))
check('命中并留说明', eff['notes'] == ['省级竞赛不接受院级立项'], str(eff['notes']))
check('产出可追溯的命中记录', len(eff['matched']) == 1 and eff['matched'][0]['effects'],
      str(eff['matched']))

# 作用域外不该命中
eff2 = RE.evaluate({'section': '荣誉', 'sig': {'levels': []}, 'text': ''},
                   {'kind': 'certificate', 'vlevel': '院级', 'sig': {}, 'cert': {}, 'reading': {}},
                   [good], score=88)
check('板块不在 scope 内则不命中', not eff2['matched'])

# 禁用规则不生效
eff3 = RE.evaluate(item, img, [{**good, 'enabled': False}], score=88)
check('enabled=false 不生效', not eff3['matched'])

# 确定性：多次求值结果一致
runs = [RE.evaluate(item, img, [good], score=88)['matched'] for _ in range(5)]
check('多次求值完全一致（可复现）', all(r == runs[0] for r in runs))

# 排序：优先级高的先应用
a = {'id': 'r_a', 'name': 'A', 'priority': 10, 'enabled': True,
     'when': [{'field': 'img.level', 'op': 'eq', 'value': '院级'}],
     'then': [{'action': 'status', 'value': 'red'}]}
b = {'id': 'r_b', 'name': 'B', 'priority': 200, 'enabled': True,
     'when': [{'field': 'img.level', 'op': 'eq', 'value': '院级'}],
     'then': [{'action': 'status', 'value': 'amber'}]}
eff4 = RE.evaluate(item, img, [a, b], score=88)
check('高优先级规则先应用', eff4['set_status'] == 'amber', str(eff4['set_status']))
order = [m['id'] for m in eff4['matched']]
check('命中顺序按 priority desc', order == ['r_b', 'r_a'], str(order))

# ---------------- C. 中文回显 ----------------
print('\nC) 中文回显（给辅导员确认）')
txt = RS.explain_dsl(good)
print('   ', txt)
for kw in ['板块是「竞赛」', '级别是「省级」', '院级', '扣 50 分', '待确认', '省级竞赛不接受院级立项']:
    check('回显包含「%s」' % kw, kw in txt, txt)

bad_txt = RS.explain_dsl({'name': 'x', 'when': [{'field': 'bad', 'op': 'eq'}], 'then': []})
check('非法规则的回显给出错误', '不合法' in bad_txt, bad_txt)

# ---------------- D. 引擎集成（真实材料） ----------------
print('\nD) 引擎集成：挂到 cfg.nl_rules 跑真实材料')
from web import create_app, db                     # noqa: E402
from web.services import engine_adapter as EA      # noqa: E402
from web.services import programs as PG            # noqa: E402

app = create_app()

# 规则不写死：先从真实数据里挑一条「申报级别 ≠ 落款级别」的样例，再据此构造规则。
# 这样测试不依赖"我猜哪个板块有院级材料"，数据变了也还能跑。
app = create_app()

with app.app_context():
    target = None
    for f in db.q("SELECT id, student_name, batch_id FROM files WHERE status='analyzed'"):
        rec = EA.load_result(f['id'])
        if not rec:
            continue
        imap = {im['file']: im for im in (rec.get('images') or [])}
        idmap = {it['id']: it for it in (rec.get('items') or [])}
        for m in (rec.get('matches') or []):
            it, img = idmap.get(m.get('item_id')), imap.get(m.get('img_file'))
            if not it or not img:
                continue
            # ★ 用**落盘的事实表**判断（而不是重新从对象算）：
            #   result.json 里的条目没有 sig，重新算会走文本兜底，
            #   与真实流水线看到的事实不一致 —— 那会让"预览"和"实跑"对不上。
            facts = RE.merge_facts(it.get('facts'), img.get('facts'))
            facts['pair.score'] = m.get('score')
            il, vl = facts.get('item.level'), facts.get('img.level')
            if il and vl and il != vl and facts.get('item.section'):
                target = (f, m, it, img, facts)
                break
        if target:
            break

    check('能从真实数据里挑出「申报级别 ≠ 落款级别」的样例', target is not None)

    if target:
        f, m, it, img, facts = target
        rule = {
            'id': 'r_test', 'name': '口径不符需人工确认', 'priority': 120, 'enabled': True,
            'scope': {'sections': [facts['item.section']], 'levels': [facts['item.level']]},
            'when': [{'field': 'img.level', 'op': 'eq', 'value': facts['img.level']}],
            'then': [{'action': 'penalty', 'value': 50},
                     {'action': 'status', 'value': 'amber'},
                     {'action': 'note', 'value': '落款级别与学生自述不一致'}],
        }
        print('   样例：%s · %s' % (f['student_name'], (it.get('text') or '')[:36]))
        print('   事实（落盘）：板块=%s 申报=%s 落款=%s 原状态=%s 原得分=%.1f'
              % (facts['item.section'], facts['item.level'], facts['img.level'],
                 m.get('status'), m.get('score')))
        print('   规则：命中「%s + %s」且落款为「%s」时扣 50 分并判待确认'
              % (facts['item.section'], facts['item.level'], facts['img.level']))

        # ---- 纯函数层：规则确实命中（输入与真实流水线一致 = 落盘事实表）----
        eff = RE.evaluate_facts(facts, [rule])
        check('规则命中该样例', bool(eff['matched']), str(facts))
        check('命中后扣分 -50', eff['score_delta'] == -50, str(eff['score_delta']))

        # ---- 变化一条事实，规则应当不再命中（证明条件是"真的在起作用"）----
        eff_no = RE.evaluate_facts({**facts, 'img.level': '国家级'}, [rule])
        check('事实不满足时规则不命中', not eff_no['matched'])

        # ---- 整条流水线：挂到 cfg.nl_rules 后结论确实变了 ----
        pid = (db.q('SELECT program_id FROM batches WHERE id=?', (f['batch_id'],), one=True)
               or {}).get('program_id')
        prog = PG.get(pid)
        row_f = db.q('SELECT * FROM files WHERE id=?', (f['id'],), one=True)

        import pipeline
        cfg_off = EA.build_cfg(prog); cfg_off.nl_rules = []
        cfg_on = EA.build_cfg(prog);  cfg_on.nl_rules = [rule]
        ref = EA.load_reference(cfg_off, prog)

        def run(cfg):
            return pipeline.analyze(row_f['stored_path'], ref['comp_items'], ref, cfg)[5]

        def pick(lab):
            for x in lab:
                if x['item']['id'] == it['id']:
                    return x
            return None

        a, b = pick(run(cfg_off)), pick(run(cfg_on))
        check('两轮都定位到该条目', a is not None and b is not None)
        if a and b:
            print('   不挂规则：状态 %s，得分 %.1f' % (a['status'], a['score']))
            print('   挂上规则：状态 %s，得分 %.1f' % (b['status'], b['score']))
            check('挂规则后得分被扣', round(b['score'], 2) < round(a['score'], 2),
                  '%s → %s' % (a['score'], b['score']))
            hits = b.get('rule_hits') or []
            check('命中记录可追溯', any('口径不符' in h for h in hits), str(hits))
            neg = [r[0] for r in b['reasons'] if r[1] < 0]
            check('扣分出现在"反证"里', any('规则' in x for x in neg), str(neg))
            check('未挂规则时没有规则痕迹',
                  not (a.get('rule_hits') or []) and not any('规则' in r[0] for r in a['reasons']))

print('\n' + '=' * 82)
print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败：', FAIL)
sys.exit(1 if FAIL else 0)
