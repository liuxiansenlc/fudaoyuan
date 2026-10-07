# -*- coding: utf-8 -*-
"""
配置文件 —— 每年换材料时只需要改这里。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))

# 学生提交的材料目录（放 .docx 文件）
STUDENT_DIR = r'D:/浏览器下载/Desktop/2.【班级汇总】国家奖学金各班级申报材料'

# 成绩排名表：可以是目录（自动递归找 xlsx），也可以是单个文件
RANKING_DIR = r'D:/浏览器下载/Desktop/【领导签字版】(缓考后）2024-2025学年专业学习成绩排名表（必修、限选）'

# A类竞赛名单（.xls 或 .xlsx）
COMPETITION_FILE = r'D:/浏览器下载/Desktop/2024年湖州师范学院A类竞赛申报汇总表.xls'

# 输出目录
OUT_DIR = os.path.join(HERE, '..', 'out')
CACHE_DIR = os.path.join(HERE, '..', 'cache')

# 配对阈值：得分低于它就不配对（宁可判"缺图"让人去看，也不硬凑）
MATCH_THRESHOLD = 50.0
# 高于它且没有接近的备选，才认为是"自动配对"（绿）
AUTO_CONFIRM_SCORE = 78.0

# 读取层是否允许"视觉模型没有结果时回退本地 OCR"。
# 现已全量改为视觉大模型识别，此项保持 False：
# 没有视觉结果的图会被明确标成"待视觉复核"，而不是用可能读错的本地结果
# 悄悄顶替——避免把 OCR 的误读当作证据（这是"减少幻觉"的关键一环）。
ALLOW_OCR_FALLBACK = False

# 体测达标线（学生手册口径：75 分以上即达标）
PHYSICAL_PASS = 75.0
