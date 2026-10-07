# -*- coding: utf-8 -*-
"""
引擎配置对象（EngineConfig）。

背景：原先阈值/权重/词表散落在 matching / reading / vision / docx_parser / config
等 8 个文件里，改一个数要翻半天代码，Web 界面也没法配置。

现在：
- 全部常量集中在 `rules_builtin.json`（由 `_gen_rules.py` 从活代码导出）；
- 各模块 `from settings import DEFAULT`，用 `DEFAULT.weights['...']` 取值；
- 函数一律接受可选 `cfg` 参数，默认 `DEFAULT` —— 所以**行为与改造前完全一致**，
  Web 层/规则引擎可以按批次注入覆盖后的配置。

注意：本模块**不得** import matching/reading/vision/docx_parser（会造成循环），
只依赖标准库与 config。
"""
import os
import json
import copy
from dataclasses import dataclass, field, fields

import config as C

HERE = os.path.dirname(os.path.abspath(__file__))
RULE_FILE = os.path.join(HERE, 'rules_builtin.json')


@dataclass
class EngineConfig:
    # ---- 规则包（来自 rules_builtin.json）----
    meta: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    thresholds: dict = field(default_factory=dict)
    vocab: dict = field(default_factory=dict)
    regex: dict = field(default_factory=dict)
    sections: dict = field(default_factory=dict)
    semantic_keys: list = field(default_factory=list)   # reading 用：[[key,[关键词...]]]
    sem_labels: list = field(default_factory=list)      # matching 用：[[key,展示名]]
    sem_label_extra: dict = field(default_factory=dict)
    level_rules: dict = field(default_factory=dict)
    reading_rules: dict = field(default_factory=dict)
    triage: dict = field(default_factory=dict)
    # 自定义规则（受限 DSL，见 rules_engine.py）。加载时不进 JSON，
    # 由 Web 层按「全局 + 奖学金项目」拼好后赋给 cfg.nl_rules。
    nl_rules: list = field(default_factory=list)

    # ---- 路径组（默认取 config.py，Web 层可按批次覆盖）----
    student_dir: str = C.STUDENT_DIR
    ranking_root: str = C.RANKING_DIR
    competition_file: str = C.COMPETITION_FILE
    out_dir: str = C.OUT_DIR
    cache_dir: str = C.CACHE_DIR

    # ---- 开关 ----
    allow_ocr_fallback: bool = C.ALLOW_OCR_FALLBACK

    # ------------------------------------------------------------------ 便捷取值
    def w(self, key, default=0.0):
        return self.weights.get(key, default)

    def t(self, key, default=0.0):
        return self.thresholds.get(key, default)

    @property
    def vision_cache_dir(self):
        return os.path.join(self.cache_dir, 'vision')

    @property
    def ocr_cache_dir(self):
        return os.path.join(self.cache_dir, 'ocr')

    # ------------------------------------------------------------------ 覆盖
    def key_map(self):
        """展平成 {'weights.type_match': 22, 'thresholds.match': 50, ...}，供界面渲染。"""
        out = {}
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, dict):
                for k, vv in v.items():
                    out['%s.%s' % (f.name, k)] = vv
        return out

    def with_overrides(self, flat):
        """
        flat: {'weights.type_match': 25, 'thresholds.match': 45, ...}
        返回新的 EngineConfig，不改原对象（配置在批次之间共享，不能就地改）。
        """
        new = copy.deepcopy(self)
        for key, val in (flat or {}).items():
            if '.' not in key:
                continue
            grp, k = key.split('.', 1)
            cur = getattr(new, grp, None)
            if isinstance(cur, dict):
                cur[k] = val
        return new

    def save_rules(self, path=None):
        """把当前规则包写回 json（供界面保存阈值面板）。"""
        path = path or RULE_FILE
        data = {f.name: getattr(self, f.name) for f in fields(self)
                if f.name not in ('student_dir', 'ranking_root', 'competition_file',
                                  'out_dir', 'cache_dir', 'allow_ocr_fallback')}
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return path

    # ------------------------------------------------------------------ 加载
    @classmethod
    def load(cls, path=None):
        path = path or RULE_FILE
        raw = {}
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                raw = json.load(f)
        else:
            raise FileNotFoundError('规则包不存在：%s' % path)

        cfg = cls(
            meta=raw.get('meta', {}),
            weights=raw.get('weights', {}),
            thresholds=raw.get('thresholds', {}),
            vocab=raw.get('vocab', {}),
            regex=raw.get('regex', {}),
            sections=raw.get('sections', {}),
            semantic_keys=raw.get('semantic_keys', []),
            sem_labels=raw.get('sem_labels', []),
            sem_label_extra=raw.get('sem_label_extra', {}),
            level_rules=raw.get('level_rules', {}),
            reading_rules=raw.get('reading_rules', {}),
            triage=raw.get('triage', {}),
        )
        return cfg


# 进程级默认配置：各模块 import 它，行为与旧版硬编码一致
DEFAULT = EngineConfig.load()


def reload_default(path=None):
    """改了 rules_builtin.json 之后热重载（Web 保存阈值后调用）。"""
    global DEFAULT
    DEFAULT = EngineConfig.load(path)
    return DEFAULT
