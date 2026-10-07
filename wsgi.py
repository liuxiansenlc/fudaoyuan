# -*- coding: utf-8 -*-
"""
WSGI 入口。

生产（宝塔 Linux）：
    gunicorn -w 3 -b 127.0.0.1:8000 wsgi:app

Windows 本机：
    python wsgi.py            # 用 waitress 起，等价于上面
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from web import create_app   # noqa: E402

app = create_app()


if __name__ == '__main__':
    host = os.environ.get('WB_HOST', '127.0.0.1')
    port = int(os.environ.get('WB_PORT', '8000'))
    try:
        from waitress import serve
        print('=' * 58)
        print('  奖学金审核工作台  →  http://%s:%d' % (host, port))
        print('  首次登录：admin / admin123（请登录后立即修改）')
        print('=' * 58)
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, debug=False)
