# -*- coding: utf-8 -*-
"""
写入胡珍华的 VLM 读取结果（人工/大模型视觉读图产物）。

来源：由视觉大模型逐张阅读 32 张图片后录入，字段含义见 vision.py 的 SCHEMA。
这份数据用来对比"本地 OCR"与"视觉大模型"的效果差距。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vision  # noqa: E402

R = [
    # ---------- 一、曾获奖学金 ----------
    ('36f7f19113a40802f4aa54e0c14c4ea35e4d414ec7d0c7ad2815408865f20873', dict(
        doc_kind='certificate', title='浙江省政府奖学金 荣誉证书',
        award_name='浙江省政府奖学金', academic_year='2022-2023',
        issuer='浙江省教育厅', level='省级', persons=['胡珍华'],
        note='编号 2023年第24868号')),
    ('2299fcdb874fb04acf49fd66015224a496f9f14d1fa0bcefb68850e8d586edc3', dict(
        doc_kind='certificate', title='浙江省政府奖学金 荣誉证书',
        award_name='浙江省政府奖学金', academic_year='2023-2024',
        issuer='浙江省教育厅', level='省级', persons=['胡珍华'],
        note='编号 2024年第26071号')),
    ('4c0512e69cd22f409516a1588dac278dedc24bb252476c4deda0a11eeec74551', dict(
        doc_kind='certificate', title='奖学金证书',
        award_name='校级一等奖学金', academic_year='2022-2023',
        issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('6a931b4c989bbe4d4b68d988b9c346fe5f51b95019e93c4f27e7f4b519e73b4d', dict(
        doc_kind='certificate', title='奖学金证书',
        award_name='校级一等奖学金', academic_year='2023-2024',
        issuer='湖州师范学院', level='校级', persons=['胡珍华'])),

    # ---------- 二、学科竞赛 ----------
    ('8c68f5437c1531ad6e60eadf5312e6201a8d7a3cbcf41c5b661d440b2405d5cd', dict(
        doc_kind='certificate', title='中国高校计算机大赛 证书',
        award_name='2024团体程序设计天梯赛全国总决赛 成功参赛奖',
        award_level='成功参赛奖', academic_year='2024', issuer='全国高等学校计算机教育研究会',
        level='国家级', is_team=True, team_size=10,
        persons=['许源', '胡珍华', '蔡悦', '李思琪', '徐千然', '杨怡',
                 '金佳成', '罗学政', '胡雨菲', '刘瑞'],
        note='团队赛，胡珍华在队员名单中')),
    ('ecc62de4793d1382b078b0dc0636e35416951dee9a81dead4627877a9d8bad5d', dict(
        doc_kind='certificate', title='蓝桥杯大赛 获奖证书',
        award_name='第十五届蓝桥杯全国软件和信息技术专业人才大赛浙江赛区C/C++程序设计大学B组三等奖',
        award_level='三等奖', academic_year='2024', issuer='工业和信息化部人才交流中心',
        level='省级', persons=['胡珍华'], note='证书编号 021541625')),
    ('15f39c0e3e2b14757f0ac26ecdf8c42034b93b4ff5a6a44a748b83e4281a4bfd', dict(
        doc_kind='certificate', title='中国高校计算机大赛 证书',
        award_name='2024网络技术挑战赛 华东赛区 二等奖',
        award_level='二等奖', academic_year='2024', issuer='全国高等学校计算机教育研究会',
        level='省级', is_team=True, team_size=5,
        persons=['王喜旭', '吕宾宾', '孟泷', '刘浩', '胡珍华'])),
    ('46fb1be2ebc3be4f844e3b51a3345522c5129b3d7aa50c10b0aca89dc29c0c02', dict(
        doc_kind='roster', title='竞赛获奖名单',
        award_name='创业计划竞赛 银奖', award_level='银奖',
        level='校级', persons=['胡珍华'], person_in_roster=True,
        note='名单截图，胡珍华出现在第52行（与陈璐瑶、项梦婷等同组）')),
    ('a7f307e9075e0f11e8524427f9a442f32d619733bab9751b814fd15bc310fb4d', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='湖州师范学院第二十届高等数学（微积分）竞赛 工科组 优胜奖',
        award_level='优胜奖', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('ed824f98e5b9ec41fe6e30c2af125c677bd25f35330f448eb6af450a491acc85', dict(
        doc_kind='roster', title='项目立项名单',
        persons=[], person_in_roster=False,
        note='医学院/护理学院项目名单，全表未见胡珍华，疑似与本人无关')),
    ('650f12bbd382ec80a9af2ff626fa9fd9fa8e69ee6b284b788719569f1646144b', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='2025年第二十届浙江省大学生电子商务竞赛暨第十五届全国大学生电子商务"创新、创意及创业"挑战赛浙江省选拔赛 本科组 三等奖',
        award_level='三等奖', academic_year='2025', issuer='浙江省大学生科技竞赛委员会',
        level='省级', is_team=True, team_size=4,
        persons=['李佳琪', '周芊芊', '赵雪娇', '胡珍华'],
        note='省级证书为三等奖；学生正文写"一等奖"，与校级选拔赛评分表一致，需分别看待')),
    ('05d2ad1078190a7e8497559e34c1b34d84d16f14bcd94d82e305c4e6293e0989', dict(
        doc_kind='roster', title='项目立项名单（含联系电话）',
        persons=['胡珍华'], person_in_roster=True,
        note='名单中"救在身边"项目组含胡珍华及其联系电话')),
    ('54c1469f7ccb51fbb4a393edf698f9e7b2971c3bd17379ba3a37b5ec5456f27c', dict(
        doc_kind='score_sheet', title='竞赛评分表',
        award_name='校级选拔赛 一等奖', award_level='一等奖',
        level='校级', persons=['胡珍华'], person_in_roster=True,
        note='"IoT的家庭记忆传承与代际沟通新方案" 项目组含胡珍华，平均分89.4，评级一等奖')),
    ('8ab4e28e339df39b57f4ea3cb2d418e61cdb7a8d909d32f3c13ef73b503d4e0c', dict(
        doc_kind='certificate', title='荣誉证书',
        award_name='湖州师范学院第五期体育与健康课程教学成果展示之"跆拳道"比赛 一等奖',
        award_level='一等奖', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('93c5709d80fdd9d8a0a2f256476105ccdf5feb637323188dd25f9e9250f02208', dict(
        doc_kind='roster', title='省级大学生创新创业训练计划立项名单',
        persons=['胡珍华'], person_in_roster=True,
        note='"面向分割数据集的遥感数据增强技术研究及应用" 一般项目，胡珍华 学号2022082336')),

    # ---------- 四、荣誉情况 ----------
    ('2905b7241324ecfa8d068b4ba9ec7faa2cdb4509c1d3e298beea335ceeffa9ac', dict(
        doc_kind='certificate', title='荣誉证书', award_name='优秀学生',
        academic_year='2022-2023', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('2aa810d8b91d3ca1337a6952c081f18be263405c40e41d3d6ff5f0d58656f1d1', dict(
        doc_kind='certificate', title='荣誉证书', award_name='优秀学生',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('21252103275342a1a836bd5d18374403f94518fe8abb68db61d108034df7caba', dict(
        doc_kind='certificate', title='荣誉证书', award_name='优秀学生干部',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('513e84c0b67785b6dce34278c923867c771be6689c9d0f7e3e3327bef16179bc', dict(
        doc_kind='roster', title='湖州师范学院2023年度优秀团干部名单（684人）',
        award_name='优秀团干部', academic_year='2023', issuer='湖州师范学院',
        level='校级', persons=[], person_in_roster=None,
        note='名单第1页（校团委/寝室团工委/研工部/安定书院），胡珍华不在此页')),
    ('2665ae517a2797ecaf5d9b8c2db0243676da86a41d777ce2c97a78880d0e43bc', dict(
        doc_kind='roster', title='湖州师范学院2023年度优秀团干部名单 · 信息工程学院（共46人）',
        award_name='优秀团干部', academic_year='2023', issuer='湖州师范学院',
        level='校级', persons=['胡珍华'], person_in_roster=True,
        note='胡珍华出现在"信息工程学院（共46人）"名单中')),
    ('30d8961887724228aa74c5103a72705abd58683a3fd85159aeae433d55a37ee9', dict(
        doc_kind='certificate', title='荣誉证书', award_name='创新创业先进个人',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('6137d31762b6ad173a5ff48064f589e4c1221af08b341a2dcf3d377421ab99b5', dict(
        doc_kind='certificate', title='荣誉证书', award_name='文明修身先进个人',
        academic_year='2022-2023', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('413150ec9345db402f993b32666c94b3c76eedd8dd0c081a19ee585385f519f2', dict(
        doc_kind='certificate', title='荣誉证书', award_name='文体活动先进个人',
        academic_year='2023-2024', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('ff2df0aeaf83594f426ef0d374c71cad14d7073b439a98f590aee8ed78d86380', dict(
        doc_kind='certificate', title='荣誉证书', award_name='平安校园创建先进个人',
        academic_year='2024', issuer='湖州师范学院', level='校级', persons=['胡珍华'],
        note='"2024年度平安校园创建工作中表现突出"，落款二〇二五年一月')),
    ('80e8a148ac3105327f6dde7565e129e87030a720aa0d223df1d5391f658160a9', dict(
        doc_kind='transcript', title='全国大学英语四级考试(CET4)成绩报告单',
        academic_year='2024', issuer='教育部教育考试院', level='国家级',
        persons=['胡珍华'], metrics={'总分': 468, '听力': 130, '阅读': 178, '写作和翻译': 160},
        note='考试时间 2024年6月；总分468 已达到425的通过线')),
    ('870b2d24900d64f812d8a1b9d6b728afbb3effd96aa43e0db287e1a93cda74d2', dict(
        doc_kind='certificate', title='普通话水平测试等级证书',
        issuer='国家语言文字工作委员会', level='国家级', persons=['胡珍华'],
        metrics={'成绩': 84.4, '等级': '二级乙等'},
        note='测试时间 2022年11月22日')),
    ('c3caca81f0b3802042dcbcef49bdc263ce95efb4d139f836ef84b007908df712', dict(
        doc_kind='certificate', title='荣誉证书', award_name='文明修身学生先进个人',
        academic_year='2022-2023', issuer='湖州师范学院', level='校级', persons=['胡珍华'])),
    ('32d9ec61aae5d08d9b7dcebd9b18c26d2c9d012228fca02e6576c65ffce95bbc', dict(
        doc_kind='certificate', title='荣誉证书', award_name='文明寝室（28-109）',
        issuer='湖州师范学院', level='校级', persons=[],
        note='2022级军训内务评比，抬头是寝室号而非个人')),
    ('ce445e9b845ddf923109415e8a66127154e078a7b9b28f23c62d82394cbb7b2d', dict(
        doc_kind='roster', title='湖州师范学院2024年度优秀共青团员名单（279人）',
        award_name='优秀共青团员', academic_year='2024', issuer='湖州师范学院',
        level='校级', persons=[], person_in_roster=None, note='名单第1页')),
    ('3c44edea07af0e3a47f4ac6f6f70d7410cd0a4acc06a83c18c6df6c77f42cd15', dict(
        doc_kind='roster', title='湖州师范学院2024年度优秀共青团员名单 · 信息工程学院（共24人）',
        award_name='优秀共青团员', academic_year='2024', issuer='湖州师范学院',
        level='校级', persons=['胡珍华'], person_in_roster=True,
        note='胡珍华出现在"信息工程学院（共24人）"名单中（同页还有黄俊杰、万亿航）')),
    ('ee7fa6b1dc82b2dc0074c524362bccff186343e6fc8bee2a598eb01ef35a9305', dict(
        doc_kind='form', title='体测成绩查询截图',
        academic_year='2024-2025', persons=['胡珍华'],
        metrics={'总分': 79.6, '等级': '及格', '学号': '2022082336'},
        note='学号 2022082336 属于 20220826 班（学号中段不等同班级号）')),
    ('9940dfa64635311d42ece23a1ae0e68e7f25e4bf7ac1f22e62e34405f424a8c2', dict(
        doc_kind='summary_table', title='志愿者时长汇总表截图',
        persons=['胡珍华'], metrics={'志愿时长': 138.5},
        note='表格截图，胡珍华一行显示 40.5 / 58 / 40，合计 138.5 小时')),
]


def main():
    n = 0
    for sha, obj in R:
        vision.put(sha, obj)
        n += 1
    print('写入 %d 条 VLM 读取结果，缓存现有 %d 条' % (n, vision.count()))


if __name__ == '__main__':
    main()
