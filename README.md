# AnquanDay

每日安全新闻：中文标题优先，展开对照原文，按日期和来源浏览。网站名称保留原名。

## 两个文件夹，分别部署到两台服务器

| 部署目录 | 服务器 | 包含内容 | 默认本地端口 |
| --- | --- | --- | --- |
| [backend_server/](backend_server/README.md) | 服务器 A | frontend/ 前端、web/ 网站后端、网站配置和依赖 | 8766 |
| [anquanday_auto/](anquanday_auto/README.md) | 服务器 B | engine/、service/ API、自动翻译、SQLite、原文证据、GPT key | 8765 |

浏览器访问服务器 A；网站后端通过 HTTP API 访问服务器 B。两边使用独立工作目录和配置，运行时不共享代码或数据库目录。

完整仓库还包含 tests/、docs/ 和 scripts/，用于开发、验收与离线快照生成。部署时只复制对应的 backend_server/ 或 anquanday_auto/ 文件夹即可。

目录已从 collector 改为 anquanday_auto、website 改为 backend_server。迁移已有服务器时，需要一并迁移各自的 .env 和 data/；这些本地文件被 Git 忽略，不会随 git pull 自动移动。Compose 项目、服务名与环境变量沿用原有名称，现有 Docker 管理命令仍有效。

## Docker：两台服务器分别启动

两个目录各有自己的 Dockerfile、compose.yaml 和 .dockerignore，无需仓库根目录，也不需要共享 Docker 网络。

1. 服务器 B 的 anquanday_auto/：保留已有 .env（GPT key）和 data/；全新部署先复制 .env.example 为 .env 并填写。仓库通过 data/.gitkeep 提供默认数据目录；Linux 上还需确认容器用户对它有写权限。执行 docker compose up -d --build，API 默认端口为 8765。
2. 服务器 A 的 backend_server/：从 .env.example 创建 .env，填写 COLLECTOR_API_BASE_URL=http://服务器B地址:8765。执行 docker compose up -d --build，网站默认端口为 8766。
3. 浏览器访问 http://服务器A地址:8766 。

容器自动执行原有采集、翻译流程，SQLite 数据持久化在 anquanday_auto/data/；密钥只在运行时挂载。Linux 的数据目录权限、端口修改与更新命令见 [采集端 Docker 部署](anquanday_auto/DOCKER.md) 和 [网站 Docker 部署](backend_server/DOCKER.md)。本机 Python 采集服务与容器不能同时使用同一个 data/，切换前先停止旧服务。

## 本地启动

在仓库根目录，两个终端分别运行：

~~~powershell
# 终端 1：收集引擎、API、自动翻译
python -m pip install -r anquanday_auto/requirements.txt
python anquanday_auto/run.py
~~~

~~~powershell
# 终端 2：网站前后端
python -m pip install -r backend_server/requirements.txt
python backend_server/run.py
~~~

打开 http://127.0.0.1:8766 。也可进入各自目录运行 python run.py，或分别运行 python -m service / python -m web。Ctrl+C 停止对应前台服务。以上 Python 启动方式需要进程持续运行；Docker 部署已配置 unless-stopped 重启策略。

## 两台服务器如何连接

1. 服务器 B：复制 anquanday_auto/，安装该目录的 requirements.txt，把 config/service.json 的 api.host 设为 B 的可达内网 IP，启动 python run.py。
2. 服务器 A：复制 backend_server/，安装该目录的 requirements.txt，把 config/web.json 的 api_base_url 设为 http://服务器B地址:8765，并设置网站监听地址，启动 python run.py。
3. 浏览器访问服务器 A 的网站端口。

具体配置例子见 [网站部署说明](backend_server/README.md) 和 [采集/API 部署说明](anquanday_auto/README.md)。本地默认仍使用 127.0.0.1，不会因为目录调整扩大网络监听范围。

## GPT key 和数据位置

- 密钥：[anquanday_auto/.env.example](anquanday_auto/.env.example) 是模板，实际填写位置为 anquanday_auto/.env。之前已经填写的 key 已移动到此处。
- 数据库：anquanday_auto/data/anquanday.sqlite3。
- 自动采集与翻译：[anquanday_auto/config/service.json](anquanday_auto/config/service.json)。
- 新闻来源：[anquanday_auto/config/sources.json](anquanday_auto/config/sources.json)。
- 网站 API 地址和显示设置：[backend_server/config/web.json](backend_server/config/web.json)。

.env、.env.*（示例模板除外）、data/ 内的运行数据、SQLite 数据库及其伴随文件、日志、缓存、虚拟环境和本地配置副本均被 Git 忽略。仅 anquanday_auto/data/.gitkeep 作为目录占位文件提交，确保全新拉取代码后 Docker 的默认数据挂载目录存在。密钥只填写在 .env；仓库中的默认 JSON 配置不应填写密钥。

截图统一保存在 docs/screenshots/，其他本地验收产物放在 docs/local-artifacts/；这两个目录不会提交。旧的 *-preview 图片和自动翻译截图也已加入忽略规则。正式网页使用的图片资源仍可正常纳入版本控制。

anquanday_auto/config/apt_catalog.json 和 ATTACK-LICENSE.txt 是部署必需的公开初始资料，需要提交；运行时更新与翻译缓存位于 anquanday_auto/data/apt/，不会提交。两个 Docker 构建上下文仅允许所需的默认配置文件，排除额外的本地配置。

自动翻译默认只处理最新有新闻的一天（Europe/Brussels），每轮最多 20 条，每个 UTC 日最多 200 次尝试。中文原文不调用模型，已有译文复用缓存；标题改变后重新翻译。网站原名、链接和 CVE 编号保留。详情见 anquanday_auto/README.md。

## 验收与开发

在完整仓库根目录运行：

~~~powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q tests
~~~

已通过全量回归和 MITRE 同步专项复验，覆盖采集、API、APT 匹配及部署。测试覆盖采集与证据、时区、缓存和重试、独立来源、自动翻译、API、网站代理、容器环境配置，以及两个部署目录的隔离运行。Docker 已分别实际构建和启动验收，见 [Docker 验收记录](docs/docker-acceptance-2026-11-09.md)。

旧离线 demo 可以在仓库根目录重新生成：

~~~powershell
python scripts/build_demo.py
~~~

输出为 backend_server/frontend/demo.html，该生成快照已被 Git 忽略；仅提交生成脚本和 demo.template.html 模板。正常网站无需执行这条命令。

## 当前边界

现阶段翻译标题，不生成中文新闻摘要。尚未实现全站历史回填、跨来源事件自动合并或重大事件排序。来源访问限制仍存在，单个来源失败不会阻塞其他来源。

- [来源测试](docs/source-tests-2026-11-09.md)
- [事件去重与摘要设计](docs/engine-design.md)
- [后端服务验收](docs/service-acceptance-2026-11-09.md)
- [翻译验收](docs/translation-acceptance-2026-11-09.md)

- [双目录部署验收](docs/deployment-split-2026-11-09.md)

APT 栏目收录 MITRE ATT&CK 公开组织档案并提供中文介绍；根据已采集新闻统计报道提及量，可展开原文与匹配依据。目录范围、匹配方法、接口及更新命令见 [采集服务说明](anquanday_auto/README.md#apt-目录与报道活跃度)。

- [APT 与顶部栏目验收](docs/apt-acceptance-2026-11-09.md)

新闻与 MITRE 使用独立定时任务，均在 API 服务器运行：默认新闻每个来源完成后等待 1 小时，MITRE 每轮成功后等待 24 小时；失败或简介未完成时默认 1 小时重试。分别配置 anquanday_auto/config/service.json 的 scheduler 和 apt_sync，修改后重启服务。MITRE 自动目录与译文保存在 anquanday_auto/data/apt/，Docker 重建后保留。

- [MITRE 自动同步与 Docker 验收](docs/mitre-sync-acceptance-2026-11-10.md)
