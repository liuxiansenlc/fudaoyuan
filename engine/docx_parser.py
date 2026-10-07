# -*- coding: utf-8 -*-
"""
docx 解析模块
把学生的 Word 申报材料拆成结构化数据：板块 / 条目 / 图片（含文档内出现顺序）。

设计要点（来自真实材料实测）：
1. 板块标题样式极不统一（实测有 pStyle=2 / 4 / TOC1，甚至无样式），
   因此板块识别只依赖文本正则，不依赖样式。
2. 图片有 inline 和 anchor（浮动）两种。实测有学生的材料 100% 是浮动图片，
   锚点段落位置不可信 —— 所以本模块只记录"出现顺序"，绝不把段落位置当作配对依据。
3. 同一张图片可能被引用多次（复用一个 media 文件），
   引用次数（occurrence）和图片文件（media）要分开统计。
"""
import os
import re
import zipfile
import hashlib
import xml.etree.ElementTree as ET

from settings import DEFAULT

NS = {
    'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'v': 'urn:schemas-microsoft-com:vml',
    'pkgrel': 'http://schemas.openxmlformats.org/package/2006/relationships',
}
def q(prefix, tag):
    return '{%s}%s' % (NS[prefix], tag)

W_P = q('w', 'p')
W_TBL = q('w', 'tbl')
W_T = q('w', 't')
W_BR = q('w', 'br')
W_CR = q('w', 'cr')
W_TAB = q('w', 'tab')
W_DRAWING = q('w', 'drawing')
W_PICT = q('w', 'pict')
W_OBJECT = q('w', 'object')
W_TXBX = q('w', 'txbxContent')
W_PSTYLE = q('w', 'pStyle')
W_VAL = q('w', 'val')
A_BLIP = q('a', 'blip')
V_IMAGEDATA = q('v', 'imagedata')
R_EMBED = q('r', 'embed')
R_ID = q('r', 'id')

# 板块标题：一、二、…… 十、
RE_SECTION = re.compile(r'^\s*([一二三四五六七八九十]+)\s*[、\.]\s*(.*)$')
# 板块关键词（用于学生漏写"一、"时的兜底识别）
SECTION_KEYWORDS = [
    ('奖学金', ['曾获奖学金', '奖学金情况', '奖学金', '获奖学金']),
    ('竞赛', ['学科竞赛', '竞赛获奖', '学科竞赛获奖']),
    ('科研', ['科研创新', '科研情况', '科研']),
    ('荣誉', ['荣誉情况', '荣誉', '获奖情况']),
    ('体测', ['体测', '体质测试', '体能测试']),
    ('志愿', ['志愿者', '志愿服务', '志愿时长']),
    ('技能', ['技能证书', '证书情况']),
    ('实践', ['社会实践', '实践情况']),
    ('四六级', ['四六级', '英语等级']),
]


def match_section_keyword(text):
    """按关键词兜底匹配板块，返回归一化的板块键或 None"""
    t = text.strip()
    if len(t) > 30:
        return None
    for key, words in SECTION_KEYWORDS:
        for w in words:
            if t == w or t.startswith(w):
                return key
    return None
# 子级： （一）国家级 / (二) 省级
RE_SUBLEVEL = re.compile(r'^\s*[（(]\s*([一二三四五六七八九十]+)\s*[)）]\s*(.*)$')
# 条目序号： 1. / 1、 / （1） / 1）
RE_ITEM = re.compile(r'^\s*(?:(\d{1,2})\s*[\.、）)]|[（(]\s*(\d{1,2})\s*[)）])')
# 圈码条目： ①②③
RE_ITEM_CIRCLED = re.compile(r'^\s*[①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳]')

LEVEL_WORDS = ['国家级', '省级', '市级', '校级', '院级', '国际级']

# 标准模板的六板块顺序（用于学生把标题写乱/漏写时兜底）
ORDINAL_KEY = {'一': '奖学金', '二': '竞赛', '三': '科研',
               '四': '荣誉', '五': '体测', '六': '志愿'}


def section_key(ordinal, rest):
    """
    把板块标题归一成稳定的键：奖学金/竞赛/科研/荣誉/体测/志愿/技能/实践/四六级。
    优先按关键词识别（学生自加的板块也能正确归类），
    关键词识别不到时，按序号落到标准模板顺序上。
    """
    kw = match_section_keyword(rest)
    if kw:
        return kw
    return ORDINAL_KEY.get(ordinal) or (rest.strip() or ordinal)


# ---------------------------------------------------------------- 文本抽取
def _extract_para_text(p):
    """抽取段落文本；跳过文本框内部文字，换行符转义为 \n"""
    parts = []

    def walk(el, inside_drawing):
        for ch in el:
            tag = ch.tag
            if tag == W_T:
                if not inside_drawing:
                    parts.append(ch.text or '')
            elif tag in (W_BR, W_CR):
                if not inside_drawing:
                    parts.append('\n')
            elif tag == W_TAB:
                if not inside_drawing:
                    parts.append('\t')
            elif tag in (W_DRAWING, W_PICT, W_OBJECT, W_TXBX):
                walk(ch, True)
            else:
                walk(ch, inside_drawing)

    walk(p, False)
    return ''.join(parts)


def _extract_para_images(p):
    """抽取段落内所有图片引用，返回 [(rId, kind)]，kind 为 inline/anchor/vml"""
    out = []
    for el in p.iter():
        if el.tag == A_BLIP:
            rid = el.get(R_EMBED)
            if rid:
                # 判断父级是 inline 还是 anchor
                out.append((rid, 'drawing'))
        elif el.tag == V_IMAGEDATA:
            rid = el.get(R_ID)
            if rid:
                out.append((rid, 'vml'))
    return out


def _rels_map(z):
    """word/_rels/document.xml.rels → {rId: 'media/image1.jpeg'}"""
    try:
        raw = z.read('word/_rels/document.xml.rels')
    except KeyError:
        return {}
    root = ET.fromstring(raw)
    m = {}
    for rel in root:
        rid = rel.get('Id')
        target = rel.get('Target') or ''
        if 'media/' in target or target.lower().endswith(
                ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.emf', '.wmf', '.tif', '.tiff')):
            name = target.split('/')[-1]
            m[rid] = 'word/media/' + name
    return m


# ---------------------------------------------------------------- 图片元信息
def _image_size(data):
    """从图片字节推断 (宽, 高)，支持 jpeg/png/gif/bmp"""
    try:
        if data[:2] == b'\xff\xd8':
            i = 2
            n = len(data)
            while i < n - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h = (data[i + 5] << 8) | data[i + 6]
                    w = (data[i + 7] << 8) | data[i + 8]
                    return w, h
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = (data[i + 2] << 8) | data[i + 3]
                i += 2 + seg
        elif data[:8] == b'\x89PNG\r\n\x1a\n':
            w = int.from_bytes(data[16:20], 'big')
            h = int.from_bytes(data[20:24], 'big')
            return w, h
        elif data[:6] in (b'GIF87a', b'GIF89a'):
            w = int.from_bytes(data[6:8], 'little')
            h = int.from_bytes(data[8:10], 'little')
            return w, h
        elif data[:2] == b'BM':
            w = int.from_bytes(data[18:22], 'little')
            h = int.from_bytes(data[22:26], 'little')
            return w, h
    except Exception:
        pass
    return None, None


# ---------------------------------------------------------------- 主解析
def parse_docx(path):
    """
    返回:
    {
      'file', 'student': {class_no, name, title},
      'blocks': [ {kind, ...} ... ]   # 按文档顺序
      'items':  [ {id, section, section_title, sublevel, level, text, block_idx} ]
      'occurrences': [ {order, media, block_idx, para_text} ]   # 图片出现顺序
      'media': { media_path: {sha256, w, h, bytes, refs:[order,...]} }
      'warnings': [...]
    }
    """
    z = zipfile.ZipFile(path)
    doc = ET.fromstring(z.read('word/document.xml'))
    body = doc.find(q('w', 'body'))
    rid2media = _rels_map(z)
    warnings = []

    blocks = []
    occurrences = []
    media = {}

    def add_image(rid, block_idx, para_text):
        mpath = rid2media.get(rid)
        if not mpath:
            warnings.append('未找到图片关系: %s' % rid)
            return
        rec = media.get(mpath)
        if rec is None:
            try:
                data = z.read(mpath)
            except KeyError:
                warnings.append('媒体文件缺失: %s' % mpath)
                return
            w, h = _image_size(data)
            rec = {
                'media': mpath,
                'sha256': hashlib.sha256(data).hexdigest(),
                'w': w, 'h': h,
                'bytes': len(data),
                'data': data,
                'refs': [],
            }
            media[mpath] = rec
        order = len(occurrences)
        occurrences.append({
            'order': order,
            'media': mpath,
            'block_idx': block_idx,
            'para_text': para_text,
        })
        rec['refs'].append(order)

    # --- 按文档顺序遍历 body
    idx = 0
    for el in body:
        if el.tag == W_P:
            text = _extract_para_text(el)
            style_el = el.find(q('w', 'pPr'))
            style = None
            if style_el is not None:
                ps = style_el.find(W_PSTYLE)
                if ps is not None:
                    style = ps.get(W_VAL)
            imgs = _extract_para_images(el)
            block = {
                'kind': 'para',
                'block_idx': idx,
                'style': style,
                'text': text,
                'n_images': len(imgs),
            }
            blocks.append(block)
            for rid, _kind in imgs:
                add_image(rid, idx, text.strip())
            idx += 1
        elif el.tag == W_TBL:
            # 表格整体作为一个块，图片归到该块
            texts = []
            for p in el.iter(W_P):
                t = _extract_para_text(p).strip()
                if t:
                    texts.append(t)
            imgs = []
            for p in el.iter(W_P):
                imgs.extend(_extract_para_images(p))
            block = {
                'kind': 'table',
                'block_idx': idx,
                'style': None,
                'text': ' | '.join(texts),
                'n_images': len(imgs),
            }
            blocks.append(block)
            for rid, _kind in imgs:
                add_image(rid, idx, '')
            idx += 1

    # --- 学生身份
    student = _guess_student(path, blocks)

    # --- 板块切分
    items = []
    first_section_block = None
    for b in blocks:
        if b['kind'] != 'para':
            continue
        m = RE_SECTION.match(b['text'].strip())
        if m and len(b['text'].strip()) <= 60:
            first_section_block = b['block_idx']
            break

    cur_section = None
    cur_section_title = None
    cur_sub = None
    cur_level = None
    item_id = 0
    if first_section_block is None:
        warnings.append('未识别到任何板块标题（一、二、…），请人工检查')

    for b in blocks:
        t = b['text'].strip()
        if b['kind'] == 'para':
            m = RE_SECTION.match(t)
            if m and len(t) <= 60:
                cur_section = section_key(m.group(1), m.group(2))
                cur_section_title = t
                cur_sub = None
                cur_level = None
                continue
            # 兜底：学生漏写"一、"等前缀时，按关键词识别板块
            if cur_section is None or len(t) <= 16:
                kw = match_section_keyword(t)
                if kw and not RE_ITEM.match(t) and not RE_ITEM_CIRCLED.match(t):
                    cur_section = kw
                    cur_section_title = t
                    cur_sub = None
                    cur_level = None
                    continue
            if cur_section is None:
                continue  # 目录区 / 标题区，跳过
            sm = RE_SUBLEVEL.match(t)
            if sm:
                cur_sub = t
                for wd in LEVEL_WORDS:
                    if wd in t:
                        cur_level = wd
                continue
            # 兜底：学生把级别单独写成一行（"校级：" / "国家级" / "省级"）
            # 只有冒号后面没有实质内容时才算"级别标记"，
            # 否则像"国家级：2024天梯赛三等奖"这种要当作奖项条目。
            bare = t.rstrip('：:').strip()
            if bare in LEVEL_WORDS:
                cur_level = bare
                cur_sub = t
                continue
            is_level_marker = False
            for wd in LEVEL_WORDS:
                for sep in ('：', ':'):
                    if t.startswith(wd + sep):
                        if len(t[len(wd) + 1:].strip()) <= 3:
                            cur_level = wd
                            cur_sub = t
                            is_level_marker = True
                        break
                if is_level_marker:
                    break
            if is_level_marker:
                continue
            # 条目判定
            if not t:
                continue
            is_item = bool(RE_ITEM.match(t) or RE_ITEM_CIRCLED.match(t))
            if not is_item:
                # 无编号但有实质内容，且像奖项/荣誉描述
                if len(t) >= 6 and not t.startswith(('无', '（无', '(无')):
                    is_item = True
                # 子级里的"国家级"这种裸级别词也不算条目
                if any(t == wd for wd in LEVEL_WORDS):
                    is_item = False
            if is_item:
                if _looks_like_junk(t):
                    continue
                lvl = cur_level
                for wd in LEVEL_WORDS:
                    if wd in t:
                        lvl = wd
                        break
                items.append({
                    'id': item_id,
                    'section': cur_section,
                    'section_title': cur_section_title,
                    'sublevel': cur_sub,
                    'level': lvl,
                    'text': t,
                    'block_idx': b['block_idx'],
                })
                item_id += 1
        else:
            # 表格里的内容，按文本行尝试抽条目
            if cur_section is None:
                continue
            for line in b['text'].split('|'):
                line = line.strip()
                if not line or any(line == wd for wd in LEVEL_WORDS):
                    continue
                if RE_ITEM.match(line) or RE_ITEM_CIRCLED.match(line):
                    items.append({
                        'id': item_id,
                        'section': cur_section,
                        'section_title': cur_section_title,
                        'sublevel': cur_sub,
                        'level': cur_level,
                        'text': line,
                        'block_idx': b['block_idx'],
                    })
                    item_id += 1

    return {
        'file': path,
        'student': student,
        'blocks': assign_block_sections(blocks),
        'items': items,
        'occurrences': occurrences,
        'media': media,
        'warnings': warnings,
    }


def _parse_identity_from_text(s):
    """从 '20220826班胡珍华国家奖学金申请支撑材料' 这类文本里解析 (班级, 姓名)"""
    if not s:
        return None, None
    m = re.search(r'(20\d{6})\s*班\s*([\u4e00-\u9fa5A-Za-z]{1,6}?)'
                  r'(?=国家|励志|校级|院级|申请|支撑|奖|资助|\.|$|\s)', s)
    if m:
        return m.group(1), m.group(2)
    # 退一步：只要班级号
    m2 = re.search(r'(20\d{6})\s*班', s)
    if m2:
        return m2.group(1), None
    return None, None


# 噪声文本：文件名、网址等，不是奖项条目
RE_JUNK_ITEM = re.compile(
    r'^[\w\-. ()（）]+\.(?:pdf|docx?|xlsx?|pptx?|jpg|jpeg|png|zip|rar)$', re.I)


def _looks_like_junk(t):
    if RE_JUNK_ITEM.match(t):
        return True
    if re.match(r'^https?://', t) or t.endswith('.com'):
        return True
    return len(t) < 4


def assign_block_sections(blocks):
    """给每个块标注它属于哪个板块（图片归属板块要用）"""
    cur = None
    for b in blocks:
        if b['kind'] == 'para':
            t = b['text'].strip()
            m = RE_SECTION.match(t)
            if m and len(t) <= 60:
                cur = section_key(m.group(1), m.group(2))
            else:
                kw = match_section_keyword(t)
                if kw and not RE_ITEM.match(t) and not RE_ITEM_CIRCLED.match(t):
                    cur = kw
        b['section'] = cur
    return blocks


def _guess_student(path, blocks):
    """
    身份判定策略（来自实测教训）：
    文件名是学生自己重命名的，相对可信，作为主来源；
    文档内标题经常是复制模板后没改干净的（实测有人的标题里还是 "xxx" 占位符、
    有人的班级号还是上一届的），因此只作交叉校验，不一致时记为警告。
    """
    fname = os.path.basename(path)
    stem = re.sub(r'\.docx?$', '', fname)
    class_no, name = _parse_identity_from_text(stem)

    title = None
    for b in blocks[:6]:
        t = (b.get('text') or '').strip()
        if t and ('支撑材料' in t or '申请' in t) and len(t) <= 60:
            title = t
            break

    t_class, t_name = _parse_identity_from_text(title or '')
    mismatches = []
    if title:
        if t_class and class_no and t_class != class_no:
            mismatches.append('文档标题班级号(%s)与文件名(%s)不一致' % (t_class, class_no))
        if t_name and name and t_name != name:
            mismatches.append('文档标题姓名(%s)与文件名(%s)不一致' % (t_name, name))
        if t_name and re.fullmatch(r'[xX*]{2,}', t_name.strip()):
            mismatches.append('文档标题姓名仍是占位符 "%s"，学生未改' % t_name)
        if not t_name and t_class is None:
            mismatches.append('文档标题无法解析出班级与姓名')

    return {
        'class_no': class_no,
        'name': name,
        'title': title,
        'filename': fname,
        'title_class_no': t_class,
        'title_name': t_name,
        'identity_warnings': mismatches,
    }


# ---------------------------------------------------------------- 图片分诊
def triage_media(rec, cfg=None):
    """
    图片分诊：判断这张图有没有必要送去读。
    返回 (类别, 说明)
    类别: certificate / long_strip / tiny / unusual
    """
    w, h = rec.get('w'), rec.get('h')
    if not w or not h:
        return 'unusual', '无法读取尺寸'
    long_side = max(w, h)
    short_side = min(w, h)
    aspect = w / h if h else 0

    _tg = (cfg or DEFAULT).triage
    _amax = _tg.get('aspect_max', 6.0)
    if aspect >= _amax or aspect <= 1 / _amax:
        return 'long_strip', '长宽比 %.1f:1，疑似横条截图或装饰图' % (aspect if aspect >= 1 else 1 / aspect)
    if short_side < _tg.get('short_min', 180) or long_side < _tg.get('long_min', 400):
        return 'tiny', '分辨率过低 (%dx%d)' % (w, h)
    _au = _tg.get('aspect_unusual', 2.6)
    if aspect > _au or aspect < 1 / _au:
        return 'unusual', '比例异常 %dx%d' % (w, h)
    return 'certificate', '正常 (%dx%d)' % (w, h)


if __name__ == '__main__':
    import sys
    import json
    p = sys.argv[1]
    r = parse_docx(p)
    print('学生:', json.dumps(r['student'], ensure_ascii=False))
    print('条目数:', len(r['items']), ' 图片引用数:', len(r['occurrences']), ' 图片文件数:', len(r['media']))
    print('警告:', r['warnings'])
    print('\n--- 图片分诊 ---')
    for m, rec in r['media'].items():
        cat, why = triage_media(rec)
        print('  %-24s %-12s x%-3d %s' % (m.split('/')[-1], cat, len(rec['refs']), why))
