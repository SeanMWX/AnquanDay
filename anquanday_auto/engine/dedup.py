"""Conservative cross-day candidate discovery. Candidates never delete or merge evidence."""
import re
from datetime import datetime
from itertools import combinations
from .parsing import normalize_url

STOP = set("the a an in on of to and for with as by from at is are has have new says hackers hacker attack attacks security news vulnerability flaw".split())

def tokens(title):
    words = set(re.findall(r"[a-z0-9][a-z0-9.-]{2,}", title.lower())) - STOP
    for text in re.findall(r"[\u4e00-\u9fff]+", title):
        words.update(text[i:i+2] for i in range(len(text)-1))
    return words

def cves(text):
    return set(re.findall(r"CVE-\d{4}-\d{4,}", text.upper()))

def elapsed_days(a, b):
    # Uncertain source times do not become exact timestamps for matching.
    pa, pb = a["payload"]["published"], b["payload"]["published"]
    if (not pa.get("utc") or not pb.get("utc") or
            pa.get("status") != "explicit_timezone" or pb.get("status") != "explicit_timezone"):
        return None
    da = datetime.fromisoformat(pa["utc"].replace("Z", "+00:00"))
    db = datetime.fromisoformat(pb["utc"].replace("Z", "+00:00"))
    return abs((da-db).total_seconds())/86400

def meaningful_refs(p):
    own = normalize_url(p["url"])
    refs = set()
    for u in p.get("outbound_links", []):
        try:
            n = normalize_url(u)
            # Avoid ubiquitous homepages falsely suggesting a shared primary source.
            if any(x in n.lower() for x in ("/lp/", "/subscribe", "/newsletter", "/category/", "/tag/")):
                continue
            if len(n.split("/", 3)[-1]) > 12 and n != own:
                refs.add(n)
        except ValueError:
            pass
    return refs

def find_candidates(rows):
    result = []
    # Suitable for initial collection samples. At scale replace O(n^2) with indexed retrieval.
    for a, b in combinations(rows, 2):
        x, y = a["payload"], b["payload"]
        reasons = []
        gap = elapsed_days(a, b)
        strong_text = len(x.get("text", "")) >= 200 and x.get("content_sha256") == y.get("content_sha256")
        shared_cves = sorted(cves(x["title"] + "\n" + x["text"]) & cves(y["title"] + "\n" + y["text"]))
        refs = sorted(meaningful_refs(x) & meaningful_refs(y))
        one_cites_other = x["url"] in y.get("outbound_links", []) or y["url"] in x.get("outbound_links", [])
        tx, ty = tokens(x["title"]), tokens(y["title"])
        common = tx & ty
        similarity = len(common) / max(1, len(tx | ty))
        if strong_text:
            reasons.append("identical_substantial_content")
        if shared_cves and (gap is None or gap <= 180):
            reasons.append("shared_cve_not_proof_of_same_incident")
        if (refs or one_cites_other) and (gap is None or gap <= 180):
            reasons.append("shared_reference_or_direct_citation")
        if (gap is None or gap <= 30) and len(common) >= 3 and similarity >= 0.30:
            reasons.append("similar_title_within_30_days")
        if reasons:
            result.append({"article_ids": [a["id"], b["id"]], "titles": [x["title"], y["title"]],
                "published_gap_days": round(gap, 3) if gap is not None else None,
                "shared_cves": shared_cves, "shared_reference_urls": refs[:5],
                "title_similarity": round(similarity, 3), "reasons": reasons,
                "decision": "same_content" if strong_text else "needs_review",
                "auto_merged": False})
    return result
