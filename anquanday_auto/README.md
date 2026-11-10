# AnquanDay 收集服务与 API

将整个 anquanday_auto/ 文件夹部署到服务器 B。engine/ 负责采集和原文证据，service/ 负责自动调度、翻译和只读 API。SQLite、原始抓取文件和 GPT key 都保存在本目录。

## Docker 部署

本目录已包含独立 Dockerfile、compose.yaml 和 .dockerignore。保留已有 .env 和 data/，在本目录执行 docker compose up -d --build。默认 API 端口为 8765，容器自动执行采集与翻译。首次初始化、Linux 目录权限、升级与迁移说明见 [DOCKER.md](DOCKER.md)。以下为直接运行 Python 的方式。

## 安装与运行

在 anquanday_auto/ 内运行：

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

本次目录迁移已保留 data/ 中的数据库和译文，已填写的 key 位于 anquanday_auto/.env。独立部署一份新目录时，将 .env.example 复制为 .env，再填写 OPENAI_API_KEY。

数据位置：

- data/anquanday.sqlite3：新闻、修订、漏洞目录、翻译缓存和调度状态。
- data/raw/：原始抓取证据。
- data/apt/：自动更新的 MITRE 目录、中文简介及许可证。
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

新闻流程仅翻译新闻标题和漏洞名称，不生成新闻摘要或翻译全文；正文仍点击原文阅读。网站名称、URL、CVE 和必要的产品专名保留原样。中文原文直接显示。

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

所有接口只接受 GET；展示端同源转发相同路径。API 不公开数据库文件、原始抓取文件或文章全文。APT 关联报道接口额外提供长度受限的名称匹配片段，供核对依据。

| 路径 | 用途 |
| --- | --- |
| /api/v1/health | 服务与 SQLite 读取检查 |
| /api/v1/sources | 来源名称、ID、类型和原始入口 |
| /api/v1/overview | 所选日期数量、正文数量、来源数量、累计数量 |
| /api/v1/calendar | 指定月份和来源每天的数量、日期未确定的数量 |
| /api/v1/articles | 按日期、来源、关键字筛选的新闻 metadata，服务端分页 |
| /api/v1/kev | 单独查询 KEV，服务端分页 |
| /api/v1/apt/groups | 公开组织目录、中文简介和近期报道提及统计 |
| /api/v1/apt/articles | 指定组织的关联报道、匹配名称与片段 |

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

## APT 目录与报道活跃度

运行目录位于 data/apt/catalog.json；config/apt_catalog.json 是随代码提供的初始种子，包含 MITRE ATT&CK v20.1 的 182 个公开组织。Docker 镜像另在 resources/ 保留种子，兼容只挂载 service.json / sources.json 的配置目录。首次运行复制种子；后续重启、重建镜像都保留 data/apt/ 中的更新结果和中文译文。许可证同时保存在 data/apt/ATTACK-LICENSE.txt。

目录合并 Enterprise、Mobile、ICS 域，按 G 编号去重，排除撤销或弃用的档案。包含 APT 及其他威胁组织，不是全球所有 APT 清单，也不能据此判断组织是否仍在活动。

MITRE 同步已接入 API 服务的独立定时任务，与新闻采集、新闻标题翻译分开运行，不占来源采集并发。首次启动立即执行；之后默认每次完成后等待 24 小时。重启沿用 SQLite 中的下一次到期时间，默认不强制重新执行。无需在宿主机或网站服务器设置 cron。

配置位于 config/service.json 的 apt_sync：

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| enabled | true | 独立开关，不受 scheduler.enabled 或 translation.enabled 影响；旧配置缺少整个段时默认为关闭 |
| run_on_startup | false | false 时恢复已保存的计划；没有计划的首次启动仍立即执行；true 时每次启动都执行 |
| interval_seconds | 86400 | 成功完成后的检查间隔，单位秒，范围 3600–31536000 |
| retry_seconds | 3600 | 失败、缺少 key、翻译未完成或达到预算后，下次尝试的间隔；范围 60–86400 |
| timeout_seconds | 1800 | 整轮子进程的最长时间；超时停止并按重试间隔继续 |
| translate | true | 自动整理新增或变更简介；关闭时仍同步原始目录和数量 |
| max_batches_per_run | 6 | 每轮最多 6 批，每批最多 10 个简介；范围 1–30 |
| daily_max_requests | 24 | 每个 UTC 日最多 24 次 GPT 请求尝试；失败、中断也计入，重启不会清零；范围 1–100 |

配置修改后重启 API 服务。新闻频率继续由 scheduler.interval_seconds 控制（默认 3600 秒），不同来源可在 scheduler.sources 单独覆盖。新闻中的 APT 报道活跃度在 API 读取时计算，随新新闻变化，不等待目录的每日同步。

每天先检查官方集合索引。三个域的版本、URL 和索引修改时间均未变时，不重新下载完整数据包；目录没有新增/简介或名称没有变化时，不调用 GPT。来源变化后校验完整目录再原子替换。下载失败或数据无效保留上一份可用目录；中文生成失败时保留已同步的原始目录和其他译文，变更条目显示待整理，不套用过期译文。下一轮从未完成条目继续。

中文简介依据原始 description 整理，记录模型、输入哈希、规则版本及时间；每个组织提供 MITRE 原始链接。复用 translation.env_file 和 translation.model，GPT key 仍只在 API 服务器；目录翻译使用独立预算。成功批次逐批原子保存。手动与自动更新共用文件锁，避免同时运行。网页刷新和报道匹配不调用 GPT。

需要立即执行一轮时，在 anquanday_auto/ 内运行：

~~~powershell
python -m service.apt_sync --config config/service.json
# 只继续未完成的中文整理，不重新请求 MITRE
python -m engine.apt_catalog --translate --config config/service.json
~~~

兼容原来的 python -m engine.apt_catalog --refresh --translate；现在写入 data/apt/，不修改只读 config。手动运行也遵守每日请求预算。最近结果保存在 SQLite 的 apt_sync:last_run，计划在 schedule:_apt_catalog，运行日志在 data/logs/_apt_catalog.log。API 自动读取更新后的目录，无需重启；响应中的 catalog.last_checked_at 为最近成功检查时间，retrieved_at 为最近完整目录获取时间。

API 示例：

~~~text
/api/v1/apt/groups?days=30&tz=Europe%2FBrussels&q=APT28&activity=all&sort=activity&page=1&page_size=12
/api/v1/apt/articles?group=G0007&days=30&tz=Europe%2FBrussels&page=1&page_size=5
~~~

共同参数：days 为 7、30、90；date 是窗口截止日，默认所选时区今天，不接受未来日期；窗口包含截止日。tz 与新闻使用相同规则；仅精确到日期的记录保留来源日期，日期未知及未来时间戳不参与统计。page/page_size 与新闻接口一致。

目录支持 q（组织名、关联名称、中文介绍）、activity=all/mentioned/quiet、sort=activity/name。返回 catalog、window、coverage、mentioned_groups、headline_groups 及分页 items。两个组数量均基于整个目录，不受 q 和 activity 筛选影响。coverage 说明实际纳入的新闻量、最早/最晚报道日期及未定日期的排除量。

匹配依据原文标题及已采集正文/来源摘要，不使用生成式推断。分析时排除具有独立 Related Articles / Related Stories / 相关阅读等标题的推荐尾部，原始采集记录保留不变。编号做边界匹配（APT1 不匹配 APT10），容忍 APT-28 / APT 28；规范名末尾 Group/Team 可省略。共享关联名称不自动分配组织；Play、Silence 等常用词需紧邻组织/攻击者/勒索等描述词。此规则仍可能误报或漏报，不能用作独立归因结论。

| 报道活跃度 | 所选窗口内的规则 |
| --- | --- |
| 报道较多 | 标题提及至少 5 篇，来自至少 2 个来源 |
| 多次报道 | 标题提及至少 2 篇 |
| 近期报道 | 标题提及 1 篇 |
| 仅正文提及 | 标题未提及，仅正文/摘要出现组织名 |
| 未见报道 | 在已收录新闻中没有匹配；不表示停止活动 |

同一文章 ID 的多个版本和多个匹配名只计一篇；不同来源对同一事件的报道仍分别计数，尚未执行跨来源事件合并。标题提及也可能是历史回顾，指标只表示报道量。证据接口保留文章 ID、原文链接、发布时间、内容版本哈希、匹配字段和有长度上限的标准化片段。中文标题复用现有翻译缓存，历史未翻译标题仍显示“标题待翻译”，可展开原文。
