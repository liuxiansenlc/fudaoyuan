# -*- coding: utf-8 -*-
"""
导出模板健壮性自检（对应线上"导出是空文件"的 bug）。

背景：模板路径曾写死在开发机的 D:/... 绝对路径，服务器上不存在 →
build_docx 对每份材料都抛 FileNotFoundError → 打包出空 zip。

这里验收：
  1. resolve_template() 能找到仓库内置模板（或至少回退到可用模板）
  2. 模板缺失时 build_docx 仍能导出（走最小版式兜底），产出非空 docx
  3. 导出正文含标题与板块，且页面尺寸为 A4
  4. 空产出的守护：_h_export 不会把"零成功"打成空 zip（用源码/case 层面校验）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

_DB = os.path.join(tempfile.gettempdir(), 'wb_export_tpl_check.db')
for _s in ('', '-wal', '-shm'):
    try:
        os.remove(_DB + _s)
    except OSError:
        pass
os.environ['WB_DB_PATH'] = _DB
os.environ['WB_INLINE_WORKER'] = '0'

from docx import Document
from docx.shared import Cm

from web.config import WebConfig
from web.services import export_service as EX


def _view(name='测试同学'):
    return {
        'student': {'name': name, 'class_no': '20230826班'},
        'sections': [
            {'key': '奖学金', 'label': '一、曾获奖学金', 'rows': [
                {'item_id': 1, 'item_text': '1. 校级一等奖学金', 'status': 'green',
                 'img': None, 'extra_imgs': []}],
             'leftover_main': [], 'leftover_self': []},
            {'key': '竞赛', 'label': '二、学科竞赛获奖', 'rows': [
                {'item_id': 2, 'item_text': '1. 全国大学生数学建模竞赛国家级一等奖',
                 'status': 'blue', 'img': {'url': '/img/none_x.png', 'vlevel': '国家级'},
                 'extra_imgs': []}],
             'leftover_main': [], 'leftover_self': []},
        ],
        'stats': {}, 'reject_mode': 'remove',
    }


def main():
    d = tempfile.mkdtemp()

    # 1) 仓库内置模板应能被解析到
    tpl = EX.resolve_template()
    assert tpl and os.path.exists(tpl), 'resolve_template 未找到可用模板'
    print('1) 解析到模板：%s' % os.path.relpath(tpl, HERE))

    # 2) 无模板也能导出（传一个不存在的路径）
    out1 = os.path.join(d, 'no_tpl.docx')
    EX.build_docx(_view('无模板同学'), out1, template=os.path.join(d, '不存在.docx'))
    assert os.path.exists(out1) and os.path.getsize(out1) > 3000, '无模板导出结果为空'
    print('2) 模板缺失兜底导出 OK（%d B）' % os.path.getsize(out1))

    # 3) 正常导出：标题、板块、A4 版式齐全，且正文非空
    out2 = os.path.join(d, 'ok.docx')
    _p, st = EX.build_docx(_view(), out2, program_name='国家奖学金')
    doc = Document(out2)
    text = '\n'.join(p.text for p in doc.paragraphs)
    assert '测试同学' in text and '申请支撑材料' in text, '标题缺失'
    assert '一、曾获奖学金' in text, '板块标题缺失'
    assert '校级一等奖学金' in text, '条目正文缺失'
    assert st['kept_items'] >= 2, 'kept_items 异常：%s' % st
    sec = doc.sections[0]
    wcm = sec.page_width.cm if sec.page_width else 0
    assert abs(wcm - 21.0) < 0.5, '页面不是 A4（宽 %.2fcm）' % wcm
    print('3) 正常导出 OK（%d B，页面宽 %.1fcm，保留 %d 项）'
          % (os.path.getsize(out2), wcm, st['kept_items']))

    # 4) 守护：导出处理器在"零成功"时必须抛错，而不是产出空 zip
    import inspect
    from web.services import task_service as TS
    src = inspect.getsource(TS._h_export)
    assert 'if not made' in src and '没有任何材料成功导出' in src, \
        '_h_export 缺少"零成功则报错"的守护'
    print('4) 空产出守护 OK（零成功时明确报错，不再给空 zip）')

    # 5) 全板块骨架：模板六大板块恒在，空板块/空子标题写「无」，不减少模板内容
    view_empty = {
        'student': {'name': '空同学', 'class_no': '20230826班'},
        'scheme': [],                       # 不依赖方案，也要输出全部模板板块
        'sections': [
            # 只有竞赛的"国家级"一条，其余板块全空
            {'key': '竞赛', 'label': '二、学科竞赛获奖（A类竞赛需注明）', 'rows': [
                {'item_id': 9, 'item_text': '1. 全国大学生数学建模竞赛国家级一等奖',
                 'status': 'blue', 'img': {'url': '/img/_none_.png', 'vlevel': '国家级'},
                 'extra_imgs': []}],
             'leftover': [], 'leftover_main': [], 'leftover_self': [], 'ok': True},
        ],
        'stats': {}, 'reject_mode': 'remove',
    }
    out3 = os.path.join(d, 'skeleton.docx')
    EX.build_docx(view_empty, out3, program_name='国家奖学金')
    doc3 = Document(out3)
    paras = [p.text.strip() for p in doc3.paragraphs if p.text.strip()]
    seq = '\n'.join(paras)
    # 六大板块标题必须全部出现（顺序一致）
    heads = ['一、曾获奖学金', '二、学科竞赛获奖（A类竞赛需注明）', '三、科研创新',
             '四、荣誉情况（校级及以上）', '五、体测', '六、志愿者时长']
    for i, hd in enumerate(heads):
        assert hd in paras, '缺少板块标题：%s' % hd
        if i:
            assert paras.index(heads[i - 1]) < paras.index(hd), '板块顺序不对：%s' % hd
    # 模板子标题恒在
    for sub in ['（一）国家级', '（二）省级', '（一）校级']:
        assert sub in paras, '缺少子标题：%s' % sub
    # 空板块/空子标题都写「无」：应至少出现 4 处（奖学金、科研、荣誉、体测、志愿 + 省级）
    assert paras.count('无') >= 4, '空板块未写「无」（%d 处）' % paras.count('无')
    # 具体抽查：曾获奖学金 紧接 无
    i0 = paras.index('一、曾获奖学金')
    assert paras[i0 + 1] == '无', '「曾获奖学金」下应为「无」，实际=%r' % paras[i0 + 1]
    # （二）省级 下应为「无」
    i1 = paras.index('（二）省级')
    assert paras[i1 + 1] == '无', '「（二）省级」下应为「无」，实际=%r' % paras[i1 + 1]
    # 科研的（一）校级 下应为「无」
    i2 = paras.index('（一）校级')
    assert paras[i2 + 1] == '无', '「（一）校级」下应为「无」，实际=%r' % paras[i2 + 1]
    # 旧的「（本板块未提交材料）」不应再出现
    assert '（本板块未提交材料）' not in seq, '仍残留旧文案'
    print('5) 全板块骨架 OK（六大板块齐全、空板块写「无」、子标题恒在）')

    print('\n全部通过')


if __name__ == '__main__':
    main()
