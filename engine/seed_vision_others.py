# -*- coding: utf-8 -*-
"""
写入其余 9 名学生"待确认/缺图"相关图片的视觉大模型读取结果。

只覆盖需要人工复核的那批图（35 张），用来评估：
  - 视觉模型在疑难图上的准确率
  - 相比本地 OCR 的成本差异
entry 键为 (学生名, 图片文件名)，脚本自动从 docx 里查出对应的 sha256。
"""
import os
import sys
import glob

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docx_parser import parse_docx   # noqa: E402
import vision                        # noqa: E402

ROOT = r'D:/浏览器下载/Desktop/2.【班级汇总】国家奖学金各班级申报材料'

R = [
    # ================= 万亿航 =================
    ('万亿航', 'image5.jpeg', dict(
        doc_kind='certificate', title='获奖证书',
        award_name='第十六届全国大学生数学竞赛（非数学A类）一等奖',
        award_level='一等奖', academic_year='2024', issuer='中国数学会', level='省级',
        persons=['万亿航'],
        note='2024年12月。证书编号 CMS(浙)F20240338 中的"浙"表明为浙江赛区，按省级计；'
             '该赛事本身不在本年度 A 类名单内（名单里是"数学建模竞赛"，不是"数学竞赛"）')),
    ('万亿航', 'image7.jpeg', dict(
        doc_kind='certificate', title='获奖证书',
        award_name='第十五届全国大学生数学竞赛（非数学A类）三等奖',
        award_level='三等奖', academic_year='2023', issuer='中国数学会', level='省级',
        persons=['万亿航'],
        note='2023年12月。编号 CMS(浙)F20232698 为浙江赛区')),
    ('万亿航', 'image8.png', dict(
        doc_kind='certificate', title='获奖证书',
        award_name='2023睿抗机器人开发者大赛（RAICOM）浙江赛区"编程技能竞赛项目"三等奖',
        award_level='三等奖', academic_year='2023', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['万亿航'], note='2023年07月21日，编号 HJROBO202307006982')),
    ('万亿航', 'image18.jpeg', dict(
        doc_kind='certificate', title='荣誉证书', award_name='文明寝室（32-433）',
        issuer='湖州师范学院', level='校级', persons=[],
        note='2022级军训内务评比，抬头为寝室号')),

    # ================= 胡微祥 =================
    ('胡微祥', 'image14.jpeg', dict(
        doc_kind='certificate', title='获奖证书',
        award_name='2024睿抗机器人开发者大赛（RAICOM）浙江省"编程技能竞赛项目"二等奖',
        award_level='二等奖', academic_year='2024', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['胡微祥'], note='编号 HJROBO202407012352')),
    ('胡微祥', 'image15.png', dict(
        doc_kind='form', title='RAICOM 证书查询结果页面',
        note='竞赛官网证书查询截图，命中 3 条：2022/2024 睿抗二等奖、2025 睿抗三等奖；'
             '属"官方查询截图"形式的补充证据')),
    ('胡微祥', 'image16.png', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='2024年浙江省第二十一届大学生程序设计竞赛 银奖',
        award_level='银奖', academic_year='2024', issuer='浙江省大学生科技竞赛委员会',
        level='省级', is_team=True, team_size=3,
        persons=['周温策', '胡微祥', '赖郑峰'], note='2024年4月')),
    ('胡微祥', 'image18.png', dict(
        doc_kind='certificate', title='中国高校计算机大赛 证书',
        award_name='2024团体程序设计天梯赛 浙江省 团队二等奖',
        award_level='二等奖', academic_year='2024', issuer='全国高等学校计算机教育研究会',
        level='省级', is_team=True, team_size=10,
        persons=['周温策', '谭中正', '赖郑峰', '章文宇', '胡微祥',
                 '钱颖敏', '孙皓', '曹明远', '郑恩祈', '彭玉杰'])),
    ('胡微祥', 'image19.png', dict(
        doc_kind='certificate', title='获奖证书',
        award_name='2025年睿抗机器人开发者大赛（RAICOM）浙江赛区"编程技能竞赛项目"三等奖',
        award_level='三等奖', academic_year='2025', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['胡微祥'], note='编号 HTCHJRAIC25010018')),
    ('胡微祥', 'image21.png', dict(
        doc_kind='certificate', title='蓝桥杯大赛 获奖证书',
        award_name='第十六届蓝桥杯全国软件和信息技术专业人才大赛浙江赛区C/C++程序设计大学B组二等奖',
        award_level='二等奖', academic_year='2025', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['胡微祥'], note='2025年5月26日，编号 1602045674')),

    # ================= 黄俊杰 =================
    ('黄俊杰', 'image5.jpeg', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='第三届"凌特杯"通信系统设计大赛 赛道二 决赛 三等奖',
        award_level='三等奖', academic_year='2025', issuer='“凌特杯”通信系统设计大赛组委会',
        level=None, persons=['黄俊杰'], note='2025年5月；参赛队长梁晟钰，队员黄俊杰；'
                                             '该赛事不在学校 A 类竞赛名单内，级别无法按名单判定')),
    ('黄俊杰', 'image7.jpeg', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='第三届"凌特杯"通信系统设计大赛 赛道二 初赛 一等奖',
        award_level='一等奖', academic_year='2025', issuer='“凌特杯”通信系统设计大赛组委会',
        level=None, persons=['黄俊杰'], note='2025年5月')),
    ('黄俊杰', 'image8.jpeg', dict(
        doc_kind='certificate', title='中国大学生机械工程创新创意大赛 证书',
        award_name='2024年中国大学生机械工程创新创意大赛 物流技术（起重机）创意赛（区域赛）三等奖',
        award_level='三等奖', academic_year='2024', issuer='中国机械工程学会', level='国家级',
        is_team=True, team_size=5,
        persons=['黄俊杰', '程丹怡', '蔡奇璇', '王胜林', '钟钰琦'],
        note='证书编号 MEICC09CLEI2024-QT3-1687')),
    ('黄俊杰', 'image10.png', dict(
        doc_kind='roster', title='湖州师范学院第十二届电子设计竞赛获奖名单',
        award_name='湖州师范学院第十二届电子设计竞赛 二等奖', award_level='二等奖',
        issuer='湖州师范学院教务处、信息工程学院', level='校级',
        persons=['黄俊杰'], person_in_roster=True,
        note='获奖名单公告截图；序号1行红框标注 黄俊杰/电子信息工程/2022082410/二等奖')),

    # ================= 李相颖 =================
    ('李相颖', 'image4.png', dict(
        doc_kind='certificate', title='中国大学生计算机设计大赛 获奖证书',
        award_name='2025年（第18届）中国大学生计算机设计大赛浙江省赛 三等奖',
        award_level='三等奖', academic_year='2025',
        issuer='中国大学生计算机设计大赛浙江省级赛组织委员会', level='省级',
        is_team=True, team_size=2, persons=['李相颖', '付前超'],
        note='作品《研海观澜——全国研究生招生可视化系统》，2025年6月')),
    ('李相颖', 'image11.png', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='2024年（第十届）全国大学生统计建模大赛湖州师范学院校内选拔 本科生组 三等奖',
        award_level='三等奖', academic_year='2024', issuer='湖州师范学院', level='校级',
        is_team=True, team_size=3, persons=['蒋臻煌', '尤姝丹', '李相颖'],
        note='2024年6月')),
    ('李相颖', 'image14.jpeg', dict(
        doc_kind='certificate', title='普通话水平测试等级证书',
        issuer='国家语言文字工作委员会', level='国家级', persons=['李相颖'],
        metrics={'成绩': 87.5, '等级': '二级甲等'},
        note='测试时间 2023年4月8日')),
    ('李相颖', 'image15.jpeg', dict(
        doc_kind='certificate', title='荣誉证书', award_name='优秀学生',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['李相颖'],
        note='证书上是 2023-2024 学年，而学生有一条写的是"2022-2023学年校级优秀学生"，学年不符')),
    ('李相颖', 'image17.jpeg', dict(
        doc_kind='certificate', title='荣誉证书', award_name='优秀学生干部',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['李相颖'])),

    # ================= 程丹怡 =================
    ('程丹怡', 'image1.jpeg', dict(
        doc_kind='certificate', title='奖学金证书', award_name='校级三等奖学金',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['程丹怡'])),
    ('程丹怡', 'image3.png', dict(
        doc_kind='certificate', title='蓝桥杯大赛 获奖证书',
        award_name='第十六届蓝桥杯全国软件和信息技术专业人才大赛浙江赛区单片机设计与开发大学组一等奖',
        award_level='一等奖', academic_year='2025', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['程丹怡'], note='2025年5月26日，编号 1608014066')),
    ('程丹怡', 'image5.png', dict(
        doc_kind='certificate', title='中国大学生机械工程创新创意大赛 证书',
        award_name='2024年中国大学生机械工程创新创意大赛 物流技术（起重机）创意赛（区域赛）三等奖',
        award_level='三等奖', academic_year='2024', issuer='中国机械工程学会', level='国家级',
        is_team=True, team_size=5,
        persons=['黄俊杰', '程丹怡', '蔡奇璇', '王胜林', '钟钰琦'])),
    ('程丹怡', 'image8.png', dict(
        doc_kind='roster', title='湖州师范学院大学生创新创业训练计划项目评审结果汇总表',
        award_name='大创项目 参与', level='校级', persons=['程丹怡'], person_in_roster=True,
        note='序号253"基于STM32的自动化犬舍研发"项目参与学生含程丹怡（负责人黄俊杰）')),
    ('程丹怡', 'image10.jpeg', dict(
        doc_kind='certificate', title='荣誉证书', award_name='文明修身先进个人',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['程丹怡'])),
    ('程丹怡', 'image11.jpeg', dict(
        doc_kind='certificate', title='荣誉证书', award_name='"闪亮新主播"大赛 二等奖',
        award_level='二等奖', academic_year='2023', issuer='中共湖州师范学院党委宣传部',
        level='校级', persons=['程丹怡'], note='2023年10月14日')),

    # ================= 王雪 =================
    ('王雪', 'image2.jpeg', dict(
        doc_kind='certificate', title='奖学金证书', award_name='校级一等奖学金',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['王雪'])),
    ('王雪', 'image5.jpeg', dict(
        doc_kind='certificate', title='蓝桥杯大赛 获奖证书',
        award_name='第十六届蓝桥杯全国软件和信息技术专业人才大赛浙江赛区单片机设计与开发大学组一等奖',
        award_level='一等奖', academic_year='2025', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['王雪'], note='2025年5月26日，编号 1608014049')),
    ('王雪', 'image6.jpeg', dict(
        doc_kind='certificate', title='蓝桥杯大赛 获奖证书',
        award_name='第十五届蓝桥杯全国软件和信息技术专业人才大赛浙江赛区单片机设计与开发大学组一等奖',
        award_level='一等奖', academic_year='2024', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['王雪'], note='2024年4月29日，编号 081512498')),
    ('王雪', 'image12.jpeg', dict(
        doc_kind='certificate', title='荣誉证书', award_name='优秀学生',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['王雪'])),

    # ================= 薛婉婷 =================
    ('薛婉婷', 'image2.jpeg', dict(
        doc_kind='certificate', title='2025年全国大学生英语竞赛（NECCS）参赛证书',
        award_name='2025年全国大学生英语竞赛 参赛',
        academic_year='2025', issuer='全国大学生英语竞赛组织委员会', level='国家级',
        persons=['薛婉婷'], note='参赛证书，非获奖证书，2025年4月13日')),
    ('薛婉婷', 'image3.png', dict(
        doc_kind='roster', title='信息工程学院2025年大学生创新创业训练计划项目推荐立项名单（公示）',
        award_name='大创项目 立项', level='院级', persons=['薛婉婷'], person_in_roster=True,
        note='序号2"面向工业物联网的开放集入侵检测技术研究"项目成员含薛婉婷/2024082233')),
    ('薛婉婷', 'image4.png', dict(
        doc_kind='roster', title='信息工程学院2025年大学生创新创业训练计划项目推荐立项名单（公示）',
        award_name='大创项目 立项', level='院级', persons=['薛婉婷'], person_in_roster=True,
        note='序号15"AI赋能科学家精神融入大学生思想政治教育创新路径研究"成员含薛婉婷')),
    ('薛婉婷', 'image5.png', dict(
        doc_kind='roster',
        title='湖州师范学院中国国际大学生创新大赛（2025）（原"互联网+"大赛）人文学院院赛结果公示',
        award_name='中国国际大学生创新大赛（原"互联网+"）院赛 一等奖', award_level='一等奖',
        academic_year='2025', level='院级', persons=['薛婉婷'], person_in_roster=True,
        note='网页公示截图，红框标出"智汇强师"项目获一等奖，团队成员含薛婉婷。'
             '注意：这是院赛结果，学生写的是校级')),

    # ================= 田羽萱 =================
    ('田羽萱', 'image1.png', dict(
        doc_kind='roster', title='附件1：2025年全国大学生电子设计竞赛获奖名单',
        award_name='2025年全国大学生电子设计竞赛 浙江赛区 一等奖', award_level='一等奖',
        academic_year='2025', issuer='全国大学生电子设计竞赛组委会', level='省级',
        is_team=True, team_size=3, persons=['林宁', '於鑫', '田羽萱'], person_in_roster=True,
        note='序号1413，浙江赛区本科组C题，湖州师范学院 林宁/於鑫/田羽萱（红圈标注）。'
             '注意：这是浙江赛区获奖名单，学生写的是"全国一等奖"')),

    # ================= 朱乐怡 =================
    ('朱乐怡', 'image2.jpeg', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='2025年浙江省大学生高等数学（微积分）竞赛校内选拔赛 三等奖',
        award_level='三等奖', academic_year='2025', issuer='湖州师范学院', level='校级',
        persons=['朱乐怡'], note='2025年5月16日')),
]


def main():
    # 建立 (学生, 文件名) -> sha 的索引
    idx = {}
    for p in glob.glob(os.path.join(ROOT, '*.docx')):
        if os.path.basename(p).startswith('~$') or '模板' in p:
            continue
        try:
            r = parse_docx(p)
        except Exception:
            continue
        nm = r['student'].get('name')
        for m, rec in r['media'].items():
            idx[(nm, m.split('/')[-1])] = rec['sha256']
    n, miss = 0, []
    for name, fname, obj in R:
        sha = idx.get((name, fname))
        if not sha:
            miss.append('%s/%s' % (name, fname))
            continue
        vision.put(sha, obj)
        n += 1
    print('写入 %d 条；视觉缓存共 %d 条' % (n, vision.count()))
    if miss:
        print('未匹配:', miss)


if __name__ == '__main__':
    main()
