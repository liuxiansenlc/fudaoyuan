# -*- coding: utf-8 -*-
"""
把散落在各模块里的常量全部外化成 engine/rules_builtin.json。

原则：
- 能**从活代码导出**的（词表、正则、板块表、语义表）一律导出，保证零转写误差；
- 内联在函数体里的数值（打分权重、门槛）只能抄，抄错由 verify_diff.py 的
  "零差异"测试兜底。

生成后本脚本即可删除。
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import matching as M          # noqa: E402
import reading as R           # noqa: E402
import vision as V            # noqa: E402
import docx_parser as D       # noqa: E402
import config as C            # noqa: E402

data = {
    'meta': {'name': '国奖审核默认规则', 'version': 1,
             'note': '由 engine/_gen_rules.py 从活代码导出；手改请同步 verify_diff 回归'},

    # ---------------- 打分权重（score_pair）----------------
    'weights': {
        'type_match': 22, 'type_mismatch': -12,
        'section_match': 16, 'section_mismatch': -8,
        'kind_match': 6, 'kind_mismatch': -4,
        'comp_std_equal': 38, 'comp_name_diff': -16,
        'comp_raw_sim_scale': 38, 'comp_raw_none': -5,
        'award_name_coverage_min': 0.6, 'award_name_scale': 38,
        'lcs_min_seg': 4, 'lcs_per_char': 8.0, 'lcs_cap': 34.0,
        'phrase_jaccard_scale': 30, 'phrase_fuzzy_min': 0.7, 'phrase_fuzzy_scale': 24,
        'award_level_match': 12, 'award_level_mismatch': -20,
        'gov_level_match': 10, 'gov_level_mismatch': -12,
        'year_match': 8, 'year_adjacent': 3, 'year_far': -6,
        'name_ok': 6, 'roster_missing': -30, 'name_conflict': -40,
        'triple_bonus': 15,
        'score_min': 0.0, 'score_max': 100.0,
    },

    # ---------------- 门槛（assign / label_matches / 参考名单）----------------
    'thresholds': {
        'match': C.MATCH_THRESHOLD,
        'auto_confirm': 80.0,
        'green_person_ok': 60.0,
        'pending_below': 60.0,
        'alternative_gap': 12.0,
        'speculative_min_score': 15.0,
        'comp_sim_min': 0.6,
        'comp_std_min': 0.5,
        'ref_comp_sim': 0.7, 'ref_comp_gap': 0.12, 'ref_prefilter': 0.15, 'ref_find': 0.7,
    },

    # ---------------- 词表（从活代码导出）----------------
    'vocab': {
        'scholarship_phrases': M.SCHOLARSHIP_PHRASES,
        'honor_phrases': M.HONOR_PHRASES,
        'skill_phrases': M.SKILL_PHRASES,
        'physical_phrases': M.PHYSICAL_PHRASES,
        'volunteer_phrases': M.VOLUNTEER_PHRASES,
        'research_phrases': M.RESEARCH_PHRASES,
        'stop_lcs': sorted(M.STOP_LCS),
        'generic_mask': M.GENERIC_MASK,
        'lead_noise': M._LEAD_NOISE,
    },

    # ---------------- 正则（从活代码导出）----------------
    'regex': {
        'award_level': M.RE_AWARD_LEVEL.pattern,
        'year_range': M.RE_YEAR_RANGE.pattern,
        'year': M.RE_YEAR.pattern,
        'comp_name': M.RE_COMP_NAME.pattern,
        'comp_short': M.RE_COMP_SHORT.pattern,
        'item_level': M.ITEM_LEVEL.pattern,
        'section': D.RE_SECTION.pattern,
    },

    # ---------------- 板块（从活代码导出，顺带合并重复的 SECTION_EXPECT）----------------
    'sections': {
        'expect': {k: sorted(v) for k, v in M.SECTION_EXPECT.items()},
        'kinds': {k: sorted(v) for k, v in M.SECTION_KINDS.items()},
        'order': ['奖学金', '竞赛', '科研', '荣誉', '体测', '志愿'],
        'ordinal_key': getattr(D, 'ORDINAL_KEY', {}),
        'keywords': [[k, v] for k, v in getattr(D, 'SECTION_KEYWORDS', [])],
    },

    # ---------------- 语义关键词（from reading）----------------
    'semantic_keys': [[k, v] for k, v in R.SEM_KEYS],

    # ---------------- 级别判定（from vision）----------------
    'level_rules': {
        'districts': V.DISTRICTS,
        'provinces': V.PROVINCES,
        'school_full': V.SCHOOL_FULL,
        'province_exception': '赛区',
    },

    # ---------------- 资格判定（体测已按学生手册改为 75）----------------
    'reading_rules': {
        'cet_pass': 425,
        'mandarin_min': ['一级甲等', '一级乙等', '二级甲等', '二级乙等'],
        'physical_pass': C.PHYSICAL_PASS,
        'year_range_min_gap': 2,
        'year_range_max_gap': 5,
    },

    # ---------------- 图片分诊（from docx_parser.triage_media）----------------
    'triage': {
        'aspect_max': 6.0,
        'short_min': 180,
        'long_min': 400,
        'aspect_unusual': 2.6,
    },
}

# 兼容：ORDINAL_KEY / SECTION_KEYWORDS 在 docx_parser 里可能是别的名字
if not data['sections']['ordinal_key']:
    for nm in ('ORDINAL_KEY', 'ORDINAL_MAP', 'ORDINAL'):
        if hasattr(D, nm):
            data['sections']['ordinal_key'] = getattr(D, nm)
            break
    else:
        print('注意：docx_parser 里没找到 ORDINAL_KEY，留空（使用方需自行兜底）')

dst = os.path.join(HERE, 'rules_builtin.json')
with open(dst, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print('已生成', dst)
print('  词表 %d 组 / 正则 %d 条 / 板块 %d 个 / 语义 %d 组 / 权重 %d 项 / 门槛 %d 项'
      % (len(data['vocab']), len(data['regex']), len(data['sections']['expect']),
         len(data['semantic_keys']), len(data['weights']), len(data['thresholds'])))
print('  体测达标线 physical_pass =', data['reading_rules']['physical_pass'])
