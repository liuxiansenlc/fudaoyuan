# -*- coding: utf-8 -*-
"""
引擎适配层 —— Web 与 engine/ 之间唯一的桥。

职责边界（很重要）：
- **只做编排和搬运**，不实现任何判定逻辑。所有规则都在 engine/ 里。
- 把「上传的文件路径」翻译成引擎要的入参，把引擎的输出翻译成「前端要的 JSON」。
- 引擎不联网：读图由 tasks 侧的 vision_client 写回 cache/vision/<sha256>.json，
  这里只读缓存。所以本模块可以在没有网络的情况下完整跑通。

这样分层的好处：引擎仍可脱离 Web 单测；Web 层换框架也不影响判定结果。
"""
import os
import sys
import json
import glob
import shutil
import zipfile

from ..config import WebConfig
from .. import db

_ENGINE_LOADED = False


def ensure_path():
    """把 engine/ 加进 sys.path（只做一次）。"""
    global _ENGINE_LOADED
    if not _ENGINE_LOADED:
        if WebConfig.ENGINE_DIR not in sys.path:
            sys.path.insert(0, WebConfig.ENGINE_DIR)
        _ENGINE_LOADED = True
    return WebConfig.ENGINE_DIR


# 进 engine 之后才能 import 这些
def _mods():
    ensure_path()
    import settings
    import reference
    import pipeline
    import vision
    import docx_parser
    return settings, reference, pipeline, vision, docx_parser


# ------------------------------------------------------------------ 配置
def build_cfg(program=None):
    """
    默认规则包 ⊕ 全局覆盖 ⊕ **该奖学金项目的覆盖** ⊕ 引擎缓存目录。

    program 可以传项目 dict 或 program_id。每次返回新对象，
    绝不就地改 DEFAULT（各批次/各项目之间共享同一个默认实例）。
    """
    from . import programs as PG
    from . import rule_service as RS
    settings, _, _, _, _ = _mods()
    pid = program.get('id') if isinstance(program, dict) else program
    overrides = PG.effective_overrides(pid)
    cfg = settings.DEFAULT.with_overrides(overrides)
    cfg.cache_dir = WebConfig.ENGINE_CACHE_DIR      # 复用已有读图缓存
    cfg.allow_ocr_fallback = False
    # 自定义规则（全局 + 本项目）。with_overrides 只处理 dict 字段，
    # 列表字段在这里显式赋值；拿到的是深拷贝，不会污染 DEFAULT。
    try:
        cfg.nl_rules = RS.effective_rules(pid)
    except Exception:
        cfg.nl_rules = []
    return cfg


def reload_rules():
    """保存规则后热重载默认配置。"""
    settings, _, _, _, _ = _mods()
    return settings.reload_default()


# 界面上给关键项配的一句人话解释（没有的就不显示）
HINTS = {
    'thresholds.match': '低于它在配对时就判「缺图」，宁可让人去看也不硬凑',
    'thresholds.auto_confirm': '达到这个分数且没有任何反证，才判「自动配对」',
    'thresholds.green_person_ok': '姓名能核对上时，可放宽到这个分数判自动配对',
    'thresholds.pending_below': '低于它又没有其他信息 → 直接标「证据不足」',
    'thresholds.comp_sim_min': '赛事名相似度低于它就当作对不上',
    'thresholds.speculative_min_score': '把条目相邻的图挂为「附件疑似」的最低分数',
    'thresholds.alternative_gap': '两张候选分差小于它 → 视为「分数接近」，降级待确认',
    'thresholds.ref_comp_sim': '与 A 类竞赛名单标准名的相似度门槛',
    'thresholds.ref_comp_gap': '与第二名的最小差距，避免两个候选都像',
    'weights.type_match': '板块类型与证书类型一致时的加分',
    'weights.comp_name': '赛事名归一到 A 类名单标准名后一致时的加分（权重最高）',
    'weights.award_level': '奖项等级一致时的加分',
    'weights.level_match': '级别（国家/省/校/院）一致时的加分',
    'weights.year_match': '年份一致时的加分（学期相邻给一半）',
    'weights.person_match': '申报人姓名出现在材料上的加分',
    'weights.person_conflict': '证书上的人不是申报人 → 硬红线',
    'weights.triple_bonus': '等级+级别+姓名同时命中，巧合概率极低',
    'reading_rules.cet_pass': '四六级合格总分（425）',
    'reading_rules.physical_pass': '体测达标线，学生手册口径为 75 分以上',
    'triage.aspect': '长宽比超过它认为是长条装饰图，直接跳过识别',
}


def rules_snapshot(program=None):
    """给界面用：分组 + 中文说明（含该项目的覆盖标记）。"""
    settings, _, _, _, _ = _mods()
    cfg = build_cfg(program)
    groups = []

    def g(title, key, desc, items, kind='number', hints=None):
        hints = hints or {}
        rows = [{'key': '%s.%s' % (key, k), 'name': k, 'value': v,
                 'hint': hints.get('%s.%s' % (key, k), '')} for k, v in items.items()]
        if rows:
            groups.append({'title': title, 'group': key, 'desc': desc,
                           'kind': kind, 'items': rows})

    g('判定门槛', 'thresholds', '低于配对门槛判「缺图」；达到自动确认线且无反证才判「自动配对」',
      cfg.thresholds, hints=HINTS)
    g('读取与达标线', 'reading_rules', '四六级、体测的判定标准',
      cfg.reading_rules, hints=HINTS)
    g('配对打分权重', 'weights', '每条「奖项条目 × 候选图片」各证据的加减分，满分 100',
      cfg.weights, hints=HINTS)
    g('图片分诊', 'triage', '识别前先排除噪声图（纯装饰、过小、异常长宽比）',
      cfg.triage, hints=HINTS)
    return groups


# ------------------------------------------------------------------ 参考数据
def load_reference(cfg=None, program=None):
    """
    加载 A 类竞赛表 + 成绩排名表。

    ★ 每类都**加载全部上传文件并合并**（多班级/多学年的成绩表是常态）。
    优先级：该奖学金项目上传的 → 全局上传的（全校通用表）→ config.py 写死的路径。
    """
    from . import programs as PG
    _, reference, _, _, _ = _mods()
    cfg = cfg or build_cfg(program)
    pid = program.get('id') if isinstance(program, dict) else program

    comp_rows = PG.refdata_list(pid, 'competition')
    rank_rows = PG.refdata_list(pid, 'ranking')
    comp_files = [r['stored_path'] for r in comp_rows if r.get('stored_path')]
    rank_files = [r['stored_path'] for r in rank_rows if r.get('stored_path')]
    # 没上传任何文件时，退回配置里写死的路径（本地开发/单机模式可用）
    if not comp_files and cfg.competition_file:
        comp_files = [cfg.competition_file]
    if not rank_files and cfg.ranking_root:
        rank_files = [cfg.ranking_root]

    def _present(paths):
        return [p for p in paths if p and os.path.exists(p)]

    comp_items, comp_note = [], ''
    comp_ok = _present(comp_files)
    if comp_ok:
        try:
            comp_items = reference.load_competitions(comp_ok, cfg=cfg)['items']
        except Exception as e:
            comp_note = 'A类竞赛表读取失败：%s' % e
    else:
        comp_note = '未提供 A 类竞赛表'

    ranking = {'by_sid': {}, 'by_name': {}, 'files': 0}
    rank_note = ''
    rank_ok = _present(rank_files)
    if rank_ok:
        try:
            ranking = reference.load_ranking(rank_ok, cfg=cfg)
            if not ranking.get('by_sid'):
                # 有文件却一条学号都没读到：多半是格式不标准（列名不同、多级表头）
                rank_note = ('成绩表读取到 0 条学号（共 %d 份）—— 列名可能不是标准格式，'
                             '建议开启「AI 智能识别」重新上传' % len(rank_ok))
        except Exception as e:
            rank_note = '成绩排名表读取失败：%s' % e
    else:
        rank_note = '未提供成绩排名表'

    def _src(rows):
        """标注这些参考材料是本项目专用，还是沿用了全校通用的。"""
        if not rows:
            return ''
        return '本项目专用' if all(r.get('program_id') for r in rows) else '全校通用'

    def _names(paths, rows):
        if rows:
            n = [r['name'] for r in rows]
        else:
            n = [os.path.basename(p) for p in paths if p]
        if not n:
            return ''
        return n[0] if len(n) == 1 else '%s 等 %d 份' % (n[0], len(n))

    return {
        'comp_items': comp_items,
        'ranking': ranking,
        'meta': {
            'competition_file': _names(comp_files, comp_rows),
            'ranking_dir': _names(rank_files, rank_rows),
            'competition_n': len(comp_items),
            'ranking_n': len(ranking.get('by_sid') or {}),
            'competition_note': comp_note,
            'ranking_note': rank_note,
            'competition_src': _src(comp_rows),
            'ranking_src': _src(rank_rows),
            'competition_files': len(comp_rows) or len(comp_files),
            'ranking_files': len(rank_rows) or len(rank_files),
        },
    }


# ------------------------------------------------------------------ 图片
def _ext_of(fname):
    return fname.rsplit('.', 1)[-1].lower() if '.' in fname else 'png'


def image_web_path(sha256, fname):
    """浏览器可访问的相对路径（data/images 下按 sha256 存，天然去重）。"""
    return '/img/%s.%s' % (sha256, _ext_of(fname))


def save_images_from_docx(docx_path):
    """
    把 docx 里的图片按 sha256 落到 data/images/。
    返回 {media: {'sha256':.., 'url':.., 'w':.., 'h':..}}
    """
    _, _, _, _, DP = _mods()
    r = DP.parse_docx(docx_path)
    out = {}
    for media, rec in r['media'].items():
        sha = rec['sha256']
        ext = _ext_of(media)
        dst = os.path.join(WebConfig.IMAGE_DIR, '%s.%s' % (sha, ext))
        if not os.path.exists(dst) or os.path.getsize(dst) != len(rec['data']):
            with open(dst, 'wb') as f:
                f.write(rec['data'])
        out[media] = {'sha256': sha, 'w': rec.get('w'), 'h': rec.get('h'),
                      'url': image_web_path(sha, media),
                      'file': media.split('/')[-1]}
    return out


# ------------------------------------------------------------------ 解析
def ingest(docx_path):
    """上传后立刻解析一次，拿到学生姓名与条目/图片数，写进 files 表。"""
    _, _, pipeline, _, _ = _mods()
    r = pipeline.step_parse(docx_path)
    sname = (r['student'].get('name') or '').strip()
    if not sname:
        base = os.path.basename(docx_path)
        sname = base.split('班')[-1].split('国家')[0][:8] if '班' in base else base[:8]
    return {
        'student_name': sname,
        'n_items': len(r['items']),
        'n_images': len(r['media']),
        'warnings': r.get('warnings') or [],
    }


# ------------------------------------------------------------------ 分析
def analyze_one(docx_path, ref, cfg=None, file_id=None):
    """
    跑一个学生的完整流水线，产出与旧版 run_report.main 完全同构的记录。
    返回记录 dict（可直接 json.dump）。
    """
    _, _, pipeline, _, _ = _mods()
    import rules_engine as RE          # 引擎侧的纯函数，用来摊平"规则事实表"
    cfg = cfg or build_cfg()

    r, items, imgs, ignored, res, labeled = pipeline.analyze(
        docx_path, ref.get('comp_items'), ref, cfg)

    save_images_from_docx(docx_path)

    record = {
        'file': docx_path,
        'file_id': file_id,
        'student': r['student'],
        # facts：把"规则要用到的扁平事实"一并落盘。
        # 否则规则影响预览只能读到精简后的条目（没有 sig），
        # 会出现"预览说没影响、实际有影响"的不一致。
        'items': [{'id': i['id'], 'section': i['section'], 'sublevel': i['sublevel'],
                   'text': i['text'], 'facts': RE.build_facts(i, None)} for i in items],
        'images': [{k: v for k, v in im.items() if k not in ('sig', 'ocr')}
                   | {'ocr_text': im['ocr'],
                      'url': image_web_path(im['sha256'], im['file']),
                      'facts': RE.build_facts(None, im)}
                   for im in imgs],
        'ignored': ignored,
        'matches': [{'item_id': x['item_id'],
                     'item_text': x['item']['text'],
                     'img_file': x['img']['file'] if x['img'] else None,
                     'img_sha': x['img']['sha256'] if x['img'] else None,
                     'img_url': image_web_path(x['img']['sha256'], x['img']['file'])
                                if x['img'] else None,
                     'score': x['score'],
                     'status': x['status'],
                     'reasons': x['reasons'],
                     'pending_reason': x.get('pending_reason') or '',
                     'rule_hits': x.get('rule_hits') or [],
                     'alternatives': [a['file'] for a in x.get('alternatives', [])]}
                    for x in labeled],
        'eligibility': res.get('eligibility') or {},
        'orphan_images': [imgs[i]['file'] for i in (res.get('orphan_images') or [])],
        'section_level_images': [imgs[i]['file'] for i in (res.get('section_level_images') or [])],
    }
    return record


def result_path(file_id):
    return os.path.join(WebConfig.RESULT_DIR, '%s.json' % file_id)


def save_result(file_id, record):
    os.makedirs(WebConfig.RESULT_DIR, exist_ok=True)
    with open(result_path(file_id), 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, default=str)
    return result_path(file_id)


def load_result(file_id):
    p = result_path(file_id)
    if not os.path.exists(p):
        return None
    with open(p, 'r', encoding='utf-8') as f:
        return json.load(f)


# ------------------------------------------------------------------ 读图进度
def read_progress(docx_path, cfg=None):
    """
    返回该文档的读图进度：{total, done, todo:[{sha256,file,url,ext}]}
    Web 层据此显示"待读图 m/n"并生成读图任务。
    """
    _, _, pipeline, vision, _ = _mods()
    cfg = cfg or build_cfg()
    r = pipeline.step_parse(docx_path)
    mf = pipeline.step_media_manifest(r)
    readings, todo = pipeline.step_readings(mf, cfg)
    return {
        'total': len(mf),
        'done': len(readings),
        'todo': [{'sha256': m['sha256'], 'file': m['file'], 'media': m['media'],
                  'ext': m['ext'], 'w': m['w'], 'h': m['h'], 'triage': m['triage']}
                 for m in todo],
        'manifest': mf,
    }


def read_image_bytes(docx_path, media_name):
    """从 docx 里取某张图的原始字节（读图任务要把它送去模型）。"""
    with zipfile.ZipFile(docx_path) as z:
        return z.read(media_name)


def placeholder_readings():
    """占位：本地 OCR 已停用，这里是明确的"尚未读取"标记。"""
    return None
