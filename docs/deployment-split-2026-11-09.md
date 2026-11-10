# 两个独立部署目录验收（2026-11-09）

- backend_server/：前端 frontend/、网站后端 web/、config/web.json、requirements.txt、run.py、README.md。
- anquanday_auto/：engine/、service/、config/、requirements.txt、run.py、README.md、本地 .env、data/。
- 根目录 tests/、scripts/ 和 docs/ 是开发资料，运行两个服务无需依赖它们。
- 已停止旧进程后移动目录，并执行 SQLite checkpoint 与 integrity_check。迁移前后均为 144 篇新闻、159 个修订、1,772 条漏洞记录、30 条译文。
- 实际启动使用独立工作目录，网站 http://127.0.0.1:8766，采集 API http://127.0.0.1:8765。正常自动采集与翻译继续运行。
- 隔离测试分别复制两个部署目录到模拟服务器 A、B 的位置，未复制密钥或真实数据库。网站通过 HTTP 读到采集端后来写入的新闻；网站未生成新闻数据库。
- 隔离采集端的自动翻译子进程也能正常启动；测试未配置 key，因此未产生模型请求。
- 采集端关闭后网站页面仍返回 200，API 代理返回 503；网站拒绝访问 .env 和数据库路径。
- 69 项测试通过，1 个已有 pyreadline 弃用警告。
- 已在新目录结构下重新生成离线 demo，并验证正常网站仍返回当天 29 篇新闻（27 篇中文译文、2 篇中文原文）。
- 本次验证在本机两个独立目录和进程完成；尚未向两台实际远程服务器部署。部署到远程时，按两个目录的 README 设置监听 IP 和网站 api_base_url。
