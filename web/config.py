# -*- coding: utf-8 -*-
"""
Web 层配置。

设计原则：Web 层只负责「输入输出 + 编排」，判定逻辑一律留在 engine/。
所以这里只管：数据目录、上传限制、会话与安全、队列节奏、模型默认值。
"""
import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE_DIR = os.path.join(REPO_ROOT, 'engine')
DATA_DIR = os.path.join(REPO_ROOT, 'data')


def _load_dotenv():
    """
    读取仓库根目录的 .env（KEY=VALUE 一行一条）。

    自己写十行解析，不引 python-dotenv：宝塔上少一个依赖，
    而且这个格式简单到不值得为它装包。已存在的环境变量优先，不覆盖。
    """
    path = os.path.join(REPO_ROOT, '.env')
    if not os.path.exists(path):
        return
    try:
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#') or '=' not in s:
                    continue
                k, v = s.split('=', 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
    except Exception:
        pass


_load_dotenv()


class WebConfig(object):
    # ---- 基础 ----
    REPO_ROOT = REPO_ROOT
    ENGINE_DIR = ENGINE_DIR
    SECRET_KEY_FILE = os.path.join(DATA_DIR, 'instance', 'secret.key')
    API_KEY_FILE = os.path.join(DATA_DIR, 'instance', 'api_key.key')

    # ---- 数据库 ----
    # sqlite（默认，本地开发/测试零依赖） | mysql（正式部署）
    # 用环境变量 WB_DB=mysql 切换
    DB_ENGINE = os.environ.get('WB_DB', 'sqlite').lower()

    MYSQL_HOST = os.environ.get('WB_MYSQL_HOST', '127.0.0.1')
    MYSQL_PORT = int(os.environ.get('WB_MYSQL_PORT', '3306'))
    MYSQL_USER = os.environ.get('WB_MYSQL_USER', 'root')
    MYSQL_PASSWORD = os.environ.get('WB_MYSQL_PASSWORD', '')
    MYSQL_DB = os.environ.get('WB_MYSQL_DB', 'scholarship_audit')
    MYSQL_CHARSET = 'utf8mb4'
    # 启动时自动建库（首次部署方便；之后可关）
    MYSQL_AUTO_CREATE = os.environ.get('WB_MYSQL_AUTO_CREATE', '1') == '1'

    # ---- 数据目录 ----
    DATA_DIR = DATA_DIR
    # 允许用 WB_DB_PATH 指定数据库文件，便于测试用独立库、不碰线上数据
    DB_PATH = os.environ.get('WB_DB_PATH') or os.path.join(DATA_DIR, 'app.db')
    UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')     # 上传的 docx / 参考表
    RESULT_DIR = os.path.join(DATA_DIR, 'results')     # 每个学生的分析结果 json
    EXPORT_DIR = os.path.join(DATA_DIR, 'exports')
    IMAGE_DIR = os.path.join(DATA_DIR, 'images')       # 浏览器可访问的图片副本
    LOG_DIR = os.path.join(DATA_DIR, 'logs')

    # ---- 引擎缓存：沿用仓库里已有的 cache/，这样 187 张已读图立刻复用 ----
    ENGINE_CACHE_DIR = os.path.join(REPO_ROOT, 'cache')
    RULES_FILE = os.path.join(ENGINE_DIR, 'rules_builtin.json')

    # 导出标准化 Word 的默认模板。
    # ★ 必须用**仓库内相对路径**：早先写死成开发机的 D:/... 绝对路径，
    #   服务器上根本不存在，导致每份导出都失败、最后打包出一个空 zip。
    #   可用 WB_TEMPLATE_DOCX 覆盖；各奖学金项目还可在界面上传自己的模板（优先级更高）。
    TEMPLATE_DOCX = os.environ.get('WB_TEMPLATE_DOCX') or os.path.join(
        ENGINE_DIR, 'assets', 'template.docx')

    # ---- 上传 ----
    ALLOWED_DOCX = {'.docx'}
    ALLOWED_REF = {'.xls', '.xlsx', '.csv'}
    MAX_CONTENT_LENGTH = 200 * 1024 * 1024      # 200MB，nginx 也要同步放开
    MAX_FILES_PER_UPLOAD = 200

    # ---- 队列 ----
    # 开发机/单进程：Web 进程内起后台线程跑任务（省一个进程）
    # 宝塔生产：设为 False，改用 `python worker.py` 独立进程，
    #          这样 gunicorn 重启不会打断正在跑的读图任务
    INLINE_WORKER = os.environ.get('WB_INLINE_WORKER', '1') == '1'
    TASK_POLL_INTERVAL_MS = 900                  # 前端轮询间隔
    VISION_CONCURRENCY = 4                       # worker 内读图并发
    VISION_MAX_SIDE = 1600                       # 送模型前缩到长边 1600
    VISION_DAILY_LIMIT = 2000                    # 每日读图上限（成本闸门）
    # 任务暂停的最长时长（秒）。超过后自动继续，防止"暂停后无人回来点继续"
    # 把单线程 worker 永久占住、拖垮整个队列。默认 15 分钟。
    TASK_MAX_PAUSE_SECONDS = int(os.environ.get('WB_MAX_PAUSE_SECONDS', '900'))

    # ---- 会话 ----
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = False                # 上了 HTTPS 再改 True
    PERMANENT_SESSION_LIFETIME = 12 * 3600
    ALLOW_SELF_REGISTER = False                  # 关闭自助注册，仅管理员建号

    # ---- 模型默认值（界面可改）----
    DEFAULT_BASE_URL = 'https://api.openai.com/v1'
    DEFAULT_MODEL = 'gpt-4o-mini'

    # ---- 首次启动自动建的管理员 ----
    BOOTSTRAP_ADMIN = ('admin', os.environ.get('WB_ADMIN_PWD', '123456'))
    # 这个密码是公开的默认值，登录后会一直提醒改掉
    DEFAULT_ADMIN_PASSWORD = '123456'

    @staticmethod
    def ensure_dirs():
        for d in (WebConfig.DATA_DIR, os.path.dirname(WebConfig.DB_PATH),
                  os.path.join(WebConfig.DATA_DIR, 'instance'),
                  WebConfig.UPLOAD_DIR, WebConfig.RESULT_DIR,
                  WebConfig.EXPORT_DIR, WebConfig.IMAGE_DIR, WebConfig.LOG_DIR):
            os.makedirs(d, exist_ok=True)
