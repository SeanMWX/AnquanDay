"""Source-isolated collection and bounded source compatibility probes."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .http import Client
from .parsing import parse_feed, extract_page, normalize_url
from .storage import now
from .locking import source_lock, LockBusy

def fetch_record(client, store, source, url):
    cached = store.cache(url)
    validators = {}
    if cached.get("etag"):
        validators["If-None-Match"] = cached["etag"]
    if cached.get("last_modified"):
        validators["If-Modified-Since"] = cached["last_modified"]
    try:
        response = client.get(url, validators)
    except Exception as exc:
        store.fetched(source["id"], url, error=str(exc))
        raise
    fid, path = store.fetched(source["id"], url, response)
    if response["status"] == 304:
        cached_path = store.root / cached.get("raw_path", "__missing__")
        if not cached_path.is_file():
            raise ValueError("304_without_cached_body")
        raw = cached_path.read_bytes()
        if not response.get("content_type"):
            response["content_type"] = cached.get("content_type", "")
            if not response["content_type"]:
                old = store.db.execute("SELECT content_type FROM fetches WHERE requested_url=? AND status=200 ORDER BY id DESC LIMIT 1", (url,)).fetchone()
                response["content_type"] = old["content_type"] if old else ""
    else:
        store.cache_response(url, response, path)
        raw = response["raw"]
    return raw, fid, response

def acquire_article(article, source, client, store, force=False, skip_body_reason=None):
    current = dict(article)
    existing = store.find_article(source["id"], current["url"], current.get("guid"))
    previous = json.loads(existing["payload"]) if existing else None
    if previous:
        current["original_url"] = previous.get("original_url", current["original_url"])
    recently_checked = False
    if existing and existing["body_checked_at"]:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(existing["body_checked_at"].replace("Z", "+00:00"))
        recently_checked = age < timedelta(hours=6)
    if previous and recently_checked and previous.get("feed_hash") == article["feed_hash"] and not force and not previous.get("body_error"):
        previous["feed_fetch_id"] = current["feed_fetch_id"]
        return previous, False
    current["body_error"] = None
    body_checked = not bool(skip_body_reason)
    try:
        if skip_body_reason:
            raise ValueError(skip_body_reason)
        raw, fid, response = fetch_record(client, store, source, current["url"])
        current["page_fetch_id"] = fid
        current["page_http_status"] = response["status"]
        current["page_final_url"] = response["url"]
        if "html" not in response["content_type"].lower():
            raise ValueError("article_not_html")
        page = extract_page(raw, response["url"], source)
        current["page_metadata"] = {k: page[k] for k in ("selector", "published", "modified", "title")}
        if not page["body_usable"]:
            raise ValueError("body_selector_missing_or_too_short")
        current["url_aliases"] = [current["url"], normalize_url(response["url"])]
        current["url"] = page["canonical_url"]
        current["text"] = page["text"]
        current["content_kind"] = "article_body"
        current["outbound_links"] = sorted(set(current["outbound_links"] + page["outbound_links"]))
        # Preserve both clocks; prefer explicit page timestamp only when feed was uncertain.
        if not current["published"]["utc"] or current["published"]["status"] == "source_timezone_assumed":
            if page["published"]["utc"]:
                current["published"] = page["published"]
        if page["modified"]["utc"]:
            current["modified"] = page["modified"]
    except Exception as exc:
        current["body_error"] = str(exc)
        # A transient fetch failure must not replace an already collected full body with an excerpt.
        if previous and previous.get("content_kind") == "article_body":
            for key in ("text", "content_kind", "page_metadata", "page_fetch_id", "outbound_links", "url"):
                if key in previous:
                    current[key] = previous[key]
            current["retained_previous_body"] = True
    return current, body_checked

def collect_source(source, store, limit=None, force_body=False, client_factory=Client):
    try:
        with source_lock(store.root, source["id"]):
            return _collect_source(source, store, limit, force_body, client_factory)
    except LockBusy:
        return {"source_id": source["id"], "source_name": source["name"],
                "started_at": now(), "finished_at": now(), "status": "skipped_busy",
                "body_ok": 0, "body_failed": 0, "new": 0, "updated": 0, "unchanged": 0}


def _collect_source(source, store, limit=None, force_body=False, client_factory=Client):
    result = {"source_id": source["id"], "source_name": source["name"], "started_at": now(),
              "status": "failed", "endpoint_attempts": [], "new": 0, "updated": 0, "unchanged": 0,
              "body_ok": 0, "body_failed": 0, "samples": []}
    client = None
    try:
        client = client_factory(source["hosts"])
        raw = None
        parsed = None
        for endpoint in [source["url"]] + source.get("fallbacks", []):
            try:
                raw, fid, response = fetch_record(client, store, source, endpoint)
                # Validate before accepting a primary; invalid primary may use a documented fallback.
                if source["kind"] == "kev":
                    parsed = json.loads(raw)
                    if not isinstance(parsed.get("vulnerabilities"), list):
                        raise ValueError("invalid_kev_catalog")
                    if not parsed.get("dateReleased") or parsed.get("count") != len(parsed["vulnerabilities"]):
                        raise ValueError("invalid_kev_metadata")
                else:
                    parsed = parse_feed(raw, source)
                result["endpoint_attempts"].append({"url": endpoint, "status": response["status"],
                    "content_type": response["content_type"], "bytes": len(raw), "final_url": response["url"]})
                result["endpoint_used"] = endpoint
                result["feed_fetch_id"] = fid
                break
            except Exception as exc:
                result["endpoint_attempts"].append({"url": endpoint, "error": str(exc)})
                parsed = None
        if parsed is None:
            result["error"] = "all_endpoints_failed"
            return result
        if source["kind"] == "kev":
            result["catalog"] = store.save_kev(parsed, fid)
            result["status"] = result["catalog"]["status"]
            return result
        result["feed_entries"] = len(parsed)
        result["feed_latest_published_utc"] = max((a["published"]["utc"] for a in parsed if a["published"]["utc"]), default=None)
        selected = parsed[:limit] if limit is not None else parsed
        result["processed"] = len(selected)
        consecutive_body_blocks = 0
        for article in selected:
            article["feed_fetch_id"] = fid
            try:
                article, checked = acquire_article(article, source, client, store, force_body,
                    "body_requests_paused_after_repeated_block" if consecutive_body_blocks >= 2 else None)
                error = article.get("body_error", "")
                if error in ("http_429", "http_429_retry_later"):
                    consecutive_body_blocks = 2
                elif error in ("http_403", "http_521", "ReadTimeout", "ConnectTimeout", "ConnectionError"):
                    consecutive_body_blocks += 1
                elif checked:
                    consecutive_body_blocks = 0
                aid, change = store.save_article(article, body_checked=checked)
                result[change] += 1
                if article.get("body_error"):
                    result["body_failed"] += 1
                elif article["content_kind"] == "article_body":
                    result["body_ok"] += 1
                result["samples"].append({"article_id": aid, "title": article["title"], "url": article["url"],
                    "published": article["published"], "modified": article["modified"],
                    "content_kind": article["content_kind"], "text_length": len(article["text"]),
                    "body_error": article.get("body_error"), "selector": article.get("page_metadata", {}).get("selector"),
                    "page_http_status": article.get("page_http_status"), "change": change,
                    "outbound_links": len(article.get("outbound_links", []))})
            except Exception as exc:
                store.db.rollback()
                result.setdefault("item_errors", []).append({"url": article["url"], "error": str(exc)})
        result["status"] = "partial" if result["body_failed"] or result.get("item_errors") else ("ok" if parsed else "empty_feed")
        return result
    except Exception as exc:
        store.db.rollback()
        result["error"] = str(exc)
        return result
    finally:
        if client is not None:
            client.close()
        result["finished_at"] = now()
        store.log_run(result)

def collect_all(sources, store, limit=None, force_body=False, client_factory=Client):
    # Deliberately serial by default: low per-host load and readable per-source diagnostics.
    results = []
    for source in sources:
        results.append(collect_source(source, store, limit, force_body, client_factory))
    return results
