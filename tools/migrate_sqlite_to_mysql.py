# -*- coding: utf-8 -*-
"""
把本地 SQLite 的数据搬到 MySQL。

场景：先在本地用 SQLite 调通了流程，要上线到宝塔的 MySQL。
     不想丢已建的奖学金项目、批次、审核动作。

用法：
    python tools/migrate_sqlite_to_mysql.py \
        --host 127.0.0.1 --user sch --password 'xxx' --database scholarship_audit \
        [--sqlite data/app.db] [--truncate]

设计要点：
- 逐表搬运，**按 id 升序**，保证自增主键顺序一致（否则外键语义会乱）。
- 每张表搬完后把 MySQL 的自增起点对齐到 max(id)+1，避免后面插入撞主键。
- 默认是"只填空白表"：目标表已有数据就跳过该表并提示（防止把生产数据覆盖）。
  加 `--truncate` 会先清空目标表再搬（危险，需要二次确认）。
- 文件类数据（uploads/、data/images/、cache/）**不经数据库**，
  部署时把整个 data/ 与 cache/ 目录一起拷过去即可。
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 按依赖顺序搬（先被引用的表先搬）
TABLES = [
    'users', 'programs', 'batches', 'files', 'refdata',
    'rules_overrides', 'program_rules', 'rule_nl', 'model_configs',
    'tasks', 'match_states', 'orphan_adoptions', 'api_usage', 'audit_log',
]


def main():
    ap = argparse.ArgumentParser(description='SQLite → MySQL 数据迁移')
    ap.add_argument('--sqlite', default=os.path.join(HERE, 'data', 'app.db'))
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=3306)
    ap.add_argument('--user', default='root')
    ap.add_argument('--password', default='')
    ap.add_argument('--database', default='scholarship_audit')
    ap.add_argument('--truncate', action='store_true', help='先清空目标表（危险）')
    ap.add_argument('--yes', action='store_true', help='跳过确认')
    a = ap.parse_args()

    if not os.path.exists(a.sqlite):
        print('找不到源库：%s' % a.sqlite)
        return 1

    import sqlite3
    import pymysql
    from pymysql.cursors import DictCursor

    src = sqlite3.connect(a.sqlite)
    src.row_factory = sqlite3.Row

    print('=' * 70)
    print('SQLite → MySQL 迁移')
    print('  源  :', a.sqlite)
    print('  目标: %s@%s:%s/%s' % (a.user, a.host, a.port, a.database))
    print('=' * 70)

    try:
        dst = pymysql.connect(host=a.host, port=a.port, user=a.user,
                              password=a.password, database=a.database,
                              charset='utf8mb4', cursorclass=DictCursor, autocommit=False)
    except Exception as e:
        print('连接 MySQL 失败：%s' % e)
        return 2

    if a.truncate and not a.yes:
        if input('⚠️  将清空目标库所有表，确认？输入 yes 继续：').strip() != 'yes':
            print('已取消')
            return 1

    total = 0
    cur = dst.cursor()
    for t in TABLES:
        try:
            rows = [dict(r) for r in src.execute('SELECT * FROM %s ORDER BY id' % t).fetchall()] \
                if _has_id(src, t) else \
                [dict(r) for r in src.execute('SELECT * FROM %s' % t).fetchall()]
        except Exception as e:
            print('  跳过 %-18s（源表不存在：%s）' % (t, e))
            continue

        # 目标是否已有数据
        try:
            cur.execute('SELECT COUNT(*) c FROM %s' % t)
            exist = list(cur.fetchone().values())[0]
        except Exception as e:
            print('  !! %-18s 目标表不存在，请先跑 tools/init_db.py（%s）' % (t, e))
            continue

        if exist and not a.truncate:
            print('  跳过 %-18s（目标已有 %d 行，避免覆盖）' % (t, exist))
            continue
        if a.truncate:
            cur.execute('DELETE FROM %s' % t)
            cur.execute('ALTER TABLE %s AUTO_INCREMENT=1' % t)

        if not rows:
            print('  -- %-18s 源表为空' % t)
            continue

        cols = list(rows[0].keys())
        ph = ', '.join(['%s'] * len(cols))
        sql = 'INSERT INTO %s (%s) VALUES (%s)' % (t, ', '.join(cols), ph)
        for r in rows:
            vals = []
            for c in cols:
                v = r[c]
                if isinstance(v, (bytes, bytearray)):
                    v = bytes(v)
                vals.append(v)
            cur.execute(sql, vals)

        # 对齐自增起点
        if 'id' in cols and _has_id(src, t):
            mx = max((r['id'] or 0) for r in rows)
            try:
                cur.execute('ALTER TABLE %s AUTO_INCREMENT=%d' % (t, int(mx) + 1))
            except Exception:
                pass
        print('  OK %-18s 搬入 %d 行' % (t, len(rows)))
        total += len(rows)

    dst.commit()
    cur.close()
    dst.close()
    src.close()
    print()
    print('完成，共搬入 %d 行。' % total)
    print()
    print('别忘了把文件也拷过去（这些不在数据库里）：')
    print('  data/uploads/  data/images/  data/results/  data/instance/  cache/')
    return 0


def _has_id(conn, table):
    try:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(%s)' % table).fetchall()]
        return 'id' in cols
    except Exception:
        return False


if __name__ == '__main__':
    sys.exit(main())
