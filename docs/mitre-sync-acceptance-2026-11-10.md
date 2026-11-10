# MITRE 自动同步与 Docker 验收

## 配置与职责

所有定时工作都在 anquanday_auto/API 服务器内运行，网站继续通过既有 API 读取。

| 任务 | 默认等待间隔（任务完成后） | 配置 |
| --- | --- | --- |
| 新闻采集 | 每个来源 3600 秒 | scheduler.interval_seconds；支持 scheduler.sources 单来源覆盖 |
| 新闻标题翻译 | 60 秒 | translation.interval_seconds |
| MITRE 正式版目录检查 | 86400 秒 | apt_sync.interval_seconds |
| MITRE 失败或简介未完成后的重试 | 3600 秒 | apt_sync.retry_seconds |

三个任务使用独立调度器和子进程；MITRE 不占用新闻来源并发。首次无计划时立即执行，默认重启恢复持久化到期时间。修改 config/service.json 后重启 API 服务或重建容器。

## 实现与数据

- runtime 目录：data/apt/catalog.json 及 ATTACK-LICENSE.txt；沿用 Docker data/ 挂载。
- config/ 继续只读；Docker 镜像新增 resources/ 种子，即使自定义配置挂载中没有目录种子也能启动。
- 每次检查官方集合索引，选择三个域的最新正式版本，忽略历史 beta。版本及索引修改时间不变时只请求索引。
- 目录校验成功后原子替换。下载/格式失败保留旧目录；模型失败保留原始更新及其他成功译文，新变化的简介不会套用旧译文。
- 每批最多 10 个简介，默认每轮最多 6 批、每天最多 24 次请求尝试。请求前持久化预算，失败和中断也计入；重启不清零。
- 中文整理复用现有 GPT key 与模型配置，仅处理新增或名称/简介变更。关联名称顺序变化不会被计为资料更新，也不触发翻译。
- API 优先读取 runtime 目录；文件原子更新后无需重启 API 即可看到新组织数及简介。catalog.last_checked_at 返回成功检查时间。
- 日志：data/logs/_apt_catalog.log；SQLite 状态：apt_sync:last_run 和 schedule:_apt_catalog。

## 自动化测试

115 项全量回归通过。最后针对“关联名称仅重排不算资料变更”的修正，重新执行 MITRE 同步专项，21 项通过（包含新增用例）。现有 pyreadline 依赖仍有一项弃用提示。

覆盖稳定版本/历史 beta、无变化只查索引、增删和名称/简介变更、译文复用、分批恢复、预算持久化、缺少 key、错误信息脱敏、下载/格式失败保留旧目录、原子写失败保护、手动/定时互斥、慢同步不阻塞新闻数据库、首次执行和失败重试、重启恢复计划及 API 即时读取更新。

## 真实流程与 Docker

本机正常自动调度与隔离 Docker 中均成功同步 MITRE ATT&CK v20.1，得到 182 个组织、182 个中文简介；本轮 GPT 请求尝试数为 0，现有译文全部复用。

Docker collector 镜像已实际重新构建。用两个隔离 Compose 项目分别启动 API（19865）和网站（19866），网站通过 HTTP 代理获取更新目录成功，两个容器均 healthy。测试禁用新闻和标题翻译，MITRE 任务仍独立执行；测试使用空 key 并关闭简介翻译。

测试挂载的只读 config/ 只有 service.json 和 sources.json，没有目录种子，容器成功从 resources/ 初始化 data/apt/。完成同步后关闭强制启动执行并两次重建容器（其中一次使用最终镜像）：目录和全部中文简介保留，next_due 与上次执行时间保持一致，没有重复执行。验收容器随后移除，本机正常服务继续自动运行。

网站无需新增定时任务。升级 API 服务器时保留 data/ 和 .env，合入 apt_sync 配置，再在 anquanday_auto/ 执行 docker compose up -d --build。
