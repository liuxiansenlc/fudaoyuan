# -*- coding: utf-8 -*-
"""
数据库初始化 / 自检工具。

用法：
    # 看当前配置连的是哪个库、能不能连上（不改任何东西）
    python tools/init_db.py --check

    # 建库 + 建表 + 补列迁移 + 建默认管理员
    python tools/init_db.py

    # 指定用 MySQL（也可以用环境变量）
    python tools/init_db.py --db mysql --host 127.0.0.1 --port 3306 \
        --user sch --password 'xxx' --database scholarship_audit

正式部署（宝塔）建议流程：
    1) 在宝塔里建一个 MySQL 库和账号
    2) 把 WB_MYSQL_* 写进 .env（或启动命令的环境变量）
    3) python tools/init_db.py            # 建表
    4) python tools/init_db.py --check    # 确认连得上、表都在
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def build_args():
    ap = argparse.ArgumentParser(description='奖学金审核工作台 · 数据库初始化')
    ap.add_argument('--db', choices=['sqlite', 'mysql'], help='数据库类型')
    ap.add_argument('--host', help='MySQL 主机')
    ap.add_argument('--port', type=int, help='MySQL 端口')
    ap.add_argument('--user', help='MySQL 用户')
    ap.add_argument('--password', help='MySQL 密码')
    ap.add_argument('--database', help='MySQL 库名')
    ap.add_argument('--check', action='store_true', help='只做连通性自检，不改库')
    ap.add_argument('--no-admin', action='store_true', help='不创建默认管理员')
    ap.add_argument('--reset-admin', action='store_true',
                    help='把 admin 的密码重置为默认密码')
    return ap.parse_args()


def main():
    a = build_args()
    # 环境变量优先级低于命令行参数
    if a.db:
        os.environ['WB_DB'] = a.db
    if a.host:
        os.environ['WB_MYSQL_HOST'] = a.host
    if a.port:
        os.environ['WB_MYSQL_PORT'] = str(a.port)
    if a.user:
        os.environ['WB_MYSQL_USER'] = a.user
    if a.password is not None:
        os.environ['WB_MYSQL_PASSWORD'] = a.password
    if a.database:
        os.environ['WB_MYSQL_DB'] = a.database

    from web import db
    from web.config import WebConfig
    from web.security import ensure_bootstrap_admin, hash_password

    print('=' * 70)
    print('奖学金审核工作台 · 数据库')
    print('=' * 70)
    print('引擎      : %s' % db.dialect())
    if db.is_mysql():
        print('目标      : %s@%s:%s/%s' % (WebConfig.MYSQL_USER, WebConfig.MYSQL_HOST,
                                          WebConfig.MYSQL_PORT, WebConfig.MYSQL_DB))
    else:
        print('目标      : %s' % WebConfig.DB_PATH)

    if a.check:
        info = db.self_check()
        print()
        print('自检结果  : %s' % ('OK' if info.get('ok') else '失败'))
        for k, v in info.items():
            print('  %-10s %s' % (k, v))
        if not info.get('ok'):
            print()
            if db.is_mysql():
                print('排查建议：')
                print(' * 确认 MySQL 已启动、账号能远程/本地登录')
                print(' * 确认账号有该库的权限（或先用 --check 之外的方式建库）')
                print(' * 若报 "Authentication plugin ... not supported"，')
                print('   请在 MySQL 里改为 mysql_native_password / caching_sha2_password：')
                print("     ALTER USER '用户'@'%' IDENTIFIED WITH mysql_native_password BY '密码';")
            else:
                print('排查建议：确认 data/ 目录可写。')
        return 0 if info.get('ok') else 2

    # ---- 建库建表 ----
    print()
    print('建表 …')
    try:
        if db.is_mysql():
            db.ensure_database()
            print('  库已就绪（不存在则已创建）')
        done = db.migrate(verbose=True)
        if not done:
            print('  结构已是最新，无需变更')
    except Exception as e:
        print('  失败：%s: %s' % (type(e).__name__, e))
        if db.is_mysql():
            print()
            print('若是权限不足，请先在宝塔/MySQL 里手工建库：')
            print('  CREATE DATABASE %s DEFAULT CHARACTER SET utf8mb4 '
                  'COLLATE utf8mb4_unicode_ci;' % WebConfig.MYSQL_DB)
            print('  GRANT ALL ON %s.* TO \'%s\'@\'%%\';'
                  % (WebConfig.MYSQL_DB, WebConfig.MYSQL_USER))
        return 1

    tables = db.table_names()
    print('  共 %d 张表：%s' % (len(tables), ', '.join(tables)))

    # ---- 管理员 ----
    if not a.no_admin:
        if a.reset_admin:
            from web.security import hash_password as _h
            n = db.ex('UPDATE users SET pwd_hash=? WHERE username=?',
                      (_h(WebConfig.DEFAULT_ADMIN_PASSWORD), 'admin'))
            print('  admin 密码已重置为默认值' if n else '  没有 admin 账号')
        else:
            created = ensure_bootstrap_admin()
            u, p = WebConfig.BOOTSTRAP_ADMIN
            print('  管理员：%s / %s%s' % (u, p, '（本次新建）' if created else '（已存在，未改动）'))
            if not created:
                print('         忘记密码可执行：python tools/init_db.py --reset-admin')

    info = db.self_check()
    print()
    print('完成。自检：%s（%s / %d 张表 / %s 个用户）'
          % ('OK' if info.get('ok') else '异常', info.get('server'),
             info.get('tables', 0), info.get('users', 0)))
    print()
    print('下一步：')
    print('  Web 进程   : gunicorn -w 3 -b 127.0.0.1:8000 wsgi:app')
    print('  读图 worker: WB_INLINE_WORKER=0 python worker.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
