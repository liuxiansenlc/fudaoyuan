# -*- coding: utf-8 -*-
"""
隐私遮挡验证（真实图片）。

拿仓库里确实含身份证号的成绩单来跑：
  1. 能识别出身份证号所在的行，并真的把那一块涂黑
  2. 遮挡后的字节与原因不同
  3. 不含敏感号的图**原样返回**（不做无谓的重编码）
  4. 正则能识别身份证 / 手机号 / 银行卡 / 学号
  5. 遮挡失败时不抛异常（必须退回原图，绝不能因此读不了图）
"""
import io
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ['WB_MASK_SENSITIVE'] = '1'

from web.config import WebConfig          # noqa: E402
from web.services import privacy as PV     # noqa: E402
from web import create_app, db             # noqa: E402
from web.services import engine_adapter as EA   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %-52s %s' % ('OK ' if cond else '!!!', name, detail if not cond else ''))


print('=' * 80)
print('隐私遮挡验证')
print('=' * 80)

# ---------------- 1. 正则 ----------------
print('\n1) 正则识别')
cases = [
    ('证件号 330227200310204417 结束', '身份证号'),
    ('身份证：330227200310204417', '身份证号'),
    ('手机 13812345678 联系', '手机号'),
    ('卡号 6222021234567890123 转账', '银行卡号'),
    ('学号 2022082410', '学号'),
    ('证书编号 S-JSJ-22-222121021100102', None),
]
for text, want in cases:
    got = PV.find_sensitive(text)
    kinds = [g[0] for g in got]
    if want:
        check('识别「%s」→ %s' % (text[:16], want), want in kinds, str(got))
    else:
        # 证书编号不应被当身份证/银行卡遮掉（不然会遮坏有用信息）
        check('不误伤「%s」' % text[:20], not any(k in ('身份证号', '银行卡号') for k in kinds),
              str(got))

masked = PV.find_sensitive('330227200310204417')[0][2]
check('遮挡后仍可辨认头尾', masked.startswith('3302') and masked.endswith('4417')
      and '*' in masked, masked)

# ---------------- 2. 真实图片 ----------------
print('\n2) 真实图片遮挡')
app = create_app()

found = None
with app.app_context():
    # 找一张读取说明里带身份证号的图（四级成绩单）
    import glob
    for p in glob.glob(os.path.join(WebConfig.IMAGE_DIR, '*')):
        name = os.path.basename(p)
        sha = name.rsplit('.', 1)[0]
        v = db.q('SELECT * FROM image_readings WHERE sha256=?', (sha,), one=True) \
            if db.table_names() and 'image_readings' in db.table_names() else None
        # 读图结果在文件缓存里，直接读
        import json
        cp = os.path.join(WebConfig.ENGINE_CACHE_DIR, 'vision', sha + '.json')
        if not os.path.exists(cp):
            continue
        try:
            obj = json.load(open(cp, encoding='utf-8'))
        except Exception:
            continue
        note = (obj.get('note') or '') + (obj.get('title') or '') + (obj.get('award_name') or '')
        if '身份证' in note:
            found = (p, obj)
            break

if found:
    path, obj = found
    print('   样例：%s（%s）' % (os.path.basename(path)[:24], obj.get('title')))
    print('   读取说明：%s' % (obj.get('note') or '')[:70])
    raw = open(path, 'rb').read()
    masked_bytes, hits = PV.mask_image(raw)
    check('识别到敏感信息', bool(hits), str(hits))
    print('   命中：%s' % [(h['kind'], h['text']) for h in hits])
    check('遮挡后字节与原图不同', masked_bytes != raw)
    # 用 OCR 确认原号码确实"看不见了"
    after = PV.preview_mask(masked_bytes)
    still = [h for h in after.get('hits', []) if h['kind'] == '身份证号']
    check('遮挡后 OCR 不再读出身份证号', not still, str(still))
else:
    check('找到含身份证号的真实样例', False)

# ---------------- 3. 无敏感信息时原样返回 ----------------
print('\n3) 无敏感信息不改变图片')
clean = None
import glob                                             # noqa: E402
for p in glob.glob(os.path.join(WebConfig.IMAGE_DIR, '*')):
    raw = open(p, 'rb').read()
    if len(raw) > 400 * 1024:      # 挑小一点的，OCR 快
        continue
    r = PV.preview_mask(raw)
    if r.get('ok') and not r['hits']:
        clean = p
        break
if clean:
    raw = open(clean, 'rb').read()
    out, hits = PV.mask_image(raw)
    check('无敏感信息时不产生新字节', out is raw or out == raw, str(hits))
    check('无敏感信息时 hits 为空', not hits, str(hits))
else:
    print('   （没找到合适的干净样例，跳过）')

# ---------------- 4. 异常兜底 ----------------
print('\n4) 异常兜底')
bad = b'not an image at all'
out, hits = PV.mask_image(bad)
check('喂坏数据不抛异常', out == bad and hits == [], str(hits)[:60])

# ---------------- 5. 开关 ----------------
print('\n5) 开关')
with app.app_context():
    old = db.get_setting(PV.SETTING_KEY)
    try:
        db.set_setting(PV.SETTING_KEY, '1')
        check('界面开关打开后 enabled() 为真', PV.enabled() is True)
        db.set_setting(PV.SETTING_KEY, '0')
        check('界面开关关闭后 enabled() 为假', PV.enabled() is False)
    finally:
        if old is None:
            db.ex('DELETE FROM app_settings WHERE k=?', (PV.SETTING_KEY,))
        else:
            db.set_setting(PV.SETTING_KEY, old)

print('\n' + '=' * 80)
print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败：', FAIL)
sys.exit(1 if FAIL else 0)
