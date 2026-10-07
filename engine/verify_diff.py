# -*- coding: utf-8 -*-
"""
重构期间的"零差异"守护工具。

用法：
    python verify_diff.py baseline        # 把当前 out/result.json 存为基线
    python verify_diff.py check           # 与基线逐字段比对，打印差异并在有差异时退出码=1

设计要点：result.json 里含浮点数（分数），统一四舍五入到 4 位再比，
避免纯粹的浮点表示差异被当成"结论变了"。
"""
import os
import sys
import json

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'out')
CUR = os.path.join(OUT, 'result.json')
BASE = os.path.join(OUT, '_baseline_result.json')


def _round(o, nd=4):
    if isinstance(o, float):
        return round(o, nd)
    if isinstance(o, dict):
        return {k: _round(v, nd) for k, v in o.items()}
    if isinstance(o, list):
        return [_round(v, nd) for v in o]
    return o


def _load(p):
    with open(p, encoding='utf-8') as f:
        return _round(json.load(f))


def diff(a, b, path='', out=None, limit=60):
    """递归找差异，返回 [(路径, 基线值, 新值)]"""
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    if type(a) is not type(b):
        out.append((path, repr(a)[:120], repr(b)[:120]))
        return out
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append((path + '.' + k, '<缺失>', repr(b[k])[:120]))
            elif k not in b:
                out.append((path + '.' + k, repr(a[k])[:120], '<缺失>'))
            else:
                diff(a[k], b[k], path + '.' + k, out, limit)
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append((path + '.__len__', str(len(a)), str(len(b))))
        for i in range(min(len(a), len(b))):
            diff(a[i], b[i], path + '[%d]' % i, out, limit)
    else:
        if a != b:
            out.append((path, repr(a)[:120], repr(b)[:120]))
    return out


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if mode == 'baseline':
        data = _load(CUR)
        with open(BASE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        n = len(data)
        items = sum(len(s.get('matches', [])) for s in data)
        print('基线已保存：%d 名学生 / %d 条匹配 → %s' % (n, items, BASE))
        return 0

    if not os.path.exists(BASE):
        print('!! 还没有基线，先跑：python verify_diff.py baseline')
        return 2
    a, b = _load(BASE), _load(CUR)
    d = diff(a, b)
    if not d:
        print('✅ 零差异：result.json 与基线完全一致')
        return 0
    print('❌ 发现 %d 处差异（最多显示 60 处）：' % len(d))
    for p, x, y in d:
        print('   %-60s 基线=%-40s 新=%s' % (p[:60], x[:40], y[:40]))
    return 1


if __name__ == '__main__':
    sys.exit(main())
