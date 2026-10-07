# -*- coding: utf-8 -*-
"""
SQL 方言单测。

本机没有可用的 MySQL（跑着的是企业版 GSSAPI 认证，pymysql 连不上），
所以不能真机验证。退而求其次：**断言生成的 MySQL 语句本身是对的**——
把方言切到 mysql，检查占位符、upsert、建表 DDL、时间比较这些容易出错的点。
这样至少能保证"发到 MySQL 上的 SQL 语法是 MySQL 的"。
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
os.environ['WB_DB'] = 'sqlite'          # 先按 sqlite 导入，再手动切方言来断言

from web import db   # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=''):
    (PASS if cond else FAIL).append(name)
    print('  %s %-46s %s' % ('OK ' if cond else '!!!', name, detail if not cond else ''))


print('=' * 78)
print('SQL 方言单测')
print('=' * 78)

# ---------------- 1) 占位符 ----------------
print('\n1) 占位符翻译')
db.DIALECT = 'sqlite'
check('sqlite 保持 ?', db._sql('SELECT * FROM t WHERE a=? AND b=?') ==
      'SELECT * FROM t WHERE a=? AND b=?')
db.DIALECT = 'mysql'
check('mysql 翻成 %s', db._sql('SELECT * FROM t WHERE a=? AND b=?') ==
      'SELECT * FROM t WHERE a=%s AND b=%s')

# ---------------- 2) upsert ----------------
print('\n2) upsert 语句')
vals = {'file_id': 1, 'item_id': 2, 'decision': 'confirm'}
db.DIALECT = 'sqlite'
sql_s, _ = db.upsert_sql('match_states', vals, key_cols=['file_id', 'item_id'])
check('sqlite 用 ON CONFLICT', 'ON CONFLICT(file_id, item_id) DO UPDATE SET' in sql_s, sql_s)
check('sqlite 用 excluded.', 'decision=excluded.decision' in sql_s, sql_s)

db.DIALECT = 'mysql'
sql_m, args_m = db.upsert_sql('match_states', vals, key_cols=['file_id', 'item_id'])
check('mysql 用 ON DUPLICATE KEY UPDATE', 'ON DUPLICATE KEY UPDATE' in sql_m, sql_m)
check('mysql 用 VALUES()', 'decision=VALUES(decision)' in sql_m, sql_m)
check('mysql 无 ON CONFLICT', 'ON CONFLICT' not in sql_m, sql_m)
check('mysql 占位符是 %s', '%s' in sql_m and '?' not in sql_m, sql_m)
check('参数顺序与列一致', args_m == (1, 2, 'confirm'), str(args_m))

# ---------------- 3) 建表 DDL ----------------
print('\n3) 建表 DDL')
sqlite_ddl = db.schema_for('sqlite')
mysql_ddl = db.schema_for('mysql')
check('sqlite 用 AUTOINCREMENT', 'AUTOINCREMENT' in sqlite_ddl)
check('mysql 用 AUTO_INCREMENT', 'AUTO_INCREMENT' in mysql_ddl)
check('mysql 无 AUTOINCREMENT 残留', 'AUTOINCREMENT' not in mysql_ddl)
check('mysql 指定 utf8mb4', 'utf8mb4' in mysql_ddl and 'ENGINE=InnoDB' in mysql_ddl)

# 被索引 / UNIQUE 的列在 MySQL 里必须是 VARCHAR
bad = []
for m in re.finditer(r'CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n\) ENGINE', mysql_ddl, re.S):
    table, body = m.group(1), m.group(2)
    for line in body.split('\n'):
        low = line.strip().lower()
        if ('unique' in low or 'index' in low or 'primary key' in low) and 'text' in low:
            bad.append('%s: %s' % (table, line.strip()[:60]))
check('mysql 索引列无 TEXT', not bad, '; '.join(bad))

# 保留字 key / value 不能作列名（MySQL 会报错）
check('mysql 不用保留字 key 作列名', not re.search(r'^\s*`?key`?\s+VARCHAR', mysql_ddl, re.M))
check('rules_overrides 用 rule_key', 'rule_key' in mysql_ddl and 'rule_value' in mysql_ddl)

# 两套 schema 的表名要一致
def tables(ddl):
    return sorted(re.findall(r'CREATE TABLE IF NOT EXISTS (\w+)', ddl))
check('两套 schema 表名一致', tables(sqlite_ddl) == tables(mysql_ddl),
      '%s vs %s' % (set(tables(sqlite_ddl)) ^ set(tables(mysql_ddl)), ''))

# ---------------- 4) 时间比较 ----------------
print('\n4) 时间比较')
cut = db.utcnow_minus(20)
check('utcnow_minus 返回 ISO 串', bool(re.match(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$', cut)), cut)
src = open(os.path.join(HERE, 'web', 'services', 'task_service.py'), encoding='utf-8').read()
check('不再使用 sqlite 的 datetime()',
      "datetime('now'" not in src and 'datetime("now"' not in src)
check('采用 Python 侧算好的截止时间', 'utcnow_minus' in src)

# ---------------- 5) 全仓不应再有裸方言写法 ----------------
print('\n5) 全仓扫描')
hits = []
for root, _dirs, files in os.walk(os.path.join(HERE, 'web')):
    for f in files:
        if not f.endswith('.py'):
            continue
        p = os.path.join(root, f)
        if p.endswith('db.py'):
            continue
        t = open(p, encoding='utf-8').read()
        for kw in ("ON CONFLICT", "datetime('now'", 'AUTOINCREMENT', 'PRAGMA ',
                   'executescript('):
            if kw in t:
                hits.append('%s: %s' % (os.path.relpath(p, HERE), kw))
check('业务代码无裸方言写法', not hits, '; '.join(hits))

# 恢复
db.DIALECT = 'sqlite'

print('\n' + '=' * 78)
print('通过 %d 项，失败 %d 项' % (len(PASS), len(FAIL)))
if FAIL:
    print('失败：', FAIL)
sys.exit(1 if FAIL else 0)
