# Tick Stock Panel 部署记录

本文档记录 Tick Stock Panel 部署到远程服务器的标准流程。不要在本文档、仓库或部署压缩包中保存 SSH、数据库、TickFlow、AI 或面板密码。

## 目标环境

- 访问域名：`tick.crayfish.cloud`
- 服务器公网 IP：`211.149.157.27`
- SSH 端口：`22000`
- 服务器部署目录：`/opt/tick-stock-panel`
- Docker Compose 项目：`/opt/tick-stock-panel`
- 容器名称：`TickFlow_Stock_Panel`
- 容器内监听：`0.0.0.0:3018`
- 服务器本机监听：`127.0.0.1:3018`（由 Nginx 反向代理）
- 系统业务时区：`Asia/Shanghai`

本项目使用 Docker 两阶段构建：前端构建产物复制到后端镜像，由单个 FastAPI 服务同时提供 API 和前端静态文件。运行时数据通过 `/opt/tick-stock-panel/data:/app/data` 持久化。

## DNS

在域名解析后台添加或修改 A 记录：

```text
主机记录: tick
记录类型: A
记录值: 211.149.157.27
TTL: 默认
```

DNS 生效前可从任意能访问服务器的机器验证：

```bash
curl --resolve tick.crayfish.cloud:3018:211.149.157.27 \
  http://tick.crayfish.cloud:3018/health
```

如果通过 Nginx 使用 80/443 端口，则验证：

```bash
curl --resolve tick.crayfish.cloud:80:211.149.157.27 \
  http://tick.crayfish.cloud/api/health
```

期望返回包含 `"status":"ok"` 的 JSON。

## 前置条件

服务器需要安装并启用：

- Docker Engine
- Docker Compose v2（`docker compose`）
- Nginx（仅在使用域名 80/443 访问时需要）

本地需要准备：

- Git、`tar`、`scp` 或 `rsync`
- Node.js 20 和 pnpm（只用于本地前端验证；远程 Docker 构建会自行安装）
- Python 3.11+ 与项目 `uv` 环境（只用于本地后端验证）

## 环境变量

在服务器部署目录创建 `.env`，权限必须为 `600`：

```bash
cd /opt/tick-stock-panel
cp .env.example .env
chmod 600 .env
```

至少检查以下配置：

```dotenv
HOST=0.0.0.0
PORT=3018
TZ=Asia/Shanghai
DATA_DIR=./data

# 留空使用 None/Free 模式；有授权 Key 时填入真实值
TICKFLOW_API_KEY=

# 公网首次部署建议预置，首次成功写入 auth.json 后可清空
AUTH_PASSWORD='请替换为不少于 6 位的面板密码'

# AI 可选；不使用时留空
AI_API_KEY=
```

`AUTH_PASSWORD` 只在尚未生成 `data/user_data/auth.json` 时使用。面板密码写入哈希后，后续修改请在页面中完成；不要把明文密码提交到 Git。

Docker Compose 会强制将容器内 `DATA_DIR` 固定为 `/app/data`，并把宿主机的 `.env` 只读挂载为 `/app/.env`，用于首次密码初始化。

## 本地验证

在打包前执行：

```bash
cd /Users/liuwei/code/trae2/tick-stock-panel

python3 -m py_compile \
  backend/app/main.py \
  backend/app/backtest/strategy.py \
  data/strategies/custom/custom_leader_trend_volume.py \
  data/strategies/custom/custom_anomaly_candidates.py

git diff --check

cd backend
uv sync --extra dev
uv run pytest -q
cd ..

cd frontend
pnpm install --frozen-lockfile
pnpm build
cd ..
```

如果本机没有完整依赖，至少执行 `py_compile` 和 `git diff --check`，并在部署后通过容器日志和健康检查完成运行时验证。

## 打包与上传

不要直接上传整个工作区。排除 Git、环境变量、运行时数据、依赖缓存和构建产物：

```bash
cd /Users/liuwei/code/trae2/tick-stock-panel

tar --exclude='.git' \
  --exclude='.env' \
  --exclude='data' \
  --exclude='backend/data' \
  --exclude='frontend/node_modules' \
  --exclude='frontend/dist' \
  --exclude='backend/static' \
  --exclude='**/__pycache__' \
  --exclude='**/.pytest_cache' \
  --exclude='**/.venv' \
  -czf /tmp/tick-stock-panel-deploy.tar.gz .

ssh -p 22000 root@211.149.157.27 \
  'mkdir -p /opt/tick-stock-panel/releases /opt/tick-stock-panel/data/strategies/custom'

scp -P 22000 /tmp/tick-stock-panel-deploy.tar.gz \
  root@211.149.157.27:/opt/tick-stock-panel/releases/
```

自定义策略位于 `data/strategies/custom/`，不纳入 Git 和 Docker 构建上下文，需要单独同步到服务器运行时数据目录：

```bash
rsync -av --delete \
  -e 'ssh -p 22000' \
  data/strategies/custom/ \
  root@211.149.157.27:/opt/tick-stock-panel/data/strategies/custom/
```

`rsync --delete` 只允许作用于上述明确的策略目录，不要把它改成 `/opt/tick-stock-panel/data/` 或其他上级目录。

## 远程原子更新

登录服务器后执行。失败时不要切换 `app.new`，线上旧版本仍保持可用：

```bash
cd /opt/tick-stock-panel

release="$(date +%Y%m%d%H%M%S)"
mkdir -p "releases/$release" data
tar -xzf "releases/tick-stock-panel-deploy.tar.gz" -C "releases/$release"

ln -sfn /opt/tick-stock-panel/.env "releases/$release/.env"
ln -sfn "releases/$release" app.new

cd app.new
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app.new/docker-compose.yml \
  config >/tmp/tick-stock-panel-compose-$release.yml
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app.new/docker-compose.yml \
  build

cd /opt/tick-stock-panel
if test -L app.prev || test -d app.prev; then
  rm -rf app.prev
fi
if test -L app || test -d app; then
  mv app app.prev
fi
mv -Tf app.new app

docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml \
  up -d --remove-orphans
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml \
  ps
```

> `--project-directory /opt/tick-stock-panel` 是必要的：Compose 的 `./data` 和 `.env` 路径统一以部署根目录解析，不会随代码版本目录切换而丢失用户数据。

## 直接部署命令

如果不需要保留多版本目录，也可以在服务器项目目录执行：

```bash
cd /opt/tick-stock-panel
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml \
  up --build -d --remove-orphans
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml \
  ps
```

部署更新不需要 `git push`。本地打包、上传和远端构建即可完成更新。

## Nginx 反向代理

创建 `/etc/nginx/conf.d/tick-stock-panel.conf`：

```nginx
server {
    listen 80;
    server_name tick.crayfish.cloud 211.149.157.27;

    gzip on;
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml image/svg+xml;
    gzip_min_length 1024;

    location / {
        proxy_pass http://127.0.0.1:3018;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_max_temp_file_size 0;
    }
}
```

验证并重载：

```bash
nginx -t
systemctl reload nginx
```

本项目的前端和 `/api/*` 都由同一个 FastAPI 容器提供，不要把 `/api/` 代理到另一个服务，也不要配置静态目录覆盖容器返回的前端资源。

## 验证

服务器本机：

```bash
cd /opt/tick-stock-panel
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml ps
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml logs --tail=100 app
curl -sS http://127.0.0.1:3018/health
curl -sS http://127.0.0.1:3018/api/health
```

Nginx 和域名：

```bash
nginx -t
curl -sS -H 'Host: tick.crayfish.cloud' \
  http://127.0.0.1/api/health
curl -sS http://tick.crayfish.cloud/api/health
curl -sS http://tick.crayfish.cloud/ | grep -o 'assets/[^"<>]*'
```

期望结果：

- `docker compose ps` 显示 `TickFlow_Stock_Panel` 为 `running` 或 `Up`
- `/health` 与 `/api/health` 返回 `status: ok`
- Nginx 配置检查成功
- 首页 HTML 含 `assets/` 下的前端 JS/CSS 文件
- 页面可以使用预置密码登录
- “设置 → 凭据与能力”能够重新检测 TickFlow 能力
- “数据”页能看到 `/opt/tick-stock-panel/data` 中的历史数据
- “策略”页能加载 `选股池` 和 `异动候选`

## 回滚

如果新版本容器启动失败或健康检查异常：

```bash
cd /opt/tick-stock-panel
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml down

rm -rf app.bad
if test -L app || test -d app; then
  mv app app.bad
fi
mv app.prev app

docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml \
  up -d --remove-orphans
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml ps
curl -sS http://127.0.0.1:3018/api/health
```

只回滚代码目录，不删除 `data/`。行情、策略参数、回测、监控和认证数据均位于运行时数据目录。

## 日常运维

```bash
cd /opt/tick-stock-panel
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml ps
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml logs -f --tail=200 app
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml restart app
docker compose --project-directory /opt/tick-stock-panel \
  -f /opt/tick-stock-panel/app/docker-compose.yml down
```

数据备份建议至少包含：

```text
/opt/tick-stock-panel/data/
/opt/tick-stock-panel/.env
```

备份 `.env` 时使用服务器受控存储，权限保持 `600`，不要上传到公共网盘、代码仓库或部署压缩包。
