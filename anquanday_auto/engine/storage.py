"""Local durable collection store; no backend/database server is required."""
import hashlib
import json
import sqlite3
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from dateutil.parser import isoparse
from .parsing import sha, evidence_for

def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)

class Store:
    def __init__(self, data_dir):
        self.root = Path(data_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "raw").mkdir(exist_ok=True)
        self.db = sqlite3.connect(str(self.root / "anquanday.sqlite3"), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS fetches (
          id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, requested_url TEXT NOT NULL,
          final_url TEXT, fetched_at TEXT NOT NULL, status INTEGER, content_type TEXT,
          raw_sha256 TEXT, raw_path TEXT, byte_count INTEGER, error TEXT);
        CREATE TABLE IF NOT EXISTS articles (
          id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, canonical_url TEXT NOT NULL,
          guid TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
          revision_hash TEXT NOT NULL, body_checked_at TEXT, payload TEXT NOT NULL,
          UNIQUE(source_id, canonical_url));
        CREATE INDEX IF NOT EXISTS article_guid ON articles(source_id,guid);
        CREATE TABLE IF NOT EXISTS aliases (
          source_id TEXT NOT NULL, url TEXT NOT NULL, article_id INTEGER NOT NULL REFERENCES articles(id),
          PRIMARY KEY(source_id,url));
        CREATE TABLE IF NOT EXISTS revisions (
          id INTEGER PRIMARY KEY, article_id INTEGER NOT NULL REFERENCES articles(id),
          revision_hash TEXT NOT NULL, captured_at TEXT NOT NULL, payload TEXT NOT NULL,
          UNIQUE(article_id,revision_hash));
        CREATE TABLE IF NOT EXISTS kev_records (
          cve_id TEXT PRIMARY KEY, record_hash TEXT NOT NULL, first_seen_at TEXT NOT NULL,
          last_seen_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS kev_changes (
          id INTEGER PRIMARY KEY, cve_id TEXT NOT NULL, kind TEXT NOT NULL,
          captured_at TEXT NOT NULL, fetch_id INTEGER REFERENCES fetches(id),
          record_hash TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY, source_id TEXT NOT NULL, started_at TEXT NOT NULL,
          finished_at TEXT NOT NULL, result TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS title_translations (
          input_hash TEXT PRIMARY KEY, original_title TEXT NOT NULL,
          policy_version TEXT NOT NULL, title_zh TEXT,
          status TEXT NOT NULL, model TEXT, attempts INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL, retry_after REAL NOT NULL DEFAULT 0,
          error_code TEXT, input_tokens INTEGER, output_tokens INTEGER);
        CREATE INDEX IF NOT EXISTS article_published_utc ON articles(json_extract(payload,'$.published.utc'));
        CREATE INDEX IF NOT EXISTS kev_date_added ON kev_records(json_extract(payload,'$.dateAdded'));
        """)

    def close(self):
        self.db.close()

    def state(self, key, default=None):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_state(self, key, value):
        self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                        (key, dumps(value)))
        self.db.commit()

    def fetched(self, source_id, requested, response=None, error=None):
        response = response or {}
        raw = response.get("raw", b"")
        digest = hashlib.sha256(raw).hexdigest() if raw else None
        path = None
        if digest:
            path = "raw/{}.bin".format(digest)
            target = self.root / path
            if not target.exists():
                # A terminated source must never leave a partial file under its final hash.
                fd, temporary = tempfile.mkstemp(prefix=".raw-", dir=str(self.root / "raw"))
                try:
                    with os.fdopen(fd, "wb") as out:
                        out.write(raw)
                        out.flush()
                    os.replace(temporary, target)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        if response.get("status") == 304:
            cached = self.cache(requested)
            path = cached.get("raw_path")
            if path and (self.root / path).is_file():
                digest = Path(path).stem
        cursor = self.db.execute("""INSERT INTO fetches
          (source_id,requested_url,final_url,fetched_at,status,content_type,raw_sha256,raw_path,byte_count,error)
          VALUES (?,?,?,?,?,?,?,?,?,?)""",
          (source_id, requested, response.get("url"), now(), response.get("status"),
           response.get("content_type"), digest, path, len(raw), error))
        self.db.commit()
        return cursor.lastrowid, path

    def cache(self, url):
        return self.state("http:" + url, {})

    def cache_response(self, url, response, path):
        if response["status"] == 200 and path:
            self.set_state("http:" + url, {"etag": response.get("etag"),
                "last_modified": response.get("last_modified"), "content_type": response.get("content_type"), "raw_path": path,
                "final_url": response["url"]})

    def find_article(self, source_id, url, guid=None):
        row = self.db.execute("""SELECT a.* FROM articles a JOIN aliases u ON u.article_id=a.id
                             WHERE u.source_id=? AND u.url=?""", (source_id, url)).fetchone()
        if not row and guid:
            row = self.db.execute("SELECT * FROM articles WHERE source_id=? AND guid=?", (source_id, guid)).fetchone()
        return dict(row) if row else None

    def save_article(self, article, body_checked=False):
        article = dict(article)
        existing = self.find_article(article["source_id"], article["url"], article.get("guid"))
        if not existing:
            existing = self.find_article(article["source_id"], article.get("original_url", article["url"]), article.get("guid"))
        article["content_sha256"] = sha(article["text"])
        article["evidence"] = evidence_for(article)
        identity = {k: article.get(k) for k in ("title", "text", "content_kind", "published", "modified", "outbound_links")}
        revision_hash = sha(dumps(identity))
        ts = now()
        if existing:
            aid = existing["id"]
            change = "unchanged" if revision_hash == existing["revision_hash"] else "updated"
            self.db.execute("""UPDATE articles SET last_seen_at=?,revision_hash=?,body_checked_at=?,payload=? WHERE id=?""",
                (ts, revision_hash, ts if body_checked else existing["body_checked_at"], dumps(article), aid))
        else:
            cursor = self.db.execute("""INSERT INTO articles
                (source_id,canonical_url,guid,first_seen_at,last_seen_at,revision_hash,body_checked_at,payload)
                VALUES (?,?,?,?,?,?,?,?)""", (article["source_id"], article["url"], article.get("guid"),
                ts, ts, revision_hash, ts if body_checked else None, dumps(article)))
            aid, change = cursor.lastrowid, "new"
        for alias in set([article["url"], article.get("original_url", article["url"])] + article.get("url_aliases", [])):
            self.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?,?)", (article["source_id"], alias, aid))
        self.db.execute("INSERT OR IGNORE INTO revisions(article_id,revision_hash,captured_at,payload) VALUES (?,?,?,?)",
                        (aid, revision_hash, ts, dumps(article)))
        self.db.commit()
        return aid, change

    def all_articles(self):
        return [dict(row, payload=json.loads(row["payload"])) for row in self.db.execute("SELECT * FROM articles ORDER BY id")]

    def save_kev(self, obj, fetch_id):
        release = obj.get("dateReleased")
        prior = self.state("kev_catalog")
        if prior and release and prior.get("dateReleased") and isoparse(release) < isoparse(prior["dateReleased"]):
            return {"status": "stale_ignored", "dateReleased": release, "stored_release": prior["dateReleased"]}
        counts = {"baseline": 0, "added": 0, "updated": 0, "unchanged": 0}
        ts = now()
        # All records + catalog checkpoint in one transaction, including on the first import.
        with self.db:
            for item in obj["vulnerabilities"]:
                cve = item["cveID"]
                rhash = sha(dumps(item))
                old = self.db.execute("SELECT record_hash FROM kev_records WHERE cve_id=?", (cve,)).fetchone()
                kind = ("baseline" if prior is None else "added") if not old else (
                    "unchanged" if old["record_hash"] == rhash else "updated")
                counts[kind] += 1
                self.db.execute("""INSERT INTO kev_records VALUES (?,?,?,?,?)
                    ON CONFLICT(cve_id) DO UPDATE SET record_hash=excluded.record_hash,
                    last_seen_at=excluded.last_seen_at,payload=excluded.payload""",
                    (cve, rhash, ts, ts, dumps(item)))
                if kind != "unchanged":
                    self.db.execute("""INSERT INTO kev_changes(cve_id,kind,captured_at,fetch_id,record_hash,payload)
                                      VALUES (?,?,?,?,?,?)""", (cve, kind, ts, fetch_id, rhash, dumps(item)))
            self.db.execute("INSERT INTO state VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            ("kev_catalog", dumps({"dateReleased": release, "count": len(obj["vulnerabilities"])})))
        return dict(counts, status="ok", dateReleased=release, count=len(obj["vulnerabilities"]))

    def log_run(self, result):
        self.db.execute("INSERT INTO runs(source_id,started_at,finished_at,result) VALUES (?,?,?,?)",
                        (result["source_id"], result["started_at"], now(), dumps(result)))
        self.db.commit()

    def counts(self):
        tables = ("articles", "revisions", "fetches", "kev_records", "kev_changes")
        return {name: self.db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in tables}
