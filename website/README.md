# AnquanDay 网站前后端

将整个 website/ 文件夹部署到服务器 A。本目录可独立运行。

| 目录 | 职责 |
| --- | --- |
| frontend/ | 中文新闻页面、来源标签、日历和原文展开 |
| web/ | 网站后端，提供静态页面并把只读 API 请求转发到采集服务器 |
| config/web.json | 网站监听地址、采集 API 地址、页面时区和刷新间隔 |

网站后端通过 HTTP API 获取新闻，浏览器始终访问网站服务器。数据库和 GPT key 由 collector/ 所在的服务器 B 管理。

## Docker 部署

本目录已包含独立 Dockerfile、compose.yaml 和 .dockerignore。在 .env 的 COLLECTOR_API_BASE_URL 填写服务器 B 的实际 API 地址后，执行 docker compose up -d --build。网站默认端口为 8766。完整步骤见 [DOCKER.md](DOCKER.md)。以下为直接运行 Python 的方式。

## 安装与运行

在 website/ 内运行：

~~~powershell
python -m pip install -r requirements.txt
python run.py
~~~

也可以使用 python -m web。默认地址 http://127.0.0.1:8766 。本目录使用 Python 标准库和 tzdata，不需要前端构建工具。

## 两台服务器部署

编辑 config/web.json，把 api_base_url 改成服务器 B 的 API 地址。例如：

~~~json
{
  "host": "0.0.0.0",
  "port": 8766,
  "api_base_url": "http://10.0.0.20:8765",
  "api_timeout_seconds": 15,
  "display_timezone": "Europe/Brussels",
  "refresh_seconds": 60
}
~~~

这里的 10.0.0.20 仅为服务器 B 内网 IP 的例子；请填写实际可达地址。网站的 host 决定访问者从哪里连接，api_base_url 由网站后端使用。修改后重启网站服务。仓库默认配置保留 127.0.0.1，便于同一电脑本地运行两个服务。

只有固定 API 路径会被转发；原始抓取文件、数据库和密钥不由网站提供。采集 API 暂时不可用时，网站仍能打开，并显示数据连接失败提示。

页面默认展示所选时区的今天，点击日历可查看历史。新闻优先显示中文标题，展开查看原文；来源名称保留原名。默认每 60 秒刷新一次，翻译结果从采集 API 读取，不由浏览器调用 GPT。

## 离线演示

frontend/demo.html 是开发阶段生成的快照，正式网站使用 frontend/index.html 和实时 API。更新离线快照时，在完整仓库根目录运行 python scripts/build_demo.py；此工具不是网站运行依赖。
