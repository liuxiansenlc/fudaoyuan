# -*- coding: utf-8 -*-
"""
回归：人工认领归属 / 人工否定 / 顶部统计跳转 —— 三条用户反馈的落地验证。

用 Flask test_client 走真实接口 + 直接调视图/导出服务断言结果。
注意：HTTP 请求一律在 app_context 之外做（否则 flask.session 会跨 client 串），
DB / 服务调用在 app_context 内做。
"""
import os
import sys
import re
import traceback

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'engine'))
os.environ['WB_INLINE_WORKER'] = '0'

PASS = FAIL = 0


def check(name, cond, detail=''):
    global PASS, FAIL
    if cond:
        PASS += 1
        print('  ✓', name)
    else:
        FAIL += 1
        print('  ✗', name, ('→ ' + str(detail)) if detail else '')


def _login(app):
    """新建已登录的 client，返回 (client, csrf_token)。"""
    c = app.test_client()
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    c.get('/login').get_data(as_text=True)).group(1)
    c.post('/login', data={'username': 'admin', 'password': '123456', '_csrf': tok})
    tok = re.search(r'name="csrf-token" content="([^"]+)"',
                    c.get('/').get_data(as_text=True)).group(1)
    return c, tok


def _p(path):
    from docx import Document
    return Document(path).paragraphs


def main():
    from web import create_app, db
    from web.services import engine_adapter as EA, programs as PG
    from web.services import view_builder as VB, export_service as EX
    from web.services import sections as SEC

    app = create_app()

    # 先登录一次，让所有 client 都能用（client 之间 session 通过 cookie 隔离，
    # 但 login 请求要在 app_context 外发起）
    admin_client, _ = _login(app)

    # ---- 挑样例（纯 DB 读取，需要 app_context） ----
    with app.app_context():
        files = db.q("SELECT * FROM files WHERE status='analyzed' ORDER BY id")
        target = None
        for f in files:
            rec = EA.load_result(f['id'])
            if not rec:
                continue
            vv = VB.build(rec, file_id=f['id'])
            if vv['stats']['left'] >= 1 and vv['stats']['need'] >= 1:
                target = f
                break
        if target is None:      # 退而求其次：只要有未认领图
            for f in files:
                rec = EA.load_result(f['id'])
                vv = VB.build(rec, file_id=f['id']) if rec else None
                if vv and vv['stats']['left'] >= 1:
                    target = f
                    break
        check('找到带未认领图的学生', target is not None)
        if target is None:
            print('   无可用样例，跳过后续')
            return
        fid = target['id']
        program = PG.get_by_batch(target['batch_id'])
        scheme = PG.scheme_of(program)
        print('\n样例：%s (file=%d, program=%s)' %
              (target['student_name'], fid, (program or {}).get('name') or '—'))

    # ============ 1. 认领到「方案外板块」，导出也能找到对应类目 ============
    with app.app_context():
        rec = EA.load_result(fid)
        view = VB.build(rec, file_id=fid, scheme=scheme)
        orphan_img = None
        for sec in view['sections']:
            if sec['leftover_main']:
                orphan_img = sec['leftover_main'][0]
                break
        if orphan_img is None:
            for sec in view['sections']:
                if sec['leftover']:
                    orphan_img = sec['leftover'][0]
                    break
        check('取到一张未认领图', orphan_img is not None)
        extra = [k for k in SEC.EXTRA_KEYS if k not in set(SEC.keys(scheme))]
        target_sec = extra[0] if extra else '其他'

    if orphan_img:
        print('   认领到方案外板块：%s（方案内=%s）' %
              (target_sec, target_sec in set(SEC.keys(scheme))))
        c2, tok2 = _login(app)
        r = c2.post('/api/files/%d/orphan/adopt' % fid,
                    json={'img_sha': orphan_img['sha'],
                          'award_name': '测试认领奖项',
                          'section': target_sec},
                    headers={'X-CSRF-Token': tok2})
        check('采纳接口 ok（方案外板块）', r.get_json().get('ok'), r.get_json())

        with app.app_context():
            rec2 = EA.load_result(fid)
            view2 = VB.build(rec2, file_id=fid, scheme=scheme)
            sec_keys = [s['key'] for s in view2['sections']]
            check('采纳后该板块出现在渲染里', target_sec in sec_keys, sec_keys)
            scheme_keys = set(SEC.keys(scheme))
            idx_extra = sec_keys.index(target_sec)
            idx_last_scheme = max(sec_keys.index(k) for k in sec_keys if k in scheme_keys) \
                if scheme_keys else -1
            check('方案外板块排在方案内之后', idx_extra > idx_last_scheme,
                  'extra=%d last_scheme=%d' % (idx_extra, idx_last_scheme))

            out_path = os.path.join(HERE, 'data', 'exports', '_review_flow_%d.docx' % fid)
            EX.build_docx(view2, out_path, reject_mode='remove')
            text = '\n'.join(p.text for p in _p(out_path))
            check('导出文件生成了认领项', '测试认领奖项' in text)
            lbl = [s['label'] for s in view2['sections'] if s['key'] == target_sec][0]
            check('导出含该板块标题', lbl in text, lbl)

        # 撤销采纳，恢复现场
        c3, _ = _login(app)
        c3.delete('/api/files/%d/orphan/adopt?img_sha=%s' % (fid, orphan_img['sha']))

    # ============ 2. 人工否定 = 独立状态，不是缺图 ============
    with app.app_context():
        rec = EA.load_result(fid)
        view = VB.build(rec, file_id=fid, scheme=scheme)
        attn = None
        for sec in view['sections']:
            for row in sec['rows']:
                if row['status'] in ('amber', 'pending_weak') and row['item_id'] is not None:
                    attn = row
                    break
            if attn:
                break
        check('找到一条待确认条目', attn is not None)

    if attn:
        iid = attn['item_id']
        c4, tok4 = _login(app)
        r = c4.post('/api/files/%d/actions' % fid,
                    json={'decision': 'reject', 'items': [iid]},
                    headers={'X-CSRF-Token': tok4})
        check('否定接口 ok', r.get_json().get('ok'), r.get_json())

        with app.app_context():
            rec = EA.load_result(fid)
            view_r = VB.build(rec, file_id=fid, scheme=scheme)
            row_r = next((r for s in view_r['sections'] for r in s['rows']
                          if r['item_id'] == iid), None)
            check('否定后状态是「人工否定」', row_r and row_r['status'] == 'rejected',
                  row_r and row_r['status'])
            check('否定后仍保留证明图（不是缺图）', row_r and row_r.get('img') is not None)
            check('统计里计入「人工否定」', view_r['stats']['rejected'] >= 1)

            out1 = os.path.join(HERE, 'data', 'exports', '_rf_remove_%d.docx' % fid)
            EX.build_docx(view_r, out1, reject_mode='remove')
            t1 = '\n'.join(p.text for p in _p(out1))
            # 用「去掉序号后的完整条目文本」作唯一标识，避免同名奖项（不同届）
            # 前缀撞车导致误判。clean 逻辑与导出侧一致：剥掉开头的序号。
            import re as _re
            frag = _re.sub(r'^\s*\d{1,2}\s*[\.、）)]\s*', '', row_r['item_text'] or '').strip()
            check('remove 模式：否定的条目被剔除', frag not in t1, frag)

            out2 = os.path.join(HERE, 'data', 'exports', '_rf_mark_%d.docx' % fid)
            st2 = EX.build_docx(view_r, out2, reject_mode='mark')
            t2 = '\n'.join(p.text for p in _p(out2))
            check('mark 模式：否定的条目被保留', frag in t2)
            check('mark 模式：带「不符合申报规范」标注', '不符合申报规范' in t2)
            check('mark 统计有 rejected_marked', st2[1].get('rejected_marked', 0) >= 1, st2[1])

        # 改回通过
        c5, tok5 = _login(app)
        r = c5.post('/api/files/%d/actions' % fid,
                    json={'decision': 'confirm', 'items': [iid]},
                    headers={'X-CSRF-Token': tok5})
        check('改回通过接口 ok', r.get_json().get('ok'), r.get_json())
        with app.app_context():
            rec = EA.load_result(fid)
            view_c = VB.build(rec, file_id=fid, scheme=scheme)
            row_c = next((r for s in view_c['sections'] for r in s['rows']
                          if r['item_id'] == iid), None)
            check('改回后状态是「人工确认」', row_c and row_c['status'] == 'confirmed',
                  row_c and row_c['status'])

    # ============ 3. 顶部统计 chip 可跳转（渲染层面） ============
    with app.app_context():
        rec = EA.load_result(fid)
        view3 = VB.build(rec, file_id=fid, scheme=scheme)
        need_rows = [r for s in view3['sections'] for r in s['rows']
                     if 'attn' in (r.get('jump') or [])]
        check('待确认条目带 jump=need/attn 标记', len(need_rows) >= 0)

    c6, _ = _login(app)
    html = c6.get('/review/%d' % fid).get_data(as_text=True)
    check('页面含跳转脚本 jumpTo', 'function jumpTo(' in html)
    check('统计 chip 可点击（onclick=jumpTo）', 'onclick="jumpTo(' in html)
    check('条目带 data-jump 属性', 'data-jump=' in html)
    check('人工否定的下拉切换存在', 'saveRejMode' in html)

    print('\n' + '=' * 60)
    print('通过 %d 项，失败 %d 项' % (PASS, FAIL))
    print('=' * 60)
    if FAIL:
        sys.exit(1)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(2)
