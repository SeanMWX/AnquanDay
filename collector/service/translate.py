"""Independent, bounded translation worker. `python -m service.translate`."""
import argparse
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from engine.locking import FileLock, LockBusy
from engine.storage import Store, now
from engine.title_translation import (POLICY_VERSION, TranslationError, is_chinese,
    read_api_key, title_hash, translate_title, validate_title)
from .config import ROOT, load_service
from .repository import Repository, get_zone, parse_day, PUB, DAY


def latest_news_day(store, zone):
    """Use publication dates in the same timezone and rules as the website."""
    days = []
    for row in store.db.execute("SELECT " + PUB + " utc," + DAY + " day,"
            "json_extract(payload,'$.published.status') status FROM articles"):
        try:
            if row["utc"]:
                day = datetime.fromisoformat(row["utc"].replace("Z", "+00:00")).astimezone(zone).date().isoformat()
            elif row["status"] == "date_only":
                day = parse_day(row["day"]).isoformat()
            else:
                continue
            days.append(day)
        except (ValueError, TypeError):
            continue
    return max(days) if days else None


def candidates(store, include_kev, day=None, zone=None):
    # News first; when scoped to a day, do not spend the remainder on history.
    repo = Repository(store.root, [])
    where, params = repo.period(day, zone) if day else ("1=1", [])
    # Finish the SELECT before yielding: a live read cursor across a network call
    # can cause SQLITE_BUSY_SNAPSHOT when a collector commits in the meantime.
    rows = store.db.execute("SELECT payload FROM articles WHERE " + where + " ORDER BY "
            "coalesce(json_extract(payload,'$.published.utc'),first_seen_at) DESC,id DESC", params).fetchall()
    for row in rows:
        yield json.loads(row["payload"]).get("title", "")
    if include_kev:
        where, params = repo.period(day, zone, kev=True) if day else ("1=1", [])
        rows = store.db.execute("SELECT payload FROM kev_records WHERE " + where + " ORDER BY "
                "json_extract(payload,'$.dateAdded') DESC,cve_id DESC", params).fetchall()
        for row in rows:
            yield json.loads(row["payload"]).get("vulnerabilityName", "")


def run_translation(config, provider=translate_title):
    settings = config["translation"]
    if not settings["enabled"]:
        return {"status": "disabled", "translated": 0}
    key = read_api_key(settings["env_file"])
    if not key:
        return {"status": "key_missing", "translated": 0}
    try:
        with FileLock(Path(config["data_dir"]) / "locks" / "translation.lock"):
            store = Store(config["data_dir"])
            try:
                return _run(store, settings, key, provider)
            finally:
                store.close()
    except LockBusy:
        return {"status": "skipped_busy", "translated": 0}


def _run(store, settings, key, provider):
    result = {"status": "ok", "translated": 0, "failed": 0, "attempted": 0}
    # We own the process lock. Any pending reservation belongs to an exited
    # worker; retry normally, retaining its attempt and daily budget charge.
    store.db.execute("UPDATE title_translations SET status='failed',error_code='worker_interrupted',"
                     "retry_after=0 WHERE status='pending'")
    store.db.commit()
    zone = get_zone(settings.get("timezone", "Europe/Brussels"))
    day = None
    if settings.get("scope", "all") == "latest_day":
        day = latest_news_day(store, zone)
        result.update(date=day, timezone=str(zone))
        if day is None:
            return dict(result, status="no_dated_news")
    if store.state("translation:retry_after", 0) > time.time():
        return dict(result, status="cooldown")
    for original in candidates(store, settings["include_kev"], day, zone):
        if not original or is_chinese(original):
            continue
        digest = title_hash(original)
        row = store.db.execute("SELECT * FROM title_translations WHERE input_hash=?", (digest,)).fetchone()
        if row and row["status"] == "translated":
            try:
                validate_title(row["title_zh"], original)
            except TranslationError:
                # Requeue cached outputs that fail strengthened validation.
                store.db.execute("UPDATE title_translations SET status='failed',title_zh=NULL,"
                    "error_code='invalid_cached_title',retry_after=0 WHERE input_hash=?", (digest,))
                store.db.commit()
                row = dict(row, status="failed", retry_after=0)
            else:
                continue
        if row and (row["attempts"] >= settings["max_attempts"] or row["retry_after"] > time.time()):
            continue
        if result["attempted"] >= settings["max_items_per_run"]:
            break
        # Reserve budget and attempt BEFORE network I/O. A crash/timeout still counts.
        budget_day = datetime.now(timezone.utc).date().isoformat()
        budget_key = "translation:requests:" + budget_day
        used = store.state(budget_key, 0)
        if used >= settings["daily_max_requests"]:
            result["status"] = "daily_limit"
            break
        store.db.execute("INSERT INTO state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (budget_key, json.dumps(used + 1)))
        store.db.execute("""INSERT INTO title_translations
            (input_hash,original_title,policy_version,status,model,attempts,updated_at,retry_after)
            VALUES (?,?,?,'pending',?,1,?,?) ON CONFLICT(input_hash) DO UPDATE SET
            status='pending',attempts=attempts+1,model=excluded.model,
            updated_at=excluded.updated_at,retry_after=excluded.retry_after""",
            (digest, original, POLICY_VERSION, settings["model"], now(), time.time() + settings["retry_seconds"]))
        store.db.commit()
        result["attempted"] += 1
        try:
            translated, usage = provider(original, key, settings)
            translated = validate_title(translated, original)
        except Exception as error:
            # Unexpected provider errors also cannot disclose credentials in a traceback.
            allowed = {"network_error", "incomplete_response", "missing_output", "invalid_output",
                "invalid_response", "invalid_source_title", "invalid_chinese_title", "changed_cve_identifier",
                "invalid_security_term"}
            value = str(error) if isinstance(error, TranslationError) else ""
            code = value if value in allowed or re.fullmatch(r"http_[1-5][0-9]{2}", value) else "provider_error"
            store.db.execute("UPDATE title_translations SET status='failed',error_code=? WHERE input_hash=?", (code, digest))
            store.db.commit()
            result["failed"] += 1
            if code in ("http_401", "http_403", "http_429", "network_error") or code.startswith("http_5"):
                store.set_state("translation:retry_after", time.time() + min(settings["retry_seconds"], 300))
                break
            continue
        store.db.execute("""UPDATE title_translations SET status='translated',title_zh=?,
            updated_at=?,error_code=NULL,retry_after=0,input_tokens=?,output_tokens=? WHERE input_hash=?""",
            (translated, now(), usage.get("input_tokens"), usage.get("output_tokens"), digest))
        store.db.commit()
        result["translated"] += 1
    return result


def main():
    parser = argparse.ArgumentParser(description="翻译已采集的外文标题（从本地 .env 读取 key）")
    parser.add_argument("--config", default=str(ROOT / "config" / "service.json"))
    args = parser.parse_args()
    config, _ = load_service(args.config)
    print(json.dumps(run_translation(config), ensure_ascii=False))


if __name__ == "__main__":
    main()
