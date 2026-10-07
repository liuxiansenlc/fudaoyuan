# -*- coding: utf-8 -*-
"""
本地 OCR 层（可插拔）

策略：本地优先，读不准才上云。
- 本地用 RapidOCR（ONNX，纯 CPU，离线，证书图片不出本机）
- 按图片 sha256 缓存结果，同一张图永不重复识别（跨学生、跨年份复用）
- 每次识别后给出"可信度评估"，低可信的图片标记为需要云端复核
"""
import os
import io
import json
import time
import hashlib

import numpy as np
from PIL import Image

from settings import DEFAULT

# 缓存目录统一由 EngineConfig 决定（默认与旧版一致），不再是各自拼接
CACHE_DIR = None


def _cache_dir(cache_dir=None):
    return cache_dir or CACHE_DIR or DEFAULT.ocr_cache_dir

_OCR = None


def get_ocr():
    global _OCR
    if _OCR is None:
        from rapidocr_onnxruntime import RapidOCR
        _OCR = RapidOCR()
    return _OCR


def _cache_path(sha, cache_dir=None):
    d = _cache_dir(cache_dir)
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, sha + '.json')


def _load_cache(sha, cache_dir=None):
    p = _cache_path(sha, cache_dir)
    if os.path.exists(p):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None
    return None


def _save_cache(sha, obj, cache_dir=None):
    try:
        with open(_cache_path(sha, cache_dir), 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


def preprocess(img, target_long=1400):
    """
    轻量预处理：灰度 + 自适应放大 + 自动对比度。
    实测学生图片长边多在 600~1400，偏小的放大后 OCR 更稳。
    """
    w, h = img.size
    long_side = max(w, h)
    if long_side < target_long:
        scale = min(target_long / long_side, 2.5)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
    return img


def ocr_image_bytes(data, sha=None, use_cache=True, cache_dir=None):
    """
    对一张图片做本地 OCR。
    返回 {
      'lines': [{'text','score'}], 'text': '拼接全文',
      'confidence': 平均置信度, 'n_lines': 行数,
      'trust': 'high'|'medium'|'low',   # 本地结果可信度
      'need_cloud': bool,               # 是否建议送云端复核
      'elapsed': 秒, 'cached': bool
    }
    """
    if sha is None:
        sha = hashlib.sha256(data).hexdigest()
    if use_cache:
        c = _load_cache(sha, cache_dir)
        if c is not None:
            c['cached'] = True
            return c

    t0 = time.time()
    img = Image.open(io.BytesIO(data)).convert('RGB')
    img = preprocess(img)
    res, _ = get_ocr()(np.array(img))

    lines = []
    if res:
        for it in res:
            txt = str(it[1]).strip()
            try:
                sc = float(it[2])
            except Exception:
                sc = 0.0
            if txt:
                lines.append({'text': txt, 'score': round(sc, 4)})

    scores = [l['score'] for l in lines]
    avg = sum(scores) / len(scores) if scores else 0.0
    total_chars = sum(len(l['text']) for l in lines)

    # 可信度评估：行数、平均置信度、总字数、低置信行占比
    low_ratio = (sum(1 for s in scores if s < 0.6) / len(scores)) if scores else 1.0
    if total_chars < 8 or avg < 0.55:
        trust = 'low'
    elif avg >= 0.75 and low_ratio <= 0.25 and total_chars >= 20:
        trust = 'high'
    else:
        trust = 'medium'

    out = {
        'sha256': sha,
        'lines': lines,
        'text': '\n'.join(l['text'] for l in lines),
        'confidence': round(avg, 4),
        'n_lines': len(lines),
        'total_chars': total_chars,
        'low_conf_ratio': round(low_ratio, 3),
        'trust': trust,
        'need_cloud': trust != 'high',
        'elapsed': round(time.time() - t0, 2),
        'cached': False,
    }
    if use_cache:
        _save_cache(sha, out, cache_dir)
    return out


def is_blank_or_icon(rec, ocr_result):
    """判断这张图是否属于"无信息量"，不值得进入配对"""
    w, h = rec.get('w') or 0, rec.get('h') or 0
    if w and h and min(w, h) < 150:
        return True, '尺寸过小 (%dx%d)' % (w, h)
    if ocr_result['total_chars'] <= 4 and rec.get('bytes', 0) < 6000:
        return True, '几乎无文字且文件极小，疑似图标/装饰'
    return False, ''
