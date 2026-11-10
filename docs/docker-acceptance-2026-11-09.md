# Docker 部署验收：2026-11-09

## 交付

- anquanday_auto/ 和 backend_server/ 分别具有 Dockerfile、compose.yaml、.dockerignore 和 DOCKER.md，可只复制对应目录部署。
- 两边独立构建、独立 Compose 项目、独立网络；网站通过显式配置的采集服务器 URL 连接。
- Python 3.12 slim 镜像，默认 UID/GID 1000，根文件系统只读，配置只读挂载，采集 data/ 单独可写。
- SQLite、原始证据与翻译缓存使用宿主机 anquanday_auto/data/；anquanday_auto/.env 在运行时只读挂载，不进入镜像。
- 网站预留 backend_server/.env 与 .env.example，COLLECTOR_API_BASE_URL 留空，待部署时填写真实服务器 B 的地址。
- 现有 anquanday_auto/.env 与生产数据未修改；网站、来源名称、中文标题展示规则保持现状。

## 实测环境

Windows Docker Desktop，Linux amd64 容器；Docker Engine 28.3.2、Compose 2.39.1。当前电脑的 docker compose 子命令不可用，实际使用 Docker Desktop 自带的 docker-compose.exe 执行相同 Compose v2 配置，未修改系统安装。

两个镜像分别从 anquanday_auto/、backend_server/ 构建成功。验收使用独立 Compose 项目 anquanday-smoke-collector 与 anquanday-smoke-website，端口分别为 18865、18866。网站通过 http://host.docker.internal:18865 连接发布的 API 端口，两边未加入共享网络。

这是同一台 Docker Desktop 上的双项目隔离测试，尚未部署到用户的两台远程服务器；远程地址和防火墙连通性需要在正式服务器填好后检查。

## 结果

| 检查 | 结果 |
| --- | --- |
| 两个镜像独立构建 | 通过 |
| 两个容器健康检查 | 均为 healthy |
| API /api/v1/health | 200，SQLite 可读 |
| 网站经发布端口代理 API | 200，读取到测试新闻 |
| 强制重建采集容器 | 测试新闻仍存在，SQLite 持久化有效 |
| 自动翻译子进程在容器中启动 | 正常读取空测试密钥文件并记录 key_missing，未调用 GPT |
| 停止采集端 | 网站 / 和 /healthz 返回 200，代理 API 返回 503 |
| 恢复采集端 | 网站 API 恢复并读到原测试新闻 |
| 两个容器收到停止信号 | 均以 exit=0 正常退出 |
| 无挂载启动镜像检查 | 无 .env、无生产数据；网站无 engine/service/数据库，只包含三个前端运行资产 |
| 运行权限 | 两个镜像均为 UID 1000，Compose 根文件系统只读 |
| 全量回归测试 | 77 passed；1 条现有 pyreadline 弃用警告 |
| 原本机服务 | 8765、8766 健康检查均返回 200 |

本次容器验收使用单独的测试数据目录，未挂载实际数据库或真实 GPT key。定时来源抓取在测试配置中关闭，自动翻译使用空测试 key；生产配置仍按原有流程启用自动采集和翻译。没有额外消耗 GPT token。

验收结束后已移除两个测试项目的容器与网络，保留已构建的本地镜像。原 Python 服务继续运行，尚未切换为 Docker 生产服务。

## 正式启动

先按 anquanday_auto/DOCKER.md 在服务器 B 初始化 .env、data/ 和 Linux 目录权限，在 anquanday_auto/ 运行 docker compose up -d --build。

再按 backend_server/DOCKER.md 在服务器 A 的 .env 填入 COLLECTOR_API_BASE_URL=http://服务器B地址:8765，在 backend_server/ 运行 docker compose up -d --build。

如果迁移现有数据库，先停止原收集进程再复制整个 data/；不要让原生 Python 与 Docker 同时使用同一数据库目录。
