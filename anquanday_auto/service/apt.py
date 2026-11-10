"""Evidence-linked reporting activity, not attribution or attack-frequency scoring."""
import json
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from engine.title_translation import title_fields
from engine.apt_catalog import SEED_CATALOG
from .repository import BadQuery, get_zone, parse_day

CATALOG = SEED_CATALOG
LEVELS = {"frequent": "报道较多", "repeated": "多次报道", "mentioned": "近期报道",
          "background": "仅正文提及", "unseen": "未见报道"}
# Ordinary words and names of tools require an adjacent actor descriptor.
AMBIGUOUS = {"play", "silence", "bitter", "patchwork", "equation", "inception", "cleaver",
             "chimera", "dragonfly", "strider", "rancor", "machete", "leafminer", "whitefly",
             "leviathan", "confucius", "platinum", "gallium", "hafnium", "hexane", "curium",
             "neodymium", "polonium", "promethium", "zirconium", "sowbug", "thrip", "rocke",
             "carbanak", "akira", "windigo", "medusa", "rtm", "axiom", "cobalt"}
DESCRIPTOR = r"(?:ransomware|hackers?|group|gang|actors?|operators?|APT|组织|团伙|黑客|勒索|攻击者)"
RELATED_FOOTER = re.compile(r"^\s*(?:related (?:articles|stories|news)|read also|also read|相关推荐|相关阅读|相关文章)\s*[:：]?\s*$", re.I | re.M)


def normalize(value):
    return unicodedata.normalize("NFKC", value or "").casefold()


def name_pattern(name):
    # APT1 must not match APT10; tolerate common spacing/hyphen spellings.
    tokens = re.split(r"[\s\-‐‑–—]+", normalize(name))
    value = r"[\s\-‐‑–—]+".join(re.escape(t) for t in tokens)
    code = re.fullmatch(r"(apt|fin|ta|unc)(\d+)", normalize(name))
    if code:
        value = re.escape(code[1]) + r"[\s\-‐‑–—]*" + code[2]
    return r"(?<![a-z0-9_])" + value + r"(?![a-z0-9_])"


class Matcher:
    def __init__(self, groups):
        owners = defaultdict(set)
        names = {}
        for group in groups:
            variants = [group["name"]] + group.get("associated_names", [])
            short = re.sub(r" (?:Group|Team)$", "", group["name"])
            if short != group["name"] and len(short) >= 4:
                variants.append(short)
            for name in variants:
                key = normalize(name)
                owners[key].add(group["id"])
                names[key] = name
        self.entries = []
        for key, ids in owners.items():
            if len(ids) != 1 or len(key) < 3:
                continue  # Shared associated names do not resolve an organization.
            pattern = name_pattern(key)
            if key in AMBIGUOUS:
                pattern = (r"(?:" + pattern + r"[\s,，:：\-]*(?:" + DESCRIPTOR + r")|" +
                    DESCRIPTOR + r"[\s,，:：\-]*(?:called\s+|known as\s+)?" + pattern + r")")
            code = re.fullmatch(r"(apt|fin|ta|unc)\d+", key)
            anchor = code[1] if code else max(re.split(r"[\s\-‐‑–—]+", key), key=len)
            self.entries.append((next(iter(ids)), names[key], anchor, re.compile(pattern, re.I)))

    def match(self, title, body):
        # The archived extraction remains unchanged. Exclude recognizable
        # recommendation tails from analysis (e.g. BleepingComputer articles).
        clean_body = RELATED_FOOTER.split(body or "", maxsplit=1)[0]
        headline, text = normalize(title), normalize(clean_body)
        matches = defaultdict(list)
        for gid, name, anchor, pattern in self.entries:
            for field, value in (("title", headline), ("text", text)):
                if anchor not in value:
                    continue
                found = pattern.search(value)
                if found:
                    matches[gid].append({"name": name, "field": field,
                        "excerpt": value[max(0, found.start() - 60):found.end() + 100]})
        return matches


@lru_cache(maxsize=2)
def cached_catalog(path, modified):
    catalog = json.loads(Path(path).read_text(encoding="utf-8"))
    return catalog, Matcher(catalog["groups"])


def load_catalog(path=None):
    path = Path(path) if path is not None and Path(path).is_file() else CATALOG
    return cached_catalog(str(path), path.stat().st_mtime_ns)


def pagination(query):
    try:
        page, size = int(query.get("page", "1")), int(query.get("page_size", "12"))
        if not 1 <= page <= 100000 or not 1 <= size <= 100:
            raise ValueError()
        return page, size
    except (TypeError, ValueError):
        raise BadQuery("invalid_pagination")


def paginate(items, page, size):
    pages = max(1, (len(items) + size - 1) // size)
    page = min(page, pages)
    return {"items": items[(page - 1) * size:page * size], "total": len(items),
            "page": page, "pages": pages, "page_size": size}


def activity(reports):
    headline = [r for r in reports if r["match_kind"] == "title"]
    source_count = len({r["source_id"] for r in headline})
    level = ("frequent" if len(headline) >= 5 and source_count >= 2 else
             "repeated" if len(headline) >= 2 else "mentioned" if headline else
             "background" if reports else "unseen")
    return {"level": level, "label": LEVELS[level], "headline_articles": len(headline),
        "body_only_articles": len(reports) - len(headline), "articles": len(reports),
        "headline_sources": source_count, "sources": len({r["source_id"] for r in reports}),
        "latest_published": reports[0]["published"] if reports else None}


def collect_reports(repo, conn, matcher, start, end, zone):
    where, params = repo.period(start.isoformat(), zone, end=end + timedelta(days=1))
    rows = conn.execute("SELECT * FROM articles WHERE " + where, params).fetchall()
    reports = defaultdict(list)
    names = {s["id"]: s["name"] for s in repo.sources}
    dated = []
    for row in rows:
        payload = json.loads(row["payload"])
        published = payload["published"]
        stamp = published.get("utc")
        # Do not count future timestamps from malformed source feeds as activity.
        if stamp and datetime.fromisoformat(stamp.replace("Z", "+00:00")) > datetime.now(timezone.utc):
            continue
        day = (datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(zone).date().isoformat()
               if stamp else published.get("date") or published.get("raw"))
        dated.append(day)
        found = matcher.match(payload.get("title"), payload.get("text"))
        if not found:
            continue
        item = {"id": row["id"], "source_id": row["source_id"],
            "source_name": names.get(row["source_id"], row["source_id"]), "url": payload["url"],
            "title": payload.get("title", ""), "published": published, "published_day": day,
            "revision_hash": row["revision_hash"], "content_kind": payload.get("content_kind"),
            **title_fields(conn, payload.get("title", ""))}
        for gid, evidence in found.items():
            reports[gid].append({**item, "matches": evidence,
                "match_kind": "title" if any(e["field"] == "title" for e in evidence) else "text"})
    for values in reports.values():
        values.sort(key=lambda r: (r["published_day"], r["published"].get("utc") or "", r["id"]), reverse=True)
    uw, up = repo.period("undated", zone)
    undated = conn.execute("SELECT COUNT(*) FROM articles WHERE " + uw, up).fetchone()[0]
    return reports, {"articles_in_window": len(dated), "first_report_day": min(dated) if dated else None,
        "last_report_day": max(dated) if dated else None, "undated_articles_excluded": undated}


def dispatch(repo, path, query):
    allowed = {"tz", "date", "days", "q", "activity", "sort", "page", "page_size"}
    if path.endswith("/articles"):
        allowed = {"tz", "date", "days", "group", "page", "page_size"}
    if set(query) - allowed:
        raise BadQuery("unknown_parameter")
    zone = get_zone(query.get("tz", "Europe/Brussels"))
    end = parse_day(query.get("date", datetime.now(zone).date().isoformat()))
    if end > datetime.now(zone).date():
        raise BadQuery("future_date")
    try:
        days = int(query.get("days", "30"))
        if days not in (7, 30, 90):
            raise ValueError()
    except ValueError:
        raise BadQuery("invalid_days")
    start = end - timedelta(days=days - 1)
    page, size = pagination(query)
    keyword = normalize(query.get("q", "").strip())
    if len(keyword) > 200:
        raise BadQuery("query_too_long")
    selected = query.get("activity", "all")
    sort = query.get("sort", "activity")
    if selected not in ("all", "mentioned", "quiet") or sort not in ("activity", "name"):
        raise BadQuery("invalid_filter")
    catalog, matcher = load_catalog(repo.catalog_path)
    group_id = query.get("group")
    if path.endswith("/articles") and group_id not in {g["id"] for g in catalog["groups"]}:
        raise BadQuery("unknown_group")
    conn = repo.connect()
    try:
        reports, coverage = collect_reports(repo, conn, matcher, start, end, zone)
    finally:
        conn.close()
    common = {"window": {"start": start.isoformat(), "end": end.isoformat(), "days": days, "timezone": str(zone)},
        "coverage": coverage, "generated_at": datetime.now(timezone.utc).isoformat()}
    if path.endswith("/articles"):
        return {**common, "group": group_id, **paginate(reports.get(group_id, []), page, size)}
    items = []
    for group in catalog["groups"]:
        a = activity(reports.get(group["id"], []))
        if (selected == "mentioned" and not a["articles"]) or (selected == "quiet" and a["articles"]):
            continue
        if keyword and keyword not in normalize(" ".join([group["id"], group["name"], group["summary_zh"]] + group["associated_names"])):
            continue
        items.append({**{k: group.get(k) for k in ("id", "name", "associated_names", "summary_zh", "description_en",
            "source_url", "modified", "source_version", "translation")}, "activity": a})
    items.sort(key=lambda g: ((-g["activity"]["headline_articles"], -g["activity"]["body_only_articles"],
        g["name"].casefold()) if sort == "activity" else g["name"].casefold()))
    return {**common, **paginate(items, page, size), "catalog": {"provider": catalog["provider"],
        "total": len(catalog["groups"]), "retrieved_at": catalog["retrieved_at"],
        "last_checked_at": catalog.get("last_checked_at"),
        "versions": sorted({s["version"] for s in catalog["sources"]}), "scope": catalog["scope_zh"]},
        "mentioned_groups": len(reports), "headline_groups": sum(any(r["match_kind"] == "title" for r in v) for v in reports.values())}
