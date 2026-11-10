"""Pure parsing: preserve raw dates, evidence and links; never invent missing facts."""
import hashlib
import html
import json
import re
import warnings
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode, urljoin
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
from dateutil import parser as dateparser
from dateutil.parser import UnknownTimezoneWarning
from defusedxml import ElementTree as SafeET
import feedparser

UTC = timezone.utc
TZ_ABBREVS = {"UTC": 0, "GMT": 0, "CET": 3600, "CEST": 7200,
              "EST": -18000, "EDT": -14400, "PST": -28800, "PDT": -25200}
TRACKING = {"fbclid", "gclid", "mc_cid", "mc_eid"}

def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def normalize_url(url):
    p = urlsplit(url.strip())
    if p.scheme.lower() not in ("http", "https") or not p.hostname or p.username or p.password:
        raise ValueError("invalid_public_url")
    # Keep http vs https and meaningful query parameters; remove only known trackers.
    host = p.hostname.lower()
    port = p.port
    netloc = host if port in (None, 80 if p.scheme.lower() == "http" else 443) else "{}:{}".format(host, port)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in TRACKING]
    return urlunsplit((p.scheme.lower(), netloc, p.path or "/", urlencode(query), ""))

def time_record(raw, hint=None):
    out = {"raw": raw or None, "utc": None, "precision": None, "status": "missing",
           "source_timezone": None}
    if not raw:
        return out
    value = str(raw).strip()
    out["raw"] = value
    # A calendar date is not a midnight timestamp.
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            datetime.strptime(value, "%Y-%m-%d")
            out.update(precision="day", status="date_only", date=value)
        except ValueError:
            out["status"] = "invalid"
        return out
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", UnknownTimezoneWarning)
            dt = dateparser.parse(value, tzinfos=TZ_ABBREVS)
    except (ValueError, TypeError, OverflowError, UnknownTimezoneWarning):
        out["status"] = "unresolved"
        return out
    if not re.search(r"\b\d{4}\b", value):
        out["status"] = "incomplete_date"
        return out
    out["precision"] = "second"
    if dt.tzinfo is None:
        if not hint:
            out["status"] = "timezone_unknown"
            return out
        try:
            zone = ZoneInfo(hint)
        except Exception:
            out["status"] = "timezone_unknown"
            return out
        # Round-trip checks catch nonexistent and ambiguous daylight-saving times.
        possibilities = set()
        for fold in (0, 1):
            aware = dt.replace(tzinfo=zone, fold=fold)
            utc = aware.astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None) == dt:
                possibilities.add(utc)
        out["source_timezone"] = hint
        if len(possibilities) != 1:
            out["status"] = "ambiguous_local_time" if possibilities else "nonexistent_local_time"
            return out
        dt = next(iter(possibilities))
        out["status"] = "source_timezone_assumed"
    else:
        out["source_timezone"] = str(dt.tzinfo)
        out["status"] = "explicit_timezone"
    out["utc"] = dt.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return out

def clean_html(fragment):
    if "<" not in (fragment or ""):
        return re.sub(r"\s+", " ", html.unescape(fragment or "")).strip()
    soup = BeautifulSoup(fragment or "", "html.parser")
    for node in soup.select("script,style,noscript,iframe,nav,form,footer,aside,.comments,#comments"):
        node.decompose()
    # Walk text once: HTML parsers can nest unclosed <p> tags.
    # Reading every paragraph recursively would duplicate the rest of the article.
    for block in list(soup.find_all(["p", "li", "h1", "h2", "h3", "h4", "blockquote", "br"])):
        block.insert_before("\n")
        block.insert_after("\n")
    lines = [re.sub(r"\s+", " ", line).strip() for line in soup.get_text(" ").splitlines()]
    return "\n".join(line for line in lines if line)

def links_from(fragment, base):
    if "<" not in (fragment or ""):
        return []
    soup = BeautifulSoup(fragment or "", "html.parser")
    result = []
    for a in soup.find_all("a", href=True):
        try:
            url = normalize_url(urljoin(base, a["href"]))
        except (ValueError, TypeError):
            continue
        if url not in result:
            result.append(url)
    return result

def parse_feed(raw, source):
    root = SafeET.fromstring(raw)  # Reject XML external entities and entity bombs.
    if root.tag.split("}")[-1].lower() not in ("rss", "feed", "rdf"):
        raise ValueError("not_an_rss_or_atom_feed")
    parsed = feedparser.parse(raw)
    if parsed.bozo:
        raise ValueError("malformed_feed: {}".format(type(parsed.bozo_exception).__name__))
    articles = []
    for item in parsed.entries:
        link = item.get("link")
        if not link or not item.get("title"):
            continue
        try:
            url = normalize_url(link)
        except ValueError:
            continue
        contents = item.get("content", [])
        full = max((x.get("value", "") for x in contents), key=len, default="")
        summary = item.get("summary", "")
        fragment = full or summary
        text = clean_html(fragment)
        # feedparser aliases get('updated') to published on some feeds; membership matters.
        modified = item["updated"] if "updated" in item else None
        articles.append({
            "source_id": source["id"], "url": url, "original_url": link,
            "guid": item.get("id") or None, "title": clean_html(item["title"]),
            "author": item.get("author"), "language": source.get("language"),
            "published": time_record(item.get("published"), source.get("timezone_hint")),
            "modified": time_record(modified, source.get("timezone_hint")),
            "happened_at": None, "feed_text": text,
            "feed_content_kind": "feed_content" if full else ("rss_excerpt" if text else "title_only"),
            "text": text, "content_kind": "feed_content" if full else ("rss_excerpt" if text else "title_only"),
            "outbound_links": links_from(fragment, url),
            "feed_hash": sha(json.dumps({"title": item["title"], "text": text,
                "published": item.get("published"), "modified": modified}, sort_keys=True)),
            "title_zh": None, "summary_zh": None, "enrichment_status": "pending",
        })
    return articles

def _jsonld_nodes(obj):
    if isinstance(obj, list):
        for x in obj:
            yield from _jsonld_nodes(x)
    elif isinstance(obj, dict):
        yield obj
        if "@graph" in obj:
            yield from _jsonld_nodes(obj["@graph"])

def extract_page(raw, url, source):
    soup = BeautifulSoup(raw, "html.parser")
    def meta(*keys):
        for key in keys:
            tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
            if tag and tag.get("content"):
                return tag["content"]
        return None
    structured = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            nodes = _jsonld_nodes(json.loads(script.string or script.get_text()))
            for obj in nodes:
                kinds = obj.get("@type", [])
                if isinstance(kinds, str):
                    kinds = [kinds]
                if any(k in ("NewsArticle", "Article", "BlogPosting", "TechArticle") for k in kinds):
                    structured = obj
                    break
        except (ValueError, TypeError):
            continue
        if structured:
            break
    node = None
    selector = None
    for sel in source.get("selectors", []) + ["[itemprop='articleBody']"]:
        node = soup.select_one(sel)
        if node is not None:
            selector = sel
            break
    if node is not None:
        for sel in source.get("exclude_selectors", []):
            for junk in node.select(sel):
                junk.decompose()
        for adlink in node.select('a[href*="utm_medium=display"],a[href*="utm_medium=paid_display"]'):
            adlink.decompose()
        for junk in node.select("script,style,noscript,iframe,nav,form,footer,aside,.related-posts,.sharedaddy,.comments"):
            junk.decompose()
        text = clean_html(str(node))
        refs = links_from(str(node), url)
    elif structured.get("articleBody"):
        text, refs, selector = clean_html(structured["articleBody"]), [], "jsonld.articleBody"
    else:
        text, refs = "", []
    canonical = soup.find("link", rel="canonical")
    canon = url
    if canonical and canonical.get("href"):
        proposed = urljoin(url, canonical["href"])
        try:
            if urlsplit(proposed).hostname in source["hosts"]:
                canon = normalize_url(proposed)
        except ValueError:
            pass
    return {
        "text": text, "outbound_links": refs, "canonical_url": canon,
        "selector": selector,
        "published": time_record(structured.get("datePublished") or meta("article:published_time", "datePublished", "date"), source.get("timezone_hint")),
        "modified": time_record(structured.get("dateModified") or meta("article:modified_time", "dateModified"), source.get("timezone_hint")),
        "title": clean_html(structured.get("headline") or meta("og:title") or ""),
        "body_usable": len(text) >= 200,
    }

def evidence_for(article):
    """Keep deterministic evidence spans tied to the selected content snapshot."""
    text = article.get("text", "")
    content_hash = sha(text)
    result = []
    offset = 0
    for line in text.splitlines(keepends=True):
        # Bound future model inputs without losing offsets into the full original text.
        for start in range(0, len(line), 1000):
            chunk = line[start:start + 1000].strip()
            if chunk:
                actual_start = offset + start + len(line[start:start+1000]) - len(line[start:start+1000].lstrip())
                result.append({"id": "{}:{}".format(content_hash[:16], actual_start),
                    "content_sha256": content_hash, "start": actual_start,
                    "end": actual_start + len(chunk), "text": chunk,
                    "kind": article["content_kind"]})
        offset += len(line)
    return result
