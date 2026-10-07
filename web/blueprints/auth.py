# -*- coding: utf-8 -*-
"""登录 / 用户管理。无自助注册，仅管理员建号。"""
from flask import (Blueprint, render_template, request, redirect, url_for,
                   flash, jsonify)

from .. import db
from ..security import (login_user, logout_user, current_user, login_required,
                        admin_required, hash_password, verify_password)

bp = Blueprint('auth', __name__)


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user():
        return redirect(url_for('main.dashboard'))
    err = None
    if request.method == 'POST':
        u = (request.form.get('username') or '').strip()
        p = request.form.get('password') or ''
        row = db.q_dict('SELECT * FROM users WHERE username=?', (u,), one=True)
        if not row or not row['is_active']:
            err = '账号不存在或已停用'
        elif not verify_password(row['pwd_hash'], p):
            err = '密码不正确'
        else:
            login_user(row)
            db.audit(row['id'], 'login', request.remote_addr or '')
            nxt = request.args.get('next') or url_for('main.dashboard')
            if not nxt.startswith('/'):
                nxt = url_for('main.dashboard')
            return redirect(nxt)
    return render_template('login.html', err=err)


@bp.route('/logout')
def logout():
    u = current_user()
    if u:
        db.audit(u['id'], 'logout')
    logout_user()
    return redirect(url_for('auth.login'))


@bp.route('/profile')
@login_required
def profile():
    from ..security import using_default_password
    u = current_user()
    return render_template('profile.html', u=u,
                           is_default=using_default_password(u))


@bp.route('/api/profile/password', methods=['POST'])
@login_required
def api_change_password():
    u = current_user()
    d = request.get_json(silent=True) or {}
    old = d.get('old') or ''
    new = d.get('new') or ''
    if not verify_password(u['pwd_hash'], old):
        return jsonify(ok=False, error='当前密码不正确'), 400
    if len(new) < 6:
        return jsonify(ok=False, error='新密码至少 6 位'), 400
    if new == old:
        return jsonify(ok=False, error='新密码不能与当前密码相同'), 400
    db.ex('UPDATE users SET pwd_hash=? WHERE id=?', (hash_password(new), u['id']))
    db.audit(u['id'], 'user.change_pwd', u['id'])
    return jsonify(ok=True)


@bp.route('/users')
@admin_required
def users():
    rows = db.q_dict('SELECT id,username,display_name,is_admin,is_active,created_at'
                     ' FROM users ORDER BY id')
    return render_template('users.html', users=rows)


@bp.route('/api/users', methods=['POST'])
@admin_required
def api_users():
    data = request.get_json(silent=True) or request.form
    action = data.get('action')
    me = current_user()

    if action == 'create':
        u = (data.get('username') or '').strip()
        p = data.get('password') or ''
        if not u or len(p) < 6:
            return jsonify(ok=False, error='用户名不能为空，密码至少 6 位'), 400
        if db.q('SELECT 1 FROM users WHERE username=?', (u,)):
            return jsonify(ok=False, error='用户名已存在'), 400
        db.ex('INSERT INTO users(username,display_name,pwd_hash,is_admin,created_at)'
              ' VALUES(?,?,?,?,?)',
              (u, (data.get('display_name') or '').strip(),
               hash_password(p), 1 if data.get('is_admin') else 0, db.utcnow()))
        db.audit(me['id'], 'user.create', u)
        return jsonify(ok=True)

    if action == 'toggle':
        uid = int(data.get('id') or 0)
        if uid == me['id']:
            return jsonify(ok=False, error='不能停用自己'), 400
        row = db.q_dict('SELECT * FROM users WHERE id=?', (uid,), one=True)
        if not row:
            return jsonify(ok=False, error='用户不存在'), 404
        db.ex('UPDATE users SET is_active=? WHERE id=?',
              (0 if row['is_active'] else 1, uid))
        db.audit(me['id'], 'user.toggle', uid)
        return jsonify(ok=True)

    if action == 'reset_pwd':
        uid = int(data.get('id') or 0)
        p = data.get('password') or ''
        if len(p) < 6:
            return jsonify(ok=False, error='密码至少 6 位'), 400
        db.ex('UPDATE users SET pwd_hash=? WHERE id=?', (hash_password(p), uid))
        db.audit(me['id'], 'user.reset_pwd', uid)
        return jsonify(ok=True)

    return jsonify(ok=False, error='未知操作'), 400
