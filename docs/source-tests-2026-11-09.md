# AnquanDay 来源实测报告

日期：2026-11-09。真实 HTTP 请求、RSS/JSON 解析、文章页提取及数据库写入已执行。

共保存 **140 篇文章记录、74 篇文章页正文、1,772 条 KEV 基线**。另有 65 篇 RSS 摘要与 1 篇只有标题/链接；缺失正文均有明确状态。未调用 GPT。

## 逐来源结果

| 来源 | 当前 RSS/JSON 条目 | 已存文章页正文 | 结果与处理 |
| --- | ---: | ---: | --- |
| [FreeBuf](https://www.freebuf.com/feed) | — | — | HTTP 405；首页与 RSS 均未取到内容。 |
| [暗网下](https://www.anwangxia.com/feed/) | 10 | 10 | RSS 与文章页正文均成功；公开 REST 候选返回 403，采用 RSS。 |
| [BleepingComputer](https://www.bleepingcomputer.com/feed/) | 15 | 15 | 此前单篇曾返回 429；退避后的后续运行成功。 |
| [The Hacker News](https://feeds.feedburner.com/TheHackersNews) | 50 | 0 | RSS 成功；两篇文章页返回 403 后暂停该源剩余正文请求，保留 50 篇摘要。 |
| [SecurityWeek](https://www.securityweek.com/feed/) | 10 | 10 | 按实测 .zox-post-body 提取；已修复不闭合段落与 304 缓存处理。 |
| [Krebs on Security](https://krebsonsecurity.com/feed/) | 10 | 10 | RSS 带正文内容，文章页也成功提取。 |
| [CERT-EU](https://cert.europa.eu/publications/security-advisories-rss) | 10 | 10 | 使用 .publication--body；RSS 的 CEST 已转换 UTC。 |
| [Seebug Paper](https://paper.seebug.org/rss/) | 15 | 0 | RSS 成功；两篇文章页返回 521 后暂停剩余正文，保留 15 篇摘要。 |
| [安全客](https://api.anquanke.com/data/v1/rss) | 20 | 19 | 19 篇正文成功；1 篇未提取到足够正文，保留标题与链接；时区暂按上海推定。 |
| [CISA KEV](https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json) | 1772 | — | 主站 JSON 成功；初次建立基线，重跑 1,772 条均未变化。 |

RSS 条目数表示本次返回的窗口，不是全站历史总量。正文暂停后未继续请求的条目，不应解读为逐篇都返回了 403/521。当前结果不保证换服务器或未来仍然相同。

## 时间字段实测

| 来源 | RSS 第一条的原始时间 | UTC / 状态 |
| --- | --- | --- |
| 暗网下 | Sun, 08 Nov 2026 03:04:00 +0000 | 2026-11-08T03:04:00Z；explicit_timezone |
| BleepingComputer | Mon, 09 Nov 2026 11:12:39 -0500 | 2026-11-09T16:12:39Z；explicit_timezone |
| The Hacker News | Mon, 09 Nov 2026 20:01:29 +0530 | 2026-11-09T14:31:29Z；explicit_timezone |
| SecurityWeek | Mon, 09 Nov 2026 14:08:08 +0000 | 2026-11-09T14:08:08Z；explicit_timezone |
| Krebs on Security | Thu, 05 Nov 2026 04:36:17 +0000 | 2026-11-05T04:36:17Z；explicit_timezone |
| CERT-EU | Fri, 23 Oct 2026 16:36:26 CEST | 2026-10-23T14:36:26Z；explicit_timezone |
| Seebug Paper | Thu, 05 Nov 2026 17:08:06 +0800 | 2026-11-05T09:08:06Z；explicit_timezone |
| 安全客 | 2026-11-09 14:59:16 | 2026-11-09T06:59:16Z；source_timezone_assumed |

安全客时间无时区，Asia/Shanghai 是显式配置的假设，尚未独立确认。其他上述样本有偏移或可识别缩写。KEV 的 dateAdded 只精确到日期，不能当作事故发生时间。

## CISA 主源和镜像

独立探测时，主站目录为 1772 条，dateReleased 为 2026-11-09T16:16:16.4275Z；官方 GitHub 镜像为 1769 条，dateReleased 为 2026-11-06T19:44:45.1797Z。备用源当时落后，engine 对更旧目录执行 stale_ignored，不覆盖新基线。

## 去重与证据验证

- 最后一次重复采集：新增文章 0、修订 0、未变 140；KEV 新增 0、更新 0、未变 1,772。
- 开发过程中提取器清理、补取正文产生的修订单独保存；155 个修订不是 155 篇文章。
- 规则产生 43 组事件关联候选，均未自动合并；不能把该数量当作确认的重复事件数。
- 导出的 2526 个证据片段均已核对与对应正文字符偏移一致。这不等于已验证未来中文摘要的语义。
- 23 项自动化测试通过。真实源请求验证了来源失败隔离、正文缺失降级及 HTTP 条件请求。

## 已修复的真实问题

1. RSS 阅读工具不支持 XML，不能据此判断 RSS 不可用；已改用实际 HTTP 客户端解析验证。
2. SecurityWeek 页面结构需要专用选择器，部分段落标签未闭合，逐段递归会重复正文。
3. 304 响应可能没有 Content-Type，需复用原始缓存的媒体类型及原始响应引用。
4. 正文内同一个广告链接会造成跨事件误关联，需过滤广告容器和营销链接。
5. CISA 的 dateReleased 有四位小数秒，已处理 Python 3.9 对该格式的兼容问题。

## 复现

```powershell
python -m engine --data-dir data/probe-run probe --sample 2
python -m engine collect
python -m engine collect
python -m engine candidates
```

配置位于 [config/sources.json](../config/sources.json)，完整运行说明见 [README](../README.md)。机器可读结果见 [source-tests-2026-11-09.json](source-tests-2026-11-09.json)。公开 API 候选的额外结果见 [extra-endpoint-tests-2026-11-09.json](extra-endpoint-tests-2026-11-09.json)。
