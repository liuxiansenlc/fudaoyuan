# -*- coding: utf-8 -*-
"""
参考材料的「AI 智能识别」：让大模型读取上传的核对材料文件，
自动分类（A 类竞赛表 / 成绩排名表），并标准化落盘成引擎能直接解析的标准 .xlsx。

为什么要有这一层：
    辅导员上传的核对文件不一定是标准格式——列名五花八门、表头不在第一行、
    甚至混了合并单元格。引擎侧的 load_competitions / load_ranking 只认固定的
    中文表头（竞赛名称/级别/竞赛类别、学号/姓名/平均绩点…），格式一偏就读不出来。

    这里让大模型「读一遍表格、吐一份标准化 JSON」，再用 openpyxl 把 JSON 重写成
    引擎认识的固定表头，替换原文件作为 refdata 的 stored_path。这样引擎完全不用改，
    判定逻辑依旧离线、确定、可复现。

分层约束（沿用 vision_client 的铁律）：
    - 联网 + 大模型调用集中在本模块（Web 层），引擎永远不联网。
    - 只在「AI 解析成功」时才落盘标准文件、写库；失败就返回错误，绝不写半成品。
"""
import os
import io
import json
import csv

import openpyxl

from ..config import WebConfig
from ..security import record_usage

# 引擎认识的中文表头（与 engine/reference.py 的 RANK_COLS / load_competitions 对齐）。
# 注意：这是「中文表头 → 引擎内部键」的映射，重写标准文件时用中文表头那一列。
RANK_HEADERS = {
    '学号': 'sid', '姓名': 'name', '总分': 'total', '总应获得学分': 'should_credit',
    '门数': 'n_course', '总学分': 'credit', '获得学分': 'earned_credit',
    '不及格学分': 'fail_credit', '通过率': 'pass_rate',
    '算术平均分': 'avg_score', '算术平均分排名': 'avg_score_rank',
    '学分加权平均分': 'weighted_avg', '学分加权平均分排名': 'weighted_avg_rank',
    '平均绩点': 'gpa', '平均绩点排名': 'gpa_rank',
    '平均学分绩点': 'weighted_gpa', '平均学分绩点排名': 'weighted_gpa_rank',
    '学分绩点和': 'gpa_sum', '学分绩点和排名': 'gpa_sum_rank',
    '不及格门次': 'fail_count', '专业': 'major', '班级': 'class_no', '备注': 'note',
}
COMP_HEADERS = {'竞赛名称': 'name', '级别': 'level', '竞赛类别': 'category'}

# 标准文件固定表头顺序
COMP_HEADER_ROW = ['竞赛名称', '级别', '竞赛类别']
RANK_HEADER_ROW = ['学号', '姓名', '班级', '专业', '平均绩点', '平均绩点排名',
                   '平均学分绩点', '平均学分绩点排名', '算术平均分', '算术平均分排名',
                   '学分加权平均分', '学分加权平均分排名', '总学分', '获得学分',
                   '不及格学分', '不及格门次', '备注']


# ------------------------------------------------------------------ 读取原始表格为文本
def read_tabular_text(path, max_rows=1200, max_cells=40):
    """
    把 xls / xlsx / csv 读成一段「表格文本」，喂给大模型理解。

    不引入 pandas：xlsx/csv 用 openpyxl / csv，xls 用 xlrd。
    ★ **遍历所有工作表**：成绩表常按班级/专业分 sheet，只读第一个会漏掉其余班级
      （和引擎侧 load_ranking 是同一个坑）。多 sheet 时插入 '### 工作表：xxx' 分隔。
    输出用 TSV 形式（制表符分隔），截断超长单元格，控制 token 成本。
    """
    ext = os.path.splitext(path)[1].lower()
    blocks = []                      # [(sheet_label, [[cells]])]
    try:
        if ext in ('.xlsx', '.xlsm'):
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                for ws in wb.worksheets:
                    rows = []
                    for i, r in enumerate(ws.iter_rows(values_only=True)):
                        if i >= max_rows:
                            break
                        rows.append([_cell(c) for c in r[:max_cells]])
                    blocks.append((ws.title, rows))
            finally:
                wb.close()
        elif ext == '.xls':
            import xlrd
            book = xlrd.open_workbook(path)
            for sh in book.sheets():
                rows = []
                for i in range(min(sh.nrows, max_rows)):
                    rows.append([_cell(sh.cell_value(i, j)) for j in range(min(sh.ncols, max_cells))])
                blocks.append((sh.name, rows))
        elif ext == '.csv':
            rows = []
            with open(path, 'r', encoding='utf-8-sig', errors='replace', newline='') as f:
                rd = csv.reader(f)
                for i, r in enumerate(rd):
                    if i >= max_rows:
                        break
                    rows.append([_cell(c) for c in r[:max_cells]])
            blocks.append(('', rows))
        else:
            raise ValueError('不支持的参考材料格式：%s' % ext)
    except Exception as e:
        raise ValueError('读取表格失败：%s' % e)

    multi = len(blocks) > 1
    parts = []
    for label, rows in blocks:
        rows = [r for r in rows if any((c or '').strip() for c in r)]   # 去掉全空行
        if not rows:
            continue
        if multi:
            parts.append('### 工作表：%s' % (label or 'Sheet'))
        parts.extend('\t'.join(r) for r in rows)
    if not parts:
        raise ValueError('表格里没有可读取的内容')
    text = '\n'.join(parts)
    if len(text) > 60000:
        text = text[:60000] + '\n…（内容过长已截断）'
    return text


def _cell(v):
    if v is None:
        return ''
    s = str(v).strip()
    # 数字去 .0 尾巴（学号、绩点等）
    if s.endswith('.0') and s[:-2].isdigit():
        s = s[:-2]
    if len(s) > 120:
        s = s[:120] + '…'
    return s


# ------------------------------------------------------------------ 大模型解析
SYSTEM = """你是奖学金申报材料的「核对数据」整理员。用户会给你一段从 Excel/CSV 表格读出来的文本（制表符分隔，第一行可能是表头也可能不是）。

你要判断这张表属于哪一类，并把内容整理成结构化 JSON。只输出一个 JSON 对象，不要任何解释、不要 markdown 代码块。

分类与输出格式：

1. 如果这张表是「A 类竞赛 / 学科竞赛申报汇总表」（内容是各种竞赛名称、级别、类别）：
   {"kind": "competition", "records": [{"name": "竞赛全称", "level": "国家级/省级/校级/院级", "category": "A+/A/A-/其他"}]}
   - name 用竞赛的标准全称；level 与 category 若表中没有就填 ""。

2. 如果这张表是「学生成绩排名表」（每行是一个学生，有学号、姓名、绩点、排名等）：
   {"kind": "ranking", "records": [{"sid": "学号", "name": "姓名", "class_no": "班级", "major": "专业", "gpa": "平均绩点", "gpa_rank": "平均绩点排名", "weighted_gpa": "平均学分绩点", "weighted_gpa_rank": "平均学分绩点排名", "avg_score": "算术平均分", "avg_score_rank": "算术平均分排名", "weighted_avg": "学分加权平均分", "weighted_avg_rank": "学分加权平均分排名", "credit": "总学分", "earned_credit": "获得学分", "fail_credit": "不及格学分", "fail_count": "不及格门次"}]}
   - 数字字段请输出纯数字字符串（去掉单位、百分号、排名里的「第」「名」等），读不出的字段填 ""。
   - 学号只保留数字；姓名只写真实出现的人名。

3. 如果两者都不是（无法归类），输出：
   {"kind": "other", "reason": "一句话说明为什么无法归类"}

要求：
- 只提取表中真实存在的信息，绝不编造。
- 表头不在第一行时要先找到真正的表头行。
- 合并单元格、多级表头请按语义归位到正确字段。
- 忽略表里的说明行、合计行、空行。
- 若某字段在整张表里都不存在，就不要输出那个键（或填空字符串）。
"""

USER = """下面是表格内容：

{text}

请按要求判断类别并输出 JSON。"""


def ai_parse(model_row, table_text):
    """
    调用大模型，返回 (parsed, error)。parsed 是 {kind, records|reason}。
    纯文本任务，复用 vision_client 的 chat_text（同一套模型配置）。
    """
    from . import vision_client as VC
    if not model_row:
        return None, '尚未配置模型，无法使用 AI 智能识别'
    text, err = VC.chat_text(model_row, SYSTEM, USER.format(text=table_text),
                             timeout=180, json_mode=True)
    if err:
        return None, err
    try:
        obj = VC.json_from_text(text)
    except Exception as e:
        return None, '模型返回不是合法 JSON：%s' % e
    if not isinstance(obj, dict):
        return None, '模型返回结构异常'
    kind = str(obj.get('kind') or '').strip().lower()
    if kind not in ('competition', 'ranking'):
        reason = str(obj.get('reason') or '未能识别表格类型')
        return None, '无法归类：%s' % reason
    records = obj.get('records')
    if not isinstance(records, list) or not records:
        return None, '模型未提取到有效记录'
    # 记一次文本调用（images=0）
    try:
        record_usage(model_row.get('model') or '', images=0, calls=1)
    except Exception:
        pass
    return {'kind': kind, 'records': records}, None


# ------------------------------------------------------------------ 标准化落盘
def materialize(records, kind, dest_dir, base_name):
    """
    把 AI 提取的记录重写成引擎认识的标准 .xlsx。
    返回 (stored_path, n_records)。
    """
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, base_name)

    wb = openpyxl.Workbook()
    ws = wb.active

    if kind == 'competition':
        ws.append(COMP_HEADER_ROW)
        n = 0
        for r in records:
            if not isinstance(r, dict):
                continue
            nm = str(r.get('name') or '').strip()
            if len(nm) < 3:
                continue
            ws.append([nm, str(r.get('level') or '').strip(),
                       str(r.get('category') or '').strip()])
            n += 1
    else:  # ranking
        ws.append(RANK_HEADER_ROW)
        # 记录里用的是英文键（模型契约），落到标准表头
        n = 0
        for r in records:
            if not isinstance(r, dict):
                continue
            sid = str(r.get('sid') or '').strip().rstrip('.0')
            if not sid:
                continue
            row = []
            for h in RANK_HEADER_ROW:
                key = RANK_HEADERS.get(h)          # 中文表头 → 英文键
                val = r.get(key) if key else None  # 优先取英文键
                if val is None and h in r:         # 模型若给中文键也兼容
                    val = r[h]
                row.append(_cell(val))
            ws.append(row)
            n += 1

    if n == 0:
        wb.close()
        return None, 0

    wb.save(dest)
    wb.close()
    return dest, n


# ------------------------------------------------------------------ 一键入口
def recognize_and_store(path, kind_hint, model_row, dest_dir, base_name, note=''):
    """
    完整流程：读表格 → AI 解析 → 标准化落盘。
    返回 (kind, stored_path, n, note) 或抛异常。
    kind_hint 是界面上用户选的目标类型（competition/ranking），
    仅作为提示参考；AI 分类结果为准（更稳定）。
    """
    table_text = read_tabular_text(path)
    parsed, err = ai_parse(model_row, table_text)
    if err:
        raise ValueError(err)
    kind = parsed['kind']
    stored, n = materialize(parsed['records'], kind, dest_dir, base_name)
    if not stored or not n:
        raise ValueError('AI 识别后没有可用的记录')
    ai_note = ('AI 智能识别 · 原文件：%s' % os.path.basename(path)) + (('；' + note) if note else '')
    return kind, stored, n, ai_note
