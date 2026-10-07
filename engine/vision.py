# -*- coding: utf-8 -*-
"""
视觉大模型读取层（VLM）

与本地 OCR 的关系：
  - 本地 OCR 只给"一堆文字行"，不理解版式；
  - VLM 直接产出结构化结果，能处理艺术字体、印章遮挡、低清照片、
    以及"名单公示表里找某个名字"这类需要理解版式的任务。
两者共用同一套图片指纹，结果按 sha256 缓存，互不重复。

读取优先级：VLM 结果 > 本地 OCR。
没有 VLM 结果的图片自动回退到本地 OCR，所以可以渐进式接入——
先把最难的图片交给 VLM，其余仍走本地。

字段约定见 SCHEMA 注释。doc_kind 明确区分证据形式：
  证书 / 名单公示 / 汇总表截图 / 成绩单 / 评分表
这些都是**有效证据**，不能因为"不是证书版式"就忽略。
"""
import os
import json

from settings import DEFAULT

# 缓存目录统一由 EngineConfig 决定（默认与旧版一致），不再是各自拼接
CACHE_DIR = None


def _cache_dir(cache_dir=None):
    return cache_dir or CACHE_DIR or DEFAULT.vision_cache_dir

SCHEMA = {
    'doc_kind': 'certificate | roster | summary_table | transcript | score_sheet | form | other',
    'title': '证据抬头，如 荣誉证书 / 浙江省政府奖学金',
    'award_name': '归一后的奖项名，如 优秀学生 / 创新创业先进个人 / 浙江省政府奖学金',
    'award_level': '一等奖/二等奖/三等奖/优胜奖/成功参赛奖/…（没有则 null）',
    'academic_year': '学年或年份，如 2022-2023 / 2024',
    'issuer': '落款单位原文，用于判定级别',
    'level': '国家级/省级/校级/院级（由落款推断）',
    'persons': '证书/名单上出现的人名列表',
    'person_in_roster': '仅名单类：申报人是否出现在名单中（true/false/null）',
    'is_team': '是否团队获奖',
    'team_size': '团队人数',
    'metrics': '量化指标，如 {总分:468} / {成绩:84.4,等级:"二级乙等"}',
    'note': '需要人工注意的说明',
}


def _path(sha, cache_dir=None):
    return os.path.join(_cache_dir(cache_dir), sha + '.json')


def get(sha, cache_dir=None):
    p = _path(sha, cache_dir)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def put(sha, obj, cache_dir=None):
    os.makedirs(_cache_dir(cache_dir), exist_ok=True)
    obj = dict(obj)
    obj['source'] = 'vlm'
    with open(_path(sha, cache_dir), 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    return obj


def count(cache_dir=None):
    d = _cache_dir(cache_dir)
    if not os.path.isdir(d):
        return 0
    return len([f for f in os.listdir(d) if f.endswith('.json')])


# ------------------------------------------------------------------ 级别判定
DISTRICTS = ['全国', '中国', '教育部', '共青团中央', '国家', '国际']
PROVINCES = ['浙江省', '省教育厅', '省大学生科技竞赛委员会', '赛区', '华东']
SCHOOL_FULL = ['湖州师范学院', '湖州师范大学']


def judge_level(*texts, cfg=None):
    """
    从落款/文本判定获奖级别。

    规则（用户口径）：
      - 出现完整校名"湖州师范学院/湖州师范大学" → 校级
      - 去掉完整校名后仍出现"XX学院" → 院级（院级材料不能算校级）
      - 出现全国/中国/教育部等 → 国家级
      - 出现浙江省等 → 省级
    注意必须先剔除完整校名，否则"湖州师范学院"里的"学院"会被误判成院级。
    """
    _c = cfg or DEFAULT
    _districts = _c.level_rules.get('districts', DISTRICTS)
    _provinces = _c.level_rules.get('provinces', PROVINCES)
    _school = _c.level_rules.get('school_full', SCHOOL_FULL)
    t = ''.join(x for x in texts if x)
    t = t.replace(' ', '')
    if any(w in t for w in DISTRICTS):
        # "全国大学生XX竞赛浙江省赛区" 这类，含省赛区字样的按省级判
        if '赛区' in t and not any(w in t for w in ['全国总决赛', '国家级']):
            return '省级'
        return '国家级'
    stripped = t
    for s in SCHOOL_FULL:
        stripped = stripped.replace(s, '')
    if any(w in t for w in SCHOOL_FULL):
        # 剔掉完整校名后还有别的"学院" → 说明出处是二级学院
        if '学院' in stripped:
            return '院级'
        return '校级'
    if '学院' in stripped:
        return '院级'
    if any(w in t for w in PROVINCES):
        return '省级'
    if '湖州' in t:
        return '校级'
    return None
