# -*- coding: utf-8 -*-
"""
读图 worker（独立进程）。

为什么独立：读一张图要调一次网络接口，一批材料可能上百张，跑几分钟很正常。
如果放在 Web 进程里，gunicorn 一重启（改代码、被宝塔守护重启）就把任务打断了。
独立进程可以随便重启 Web，任务继续跑。

启动：
    WB_INLINE_WORKER=0 python worker.py

宝塔里用「守护进程 / supervisor」托住它，崩溃自动拉起。
"""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from web import create_app                      # noqa: E402
from web.services import task_service as TS     # noqa: E402

if __name__ == '__main__':
    app = create_app()
    with app.app_context():
        print('=' * 58)
        print('  worker 已启动  id=%s' % TS.WORKER_ID)
        print('  轮询间隔 2s；Ctrl+C 退出')
        print('=' * 58)
        TS.requeue_stale(20)
        while True:
            try:
                did = TS.loop_once()
                if not did:
                    time.sleep(2.0)
            except KeyboardInterrupt:
                print('bye')
                break
            except Exception:
                import traceback
                traceback.print_exc()
                time.sleep(3.0)
