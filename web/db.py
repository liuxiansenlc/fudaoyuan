# -*- coding: utf-8 -*-
"""
数据访问层 —— 同时支持 MySQL 与 SQLite。

为什么要有方言层：
    本地开发和自动化测试用 SQLite（零依赖、进程内、跑得快），
    正式部署用 MySQL（多进程并发写更稳）。两者 SQL 方言差别不小，
    散在业务代码里 `if mysql:` 会很快失控，所以差异全部收在本模块。

方言差异一览（本模块负责抹平）：
    | 项         | SQLite                           | MySQL                        |
    |------------|----------------------------------|------------------------------|
    | 占位符     | ?                                | %s                           |
    | 自增列     | INTEGER PRIMARY KEY AUTOINCREMENT | INT PRIMARY KEY AUTO_INCREMENT|
    | upsert     | ON CONFLICT(..) DO UPDATE SET    | ON DUPLICATE KEY UPDATE      |
    |            |   x=excluded.x                   |   x=VALUES(x)                |
    | 索引长文本 | 可以                             | 必须 VARCHAR(n)               |
    | 建表脚本   | executescript                    | 逐条执行                      |
    | 保留字     | 宽松                             | `key` 等要避开（列名用 rule_key）|

约定：
- 所有时间戳存 **UTC ISO8601 字符串**（'YYYY-MM-DDTHH:MM:SS'），
  VARCHAR 存储，字典序即时间序；比较时**在 Python 侧算好截止值再传参**，
  避免各家日期函数的差异。
- 对外统一返回 **dict**，不泄露 sqlite3.Row / pymysql 游标类型。
"""
import os
import json
import threading
import datetime
from contextlib import contextmanager

from .config import WebConfig

# ------------------------------------------------------------------ 方言
DIALECT = (os.environ.get('WB_DB') or getattr(WebConfig, 'DB_ENGINE', 'sqlite')).strip().lower()
if DIALECT not in ('sqlite', 'mysql'):
    DIALECT = 'sqlite'

_local = threading.local()


def dialect():
    return DIALECT


def is_mysql():
    return DIALECT == 'mysql'


def _sql(sql):
    """把业务代码里的 `?` 占位符翻成当前方言。"""
    return sql.replace('?', '%s') if DIALECT == 'mysql' else sql


# ------------------------------------------------------------------ 建表
SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS users (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    username     TEXT NOT NULL UNIQUE,
    display_name TEXT DEFAULT '',
    pwd_hash     TEXT NOT NULL,
    is_admin     INTEGER NOT NULL DEFAULT 0,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    category        TEXT DEFAULT '国家级',
    description     TEXT DEFAULT '',
    section_scheme  TEXT DEFAULT '',
    rules_overrides TEXT DEFAULT '',
    template_path   TEXT DEFAULT '',
    quota           INTEGER DEFAULT 0,
    reject_mode     TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'active',
    sort_order      INTEGER NOT NULL DEFAULT 100,
    created_by      INTEGER,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

CREATE TABLE IF NOT EXISTS batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id  INTEGER,
    name        TEXT NOT NULL,
    note        TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'draft',
    created_by  INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id     INTEGER NOT NULL,
    student_name TEXT DEFAULT '',
    orig_name    TEXT NOT NULL,
    stored_path  TEXT NOT NULL,
    sha256       TEXT DEFAULT '',
    size         INTEGER DEFAULT 0,
    n_items      INTEGER DEFAULT 0,
    n_images     INTEGER DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'uploaded',
    error        TEXT DEFAULT '',
    analyzed_at  TEXT,
    created_at   TEXT NOT NULL,
    UNIQUE(batch_id, orig_name)
);

CREATE TABLE IF NOT EXISTS refdata (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id  INTEGER,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    size        INTEGER DEFAULT 0,
    note        TEXT DEFAULT '',
    uploaded_by INTEGER,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules_overrides (
    rule_key   TEXT PRIMARY KEY,
    rule_value TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS program_rules (
    program_id INTEGER NOT NULL,
    rule_key   TEXT NOT NULL,
    rule_value TEXT NOT NULL,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (program_id, rule_key)
);

CREATE TABLE IF NOT EXISTS rule_nl (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    program_id   INTEGER,
    nl_text      TEXT NOT NULL,
    dsl          TEXT DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'draft',
    priority     INTEGER NOT NULL DEFAULT 100,
    compile_note TEXT DEFAULT '',
    created_by   INTEGER,
    created_at   TEXT NOT NULL,
    updated_at   TEXT
);

CREATE TABLE IF NOT EXISTS model_configs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    base_url    TEXT NOT NULL,
    model       TEXT NOT NULL,
    api_key_enc BLOB,
    is_active   INTEGER NOT NULL DEFAULT 0,
    extra       TEXT DEFAULT '{}',
    created_by  INTEGER,
    created_at  TEXT NOT NULL,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kind           TEXT NOT NULL,
    batch_id       INTEGER,
    file_id        INTEGER,
    payload        TEXT DEFAULT '{}',
    status         TEXT NOT NULL DEFAULT 'queued',
    phase          TEXT DEFAULT '',
    progress_done  INTEGER NOT NULL DEFAULT 0,
    progress_total INTEGER NOT NULL DEFAULT 0,
    message        TEXT DEFAULT '',
    error          TEXT DEFAULT '',
    created_by     INTEGER,
    created_at     TEXT NOT NULL,
    started_at     TEXT,
    finished_at    TEXT,
    claimed_by     TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS match_states (
    file_id    INTEGER NOT NULL,
    item_id    INTEGER NOT NULL,
    decision   TEXT NOT NULL,
    img_sha    TEXT DEFAULT '',
    img_file   TEXT DEFAULT '',
    note       TEXT DEFAULT '',
    is_manual  INTEGER NOT NULL DEFAULT 1,
    updated_by INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (file_id, item_id)
);

CREATE TABLE IF NOT EXISTS orphan_adoptions (
    file_id    INTEGER NOT NULL,
    img_sha    TEXT NOT NULL,
    award_name TEXT NOT NULL,
    section    TEXT DEFAULT '',
    adopted_by INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (file_id, img_sha)
);

CREATE TABLE IF NOT EXISTS api_usage (
    day    TEXT NOT NULL,
    model  TEXT NOT NULL DEFAULT '',
    calls  INTEGER NOT NULL DEFAULT 0,
    images INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    actor  INTEGER,
    action TEXT NOT NULL,
    target TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    k          TEXT PRIMARY KEY,
    v          TEXT DEFAULT '',
    updated_by INTEGER,
    updated_at TEXT
);
"""

# 索引单独放：老库的 batches/refdata 要先 ALTER 补出 program_id，才能建对应索引，
# 所以顺序必须是「建表 → 补列 → 建索引」。
SCHEMA_SQLITE_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_files_batch  ON files(batch_id);
CREATE INDEX IF NOT EXISTS idx_batches_prog ON batches(program_id);
CREATE INDEX IF NOT EXISTS idx_refdata_prog ON refdata(program_id, kind);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, id);
"""

# MySQL 版：所有被索引 / UNIQUE 的列必须是 VARCHAR(n)，TEXT 不能直接建索引
SCHEMA_MYSQL = """
CREATE TABLE IF NOT EXISTS users (
    id           INT PRIMARY KEY AUTO_INCREMENT,
    username     VARCHAR(64) NOT NULL UNIQUE,
    display_name VARCHAR(64) DEFAULT '',
    pwd_hash     VARCHAR(255) NOT NULL,
    is_admin     TINYINT NOT NULL DEFAULT 0,
    is_active    TINYINT NOT NULL DEFAULT 1,
    created_at   VARCHAR(20) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS programs (
    id              INT PRIMARY KEY AUTO_INCREMENT,
    name            VARCHAR(128) NOT NULL,
    category        VARCHAR(32) DEFAULT '国家级',
    description     VARCHAR(512) DEFAULT '',
    section_scheme  TEXT,
    rules_overrides TEXT,
    template_path   VARCHAR(512) DEFAULT '',
    quota           INT DEFAULT 0,
    reject_mode     VARCHAR(16) DEFAULT '',
    status          VARCHAR(16) NOT NULL DEFAULT 'active',
    sort_order      INT NOT NULL DEFAULT 100,
    created_by      INT,
    created_at      VARCHAR(20) NOT NULL,
    updated_at      VARCHAR(20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS batches (
    id          INT PRIMARY KEY AUTO_INCREMENT,
    program_id  INT,
    name        VARCHAR(128) NOT NULL,
    note        VARCHAR(512) DEFAULT '',
    status      VARCHAR(16) NOT NULL DEFAULT 'draft',
    created_by  INT,
    created_at  VARCHAR(20) NOT NULL,
    updated_at  VARCHAR(20),
    INDEX idx_batches_prog (program_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS files (
    id           INT PRIMARY KEY AUTO_INCREMENT,
    batch_id     INT NOT NULL,
    student_name VARCHAR(64) DEFAULT '',
    orig_name    VARCHAR(255) NOT NULL,
    stored_path  VARCHAR(512) NOT NULL,
    sha256       VARCHAR(64) DEFAULT '',
    size         BIGINT DEFAULT 0,
    n_items      INT DEFAULT 0,
    n_images     INT DEFAULT 0,
    status       VARCHAR(24) NOT NULL DEFAULT 'uploaded',
    error        TEXT,
    analyzed_at  VARCHAR(20),
    created_at   VARCHAR(20) NOT NULL,
    UNIQUE KEY uk_batch_name (batch_id, orig_name),
    INDEX idx_files_batch (batch_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS refdata (
    id          INT PRIMARY KEY AUTO_INCREMENT,
    program_id  INT,
    kind        VARCHAR(24) NOT NULL,
    name        VARCHAR(255) NOT NULL,
    stored_path VARCHAR(512) NOT NULL,
    size        BIGINT DEFAULT 0,
    note        VARCHAR(512) DEFAULT '',
    uploaded_by INT,
    created_at  VARCHAR(20) NOT NULL,
    INDEX idx_refdata_prog (program_id, kind)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rules_overrides (
    rule_key   VARCHAR(96) PRIMARY KEY,
    rule_value VARCHAR(512) NOT NULL,
    updated_by INT,
    updated_at VARCHAR(20) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS program_rules (
    program_id INT NOT NULL,
    rule_key   VARCHAR(96) NOT NULL,
    rule_value VARCHAR(512) NOT NULL,
    updated_by INT,
    updated_at VARCHAR(20) NOT NULL,
    PRIMARY KEY (program_id, rule_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS rule_nl (
    id           INT PRIMARY KEY AUTO_INCREMENT,
    program_id   INT,
    nl_text      TEXT NOT NULL,
    dsl          TEXT,
    status       VARCHAR(16) NOT NULL DEFAULT 'draft',
    priority     INT NOT NULL DEFAULT 100,
    compile_note TEXT,
    created_by   INT,
    created_at   VARCHAR(20) NOT NULL,
    updated_at   VARCHAR(20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS model_configs (
    id          INT PRIMARY KEY AUTO_INCREMENT,
    name        VARCHAR(64) NOT NULL,
    base_url    VARCHAR(255) NOT NULL,
    model       VARCHAR(128) NOT NULL,
    api_key_enc BLOB,
    is_active   TINYINT NOT NULL DEFAULT 0,
    extra       TEXT,
    created_by  INT,
    created_at  VARCHAR(20) NOT NULL,
    updated_at  VARCHAR(20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tasks (
    id             INT PRIMARY KEY AUTO_INCREMENT,
    kind           VARCHAR(24) NOT NULL,
    batch_id       INT,
    file_id        INT,
    payload        TEXT,
    status         VARCHAR(16) NOT NULL DEFAULT 'queued',
    phase          VARCHAR(64) DEFAULT '',
    progress_done  INT NOT NULL DEFAULT 0,
    progress_total INT NOT NULL DEFAULT 0,
    message        VARCHAR(512) DEFAULT '',
    error          TEXT,
    created_by     INT,
    created_at     VARCHAR(20) NOT NULL,
    started_at     VARCHAR(20),
    finished_at    VARCHAR(20),
    claimed_by     VARCHAR(96) DEFAULT '',
    INDEX idx_tasks_status (status, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS match_states (
    file_id    INT NOT NULL,
    item_id    INT NOT NULL,
    decision   VARCHAR(16) NOT NULL,
    img_sha    VARCHAR(64) DEFAULT '',
    img_file   VARCHAR(128) DEFAULT '',
    note       VARCHAR(512) DEFAULT '',
    is_manual  TINYINT NOT NULL DEFAULT 1,
    updated_by INT,
    updated_at VARCHAR(20) NOT NULL,
    PRIMARY KEY (file_id, item_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS orphan_adoptions (
    file_id    INT NOT NULL,
    img_sha    VARCHAR(64) NOT NULL,
    award_name VARCHAR(255) NOT NULL,
    section    VARCHAR(64) DEFAULT '',
    adopted_by INT,
    created_at VARCHAR(20) NOT NULL,
    PRIMARY KEY (file_id, img_sha)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS api_usage (
    day    VARCHAR(12) NOT NULL,
    model  VARCHAR(128) NOT NULL DEFAULT '',
    calls  INT NOT NULL DEFAULT 0,
    images INT NOT NULL DEFAULT 0,
    PRIMARY KEY (day, model)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_log (
    id     INT PRIMARY KEY AUTO_INCREMENT,
    actor  INT,
    action VARCHAR(64) NOT NULL,
    target VARCHAR(128) DEFAULT '',
    detail VARCHAR(512) DEFAULT '',
    at     VARCHAR(20) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS app_settings (
    k          VARCHAR(64) PRIMARY KEY,
    v          VARCHAR(512) DEFAULT '',
    updated_by INT,
    updated_at VARCHAR(20)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


def schema_for(dialect_name=None):
    return SCHEMA_MYSQL if (dialect_name or DIALECT) == 'mysql' else SCHEMA_SQLITE


# ------------------------------------------------------------------ 连接
def _sqlite_connect(path=None):
    import sqlite3
    conn = sqlite3.connect(path or WebConfig.DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=5000')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _mysql_connect(with_db=True):
    import pymysql
    from pymysql.cursors import DictCursor
    kw = dict(host=WebConfig.MYSQL_HOST, port=int(WebConfig.MYSQL_PORT),
              user=WebConfig.MYSQL_USER, password=WebConfig.MYSQL_PASSWORD,
              charset=WebConfig.MYSQL_CHARSET, cursorclass=DictCursor,
              autocommit=True, connect_timeout=8, read_timeout=180, write_timeout=180)
    if with_db:
        kw['database'] = WebConfig.MYSQL_DB
    return pymysql.connect(**kw)


def _shared():
    """读写用的长连接。MySQL 每线程复用一个，避免每次查询都重新认证。"""
    if not is_mysql():
        return _sqlite_connect()
    c = getattr(_local, 'conn', None)
    if c is None:
        c = _mysql_connect()
        _local.conn = c
    else:
        try:
            c.ping(reconnect=True)
        except Exception:
            try:
                c.close()
            except Exception:
                pass
            c = _mysql_connect()
            _local.conn = c
    return c


class Conn(object):
    """统一游标接口：execute() 返回原生游标，两侧 fetchone/fetchall/rowcount 都兼容。"""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, args=()):
        cur = self.raw.cursor()
        cur.execute(_sql(sql), args)
        return cur

    def executescript(self, script):
        if not is_mysql():
            self.raw.executescript(script)
            return
        for stmt in _split_sql(script):
            cur = self.raw.cursor()
            cur.execute(stmt)

    def close(self):
        if is_mysql():
            return          # MySQL 走线程复用，不在这里关
        try:
            self.raw.close()
        except Exception:
            pass


def _split_sql(script):
    lines = []
    for line in script.split('\n'):
        s = line.strip()
        if not s or s.startswith('--') or s.startswith('#'):
            continue
        lines.append(s)
    return [x.strip() for x in ' '.join(lines).split(';') if x.strip()]


def connect():
    return Conn(_shared())


@contextmanager
def tx():
    """
    短事务。MySQL 用**独立连接**（autocommit=0），避免与线程复用的共享连接互相干扰；
    SQLite 用 BEGIN IMMEDIATE 抢占写锁。
    """
    if is_mysql():
        raw = _mysql_connect()
        try:
            raw.begin()
        except Exception:
            pass
        c = Conn(raw)
        try:
            yield c
            raw.commit()
        except Exception:
            try:
                raw.rollback()
            except Exception:
                pass
            raise
        finally:
            try:
                raw.close()
            except Exception:
                pass
    else:
        raw = _sqlite_connect()
        try:
            raw.execute('BEGIN IMMEDIATE')
            yield Conn(raw)
            raw.execute('COMMIT')
        except Exception:
            try:
                raw.execute('ROLLBACK')
            except Exception:
                pass
            raise
        finally:
            try:
                raw.close()
            except Exception:
                pass


# ------------------------------------------------------------------ 查询与写入
def utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')


def utcnow_minus(minutes):
    """时间比较在 Python 侧算好再传参——绕开各家的日期函数差异。"""
    dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=minutes)
    return dt.strftime('%Y-%m-%dT%H:%M:%S')


def _fetch(cur, one):
    out = [dict(r) for r in cur.fetchall()]
    if one:
        return out[0] if out else None
    return out


def q(sql, args=(), one=False):
    """查询，统一返回 dict / [dict] / None。"""
    c = connect()
    try:
        return _fetch(c.execute(sql, args), one)
    finally:
        c.close()


# 兼容旧名（原先 q 返回 Row、q_dict 返回 dict，现在统一成 dict）
q_dict = q


def ex(sql, args=()):
    """执行写语句，返回自增主键。"""
    c = connect()
    try:
        return c.execute(sql, args).lastrowid
    finally:
        c.close()


def ex_rowcount(sql, args=()):
    c = connect()
    try:
        return c.execute(sql, args).rowcount
    finally:
        c.close()


def insert(table, values):
    cols = list(values.keys())
    sql = 'INSERT INTO %s (%s) VALUES (%s)' % (
        table, ', '.join(cols), ', '.join(['?'] * len(cols)))
    return ex(sql, tuple(values[k] for k in cols))


def upsert_sql(table, values, update_cols=None, key_cols=None):
    """
    生成跨方言 upsert 语句，返回 (sql, args)。

    单独抽出来是因为要在**事务内**执行时没法直接调 upsert()
    （那会另开一条连接，破坏原子性）：
        with db.tx() as conn:
            sql, args = db.upsert_sql('match_states', {...}, key_cols=['file_id','item_id'])
            conn.execute(sql, args)
    """
    cols = list(values.keys())
    args = tuple(values[k] for k in cols)
    upd = update_cols or [c for c in cols if c not in (key_cols or [])]
    if is_mysql():
        sets = ', '.join('%s=VALUES(%s)' % (c, c) for c in upd)
        sql = ('INSERT INTO %s (%s) VALUES (%s) ON DUPLICATE KEY UPDATE %s'
               % (table, ', '.join(cols), ', '.join(['?'] * len(cols)), sets))
    else:
        sets = ', '.join('%s=excluded.%s' % (c, c) for c in upd)
        conflict = '(%s)' % ', '.join(key_cols) if key_cols else ''
        sql = ('INSERT INTO %s (%s) VALUES (%s) ON CONFLICT%s DO UPDATE SET %s'
               % (table, ', '.join(cols), ', '.join(['?'] * len(cols)), conflict, sets))
    # 直接返回当前方言可执行的语句。Conn.execute() 还会再翻一次，
    # 但那时已经不含 `?`，是幂等的——契约清楚比省一次 replace 重要。
    return _sql(sql), args


def upsert(table, values, update_cols=None, key_cols=None):
    sql, args = upsert_sql(table, values, update_cols, key_cols)
    return ex(sql, args)


def get_setting(key, default=None):
    try:
        r = q('SELECT v FROM app_settings WHERE k=?', (key,), one=True)
        return r['v'] if r and r.get('v') is not None else default
    except Exception:
        return default


def set_setting(key, value, user_id=None):
    upsert('app_settings',
           {'k': key, 'v': str(value), 'updated_by': user_id, 'updated_at': utcnow()},
           update_cols=['v', 'updated_by', 'updated_at'], key_cols=['k'])


def get_bool(key, default=False):
    v = get_setting(key)
    if v is None:
        return default
    return str(v).strip().lower() in ('1', 'true', 'yes', 'on')


def audit(actor, action, target='', detail=''):
    try:
        ex('INSERT INTO audit_log(actor,action,target,detail,at) VALUES(?,?,?,?,?)',
           (actor, action, str(target), (detail or '')[:500], utcnow()))
    except Exception:
        pass


def jloads(s, default=None):
    try:
        return json.loads(s) if s else (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def row_to_dict(row):
    return dict(row) if row is not None else None


# ------------------------------------------------------------------ 建库与自检
def ensure_database():
    """MySQL：库不存在就建（账号需有 CREATE 权限）。"""
    if not is_mysql():
        return False
    try:
        raw = _mysql_connect(with_db=False)
    except Exception as e:
        raise RuntimeError('连接 MySQL 失败：%s（请检查 WB_MYSQL_* 配置）' % e)
    try:
        raw.cursor().execute(
            'CREATE DATABASE IF NOT EXISTS `%s` DEFAULT CHARACTER SET utf8mb4 '
            'COLLATE utf8mb4_unicode_ci' % WebConfig.MYSQL_DB)
    finally:
        raw.close()
    return True


def init_db(create_db=False):
    """建表（不含索引，索引由 ensure_indexes 建，见下方说明）。"""
    WebConfig.ensure_dirs()
    if create_db:
        ensure_database()
    c = connect()
    try:
        c.executescript(schema_for())
    finally:
        c.close()


def ensure_indexes():
    if is_mysql():
        return []          # MySQL 的索引写在 CREATE TABLE 里，不需要单独建
    c = connect()
    try:
        c.executescript(SCHEMA_SQLITE_INDEXES)
    except Exception as e:
        return ['!indexes (%s)' % e]
    finally:
        c.close()
    return []


# ------------------------------------------------------------------ 迁移
# `CREATE TABLE IF NOT EXISTS` 不会给**已存在**的表补列，所以老库要靠 ALTER 补。
# 这里只列"新增过的列"，按需往下追加，幂等。
ADDED_COLUMNS = [
    ('batches', 'program_id', 'INT'),
    ('refdata', 'program_id', 'INT'),
    ('refdata', 'note', "VARCHAR(512) DEFAULT ''"),
    ('programs', 'template_path', "VARCHAR(512) DEFAULT ''"),
    # 人工否定的条目导出时怎么处理：'' = 跟随全局默认，remove = 剔除，mark = 保留并标注
    ('programs', 'reject_mode', "VARCHAR(16) DEFAULT ''"),
]
# 列名变更（旧列 → 新列 + MySQL 需要的类型）
RENAMED_COLUMNS = [
    ('rules_overrides', 'key', 'rule_key', 'VARCHAR(96) NOT NULL'),
    ('rules_overrides', 'value', 'rule_value', 'VARCHAR(512) NOT NULL'),
]


def columns(table):
    if is_mysql():
        rows = q('SELECT COLUMN_NAME c FROM information_schema.COLUMNS'
                 ' WHERE TABLE_SCHEMA=? AND TABLE_NAME=?',
                 (WebConfig.MYSQL_DB, table))
        return {str(r['c']).lower() for r in rows}
    try:
        return {str(r['name']).lower() for r in q('PRAGMA table_info(%s)' % table)}
    except Exception:
        return set()


def migrate(verbose=False):
    """建表 → 补列/改列名 → 建索引。可在每次启动时安全调用（幂等）。"""
    init_db()
    done = []
    existing = set(table_names())
    for table, col, decl in ADDED_COLUMNS:
        if table not in existing:
            continue
        have = columns(table)
        if col.lower() in have:
            continue
        d = decl
        if not is_mysql() and d.startswith('VARCHAR'):
            d = 'TEXT'
        try:
            ex('ALTER TABLE %s ADD COLUMN %s %s' % (table, col, d))
            done.append('+%s.%s' % (table, col))
        except Exception as e:
            done.append('!%s.%s (%s)' % (table, col, e))

    for table, old, new, decl in RENAMED_COLUMNS:
        if table not in existing:
            continue
        have = columns(table)
        if new.lower() in have or old.lower() not in have:
            continue
        try:
            if is_mysql():
                ex('ALTER TABLE %s CHANGE COLUMN `%s` %s %s' % (table, old, new, decl))
            else:
                ex('ALTER TABLE %s RENAME COLUMN "%s" TO %s' % (table, old, new))
            done.append('~%s.%s→%s' % (table, old, new))
        except Exception as e:
            done.append('!%s.%s→%s (%s)' % (table, old, new, e))
    done += ensure_indexes()
    if verbose and done:
        print('数据库迁移：', ', '.join(done))
    return done


def table_names():
    if is_mysql():
        rows = q('SELECT TABLE_NAME t FROM information_schema.TABLES WHERE TABLE_SCHEMA=?',
                 (WebConfig.MYSQL_DB,))
    else:
        rows = q("SELECT name t FROM sqlite_master WHERE type='table'"
                 " AND name NOT LIKE 'sqlite_%'")
    return sorted(r['t'] for r in rows)


def self_check():
    """连通性自检，给部署工具用。"""
    info = {'dialect': DIALECT}
    try:
        c = connect()
        try:
            if is_mysql():
                r = dict(c.execute('SELECT VERSION() v, DATABASE() d').fetchone() or {})
                info.update({'server': 'MySQL ' + str(r.get('v')), 'database': r.get('d')})
            else:
                cur = c.execute('SELECT sqlite_version() v')
                row = cur.fetchone()
                v = list(row.values())[0] if isinstance(row, dict) else list(row)[0]
                info.update({'server': 'SQLite ' + str(v), 'database': WebConfig.DB_PATH})
        finally:
            c.close()
        info['tables'] = len(table_names())
        n = q('SELECT COUNT(*) c FROM users', one=True)
        info['users'] = (n or {}).get('c', 0)
        info['ok'] = True
    except Exception as e:
        info['ok'] = False
        info['error'] = '%s: %s' % (type(e).__name__, e)
    return info
