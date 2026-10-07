# -*- coding: utf-8 -*-
"""
流水线步骤 —— Web 化的关键。

把原来 `run_report.analyze_file` 那一大坨，拆成**可独立调用、可中断、可复用**的步骤。
每一步的入参都是显式的（`ref` 参考数据、`cfg` 引擎配置），不再依赖模块级全局变量。

Web 层的典型用法：

    from pipeline import step_parse, step_media_manifest, analyze

    r = step_parse(docx_path)                 # 1. 解析 docx（纯本地、很快）
    manifest = step_media_manifest(r)         # 2. 列出图片清单（前端据此显示"待读图 m/n"）
    #   ← 这里由 Web 层的 worker 调视觉大模型，把结果写回 cache/vision/<sha256>.json
    rec = analyze(docx_path, comp_items, ref, cfg)   # 3. 读缓存 → 配对 → 出结论

之所以要拆开，是因为：
- 读图要调很多次网络接口，必须能异步、能报进度、能中断重来；
- 解析和配对是纯本地计算，秒级完成，不该被网络卡住；
- 图片按 sha256 缓存，跨文件/跨批次/跨年份复用，读图这一步必须能单独重跑。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import matching                     # noqa: E402
from docx_parser import parse_docx  # noqa: E402
from settings import DEFAULT        # noqa: E402


# ============================================================ 步骤 1：解析
def step_parse(docx_path):
    """解析 docx → {student, blocks, items, occurrences, media, warnings}（纯本地）。"""
    return parse_docx(docx_path)


def step_media_manifest(r):
    """
    列出该文档里所有图片的清单，供"待读图"队列用。
    返回 [{media, file, sha256, ext, w, h, triage, section}]。
    """
    import docx_parser as DP
    out = []
    occ_sec = {}
    for o in r.get('occurrences', []):
        blk = o.get('block_idx')
        if 0 <= blk < len(r.get('blocks', [])):
            occ_sec.setdefault(o['media'], r['blocks'][blk].get('section'))
    for media, rec in r['media'].items():
        cat, _why = DP.triage_media(rec)
        out.append({
            'media': media,
            'file': media.split('/')[-1],
            'sha256': rec['sha256'],
            'ext': media.rsplit('.', 1)[-1].lower(),
            'w': rec.get('w'), 'h': rec.get('h'),
            'triage': cat,
            'section': occ_sec.get(media),
        })
    return out


def step_readings(manifest, cfg=None):
    """从缓存里把已读结果取出来（未读的缺键）。Web 层据此决定还要读哪几张。"""
    import vision
    cfg = cfg or DEFAULT
    out = {}
    todo = []
    for m in manifest:
        v = vision.get(m['sha256'], cache_dir=cfg.vision_cache_dir)
        if v:
            out[m['sha256']] = v
        else:
            todo.append(m)
    return out, todo


# ============================================================ 步骤 2：组装
def step_build_images(r, ref, cfg=None, ref_globals=None):
    """读缓存 + 分类，得到可比对的图片结构。（网络读取在 Web 层完成，这里只读缓存）"""
    import run_report as RR
    return RR.build_images(r, ref=ref, cfg=cfg)


def step_build_items(r, comp_items, cfg=None):
    import run_report as RR
    return RR.build_items(r, comp_items, cfg=cfg)


def step_link_adjacency(items, imgs):
    """把"条目 → 文档里紧邻的图片 id"算出来（位置是弱信号，但比不管强）。"""
    order2img = {}
    for im in imgs:
        for od in im['orders']:
            order2img[od] = im['id']
    for d in items:
        d['adjacent_img_ids'] = sorted({order2img[o] for o in d.get('adjacent_orders', [])
                                        if o in order2img})
    return items


# ============================================================ 步骤 3：配对
def step_match(items, imgs, ctx, cfg=None):
    """
    全局最优配对 + 推测性挂载 + 状态标注。
    返回 (res, labeled)。
    """
    cfg = cfg or DEFAULT
    # 学生主动写了"（无材料）"的条目，不参与配对——否则它会抢占本该给同类
    # 有材料条目的那张图，把真正有图的条目挤成"缺图"。
    declared_ids = [it['id'] for it in items
                    if ('无材料' in it['text']) or ('无证明材料' in it['text'])]
    _decl = set(declared_ids)
    matchable = [it for it in items if it['id'] not in _decl]

    res = matching.assign(matchable, imgs, ctx,
                          threshold=cfg.t('match'), cfg=cfg)
    res['unmatched_items'] = list(res['unmatched_items']) + declared_ids

    spec = matching.speculative_pairs(items, imgs, res, ctx, cfg=cfg)
    if spec:
        res['matches'].extend(spec)
        si = {x['item_id'] for x in spec}
        sf = {x['img_id'] for x in spec}
        res['unmatched_items'] = [i for i in res['unmatched_items'] if i not in si]
        res['unmatched_images'] = [i for i in res['unmatched_images'] if i not in sf]

    labeled = matching.label_matches(res, items, imgs, cfg=cfg)
    return res, labeled


def step_split_unmatched(items, imgs, res):
    """
    区分两类"没配上的图"：
      板块级材料 —— 体测/志愿者这类板块本来就是整块一张截图，不算异常
      真正无归属 —— 竞赛/科研/荣誉板块里没主的图，才是要人工看的
    """
    sec_has_items = {}
    for it in items:
        sec_has_items[it['section']] = sec_has_items.get(it['section'], 0) + 1
    section_level, orphan_real = [], []
    for i in res['unmatched_images']:
        sec = imgs[i].get('section')
        if sec in ('体测', '志愿', '技能', '四六级') or not sec_has_items.get(sec):
            section_level.append(i)
        else:
            orphan_real.append(i)
    res['section_level_images'] = section_level
    res['orphan_images'] = orphan_real
    return section_level, orphan_real


# ============================================================ 步骤 4：核验与兜底
def step_eligibility(r, imgs, ref, cfg=None):
    import run_report as RR
    return RR.check_eligibility(r, imgs, ref=ref, cfg=cfg)


def step_annotate(items, imgs, res, ctx, cfg=None):
    import run_report as RR
    return RR.annotate_materials(items, imgs, res, ctx, cfg=cfg)


def step_need_cloud(imgs, res):
    """哪些图"本地读不准、又没配上对"，值得升级到更强模型复核。"""
    used = {m['img_id'] for m in res['matches']}
    out = []
    for im in imgs:
        im['need_cloud_local'] = im.get('need_cloud')
        if im.get('need_cloud') and im['id'] not in used:
            out.append(im)
    return out


# ============================================================ 总装
def analyze(path, comp_items=None, ref=None, cfg=None):
    """
    跑完一个学生的完整流水线，返回 (r, items, imgs, ignored, res, labeled)。
    与旧版 `run_report.analyze_file` 的返回契约完全一致。
    """
    cfg = cfg or DEFAULT
    if ref is None:                       # 兼容旧调用：没传 ref 时退回 run_report 的全局 REF
        import run_report as _RR
        ref = getattr(_RR, 'REF', None) or {
            'comp_items': comp_items or [], 'ranking': {'by_name': {}, 'by_sid': {}}}

    r = step_parse(path)
    imgs, ignored = step_build_images(r, ref, cfg=cfg)
    items = step_build_items(r, ref.get('comp_items') or comp_items, cfg=cfg)
    step_link_adjacency(items, imgs)

    ctx = {'student_name': r['student'].get('name')}
    res, labeled = step_match(items, imgs, ctx, cfg=cfg)
    step_split_unmatched(items, imgs, res)
    res['eligibility'] = step_eligibility(r, imgs, ref, cfg=cfg)
    step_annotate(items, imgs, res, ctx, cfg=cfg)
    step_need_cloud(imgs, res)

    return r, items, imgs, ignored, res, labeled
