# AnquanDay 网站前后端

将整个 website/ 文件夹部署到服务器 A。本目录可独立运行。

| 目录 | 职责 |
| --- | --- |
| frontend/ | 新闻、恶意软件占位页、APT 组织档案、来源标签和日历 |
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

frontend/demo.html 是本地生成的快照，已被 Git 忽略，不随仓库发布。正式网站使用 frontend/index.html 和实时 API。需要离线快照时，在完整仓库根目录运行 python scripts/build_demo.py；生成脚本和 frontend/demo.template.html 模板仍保留，此工具不是网站运行依赖。

## 顶部栏目

顶部提供“新闻 / 恶意软件 / APT”，使用 #news、#malware、#apt 地址，支持刷新及浏览器前进/后退。点击 AnquanDay 标志会回到今天的全部新闻，并清除新闻搜索和筛选。恶意软件栏目目前仅占位。

APT 栏目展示公开组织的中文简介、关联名称、原始出处及近 7/30/90 天的报道提及量，支持搜索、筛选、分页及查看匹配证据。统计由 collector API 计算，网站后端只转发 /api/v1/apt/groups 和 /api/v1/apt/articles。网站与 API 通过现有接口连接。MITRE 自动同步只运行在 collector 服务器，更新后的目录保存在其 data/apt/；网站无需额外定时任务或目录文件。

报道活跃度不等于实际攻击频率；目录包含 APT 及其他公开威胁组织。列表不触发 GPT 请求。已展开的 APT 资料阅读期间暂停自动重绘，避免打断阅读；切换筛选或重新进入页面会获取新数据。
