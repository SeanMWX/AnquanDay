# AnquanDay 收集服务与 API

将整个 collector/ 文件夹部署到服务器 B。engine/ 负责采集和原文证据，service/ 负责自动调度、翻译和只读 API。SQLite、原始抓取文件和 GPT key 都保存在本目录。

## Docker 部署

本目录已包含独立 Dockerfile、compose.yaml 和 .dockerignore。保留已有 .env 和 data/，在本目录执行 docker compose up -d --build。默认 API 端口为 8765，容器自动执行采集与翻译。首次初始化、Linux 目录权限、升级与迁移说明见 [DOCKER.md](DOCKER.md)。以下为直接运行 Python 的方式。

## 安装与运行

在 collector/ 内运行：

~~~powershell
python -m pip install -r requirements.txt
python run.py
~~~

也可以使用 python -m service。推荐 Python 3.12，当前验收兼容 Python 3.9+。默认监听 http://127.0.0.1:8765 。

## 与网站服务器连接

两台服务器部署时，把 config/service.json 的 api.host 改为服务器 B 的可达内网 IP；网站服务器的 api_base_url 指向这个 IP。例如服务器 B 的内网 IP 为 10.0.0.20：

~~~json
"api": {"host": "10.0.0.20", "port": 8765}
~~~

修改监听配置后重启本服务。data_dir、sources_file、translation.env_file 都相对于配置文件所在目录解析，不依赖仓库外的路径。

本次目录迁移已保留 data/ 中的数据库和译文，已填写的 key 位于 collector/.env。独立部署一份新目录时，将 .env.example 复制为 .env，再填写 OPENAI_API_KEY。

数据位置：

- data/anquanday.sqlite3：新闻、修订、漏洞目录、翻译缓存和调度状态。
- data/raw/：原始抓取证据。
- data/logs/：来源与翻译日志。
- .env：本地 GPT key，已被 Git 忽略。

SQLite 使用 WAL，每个来源和翻译任务独立运行。API 使用只读数据库连接。服务持续运行时自动采集与翻译；直接运行 Python 未安装开机自启，Docker 已配置 unless-stopped 重启策略。

## GPT key 与中文标题

本目录已预留 .env，直接在 OPENAI_API_KEY= 后粘贴 key 并保存即可：

~~~dotenv
OPENAI_API_KEY=你的key
~~~

只在本机填写，不需要发到聊天或放入网页。新 checkout 可以将 .env.example 复制为 .env。.env 已加入 Git 忽略规则；展示服务不读取或提供此文件。系统环境变量 OPENAI_API_KEY 非空时优先于 .env。

更新代码后重启收集服务和展示服务并刷新页面。之后仅修改 .env 不需要重启：每轮翻译子进程会重新读取，默认每轮完成后等待 60 秒。服务必须持续运行。手动处理一轮可运行：

~~~powershell
python -m service.translate --config config/service.json
~~~

翻译配置位于 config/service.json 的 translation 部分：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| enabled | true | 独立开关；不会阻止采集或 API |
| scope | latest_day | 只处理最新有新闻的一天；改为 all 可补译历史 |
| timezone | Europe/Brussels | 判断新闻归属日期的时区，与默认页面一致 |
| env_file | ../.env | 相对于 service.json 的本地密钥文件 |
| model | gpt-4.1-mini | 可改为支持 Responses 结构化输出的可用模型 |
| interval_seconds | 60 | 每轮完成后的等待秒数 |
| max_items_per_run | 20 | 每轮最多尝试翻译多少个标题 |
| daily_max_requests | 200 | 每个 UTC 自然日最多请求尝试次数；失败和中断也计入 |
| request_timeout_seconds | 30 | 单次请求的读取超时；连接超时为 5 秒 |
| retry_seconds | 3600 | 同一标题失败后等待一小时再重试 |
| max_attempts | 3 | 同一标题最多尝试三次；需要更多重试时可提高此值并重启 |
| include_kev | true | 新闻之后逐步翻译漏洞目录名称，可关闭 |

正常自动流程默认只翻译最新有新闻的一天：按 translation.timezone 计算新闻发布日期，漏洞按该日加入目录的日期筛选。之后有更新日期的新闻入库，下一轮会自动跟随新日期；历史和日期未知的条目不进入此次翻译队列。仅有日期的新闻保持来源原始日期。修改网站默认时区时，可同步调整 translation.timezone。

现阶段仅翻译新闻标题和漏洞名称，不生成摘要或翻译全文；正文仍点击原文阅读。网站名称、URL、CVE 和必要的产品专名保留原样。中文原文直接显示。

译文保存在 SQLite 的 title_translations 表，按原文标题与翻译规则版本的哈希缓存。相同标题跨来源也可复用；刷新网页、重复采集或只改变正文不会重复调用模型。标题改变后旧译文不再显示，重新排队。改模型配置不会强制重译缓存。

原文标题、正文、证据及修订历史保持不变，译文能对应到输入标题。结构化输出、中文和 CVE 校验不能保证语义零误差，所以保留“中文译文”标识和展开原文对照。未经翻译或翻译失败时显示“标题待翻译”，不把英文自动放到第一面。

翻译有独立进程和锁，不占用来源采集并发。鉴权失败、限流、网络错误和服务端错误会短暂暂停翻译请求；来源采集和 API 正常继续。已通过正常自动任务完成 2026-11-09 的整日翻译：27 篇外文新闻标题、3 条漏洞名称；另有 2 篇中文原文直接显示。下一轮自动任务复用缓存，不重复请求。

## 配置定时采集

[config/service.json](config/service.json) 启动时读取；修改后重启收集服务。data_dir 与 sources_file 的相对路径以该配置文件所在目录为基准。

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| scheduler.enabled | true | 启用定时采集；false 时停止定时采集；翻译任务由 translation.enabled 独立控制 |
| scheduler.run_on_startup | true | 启动后立即采集一轮 |
| scheduler.interval_seconds | 3600 | 每个来源完成后到下次启动的间隔 |
| scheduler.source_timeout_seconds | 300 | 每个来源整个任务的最长时间，超时终止该子进程 |
| scheduler.max_parallel_sources | 4 | 同时运行的来源数量，其他来源按到期时间排队 |
| scheduler.sources | {} | 按来源 ID 覆盖开关、间隔或超时 |

例如，把 scheduler.sources 改成：

~~~json
{
  "anwangxia": {"interval_seconds": 1800},
  "freebuf": {"enabled": false},
  "cisa_kev": {"interval_seconds": 21600, "source_timeout_seconds": 120}
}
~~~

来源入口和提取规则在 [config/sources.json](config/sources.json)。

每个来源有独立子进程、HTTP 客户端、SQLite 连接、到期时间与日志。失败不取消其他任务；超时释放并发槽位。相同来源通过操作系统文件锁防止手动采集与定时采集重叠；同一数据目录也只允许一个收集服务。退出或进程崩溃后，操作系统会释放锁，锁文件本身无需删除。

下一次到期时间保存在 SQLite。run_on_startup=false 时，重启会恢复保存的到期时间；第一次启动且没有检查点时，等待一个间隔。run_on_startup=true 会立即采集。超过计划时间不会补跑大量积压任务。

## 最小 API

所有接口只接受 GET；展示端同源转发相同路径。API 不公开数据库文件、原始抓取文件、文章全文或证据正文。

| 路径 | 用途 |
| --- | --- |
| /api/v1/health | 服务与 SQLite 读取检查 |
| /api/v1/sources | 来源名称、ID、类型和原始入口 |
| /api/v1/overview | 所选日期数量、正文数量、来源数量、累计数量 |
| /api/v1/calendar | 指定月份和来源每天的数量、日期未确定的数量 |
| /api/v1/articles | 按日期、来源、关键字筛选的新闻 metadata，服务端分页 |
| /api/v1/kev | 单独查询 KEV，服务端分页 |

示例：

~~~text
http://127.0.0.1:8765/api/v1/articles?date=2026-11-09&tz=Europe%2FBrussels&source=bleepingcomputer&page=1&page_size=12
http://127.0.0.1:8765/api/v1/calendar?month=2026-11&tz=Asia%2FShanghai&source=all
~~~

参数：date=YYYY-MM-DD 或 undated；month=YYYY-MM；tz=IANA 时区（默认 Europe/Brussels）；source=all 或来源 ID；q=关键词；kind=all/article_body/rss_excerpt/title_only；sort=published/collected；page 从 1 开始；page_size 为 1–100。日期默认是所选时区的今天。KEV 使用 /kev，不与新闻混合。关键词同时搜索中文译名和原文；新闻还搜索 GUID，KEV 还搜索 CVE/厂商/产品/描述。

列表返回 items、total、page、page_size、pages、date、timezone、source。列表的 title 保留原文，title_zh 是中文译文，display_title 是前端默认标题；translation 提供翻译状态、输入哈希、模型和完成时间。文章 metadata 还包括原文 URL、原始发布时间、UTC、时区状态、首次发现、最近采集、修订数、证据数量、内容哈希等。错误用 HTTP 400/404/503 等返回；页面明确提示 API 不可用，可重试恢复。

## 手动采集命令

~~~powershell
python -m engine collect
python -m engine collect --sources anwangxia,bleepingcomputer
python -m engine status
python -m engine candidates
python -m engine export
python -m engine packet --article-ids 1,2
~~~

手动 collect 保留原有串行行为；定时服务使用独立子进程并发。URL/GUID 去重与内容修订规则共用。相同文章重复读取只更新采集时间，不新增文章；正文改变会保留内容版本。抓取失败不会用摘要覆盖已有正文。
