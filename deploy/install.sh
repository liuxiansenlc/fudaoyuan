#!/usr/bin/env bash
# ============================================================================
# 奖学金材料审核工作台 · 宝塔 Ubuntu 24 一键部署脚本
# 环境：宝塔面板 + /www/wwwroot/ 路径 + MySQL(dy 账号) + 域名 dy.supermans.top
# 用法：把整个项目文件夹上传/拷贝到 /www/wwwroot/fudao 后，在项目根目录执行：
#       sudo bash deploy/install.sh
# ============================================================================
set -euo pipefail

# ---------------------- 部署配置（已按你的环境填好） ----------------------
APP_NAME="fudao"
APP_DIR="/www/wwwroot/${APP_NAME}"              # 宝塔标准部署路径
VENV_DIR="${APP_DIR}/venv"
DOMAIN="dy.supermans.top"                       # 你的域名
PORT="8000"                                     # gunicorn 内部监听端口（Nginx 反代到 80）

# MySQL（宝塔里已建的 dy 账号）
DB_HOST="127.0.0.1"
DB_PORT="3306"
DB_NAME="dy"                                    # 库名（宝塔默认账号=库名）
DB_USER="dy"                                    # 用户名
# ★ 密码不要写死在脚本里（脚本会进 git）。两种方式二选一：
#   1) 运行时用环境变量传：DB_PASS=你的密码 sudo -E bash deploy/install.sh
#   2) 直接改这里（改完别把本文件推到公开仓库）
DB_PASS="${DB_PASS:-}"                          # 从环境变量读取，默认空

# ---------------------- 颜色输出 ----------------------
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
err()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }

# ---------------------- 前置检查 ----------------------
[[ $EUID -eq 0 ]] || err "请用 sudo 运行：sudo bash deploy/install.sh"
[[ -d "${APP_DIR}" ]] || err "目录 ${APP_DIR} 不存在。请先把项目放到 /www/wwwroot/fudao"

# 密码没传就在交互式终端询问（不在终端里跑则报错）
if [[ -z "${DB_PASS}" ]]; then
    if [[ -t 0 ]]; then
        read -rsp "请输入 MySQL 用户 ${DB_USER} 的密码: " DB_PASS; echo
    else
        err "请用环境变量传入密码：DB_PASS=你的密码 sudo -E bash deploy/install.sh"
    fi
fi

# ---------------------- 1. 系统依赖 ----------------------
info "安装系统依赖..."
apt-get update -y -qq
apt-get install -y -qq python3 python3-venv python3-pip python3-dev build-essential nginx 2>/dev/null \
  || apt-get install -y python3 python3-venv python3-pip python3-dev build-essential nginx

# ---------------------- 2. 目录准备 ----------------------
info "准备数据目录..."
mkdir -p "${APP_DIR}/data"/{uploads,results,images,exports,logs,instance}

# ---------------------- 3. Python 虚拟环境与依赖 ----------------------
info "创建虚拟环境并安装依赖（这一步较久，请耐心）..."
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip -q
"${VENV_DIR}/bin/pip" install -r "${APP_DIR}/requirements.txt"
info "依赖安装完成"

# ---------------------- 4. MySQL 建库建表 ----------------------
info "配置 MySQL 数据库..."
# 用 dy 账号连接；如果库不存在则尝试用 dy 自身权限建（宝塔账号通常有权限）
if ! mysql -h "${DB_HOST}" -P "${DB_PORT}" -u"${DB_USER}" -p"${DB_PASS}" -e "USE \`${DB_NAME}\`" 2>/dev/null; then
    warn "库 ${DB_NAME} 不存在，尝试创建..."
    # 先试 dy 账号自建，再退回 root（root 密码需要你在宝塔里看）
    if mysql -h "${DB_HOST}" -P "${DB_PORT}" -u"${DB_USER}" -p"${DB_PASS}" -e "CREATE DATABASE IF NOT EXISTS \`${DB_NAME}\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci" 2>/dev/null; then
        info "已用 dy 账号创建数据库 ${DB_NAME}"
    else
        warn "dy 账号无建库权限，请在宝塔「数据库」里手动建一个名为 dy 的库，然后重新运行本脚本"
    fi
fi
# 建表（带中文注释）
mysql -h "${DB_HOST}" -P "${DB_PORT}" -u"${DB_USER}" -p"${DB_PASS}" "${DB_NAME}" < "${APP_DIR}/deploy/schema.sql"
info "数据库 ${DB_NAME} 建表完成"

# ---------------------- 5. 生成 .env ----------------------
info "生成 .env 配置..."
cat > "${APP_DIR}/.env" <<EOF
WB_DB=mysql
WB_MYSQL_HOST=${DB_HOST}
WB_MYSQL_PORT=${DB_PORT}
WB_MYSQL_USER=${DB_USER}
WB_MYSQL_PASSWORD=${DB_PASS}
WB_MYSQL_DB=${DB_NAME}
WB_MYSQL_AUTO_CREATE=1
WB_PORT=${PORT}
WB_HOST=127.0.0.1
WB_INLINE_WORKER=0
WB_MASK_SENSITIVE=0
EOF
chmod 600 "${APP_DIR}/.env"

# ---------------------- 6. systemd 服务（Web + Worker） ----------------------
info "创建 systemd 服务..."
cat > /etc/systemd/system/${APP_NAME}.service <<EOF
[Unit]
Description=${APP_NAME} web service (gunicorn)
After=network.target

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/gunicorn -w 3 -b 127.0.0.1:${PORT} wsgi:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/${APP_NAME}-worker.service <<EOF
[Unit]
Description=${APP_NAME} read-image worker
After=network.target

[Service]
WorkingDirectory=${APP_DIR}
EnvironmentFile=${APP_DIR}/.env
ExecStart=${VENV_DIR}/bin/python worker.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${APP_NAME}" "${APP_NAME}-worker"

# ---------------------- 7. Nginx 配置 ----------------------
# 宝塔通常已经用「网站」管理 Nginx 站点。如果宝塔里已有 dy.supermans.top 站点，
# 这里不再写独立的 Nginx 文件（避免冲突），改由你在宝塔网页加「反向代理」。
info "配置 Nginx..."
if [[ -d "/www/server/panel" ]]; then
    warn "检测到宝塔面板。为避免与宝塔的网站管理冲突，跳过自动写 Nginx 配置。"
    warn "请在宝塔 → 网站 → dy.supermans.top → 设置 → 反向代理 添加："
    warn "    目标 URL：http://127.0.0.1:${PORT}"
    warn "    并确保 Nginx 配置里有：client_max_body_size 200m;"
else
    cat > /etc/nginx/sites-available/${APP_NAME} <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    client_max_body_size 200m;

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 300s;
        proxy_read_timeout 300s;
    }

    location /static/ {
        alias ${APP_DIR}/web/static/;
        expires 30d;
    }
}
EOF
    ln -sf /etc/nginx/sites-available/${APP_NAME} /etc/nginx/sites-enabled/${APP_NAME}
    nginx -t && systemctl reload nginx || warn "Nginx 配置校验失败"
fi

# ---------------------- 8. 启动 ----------------------
info "启动服务..."
systemctl restart "${APP_NAME}" "${APP_NAME}-worker"
sleep 2
systemctl status "${APP_NAME}" --no-pager -l || true

# ---------------------- 完成 ----------------------
cat <<DONE

${GREEN}=====================================================${NC}
${GREEN}  部署完成！${NC}
${GREEN}=====================================================${NC}

  访问地址：http://${DOMAIN}
  默认管理员：admin / 123456（首次登录后请立即修改）

  常用命令：
    systemctl restart ${APP_NAME}            # 重启 Web
    systemctl restart ${APP_NAME}-worker     # 重启读图 worker
    journalctl -u ${APP_NAME} -f             # 看 Web 日志
    journalctl -u ${APP_NAME}-worker -f      # 看 worker 日志

  更新代码后：
    cd ${APP_DIR} && 重新拷贝代码
    systemctl restart ${APP_NAME} ${APP_NAME}-worker

  ★ 部署后还需你在网页上做三件事：
    1. 「模型设置」里配置大模型 API Key
    2. 「参考数据」上传 A 类竞赛表、成绩排名表
    3. 「我的账号」改掉默认密码

  ★ 开通 HTTPS（建议）：
    宝塔 → 网站 → 设置 → SSL → 申请 Let's Encrypt 证书（一键）
DONE
