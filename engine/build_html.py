# -*- coding: utf-8 -*-
"""
生成审核工作台页面（单页 HTML + 图片目录）。

读 run_report.py 产出的 result.json，把证书图片导出到 out/images/，
生成一个可以直接双击打开的审核页：
  - 顶部总览：每个学生的问题数，按严重程度排序
  - 每人详情：左奖项条目、右自动配对的证明图，颜色标注配对状态与理由
  - 未归属图片单独列出（可能是学生漏写的奖项）
"""
import os
import re
import sys
import json
import html
import zipfile
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, '..', 'out')
IMG_DIR = os.path.join(OUT, 'images')

# 模板要求的六大板块（顺序即模板顺序）
TPL_SECTIONS = [
    ('奖学金', '一、曾获奖学金'),
    ('竞赛',   '二、学科竞赛获奖（A类需注明）'),
    ('科研',   '三、科研创新'),
    ('荣誉',   '四、荣誉情况（校级及以上）'),
    ('体测',   '五、体测'),
    ('志愿',   '六、志愿者时长'),
]
# 模板之外、但学生确实交了的材料
EXTRA_SECTIONS = [
    ('技能', '技能证书'), ('四六级', '四六级'), ('实践', '社会实践'),
    ('学生干部任职经历', '任职经历'), ('其他', '其他'),
]
SEC_LABEL = dict(TPL_SECTIONS) | dict(EXTRA_SECTIONS)

STATUS = {
    'green': ('自动配对', 'ok'),
    'blue': ('跨位纠正', 'blue'),
    'amber': ('待确认', 'warn'),
    'red': ('缺图', 'bad'),
    'red_flag': ('姓名异常', 'bad'),
    'pending_weak': ('待确认·附件疑似', 'warn'),
    'declared': ('学生声明无材料', 'muted'),
}
SECTION_ORDER = ['奖学金', '竞赛', '科研', '荣誉', '体测', '志愿', '技能', '实践', '四六级']


def ext_of(name):
    return name.rsplit('.', 1)[-1].lower()


def export_images(students):
    """把每份材料里的图片导出到 out/images/"""
    if os.path.isdir(IMG_DIR):
        shutil.rmtree(IMG_DIR)
    os.makedirs(IMG_DIR, exist_ok=True)
    for s in students:
        try:
            z = zipfile.ZipFile(s['file'])
        except Exception:
            continue
        for im in s['images']:
            dst = os.path.join(IMG_DIR, im['sha256'][:16] + '.' + ext_of(im['file']))
            if not os.path.exists(dst):
                try:
                    with open(dst, 'wb') as f:
                        f.write(z.read(im['media']))
                except Exception:
                    pass
        z.close()


def img_src(sha, fname):
    return 'images/%s.%s' % (sha[:16], ext_of(fname))


def esc(s):
    return html.escape(str(s or ''))


def sev_key(s):
    """学生严重度：红 > 黄 > 蓝；无问题的排最后"""
    bad = sum(1 for m in s['matches'] if m['status'] in ('red', 'red_flag'))
    warn = sum(1 for m in s['matches'] if m['status'] == 'amber')
    blue = sum(1 for m in s['matches'] if m['status'] == 'blue')
    return (-bad, -warn, -blue)


def build(students):
    n_stu = len(students)
    all_m = [m for s in students for m in s['matches']]
    cnt = {}
    for m in all_m:
        cnt[m['status']] = cnt.get(m['status'], 0) + 1
    total = len(all_m)
    orphans = sum(len(s.get('orphan_images') or []) for s in students)
    n_img = sum(len(s['images']) for s in students)

    parts = []
    parts.append('''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>奖学金材料审核工作台</title>
<style>
:root{--bg:#f6f6f4;--card:#fff;--line:#e3e2dd;--tx:#1f1f1e;--tx2:#6b6a64;
--ok:#0f6e56;--okbg:#e1f5ee;--blue:#185fa5;--bluebg:#e6f1fb;
--warn:#854f0b;--warnbg:#faeeda;--bad:#a32d2d;--badbg:#fcebeb;--muted:#8a8983;--mutedbg:#f1efe8}
@media (prefers-color-scheme:dark){:root{--bg:#1c1c1b;--card:#2c2c2a;--line:#444441;--tx:#f1efe8;--tx2:#b4b2a9;
--ok:#5dcaa5;--okbg:#04342c;--blue:#85b7eb;--bluebg:#042c53;--warn:#ef9f27;--warnbg:#412402;
--bad:#f09595;--badbg:#501313;--muted:#888780;--mutedbg:#2c2c2a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
font:13px/1.6 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px 20px 60px}
h1{font-size:19px;font-weight:500;margin:0 0 4px}
.sub{color:var(--tx2);font-size:12px;margin-bottom:18px}
.stats{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:22px}
.stat{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 16px;min-width:104px}
.stat b{display:block;font-size:21px;font-weight:500;line-height:1.25}
.stat span{font-size:12px;color:var(--tx2)}
.stu{background:var(--card);border:1px solid var(--line);border-radius:12px;margin-bottom:12px;overflow:hidden}
.sh{display:flex;align-items:center;gap:12px;padding:12px 16px;cursor:pointer;user-select:none}
.sh:hover{background:var(--mutedbg)}
.sh .nm{font-size:14px;font-weight:500}
.sh .cl{font-size:12px;color:var(--tx2)}
.sh .sp{flex:1}
.chip{font-size:11px;padding:2px 9px;border-radius:20px;white-space:nowrap;border:1px solid transparent}
.c-ok{background:var(--okbg);color:var(--ok);border-color:var(--ok)}
.c-blue{background:var(--bluebg);color:var(--blue);border-color:var(--blue)}
.c-warn{background:var(--warnbg);color:var(--warn);border-color:var(--warn)}
.c-bad{background:var(--badbg);color:var(--bad);border-color:var(--bad)}
.c-muted{background:var(--mutedbg);color:var(--muted);border-color:var(--line)}
.bd{display:none;border-top:1px solid var(--line);padding:6px 16px 14px}
.stu.open .bd{display:block}
.sec{font-size:12px;color:var(--tx2);margin:14px 0 6px;padding-bottom:4px;border-bottom:1px dashed var(--line)}
.row{display:flex;gap:12px;padding:9px 0;border-bottom:1px solid var(--line);align-items:flex-start}
.row:last-child{border-bottom:0}
.it{flex:1;min-width:0}
.it .t{font-weight:500;word-break:break-word}
.it .why{font-size:12px;color:var(--tx2);margin-top:2px;word-break:break-word}
.it .why em{font-style:normal;color:var(--ok)}
.alt{font-size:12px;color:var(--warn)}
.th{width:92px;flex:0 0 92px}
.th img{width:92px;height:66px;object-fit:cover;border-radius:6px;border:1px solid var(--line);
cursor:zoom-in;background:var(--mutedbg);display:block}
.th .empty{width:92px;height:66px;border:1px dashed var(--bad);border-radius:6px;
display:flex;align-items:center;justify-content:center;color:var(--bad);font-size:11px}
.meta{font-size:11px;color:var(--tx2);margin-top:3px}
.orph{background:var(--warnbg);border:1px solid var(--warn);border-radius:8px;padding:8px 12px;margin-top:12px}
.orph .h{color:var(--warn);font-size:12px;font-weight:500;margin-bottom:6px}
.og{display:flex;gap:10px;flex-wrap:wrap}
.og div{width:118px;font-size:11px;color:var(--tx2)}
.og img{width:118px;height:84px;object-fit:cover;border-radius:6px;border:1px solid var(--line);cursor:zoom-in;background:var(--mutedbg)}
.warnbar{background:var(--badbg);border:1px solid var(--bad);color:var(--bad);
border-radius:8px;padding:8px 12px;font-size:12px;margin-bottom:12px}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.86);display:none;align-items:center;
justify-content:center;z-index:99;padding:24px}
#lb img{max-width:100%;max-height:100%;border-radius:8px}
.hint{color:var(--tx2);font-size:12px;margin-bottom:14px}
.eligbox{background:var(--okbg);border:1px solid var(--ok);border-radius:8px;padding:8px 12px;margin:10px 0}
.eligbox .h{font-size:12px;font-weight:500;color:var(--ok);margin-bottom:5px}
.ei{font-size:12px;color:var(--tx);margin:2px 0}
.ei b{display:inline-block;min-width:34px;font-weight:500;margin-right:6px}
.ei.ok b{color:var(--ok)} .ei.bad b{color:var(--bad)}
.ei.warn b{color:var(--warn)} .ei.info b{color:var(--tx2)}
.neg{font-style:normal;color:var(--bad)}
.src-vlm{color:var(--blue)}
.guess{color:var(--warn)}
.note{color:var(--tx2);font-size:11px;margin-top:3px;line-height:1.5;border-left:2px solid var(--line);padding-left:6px}
.eligbox{background:var(--okbg);border:1px solid var(--ok);border-radius:8px;padding:8px 12px;margin:10px 0}
.eligbox .h{font-size:12px;font-weight:500;color:var(--ok);margin-bottom:5px}
.ei{font-size:12px;color:var(--tx);margin:2px 0}
.ei b{display:inline-block;min-width:34px;font-weight:500;margin-right:6px}
.ei.ok b{color:var(--ok)} .ei.bad b{color:var(--bad)}
.ei.warn b{color:var(--warn)} .ei.info b{color:var(--tx2)}
.neg{font-style:normal;color:var(--bad)}
.src-vlm{color:var(--blue)}
.guess{color:var(--warn)}

.row{align-items:flex-start}
.cap{font-size:11.5px;color:var(--tx);margin-top:5px;line-height:1.4;text-align:center}
summary{cursor:pointer;font-size:11px;color:var(--tx2);list-style:none;text-align:center;
  padding:3px 0;border-top:1px dashed var(--line);margin-top:6px}
summary::-webkit-details-marker{display:none}
summary:hover{color:var(--tx)}
summary::before{content:"▸ 详情"}
details[open] summary::before{content:"▾ 收起"}
.det{font-size:11px;color:var(--tx2);line-height:1.6;text-align:left;
  background:rgba(127,127,127,.08);border-radius:6px;padding:6px 8px;margin-top:4px}
.det b{color:var(--tx);font-weight:500}
.tpl{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin:10px 0}
.tpl .tl{font-size:11.5px;color:var(--tx2);margin-right:2px}
.mats{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
.mats .mh{font-size:12px;color:var(--tx2);margin-bottom:8px}
.mg{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
.mc{border:1px solid var(--line);border-radius:8px;padding:8px;background:rgba(127,127,127,.05)}
.mc img{width:100%;max-height:150px;object-fit:contain;border-radius:5px;cursor:zoom-in;
  background:rgba(127,127,127,.10)}
.mc .mt{font-size:11.5px;color:var(--tx);margin-top:6px;line-height:1.4}
.mc .mv{font-size:12px;color:var(--tx);font-weight:500;margin-top:3px}
.mc .ms{font-size:11px;color:var(--tx2);margin-top:3px;line-height:1.5}
.tag{display:inline-block;font-size:10.5px;padding:1px 5px;border-radius:4px;margin:2px 3px 0 0}
.tag.w{background:rgba(230,180,60,.18);color:var(--warn)}
.tag.g{background:rgba(70,160,120,.16);color:var(--ok)}
.ask{color:var(--warn);font-style:normal}
.ai{color:var(--tx);background:rgba(127,127,127,.10);border-radius:5px;padding:4px 6px;margin-top:5px}
.sug{margin-top:6px;padding:6px 8px;border-radius:6px;background:rgba(230,180,60,.20);border:1px solid rgba(230,180,60,.55);font-size:12px;color:var(--tx);line-height:1.5}
.sug b{font-size:13.5px;color:var(--warn)}
.sug .sub{display:block;font-size:11px;color:var(--tx2);margin-top:3px}
.th{width:200px}
.th img{max-width:160px;max-height:170px;object-fit:contain}
</style></head><body><div class="wrap">''')

    parts.append('<h1>奖学金申请材料审核工作台</h1>')
    parts.append('<div class="sub">%d 名学生 · %d 条奖项 · %d 张证明图 · 全部解析与配对在本机完成</div>'
                 % (n_stu, total, n_img))
    parts.append('<div class="stats">')
    for key, cls in (('green', 'ok'), ('blue', 'blue'), ('amber', 'warn'),
                     ('pending_weak', 'warn'), ('red', 'bad'),
                     ('red_flag', 'bad'), ('declared', 'muted')):
        label = STATUS[key][0]
        parts.append('<div class="stat"><b style="color:var(--%s)">%d</b><span>%s</span></div>'
                     % (cls, cnt.get(key, 0), label))
    parts.append('<div class="stat"><b style="color:var(--warn)">%d</b><span>未归属图片</span></div>' % orphans)
    parts.append('</div>')
    parts.append('<div class="hint">点击学生展开详情。绿色=系统自动配对（扫一眼即可）；'
                 '蓝色=系统纠正了原来的错位（需你确认）；黄色=多个候选或证据不足（需你判断）；'
                 '深黄=条目下面有图但内容对不上（需你判断）；红色=确实没有配图。每张图下方标注了视觉模型读到的奖项名/等级/年份与识别说明；带 ▸详情 的可以点开看完整依据。<br>黄色框里的「建议补写奖项名」是系统按证书内容拟的——这类图通常说明学生漏写了奖项，可直接采用。<br>每个板块最后会把<b>没有认领到具体条目的图片</b>单独列出来——学生在 Word 里插图通常意味着确有该奖项，供你人工指定归属。模板板块没交材料的按顺序标为“未提交”，不影响其余板块的审核。</div>')

    for s in sorted(students, key=sev_key):
        st = s['student']
        bad = sum(1 for m in s['matches'] if m['status'] in ('red', 'red_flag'))
        warn = sum(1 for m in s['matches'] if m['status'] in ('amber', 'pending_weak'))
        blue = sum(1 for m in s['matches'] if m['status'] == 'blue')
        okc = sum(1 for m in s['matches'] if m['status'] == 'green')
        open_cls = ' open' if (bad or warn) else ''
        parts.append('<div class="stu%s"><div class="sh" onclick="this.parentNode.classList.toggle(\'open\')">'
                     % open_cls)
        parts.append('<div><div class="nm">%s</div><div class="cl">%s班</div></div>'
                     % (esc(st.get('name')), esc(st.get('class_no'))))
        parts.append('<div class="sp"></div>')
        if okc:
            parts.append('<span class="chip c-ok">自动配对 %d</span>' % okc)
        if blue:
            parts.append('<span class="chip c-blue">跨位纠正 %d</span>' % blue)
        if warn:
            parts.append('<span class="chip c-warn">待确认 %d</span>' % warn)
        if bad:
            parts.append('<span class="chip c-bad">缺图/异常 %d</span>' % bad)
        parts.append('</div><div class="bd">')

        for w in st.get('identity_warnings') or []:
            parts.append('<div class="warnbar">身份信息：%s</div>' % esc(w))

        el = s.get('eligibility') or {}
        for lv, msg in (el.get('identity') or []):
            parts.append('<div class="warnbar">%s</div>' % esc(msg))
        if el.get('items'):
            parts.append('<div class="eligbox"><div class="h">资格核验</div>')
            for lv, msg in el['items']:
                parts.append('<div class="ei %s"><b>%s</b>%s</div>'
                             % (lv, esc({'ok': '通过', 'bad': '异常',
                                         'warn': '注意', 'info': '信息'}.get(lv, lv)), esc(msg)))
            parts.append('</div>')

        # ---------------- 模板六大板块：有没有交材料 ----------------
        sec_items, sec_imgs = {}, {}
        for it in s['items']:
            sec_items[it['section']] = sec_items.get(it['section'], 0) + 1
        for im in s['images']:
            k = im.get('section')
            sec_imgs[k] = sec_imgs.get(k, 0) + 1

        parts.append('<div class="tpl"><span class="tl">模板板块</span>')
        miss = []
        for key, full in TPL_SECTIONS:
            ni, nm = sec_items.get(key, 0), sec_imgs.get(key, 0)
            if ni or nm:
                parts.append('<span class="chip c-ok">%s · %d条/%d图</span>'
                             % (esc(full), ni, nm))
            else:
                miss.append(full)
                parts.append('<span class="chip c-muted">%s · 未提交</span>' % esc(full))
        parts.append('</div>')

        # ---------------- 配对结果与"没配上的材料"按板块归拢 ----------------
        imap = {x['file']: x for x in s['images']}
        used = {m['img_file'] for m in s['matches'] if m.get('img_file')}
        orphan_set = set(s.get('orphan_images') or [])

        isec = {it['text']: it['section'] for it in s['items']}
        sec_rows = {}
        for m in s['matches']:
            sec_rows.setdefault(isec.get(m.get('item_text') or ''), []).append(m)

        sec_left = {}
        for im in s['images']:
            if im['file'] in used:
                continue
            sec_left.setdefault(im.get('section'), []).append(im)

        order = [k for k, _ in TPL_SECTIONS] + [k for k, _ in EXTRA_SECTIONS]
        keys = [k for k in order if k in sec_rows or k in sec_left]
        for k in list(sec_rows) + list(sec_left):
            if k not in keys:
                keys.append(k)

        def img_cell(im):
            """右侧：缩略图 + 一行摘要；详情点开才展开（避免右栏过长）"""
            rd = im.get('reading') or {}
            bits = []
            nm = rd.get('award_name') or rd.get('title')
            if nm:
                bits.append(nm)
            if rd.get('award_level'):
                bits.append(str(rd['award_level']))
            yr = rd.get('academic_year') or rd.get('year')
            if yr:
                bits.append(str(yr))
            out = ['<img src="%s" loading="lazy" onclick="zoom(this)" alt="">'
                   % img_src(im['sha256'], im['file'])]
            out.append('<div class="cap">%s</div>' % esc(' · '.join(bits) or im['file']))
            det = []
            det.append('<div><b>类型</b> %s · %s</div>'
                       % (esc(im.get('kind_label') or ''), esc(im['size'])))
            if rd.get('issuer'):
                det.append('<div><b>落款单位</b> %s</div>' % esc(rd['issuer']))
            if im.get('vlevel'):
                det.append('<div><b>判定的级别</b> %s</div>' % esc(im['vlevel']))
            if rd.get('persons'):
                det.append('<div><b>材料上的人名</b> %s</div>'
                           % esc('、'.join(rd['persons'][:6])))
            if rd.get('metrics'):
                det.append('<div><b>指标</b> %s</div>'
                           % esc('、'.join('%s %s' % (k, v) for k, v in rd['metrics'].items())))
            if rd.get('note'):
                det.append('<div><b>识别说明</b> %s</div>' % esc(rd['note']))
            det.append('<div>来源：%s</div>'
                       % ('视觉模型读取' if im.get('source') == 'vlm' else '本地OCR'))
            out.append('<details><summary></summary><div class="det">%s</div></details>'
                       % ''.join(det))
            return ''.join(out)

        def mat_card(im):
            """没有对应文字条目的材料卡片 —— 保证不漏，同时提示口径不明"""
            rd = im.get('reading') or {}
            out = ['<div class="mc">']
            out.append('<img src="%s" loading="lazy" onclick="zoom(this)" alt="">'
                       % img_src(im['sha256'], im['file']))
            out.append('<div class="mt">%s</div>'
                       % esc(rd.get('award_name') or rd.get('title') or im['file']))
            if im.get('suggest_ok') and im.get('suggest_award'):
                out.append('<div class="sug">建议补写奖项名：<b>{%s}</b>'
                           '<span class="sub">证书上能核对到本人姓名，'
                           '学生只是没在正文里写这条奖项；可直接采纳为一条</span></div>'
                           % esc(im['suggest_award']))
            if rd.get('metrics'):
                out.append('<div class="mv">%s</div>'
                           % esc('、'.join('%s %s' % (k, v) for k, v in rd['metrics'].items())))
            out.append('<div class="ms">%s · %s</div>'
                       % (esc(im.get('kind_label') or ''), esc(im['size'])))
            _aj = im.get('ai_judgment')
            if _aj:
                out.append('<div class="ms ai">AI 判断：%s</div>' % esc(_aj))
            _n = im.get('nearest') or {}
            if _n.get('item'):
                _sc = float(_n.get('score') or 0)
                if _sc >= 40:
                    out.append('<div class="ms">最接近的条目：「…%s」（%s 分）'
                               '—— 若确认是这条的证明，请人工指定归属</div>'
                               % (esc((_n['item'] or '')[-30:]), _sc))
                else:
                    out.append('<div class="ms">没有可挂靠的已写条目'
                               '（最高只有 %s 分）—— 这份材料在正文里没有对应条目，'
                               '如需计入，请人工在相应板块补写一条</div>' % _sc)
            yr = rd.get('academic_year') or rd.get('year')
            if yr:
                out.append('<div class="ms">年份：%s</div>' % esc(str(yr)))
            else:
                out.append('<span class="tag w">年份不明确</span>')
            if im['file'] in orphan_set:
                out.append('<span class="tag w">条目里没写到这份材料</span>')
            if not (rd.get('persons') or []):
                out.append('<span class="tag w">图上未显示姓名，无法核对本人</span>')
            if im.get('person_status') in ('name_conflict', 'roster_missing'):
                out.append('<span class="tag w">姓名未能与申报人对上</span>')
            if rd.get('note'):
                out.append('<details><summary></summary><div class="det">%s</div></details>'
                           % esc(rd['note']))
            out.append('</div>')
            return ''.join(out)

        # ---------------- 逐板块输出 ----------------
        for key in keys:
            label = SEC_LABEL.get(key) or (
                '模板之外的材料' if key is None else key)
            rows = sec_rows.get(key) or []
            left = sec_left.get(key) or []
            parts.append('<div class="sec">%s</div>' % esc(label))

            for m in rows:
                lab2, cls = STATUS.get(m['status'], ('?', 'muted'))
                parts.append('<div class="row"><div class="it">')
                parts.append('<div class="t">%s <span class="chip c-%s">%s</span></div>'
                             % (esc(m.get('item_text') or ''), cls, lab2))
                why = m.get('reasons') or []
                if why:
                    neg = [r[0] for r in why if r[1] < 0]
                    pos = [r[0] for r in why if r[1] >= 0]
                    if pos:
                        parts.append('<div class="why">%s</div>' % esc('、'.join(pos)))
                    if neg:
                        parts.append('<div class="why"><em class="neg">反证：%s</em></div>'
                                     % esc('、'.join(neg)))
                pr = m.get('pending_reason')
                if pr and pr != '；'.join(neg):
                    parts.append('<div class="why"><em class="ask">待确认原因：%s</em></div>'
                                 % esc(pr))
                if m.get('score'):
                    parts.append('<div class="alt">匹配度 %.0f 分</div>' % m['score'])
                if m['status'] == 'blue' and m.get('adjacent'):
                    parts.append('<div class="alt">原位置上放的是：%s（已改配到本图，请确认）</div>'
                                 % esc('、'.join(a['file'] for a in m['adjacent'])))
                if m.get('alternatives'):
                    parts.append('<div class="alt">另有候选：%s</div>'
                                 % esc('、'.join(m['alternatives'])))
                parts.append('</div><div class="th">')
                im = imap.get(m.get('img_file')) if m.get('img_file') else None
                if im:
                    parts.append(img_cell(im))
                elif m['status'] == 'declared':
                    parts.append('<div class="empty" style="border-color:var(--line);'
                                 'color:var(--muted)">—</div>')
                else:
                    parts.append('<div class="empty">缺图</div>')
                parts.append('</div></div>')

            if left:
                parts.append('<div class="mats"><div class="mh">'
                             '本板块有 %d 张图片【未认领】到具体奖项 —— '
                             '学生在 Word 里插入图片通常意味着确有该奖项，请人工指定归属'
                             '（年份或口径不明确的已标注；也可能是与本次申报无关的图）'
                             '</div><div class="mg">' % len(left))
                for im in left:
                    parts.append(mat_card(im))
                parts.append('</div></div>')

        parts.append('</div></div>')

    parts.append('</div><div id="lb" onclick="this.style.display=\'none\'"><img id="lbi" alt=""></div>')
    parts.append('<script>function zoom(el){document.getElementById("lbi").src=el.src;'
                 'document.getElementById("lb").style.display="flex";}'
                 'document.addEventListener("keydown",function(e){if(e.key==="Escape")'
                 'document.getElementById("lb").style.display="none";});</script>')
    parts.append('</body></html>')
    return '\n'.join(parts)


def main():
    data = json.load(open(os.path.join(OUT, 'result.json'), encoding='utf-8'))
    export_images(data)
    html_str = build(data)
    dst = os.path.join(OUT, '审核工作台.html')
    with open(dst, 'w', encoding='utf-8') as f:
        f.write(html_str)
    print('已生成: %s' % dst)
    print('图片目录: %s (%d 个文件)' % (IMG_DIR, len(os.listdir(IMG_DIR))))


if __name__ == '__main__':
    main()
