# 网站前后端：服务器 A

整个 backend_server/ 目录可以单独复制到服务器 A；不需要 anquanday_auto/、SQLite 或 GPT key。一个容器同时提供前端页面和网站后端。

## 首次部署

1. 安装并启动 Docker 与 Compose v2。
2. 将 .env.example 复制为 .env（已有 .env 则直接编辑），填写服务器 B 的实际地址：

~~~dotenv
COLLECTOR_API_BASE_URL=http://10.0.0.20:8765
WEBSITE_PORT=8766
WEBSITE_BIND_IP=0.0.0.0
~~~

10.0.0.20 只是示例，请换成 A 能访问的 B 的 IP 或域名。B 的 COLLECTOR_PORT 改动后，这里的端口也要同步。跨服务器不能填写 127.0.0.1、localhost 或另一套 Compose 的服务名 collector。地址只填写协议、主机和端口，不加 /api/v1。没有填写地址时 Compose 会明确报错，防止错误连接。

3. 在 backend_server/ 内运行：

~~~sh
docker compose up -d --build
docker compose ps
~~~

浏览器打开 http://服务器A地址:8766 。先启动采集端 B，再启动网站端 A。两套 Compose 相互独立，不依赖共享 Docker 网络或共享文件。

## 配置与更新

config/web.json 挂载到 /app/config/web.json，用于页面时区、刷新频率和上游请求超时。容器监听地址固定为 0.0.0.0:8766；API 地址由 .env 的 COLLECTOR_API_BASE_URL 覆盖 JSON 中的本机地址。WEBSITE_PORT 控制宿主机对外端口。

~~~sh
# 更新代码
docker compose up -d --build
# 修改 API 地址、端口或配置文件后重建容器
docker compose up -d --force-recreate
# 查看日志
docker compose logs --tail=100 -f website
# 停止网站
docker compose down
~~~

GET /healthz 只检查网站进程；GET /api/v1/health 通过网站后端检查采集服务。采集服务不可用时，网站页面仍能打开，数据 API 返回 503，页面显示连接失败提示。浏览器只访问 A 的同源接口，不直接访问 B。

网站镜像只包含运行需要的页面、web 后端、配置与依赖。没有采集引擎、SQLite、GPT key、截图或离线快照。容器默认以非 root 用户运行，文件系统只读。

## 同一台电脑测试

Docker Desktop 本机测试时，网站可填写 http://host.docker.internal:18865，采集端 COLLECTOR_PORT 设为 18865，网站 WEBSITE_PORT 设为 18866，避免占用现有 Python 服务的 8765/8766。host.docker.internal 仅为这种本机测试使用，真实两台服务器部署仍填写 B 的地址。

本机如果 docker compose 不可用，但已安装独立 Compose v2，可将命令替换为 docker-compose。Windows PowerShell 可运行：

~~~powershell
& "C:\Program Files\Docker\Docker\resources\bin\docker-compose.exe" up -d --build
~~~
