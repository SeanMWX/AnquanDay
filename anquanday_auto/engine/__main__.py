import argparse
import json
import sys
from pathlib import Path
from .storage import Store, now
from .collector import collect_all
from .dedup import find_candidates
from .evidence import build_packet

ROOT = Path(__file__).resolve().parents[1]

def write_json(path, obj):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="AnquanDay 收集 engine：独立采集、时间归一化、证据存储")
    parser.add_argument("--data-dir", default=str(ROOT / "data"))
    parser.add_argument("--config", default=str(ROOT / "config" / "sources.json"))
    subs = parser.add_subparsers(dest="command", required=True)
    collect = subs.add_parser("collect", help="采集 RSS 当前窗口和 KEV 目录；默认保留全部当前条目")
    collect.add_argument("--sources", help="来源 ID，逗号分隔；默认全部")
    collect.add_argument("--limit", type=int, help="每个新闻源的最大条目数，仅用于小批次测试")
    collect.add_argument("--force-body", action="store_true")
    collect.add_argument("--output", default=str(ROOT / "data" / "last-collection.json"))
    probe = subs.add_parser("probe", help="逐个测试来源，并抓取样本文章正文")
    probe.add_argument("--sources")
    probe.add_argument("--sample", type=int, default=2)
    probe.add_argument("--output", default=str(ROOT / "data" / "source-probe.json"))
    export = subs.add_parser("export", help="导出原文和可追溯证据 JSONL")
    export.add_argument("--output", default=str(ROOT / "data" / "articles.jsonl"))
    candidates = subs.add_parser("candidates", help="生成跨天重复/后续事件候选，不自动合并")
    candidates.add_argument("--output", default=str(ROOT / "data" / "duplicate-candidates.json"))
    packet = subs.add_parser("packet", help="为后续中文 GPT 摘要导出证据包；不调用模型")
    packet.add_argument("--article-ids", required=True)
    packet.add_argument("--output", default=str(ROOT / "data" / "evidence-packet.json"))
    subs.add_parser("status", help="查看本地采集数量")
    args = parser.parse_args()
    store = Store(args.data_dir)
    try:
        if args.command in ("collect", "probe"):
            limit = args.sample if args.command == "probe" else args.limit
            if limit is not None and limit < 1:
                parser.error("条目数必须大于 0")
            sources = json.loads(Path(args.config).read_text(encoding="utf-8"))
            if args.sources:
                wanted = set(args.sources.split(","))
                missing = wanted - {s["id"] for s in sources}
                if missing:
                    parser.error("未知来源：" + ", ".join(sorted(missing)))
                sources = [s for s in sources if s["id"] in wanted]
            results = collect_all(sources, store, limit, args.command == "probe" or args.force_body)
            report = {"generated_at": now(), "mode": args.command,
                "scope": "current_feed_window_and_full_kev_catalog_not_full_historical_archive",
                "model_calls": 0, "counts": store.counts(), "sources": results}
            write_json(args.output, report)
            for r in results:
                print("{}: {}；RSS 条目 {}；正文成功 {}；正文降级 {}".format(
                    r["source_name"], r["status"], r.get("feed_entries", "-"), r["body_ok"], r["body_failed"]))
            print("结果：" + str(Path(args.output).resolve()))
            # A failed source is visible in output without interrupting other sources.
            return 0 if any(r["status"] in ("ok", "partial", "empty_feed") for r in results) else 2
        elif args.command == "export":
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", encoding="utf-8") as out:
                for row in store.all_articles():
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
            print("已导出：" + str(target.resolve()))
        elif args.command == "candidates":
            result = find_candidates(store.all_articles())
            write_json(args.output, {"generated_at": now(), "auto_merged": False, "candidates": result})
            print("发现 {} 组待判断候选；未删除或合并文章。".format(len(result)))
        elif args.command == "packet":
            ids = [int(x) for x in args.article_ids.split(",")]
            write_json(args.output, build_packet(store.all_articles(), ids))
            print("已导出中文摘要证据包；GPT 调用次数：0。")
        else:
            print(json.dumps(store.counts(), ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
