# -*- coding: utf-8 -*-
"""
送模型前的本地隐私遮挡。

为什么需要：成绩单、四六级成绩单、计算机等级证书上常常带**身份证号**，
这些图片会以 base64 送到外部模型服务。遮挡掉再送，风险小得多。

做法（纯本地、不联网）：
    1. 用 RapidOCR 拿到每一行文字的坐标框；
    2. 用正则识别身份证号 / 手机号 / 银行卡号 / 学号；
    3. 用 PIL 在对应坐标**画实心矩形**盖掉。

设计取舍：
- **默认关闭**（`WB_MASK_SENSITIVE=1` 打开）。遮挡是有代价的：
  可能遮掉证书编号等有用信息，而且 OCR 多跑一遍会拖慢读图。
  由使用者按自己对接的服务商可信度决定。
- 遮挡**只改送去模型的那份字节**，落盘的原始图片不动（人工复核时仍看得到原图）。
- 缓存里会标 `masked: true`，避免"某天开了遮挡、结果对不上"时无从判断。
"""
import io
import os
import re
import threading

from ..config import WebConfig

_lock = threading.Lock()
_ocr = None

# 需要遮挡的模式：(名称, 正则, 是否保留后 4 位)
PATTERNS = [
    ('身份证号', re.compile(r'(?<!\d)(\d{6})(\d{8})(\d{3})([\dXx])(?!\d)'), True),
    ('身份证号(15位)', re.compile(r'(?<!\d)(\d{6})(\d{6})(\d{3})(?!\d)'), True),
    ('手机号', re.compile(r'(?<!\d)(1[3-9]\d)(\d{4})(\d{4})(?!\d)'), True),
    ('银行卡号', re.compile(r'(?<!\d)(\d{6})(\d{6,9})(\d{4})(?!\d)'), True),
    ('学号', re.compile(r'(?<!\d)(20\d{6,10})(?!\d)'), False),
]


SETTING_KEY = 'mask_sensitive'

# 遮挡依赖 OpenCV，而 OpenCV 在**无图形界面的 Linux 服务器**上需要系统库
# libGL.so.1 / libglib-2.0.so.0。缺了就会：
#     ImportError: libGL.so.1: cannot open shared object file
# 这是很常见的部署坑，所以这里把它翻译成"照着敲一行命令"的人话。
_LAST_ERR = ''


class MaskUnavailable(RuntimeError):
    """遮挡能力不可用（通常是 OpenCV 的系统库缺失）。"""


def _friendly_import_error(e):
    msg = str(e) or ''
    if 'libGL' in msg or 'libglib' in msg or 'libGL.so' in msg:
        return (
            '遮挡功能不可用：本机缺少 OpenCV 需要的系统库（%s）。\n'
            '在服务器上执行下面这行即可修复（Ubuntu/Debian）：\n'
            '    apt-get update && apt-get install -y libgl1 libglib2.0-0\n'
            '或者改用无界面版 OpenCV：pip install opencv-python-headless'
            % msg
        )
    return '遮挡功能不可用：%s' % msg


def available():
    """
    遮挡能力是否就绪（OpenCV 能否导入）。返回 (ok, message)。
    给设置页/系统页显示用，避免"开了遮挡其实没生效"这种静默风险。
    """
    try:
        import cv2  # noqa: F401
        return True, ''
    except Exception as e:
        return False, _friendly_import_error(e)


def last_error():
    return _LAST_ERR


def enabled():
    """运行期设置优先（界面上可切换），其次环境变量。"""
    try:
        from .. import db
        return db.get_bool(SETTING_KEY, default=(os.environ.get('WB_MASK_SENSITIVE') == '1'))
    except Exception:
        return os.environ.get('WB_MASK_SENSITIVE', '0') == '1'


def _engine():
    """RapidOCR 加载较慢（秒级），进程内只加载一次。"""
    global _ocr
    if _ocr is None:
        with _lock:
            if _ocr is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _ocr = RapidOCR()
                except ImportError as e:
                    # 把 libGL 这类系统级 ImportError 翻成人话再抛出
                    raise MaskUnavailable(_friendly_import_error(e))
    return _ocr


def _mask_text(s):
    """把命中的号码变成 3302**********4417 这种可读形式（用于日志/审计）。"""
    s = str(s)
    if len(s) <= 8:
        return '*' * len(s)
    return s[:4] + '*' * (len(s) - 8) + s[-4:]


def find_sensitive(text):
    """在一段文字里找敏感号码，返回 [(类型, 原文, 遮挡后), ...]"""
    out = []
    seen = set()
    for name, pat, keep in PATTERNS:
        for m in pat.finditer(text or ''):
            raw = m.group(0)
            # 18 位身份证也会命中"16~19 位银行卡"的规则；同一串只报一次，
            # 否则日志里会出现"一个号码既是身份证又是银行卡"的噪声
            if raw in seen:
                continue
            seen.add(raw)
            out.append((name, raw, _mask_text(raw) if keep else '*' * len(raw)))
    return out


_warned = False


def _warn_once(msg):
    """遮挡不可用只提醒一次，避免每张图刷屏。"""
    global _warned
    if _warned:
        return
    _warned = True
    import sys
    sys.stderr.write('[privacy] ' + msg + '\n')


def mask_image(data, min_conf=0.5, pad=2):
    """
    对图片做遮挡。返回 (masked_bytes, hits)。
    hits: [{'kind': '身份证号', 'text': '3302**********4417', 'box': [x0,y0,x1,y1]}]
    任何异常都退回原图（绝不能因为遮挡失败就没法读图）。
    """
    global _LAST_ERR
    hits = []
    try:
        from PIL import Image, ImageDraw
        engine = _engine()
        arr = None
        try:
            import numpy as np
            im = Image.open(io.BytesIO(data))
            if im.mode != 'RGB':
                im = im.convert('RGB')
            arr = np.array(im)
        except Exception:
            return data, hits

        result, _elapse = engine(arr)
        if not result:
            return data, hits

        draw = ImageDraw.Draw(im)
        for row in result:
            try:
                box, text, conf = row[0], row[1], (row[2] if len(row) > 2 else 1.0)
            except Exception:
                continue
            if conf is not None and float(conf) < min_conf:
                continue
            found = find_sensitive(text)
            if not found:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            x0, y0 = max(0, int(min(xs)) - pad), max(0, int(min(ys)) - pad)
            x1, y1 = int(max(xs)) + pad, int(max(ys)) + pad
            draw.rectangle([x0, y0, x1, y1], fill=(0, 0, 0))
            for kind, raw, masked in found:
                hits.append({'kind': kind, 'text': masked,
                             'n': len(raw), 'box': [x0, y0, x1, y1]})

        if not hits:
            return data, hits
        buf = io.BytesIO()
        im.save(buf, format='JPEG', quality=90)
        return buf.getvalue(), hits
    except MaskUnavailable as e:
        # ★ 绝不因为遮挡坏了就不读图 —— 但要留下痕迹：
        #   否则会出现"界面显示遮挡已开启、其实一张都没遮"的静默隐私风险。
        _LAST_ERR = str(e)
        _warn_once(str(e))
        return data, hits
    except Exception:
        return data, hits


def mask_file(path):
    """便利函数：对磁盘上的图片文件做遮挡，返回 (bytes, hits)。"""
    with open(path, 'rb') as f:
        return mask_image(f.read())


def preview_mask(data):
    """只报告会遮什么，不产生图片（用于设置页"检查这张图会不会被遮"）。"""
    hits = []
    try:
        from PIL import Image
        import numpy as np
        im = Image.open(io.BytesIO(data))
        if im.mode != 'RGB':
            im = im.convert('RGB')
        result, _ = _engine()(np.array(im))
        for row in (result or []):
            try:
                text = row[1]
            except Exception:
                continue
            for kind, raw, masked in find_sensitive(text):
                hits.append({'kind': kind, 'text': masked, 'n': len(raw)})
    except MaskUnavailable as e:
        return {'ok': False, 'error': str(e), 'hits': []}
    except ImportError as e:
        return {'ok': False, 'error': _friendly_import_error(e), 'hits': []}
    except Exception as e:
        return {'ok': False, 'error': '%s: %s' % (type(e).__name__, e), 'hits': []}
    return {'ok': True, 'error': '', 'hits': hits}
