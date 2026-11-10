"""Read model: one read-only SQLite transaction per API request."""
import calendar
import json
import re
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from engine.title_translation import title_hash, title_fields

PUB = "json_extract(payload,'$.published.utc')"
DAY = "coalesce(json_extract(payload,'$.published.date'),json_extract(payload,'$.published.raw'))"
DATE_ONLY = "json_extract(payload,'$.published.status')='date_only'"
KEV_DAY = "json_extract(payload,'$.dateAdded')"


class BadQuery(ValueError):
    pass


def get_zone(value):
    if not value or len(value) > 64:
        raise BadQuery("invalid_timezone")
    try:
        return ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise BadQuery("invalid_timezone")


def parse_day(value):
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError()
        parsed = date.fromisoformat(value)
        if not 1900 <= parsed.year <= 2200:
            raise ValueError()
        return parsed
    except (ValueError, TypeError):
        raise BadQuery("invalid_date")


def utc_boundary(day, zone):
    return datetime.combine(day, time.min, tzinfo=zone).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def article_metadata(row, revision_count):
    p = json.loads(row["payload"])
    a = {k: p.get(k) for k in ("source_id", "title", "language", "url", "original_url", "guid", "author",
         "published", "modified", "happened_at", "content_kind", "feed_content_kind", "content_sha256",
         "body_error", "page_http_status", "feed_fetch_id", "page_fetch_id")}
    a.update(id=row["id"], first_seen_at=row["first_seen_at"], last_seen_at=row["last_seen_at"],
        revision_hash=row["revision_hash"], revision_count=revision_count,
        text_length=len(p.get("text", "")), evidence_count=len(p.get("evidence", [])),
        reference_count=len(p.get("outbound_links", [])), selector=p.get("page_metadata", {}).get("selector"))
    return a


class Repository:
    def __init__(self, data_dir, sources):
        self.db_path = Path(data_dir) / "anquanday.sqlite3"
        self.catalog_path = Path(data_dir) / "apt" / "catalog.json"
        self.sources = sources
        self.ids = {s["id"] for s in sources}

    def connect(self):
        conn = sqlite3.connect(self.db_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.create_function("title_hash", 1, title_hash, deterministic=True)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        return conn

    def source(self, value):
        if value != "all" and value not in self.ids:
            raise BadQuery("unknown_source")
        return value

    def period(self, day, zone, kev=False, end=None):
        if day == "undated":
            return ((KEV_DAY + " IS NULL", []) if kev else
                (PUB + " IS NULL AND NOT coalesce((" + DATE_ONLY + " AND " + DAY + " IS NOT NULL),0)", []))
        start = parse_day(day)
        end = end or start + timedelta(days=1)
        if kev:
            return KEV_DAY + ">=? AND " + KEV_DAY + "<?", [day, end.isoformat()]
        return ("((" + PUB + ">=? AND " + PUB + "<?) OR (" + PUB + " IS NULL AND " + DATE_ONLY +
            " AND " + DAY + ">=? AND " + DAY + "<?))",
            [utc_boundary(start, zone), utc_boundary(end, zone), day, end.isoformat()])

    def source_list(self):
        return [{k: s.get(k) for k in ("id", "name", "kind", "language", "url", "timezone_hint")} for s in self.sources]

    def overview(self, conn, day, zone):
        where, params = self.period(day, zone)
        rows = conn.execute("SELECT source_id,json_extract(payload,'$.content_kind') kind,COUNT(*) n "
            "FROM articles WHERE " + where + " GROUP BY source_id,kind", params).fetchall()
        counts = {s["id"]: 0 for s in self.sources}
        kinds = {}
        for row in rows:
            counts[row["source_id"]] = counts.get(row["source_id"], 0) + row["n"]
            kinds[row["kind"]] = kinds.get(row["kind"], 0) + row["n"]
        kw, kp = self.period(day, zone, True)
        counts["cisa_kev"] = conn.execute("SELECT COUNT(*) FROM kev_records WHERE " + kw, kp).fetchone()[0]
        totals = {name: conn.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in ("articles", "revisions", "kev_records")}
        source_totals = {r[0]: r[1] for r in conn.execute("SELECT source_id,COUNT(*) FROM articles GROUP BY source_id")}
        source_totals["cisa_kev"] = totals["kev_records"]
        return {"date": day, "timezone": str(zone), "news": sum(kinds.values()),
            "body": kinds.get("article_body", 0), "excerpt": kinds.get("rss_excerpt", 0) + kinds.get("feed_content", 0),
            "title_only": kinds.get("title_only", 0), "kev": counts["cisa_kev"], "source_counts": counts,
            "source_totals": source_totals, "totals": totals}

    def calendar(self, conn, month, zone, source):
        if not re.fullmatch(r"\d{4}-\d{2}", month):
            raise BadQuery("invalid_month")
        start = parse_day(month + "-01")
        end = start + timedelta(days=calendar.monthrange(start.year, start.month)[1])
        kev = source == "cisa_kev"
        where, params = self.period(start.isoformat(), zone, kev, end)
        extra = " AND source_id=?" if not kev and source != "all" else ""
        scope = [source] if extra else []
        counts = {}
        table = "kev_records" if kev else "articles"
        fields = KEV_DAY + " day" if kev else PUB + " utc," + DAY + " day"
        for row in conn.execute("SELECT " + fields + " FROM " + table + " WHERE " + where + extra, params + scope):
            day = row["day"] if kev or not row["utc"] else datetime.fromisoformat(row["utc"].replace("Z", "+00:00")).astimezone(zone).date().isoformat()
            counts[day] = counts.get(day, 0) + 1
        uw, up = self.period("undated", zone, kev)
        unknown = conn.execute("SELECT COUNT(*) FROM " + table + " WHERE (" + uw + ")" + extra, up + scope).fetchone()[0]
        return {"month": month, "timezone": str(zone), "source": source, "days": counts, "undated": unknown}

    def listing(self, conn, query, zone, kev=False):
        source = self.source(query.get("source", "all"))
        if not kev and source == "cisa_kev":
            raise BadQuery("use_kev_endpoint")
        day = query.get("date", datetime.now(zone).date().isoformat())
        where, params = self.period(day, zone, kev)
        if not kev and source != "all":
            where += " AND source_id=?"
            params.append(source)
        keyword = query.get("q", "").strip()
        if len(keyword) > 200:
            raise BadQuery("query_too_long")
        if keyword:
            paths = ("cveID", "vendorProject", "product", "vulnerabilityName", "shortDescription") if kev else ("title", "guid")
            expressions = ["coalesce(json_extract(payload,'$." + k + "'),'')" for k in paths]
            title_path = "vulnerabilityName" if kev else "title"
            expressions.append("coalesce((SELECT title_zh FROM title_translations WHERE "
                "status='translated' AND input_hash=title_hash(json_extract(payload,'$." + title_path + "'))),'')")
            where += " AND instr(lower(" + " || ' ' || ".join(expressions) + "),lower(?))>0"
            params.append(keyword)
        kind = query.get("kind", "all")
        if kind not in ("all", "article_body", "rss_excerpt", "title_only"):
            raise BadQuery("invalid_kind")
        if not kev and kind != "all":
            if kind == "rss_excerpt":
                where += " AND json_extract(payload,'$.content_kind') IN ('rss_excerpt','feed_content')"
            else:
                where += " AND json_extract(payload,'$.content_kind')=?"
                params.append(kind)
        sort = query.get("sort", "published")
        if sort not in ("published", "collected"):
            raise BadQuery("invalid_sort")
        try:
            page, size = int(query.get("page", "1")), int(query.get("page_size", "12"))
            if not 1 <= page <= 100000 or not 1 <= size <= 100:
                raise ValueError()
        except ValueError:
            raise BadQuery("invalid_pagination")
        table = "kev_records" if kev else "articles"
        total = conn.execute("SELECT COUNT(*) FROM " + table + " WHERE " + where, params).fetchone()[0]
        pages = max(1, (total + size - 1) // size)
        page = min(page, pages)
        order = "last_seen_at" if sort == "collected" else KEV_DAY if kev else "coalesce(" + PUB + "," + DAY + ")"
        tie = "cve_id" if kev else "id"
        selected = conn.execute("SELECT * FROM " + table + " WHERE " + where + " ORDER BY " + order +
            " DESC," + tie + " DESC LIMIT ? OFFSET ?", params + [size, (page - 1) * size]).fetchall()
        items = []
        for row in selected:
            if kev:
                item = json.loads(row["payload"])
                item.update(first_seen_at=row["first_seen_at"], last_seen_at=row["last_seen_at"])
            else:
                revisions = conn.execute("SELECT COUNT(*) FROM revisions WHERE article_id=?", (row["id"],)).fetchone()[0]
                item = article_metadata(row, revisions)
            item.update(title_fields(conn, item.get("vulnerabilityName" if kev else "title", "")))
            items.append(item)
        return {"date": day, "timezone": str(zone), "source": source, "items": items, "total": total,
                "page": page, "page_size": size, "pages": pages}

    def dispatch(self, path, query):
        if path in ("/api/v1/apt/groups", "/api/v1/apt/articles"):
            from .apt import dispatch
            return dispatch(self, path, query)
        allowed = {"date", "tz", "month", "source", "q", "kind", "sort", "page", "page_size"}
        if set(query) - allowed:
            raise BadQuery("unknown_parameter")
        zone = get_zone(query.get("tz", "Europe/Brussels"))
        source = self.source(query.get("source", "all"))
        if path == "/api/v1/sources":
            return {"items": self.source_list()}
        conn = self.connect()
        try:
            if path == "/api/v1/health":
                conn.execute("SELECT COUNT(*) FROM articles").fetchone()
                return {"ok": True, "database": "sqlite", "api_version": 1}
            if path == "/api/v1/overview":
                return self.overview(conn, query.get("date", datetime.now(zone).date().isoformat()), zone)
            if path == "/api/v1/calendar":
                return self.calendar(conn, query.get("month", datetime.now(zone).strftime("%Y-%m")), zone, source)
            if path in ("/api/v1/articles", "/api/v1/kev"):
                return self.listing(conn, query, zone, path.endswith("/kev"))
            raise KeyError("not_found")
        finally:
            conn.close()
