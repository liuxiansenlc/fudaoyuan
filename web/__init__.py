# -*- coding: utf-8 -*-
"""Flask 应用工厂。"""
import os
import datetime

from flask import Flask, session, jsonify, request, g, redirect, url_for

from .config import WebConfig


def create_app(init=True):
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.config.from_object(WebConfig)
    WebConfig.ensure_dirs()

    # 生产模式下 Flask 会**把模板缓存在内存里**——改了 .html 不重启就一直用旧的，
    # 表现是"代码明明改对了页面还是 500"，极容易误判成代码 bug。
    # Jinja 的自动重载只是比对 mtime，开销很小，这里默认打开。
    import os as _os
    app.config['TEMPLATES_AUTO_RELOAD'] = _os.environ.get('WB_TEMPLATE_RELOAD', '1') == '1'
    app.jinja_env.auto_reload = app.config['TEMPLATES_AUTO_RELOAD']

    from .security import get_secret_key, ensure_bootstrap_admin
    app.secret_key = get_secret_key()

    from . import db
    if init:
        # MySQL 首次部署时自动建库（可用 WB_MYSQL_AUTO_CREATE=0 关掉）
        db.init_db(create_db=(db.is_mysql() and app.config.get('MYSQL_AUTO_CREATE')))
        # 老库补列/改列名（幂等）
        db.migrate(verbose=bool(os.environ.get('WB_VERBOSE')))
        ensure_bootstrap_admin()

    # ---------------- 蓝图 ----------------
    from .blueprints.auth import bp as auth_bp
    from .blueprints.main import bp as main_bp
    from .blueprints.review import bp as review_bp
    from .blueprints.config_bp import bp as config_bp
    from .blueprints.api import bp as api_bp
    from .blueprints.programs_bp import bp as programs_bp
    app.register_blueprint(auth_bp)
    app.register_blueprint(main_bp)
    app.register_blueprint(review_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(api_bp)
    app.register_blueprint(programs_bp)

    # ---------------- 全局钩子 ----------------
    from .security import current_user, check_csrf

    @app.before_request
    def _csrf_guard():
        if request.method in ('GET', 'HEAD', 'OPTIONS'):
            return None
        if request.path.startswith('/static/'):
            return None
        if not check_csrf():
            if request.path.startswith('/api/'):
                return jsonify(ok=False, error='CSRF 校验失败，请刷新页面'), 400
            return '会话已过期，请返回重新操作', 400
        return None

    @app.context_processor
    def _inject():
        from .security import csrf_token, usage_today, using_default_password
        try:
            calls, images = usage_today()
        except Exception:
            calls, images = 0, 0
        from . import db as _db
        _u = current_user()
        return {
            'cu': _u,
            'default_pwd_warn': using_default_password(_u),
            'db_dialect': _db.dialect(),
            'csrf': csrf_token,
            'now': datetime.datetime.now().strftime('%Y-%m-%d %H:%M'),
            'usage_calls': calls,
            'usage_images': images,
            'app_name': '奖学金材料审核工作台',
        }

    @app.template_filter('dt')
    def _fmt_dt(v, fmt='%Y-%m-%d %H:%M'):
        if not v:
            return ''
        try:
            s = str(v).replace('Z', '')
            dt = datetime.datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            # DB 存 UTC，界面统一按北京时间显示
            dt = dt.astimezone(datetime.timezone(datetime.timedelta(hours=8)))
            return dt.strftime(fmt)
        except Exception:
            return str(v)

    @app.errorhandler(403)
    def _403(e):
        return '没有权限访问该页面', 403

    @app.errorhandler(404)
    def _404(e):
        if request.path.startswith('/api/'):
            return jsonify(ok=False, error='接口不存在'), 404
        return '页面不存在', 404

    # ---------------- 内嵌 worker ----------------
    from .services import task_service
    task_service.start_inline_worker(app)

    return app
