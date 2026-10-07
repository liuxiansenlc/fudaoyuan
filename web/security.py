# -*- coding: utf-8 -*-
"""
安全相关：会话密钥、密码哈希、CSRF、登录校验、api_key 加密。

不用 Flask-Login / Flask-WTF：这两者在"内部工具、无自助注册"的场景里
带来的便利有限，但会多两个依赖。这里用 Flask 自带的 session + 手写装饰器，
行为更透明、部署更省事。
"""
import os
import hmac
import base64
import functools
import datetime

from flask import session, request, redirect, url_for, jsonify, g, abort
from werkzeug.security import generate_password_hash, check_password_hash

from .config import WebConfig
from . import db


# ------------------------------------------------------------------ 密钥文件
def _load_or_create_key(path, nbytes=32):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read()
    key = os.urandom(nbytes)
    with open(path, 'wb') as f:
        f.write(key)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass
    return key


def get_secret_key():
    return _load_or_create_key(WebConfig.SECRET_KEY_FILE, 48)


# ------------------------------------------------------------------ 密码
def hash_password(raw):
    return generate_password_hash(raw, method='pbkdf2:sha256:120000')


def verify_password(pwd_hash, raw):
    try:
        return check_password_hash(pwd_hash, raw)
    except Exception:
        return False


# ------------------------------------------------------------------ api_key 加密
def _fernet():
    from cryptography.fernet import Fernet
    key = _load_or_create_key(WebConfig.API_KEY_FILE)
    return Fernet(base64.urlsafe_b64encode(key[:32]))


def encrypt_secret(text):
    if not text:
        return None
    return _fernet().encrypt(text.encode('utf-8'))


def decrypt_secret(blob):
    if not blob:
        return ''
    try:
        return _fernet().decrypt(bytes(blob)).decode('utf-8')
    except Exception:
        return ''


def mask_secret(text):
    """界面回显用：只露头尾。"""
    if not text:
        return ''
    if len(text) <= 10:
        return '*' * len(text)
    return text[:4] + '*' * 8 + text[-4:]


# ------------------------------------------------------------------ CSRF
def csrf_token():
    tok = session.get('_csrf')
    if not tok:
        tok = base64.urlsafe_b64encode(os.urandom(24)).decode('ascii').rstrip('=')
        session['_csrf'] = tok
    return tok


def check_csrf():
    if request.method in ('GET', 'HEAD', 'OPTIONS'):
        return True
    sent = (request.headers.get('X-CSRF-Token')
            or request.form.get('_csrf') or '')
    tok = session.get('_csrf') or ''
    return bool(tok) and hmac.compare_digest(str(sent), str(tok))


# ------------------------------------------------------------------ 登录
def current_user():
    if getattr(g, '_user', None) is not None:
        return g._user
    uid = session.get('uid')
    g._user = db.q_dict('SELECT * FROM users WHERE id=? AND is_active=1', (uid,), one=True) \
        if uid else None
    return g._user


def login_user(user):
    session.clear()
    session['uid'] = user['id']
    session['uname'] = user['username']
    session.permanent = True
    g._user = user


def logout_user():
    session.clear()
    g._user = None


def login_required(view):
    @functools.wraps(view)
    def wrapper(*a, **kw):
        if current_user() is None:
            if request.path.startswith('/api/') or request.is_json:
                return jsonify(ok=False, error='未登录', need_login=True), 401
            return redirect(url_for('auth.login', next=request.path))
        return view(*a, **kw)
    return wrapper


def admin_required(view):
    @functools.wraps(view)
    def wrapper(*a, **kw):
        u = current_user()
        if u is None:
            if request.path.startswith('/api/'):
                return jsonify(ok=False, error='未登录', need_login=True), 401
            return redirect(url_for('auth.login', next=request.path))
        if not u.get('is_admin'):
            abort(403)
        return view(*a, **kw)
    return wrapper


def using_default_password(user):
    """还在用公开的默认密码？（登录后应当一直提醒改掉）"""
    if not user:
        return False
    try:
        return verify_password(user.get('pwd_hash') or '',
                               WebConfig.DEFAULT_ADMIN_PASSWORD)
    except Exception:
        return False


def ensure_bootstrap_admin():
    """首次启动建一个管理员，避免登录页进不去。"""
    n = db.q('SELECT COUNT(*) c FROM users', one=True)['c']
    if n == 0:
        u, p = WebConfig.BOOTSTRAP_ADMIN
        db.ex('INSERT INTO users(username,display_name,pwd_hash,is_admin,created_at)'
              ' VALUES(?,?,?,?,?)',
              (u, '管理员', hash_password(p), 1, db.utcnow()))
        return u, p
    return None


def record_usage(model, images=1, calls=1):
    day = datetime.datetime.now().strftime('%Y-%m-%d')
    with db.tx() as conn:
        # 累加式 upsert：MySQL 用 VALUES()，SQLite 用 excluded.，两者写法一致
        row = db.q('SELECT calls,images FROM api_usage WHERE day=? AND model=?',
                   (day, model or ''), one=True)
        if row:
            sql, args = db.upsert_sql(
                'api_usage',
                {'day': day, 'model': model or '',
                 'calls': int(row.get('calls') or 0) + calls,
                 'images': int(row.get('images') or 0) + images},
                key_cols=['day', 'model'])
        else:
            sql, args = db.upsert_sql(
                'api_usage',
                {'day': day, 'model': model or '', 'calls': calls, 'images': images},
                key_cols=['day', 'model'])
        conn.execute(sql, args)


def usage_today():
    day = datetime.datetime.now().strftime('%Y-%m-%d')
    row = db.q_dict('SELECT SUM(calls) c, SUM(images) i FROM api_usage WHERE day=?',
                    (day,), one=True) or {}
    return int(row.get('c') or 0), int(row.get('i') or 0)
