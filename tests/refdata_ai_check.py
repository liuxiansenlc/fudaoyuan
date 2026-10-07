# -*- coding: utf-8 -*-
"""
参考材料「AI 智能识别」的纯逻辑测试（不联网）：
只测 read_tabular_text / materialize 两段本地逻辑，以及标准化文件能否被引擎读取。
AI 调用部分（ai_parse）依赖网络与模型，这里用 monkeypatch 模拟。
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'engine'))

from web.services import refdata_ai as RAI
import reference as ref


def _mk_xlsx(rows, path):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(path)
    wb.close()


def test_read_tabular_text_xlsx():
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'a.xlsx')
    _mk_xlsx([['学号', '姓名', '平均绩点'], ['20230826', '张三', '4.2']], p)
    t = RAI.read_tabular_text(p)
    assert '学号' in t and '20230826' in t and '张三' in t
    print('read_tabular_text xlsx OK')


def test_read_tabular_text_csv():
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'a.csv')
    with open(p, 'w', encoding='utf-8') as f:
        f.write('学号,姓名,平均绩点\n20230826,张三,4.2\n')
    t = RAI.read_tabular_text(p)
    assert '20230826' in t and '张三' in t
    print('read_tabular_text csv OK')


def test_materialize_competition_roundtrip():
    d = tempfile.mkdtemp()
    recs = [{'name': '全国大学生数学建模竞赛', 'level': '国家级', 'category': 'A+'},
            {'name': '蓝桥杯全国软件和信息技术专业人才大赛', 'level': '省级', 'category': 'A'}]
    dest, n = RAI.materialize(recs, 'competition', d, 'c.xlsx')
    assert n == 2
    c = ref.load_competitions(dest)
    assert len(c['items']) == 2
    assert c['items'][0]['name'] == '全国大学生数学建模竞赛'
    assert c['items'][0]['level'] == '国家级'
    assert c['items'][0]['category'] == 'A+'
    print('materialize competition roundtrip OK')


def test_materialize_ranking_roundtrip():
    d = tempfile.mkdtemp()
    recs = [{'sid': '20230826', 'name': '张三', 'class_no': '20230826班', 'gpa': '4.2',
             'gpa_rank': '1', 'fail_credit': '0'},
            {'sid': '20230827', 'name': '李四', 'gpa': '3.1', 'gpa_rank': '5'}]
    dest, n = RAI.materialize(recs, 'ranking', d, 'r.xlsx')
    assert n == 2
    r = ref.load_ranking(dest)
    assert set(r['by_sid'].keys()) == {'20230826', '20230827'}
    assert r['by_sid']['20230826']['name'] == '张三'
    assert r['by_sid']['20230826']['gpa'] == '4.2'
    assert r['by_sid']['20230826']['gpa_rank'] == '1'
    assert r['by_sid']['20230827']['gpa'] == '3.1'
    print('materialize ranking roundtrip OK')


def test_materialize_drops_bad_rows():
    d = tempfile.mkdtemp()
    recs = [{'sid': '', 'name': '无学号'}, {'name': '也无效'}]
    dest, n = RAI.materialize(recs, 'ranking', d, 'r.xlsx')
    assert n == 0
    assert dest is None
    print('materialize drops bad rows OK')


def test_load_competitions_supports_xlsx():
    """增强后的 load_competitions 应能读 .xlsx（不再是只有 xlrd 读 .xls）。"""
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'comp.xlsx')
    _mk_xlsx([['竞赛名称', '级别', '竞赛类别'],
              ['全国大学生数学建模竞赛', '国家级', 'A'],
              ['蓝桥杯', '省级', 'A-']], p)
    c = ref.load_competitions(p)
    assert len(c['items']) == 2
    print('load_competitions xlsx support OK')


if __name__ == '__main__':
    test_read_tabular_text_xlsx()
    test_read_tabular_text_csv()
    test_materialize_competition_roundtrip()
    test_materialize_ranking_roundtrip()
    test_materialize_drops_bad_rows()
    test_load_competitions_supports_xlsx()
    print('\n全部通过')
