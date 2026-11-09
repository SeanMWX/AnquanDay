# 收集服务：服务器 B

整个 collector/ 目录可以单独复制到服务器 B；不需要 website/ 或仓库根目录。Docker 镜像内包含 engine、API、来源独立调度和自动翻译。

## 首次部署

1. 安装并启动 Docker Engine / Docker Desktop 与 Compose v2。
2. 保留已有的 .env 和 data/。全新部署时，将 .env.example 复制为 .env，填写 OPENAI_API_KEY，并创建空 data/ 目录。已有 .env 不要用模板覆盖。
3. 在 collector/ 目录执行：

~~~sh
docker compose up -d --build
docker compose ps
~~~

默认开放服务器 B 的 8765 端口。验证：http://服务器B地址:8765/api/v1/health ，正常返回 ok=true。网站服务器 A 必须能够访问这个地址；如果使用内网或 Tailscale，填写 B 对应的可达 IP，并只允许需要的服务器访问采集端口。

Linux 上，容器默认 UID/GID 为 1000:1000。先创建 data/，再将 .env 的 PUID、PGID 填为拥有 data/ 的用户的 id -u、id -g 结果；该用户还需能读取 .env 与 config/。Windows Docker Desktop 通常保持默认即可。

首次创建目录的命令：Linux 用 mkdir -p data；PowerShell 用 New-Item -ItemType Directory -Force data。Compose 刻意不自动创建缺失挂载路径，避免拼错路径后悄悄使用空数据库。

## 配置与数据

| 宿主机位置 | 容器位置 | 用途 |
| --- | --- | --- |
| data/ | /app/data | SQLite、原始证据、日志、锁与调度状态；可写 |
| config/ | /app/config | 来源和定时采集、翻译设置；只读 |
| .env | /app/.env | GPT key；只读，未打包进镜像 |

镜像和构建上下文排除了 .env、data/、离线 demo 和截图。GPT key 不通过 Compose 的 environment 传递；只有翻译子进程从挂载文件读取。现有的 .env 可以直接使用。

容器内固定监听 0.0.0.0:8765，数据目录固定 /app/data。JSON 中的本地监听地址不需要修改。定时采集和翻译仍使用 config/service.json：按来源独立运行，默认每小时采集、每 60 秒检查最新日期的待翻译标题。已有翻译缓存继续复用。

可在 .env 追加以下可选项，不能覆盖已有 OPENAI_API_KEY：

~~~dotenv
COLLECTOR_PORT=8765
COLLECTOR_BIND_IP=0.0.0.0
PUID=1000
PGID=1000
~~~

COLLECTOR_BIND_IP 是宿主机监听地址，可改为 B 的内网 IP。网站填写的 API 端口应与 COLLECTOR_PORT 一致。高级路径覆盖为 COLLECTOR_DATA_DIR、COLLECTOR_CONFIG_DIR、COLLECTOR_ENV_FILE，默认分别为 ./data、./config、./.env；自定义路径必须已经存在。

## 更新与停止

~~~sh
# 代码更新：重建镜像并启动
docker compose up -d --build
# 修改配置、key 或 .env 中的端口：重建容器以刷新文件挂载与环境
docker compose up -d --force-recreate
# 主服务日志
docker compose logs --tail=100 -f collector
# 停止并移除容器，data/ 中的数据保留
docker compose down
~~~

各来源和翻译任务的详细日志在 data/logs/。健康检查验证 API 和 SQLite 可读性，不代表每个来源都能成功抓取。服务异常退出会按 unless-stopped 重启；Docker 守护进程也需要配置开机启动，手动停止的容器不会自行启动。

从本机 Python 服务切换到 Docker，或把已有数据迁往另一台服务器时，先停止旧收集服务，再复制整个 data/ 与 .env，最后启动新容器。不要让本机 Python 与 Docker 同时操作同一个 SQLite 数据目录。迁移整个目录可保留原文证据和已翻译缓存。

本机如果 docker compose 不可用，但已安装独立 Compose v2，可将命令替换为 docker-compose。Windows Docker Desktop 的完整路径通常为 C:\Program Files\Docker\Docker\resources\bin\docker-compose.exe。
