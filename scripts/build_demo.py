"""Build an offline, metadata-only HTML preview from the collection database."""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "anquanday_auto"))
from engine.title_translation import title_fields


def snapshot(db_path, source_path):
    conn = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN")
    try:
        sources = json.loads(source_path.read_text(encoding="utf-8"))
        runs = {}
        for row in conn.execute("SELECT source_id,result FROM runs ORDER BY id DESC"):
            if row["source_id"] not in runs:
                runs[row["source_id"]] = json.loads(row["result"])
        revisions = dict(conn.execute("SELECT article_id,COUNT(*) FROM revisions GROUP BY article_id"))
        articles = []
        for row in conn.execute("SELECT * FROM articles ORDER BY id"):
            p = json.loads(row["payload"])
            # Export metadata, not downloaded full articles or model evidence text.
            article = {k: p.get(k) for k in (
                "source_id", "title", "title_zh", "summary_zh", "language", "url",
                "original_url", "guid", "author", "published", "modified", "happened_at",
                "content_kind", "feed_content_kind", "content_sha256", "body_error",
                "enrichment_status", "page_http_status", "feed_fetch_id", "page_fetch_id")}
            article.update(id=row["id"], first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"], revision_hash=row["revision_hash"],
                revision_count=revisions.get(row["id"], 0),
                text_length=len(p.get("text", "")), evidence_count=len(p.get("evidence", [])),
                reference_count=len(p.get("outbound_links", [])),
                selector=p.get("page_metadata", {}).get("selector"))
            article.update(title_fields(conn, article["title"]))
            articles.append(article)
        kev = []
        for row in conn.execute("SELECT * FROM kev_records ORDER BY cve_id"):
            item = json.loads(row["payload"])
            item.update(first_seen_at=row["first_seen_at"], last_seen_at=row["last_seen_at"])
            item.update(title_fields(conn, item.get("vulnerabilityName", "")))
            kev.append(item)
        for source in sources:
            run = runs.get(source["id"], {})
            source["latest_run"] = {k: run.get(k) for k in (
                "status", "started_at", "finished_at", "feed_entries", "endpoint_used",
                "endpoint_attempts", "body_ok", "body_failed", "error", "catalog")}
        return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "collected_at": max((r.get("finished_at", "") for r in runs.values()), default=None),
            "sources": sources, "articles": articles, "kev": kev,
            "revision_count": sum(revisions.values()),
            "model_calls": sum(row[0] for row in conn.execute("SELECT attempts FROM title_translations"))}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="生成可离线打开的 AnquanDay HTML demo")
    parser.add_argument("--db", type=Path, default=ROOT / "anquanday_auto" / "data" / "anquanday.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "backend_server" / "frontend" / "demo.html")
    args = parser.parse_args()
    data = snapshot(args.db, ROOT / "anquanday_auto" / "config" / "sources.json")
    # Script data cannot terminate its element, even if a feed title contains HTML.
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    for char, escaped in [("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                          ("\u2028", "\\u2028"), ("\u2029", "\\u2029")]:
        payload = payload.replace(char, escaped)
    template = (ROOT / "backend_server" / "frontend" / "demo.template.html").read_text(encoding="utf-8")
    if template.count("__ANQUANDAY_DATA__") != 1:
        raise ValueError("Expected one data placeholder")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(template.replace("__ANQUANDAY_DATA__", payload), encoding="utf-8")
    print("HTML: {} | articles={} | KEV={}".format(args.output, len(data["articles"]), len(data["kev"])))


if __name__ == "__main__":
    main()
