import json
import pytest
from engine.storage import Store
from service.apt import Matcher, activity, load_catalog
from service.repository import Repository, BadQuery
from test_engine import article, SOURCE


def group(gid, name, aliases=()):
    return {"id": gid, "name": name, "associated_names": list(aliases)}


def test_name_boundaries_spacing_aliases_and_shared_names():
    matcher = Matcher([group("1", "APT1", ["Shared Alias"]), group("10", "APT10", ["Shared Alias"]),
        group("28", "APT28", ["Fancy Bear"]), group("l", "Lazarus Group")])
    assert set(matcher.match("APT10 and APT 28", "")) == {"10", "28"}
    assert set(matcher.match("APT-28：新闻", "Fancy Bear")) == {"28"}
    assert set(matcher.match("APT280 and xAPT1 and Shared Alias", "")) == set()
    assert set(matcher.match("Lazarus attacks", "")) == {"l"}
    assert len(matcher.match("APT28 Fancy Bear Fancy Bear", "APT28")["28"]) == 3


def test_generic_names_need_actor_context():
    matcher = Matcher([group("p", "Play"), group("b", "BITTER"), group("s", "Silence")])
    assert not matcher.match("Play an important role in preventing attacks", "a bitter lesson and silence")
    assert set(matcher.match("Play ransomware targets firms", "BITTER group")) == {"p", "b"}
    assert set(matcher.match("Silence黑客组织", "")) == {"s"}


def test_related_article_footer_is_not_evidence():
    matcher = Matcher([group("28", "APT28"), group("29", "APT29")])
    found = matcher.match("A breach report", "Historical APT28 comparison.\nRelated Articles:\nAPT29 targets firms")
    assert set(found) == {"28"}
    assert not matcher.match("Other news", "No known attribution.\n相关推荐\nAPT28 活动")


@pytest.fixture
def apt_repo(tmp_path):
    store = Store(tmp_path)
    samples = [
        article("APT28 and Fancy Bear report", published="2026-11-08T23:30:00Z"),
        article("A separate story", url="https://example.com/body", text="Historical comparison with APT28."),
        article("APT28 report", url="https://example.com/old", published="2026-10-01T10:00:00Z"),
        article("APT28 undated", url="https://example.com/undated", published=None),
        article("APT29 update", url="https://example.com/date", published="2026-11-09"),
        article("APT28 future", url="https://example.com/future", published="2200-01-01T00:00:00Z"),
    ]
    for sample in samples:
        store.save_article(sample)
    # Recollection and a changed revision still represent one article.
    assert store.save_article(samples[0])[1] == "unchanged"
    samples[0]["text"] += " Updated information."
    assert store.save_article(samples[0])[1] == "updated"
    store.close()
    return Repository(tmp_path, [SOURCE])


def listing(repo, **query):
    return repo.dispatch("/api/v1/apt/groups", {"date": "2026-11-09", "q": "APT28", **query})


def test_activity_uses_publication_not_collection_and_exposes_evidence(apt_repo):
    result = listing(apt_repo)
    item = next(g for g in result["items"] if g["name"] == "APT28")
    assert item["activity"]["headline_articles"] == 1
    assert item["activity"]["body_only_articles"] == 1
    assert item["activity"]["level"] == "mentioned"
    assert result["coverage"]["undated_articles_excluded"] == 1
    reports = apt_repo.dispatch("/api/v1/apt/articles", {"date": "2026-11-09", "group": item["id"], "page_size": "1"})
    assert reports["total"] == 2 and reports["pages"] == 2
    assert reports["items"][0]["match_kind"] == "text"
    assert reports["items"][0]["matches"][0]["name"] == "APT28"
    assert reports["items"][0]["revision_hash"]
    assert reports["items"][0]["url"] == "https://example.com/body"
    assert "text" not in reports["items"][0]  # Only bounded match excerpts are exposed.
    assert listing(apt_repo, days="90")["items"][0]["activity"]["headline_articles"] == 2


def test_timezone_start_boundary(tmp_path):
    store = Store(tmp_path)
    store.save_article(article("APT28", published="2026-11-02T23:30:00Z"))
    store.save_article(article("APT29", url="https://example.com/day", published="2026-11-03"))
    store.close()
    repo = Repository(tmp_path, [SOURCE])
    assert listing(repo, days="7", tz="Europe/Brussels")["items"][0]["activity"]["headline_articles"] == 1
    assert listing(repo, days="7", tz="UTC")["items"][0]["activity"]["headline_articles"] == 0
    for zone in ("UTC", "Asia/Shanghai", "Europe/Brussels"):
        apt29 = listing(repo, q="APT29", days="7", tz=zone)["items"][0]
        assert apt29["activity"]["headline_articles"] == 1


def test_thresholds_distinguish_body_from_headlines():
    def report(source, kind):
        return {"source_id": source, "match_kind": kind, "published": {"utc": "2026-11-09T08:00:00Z"}}
    assert activity([])["level"] == "unseen"
    assert activity([report("a", "text")] * 20)["level"] == "background"
    assert activity([report("a", "title")] * 5)["level"] == "repeated"
    assert activity([report("a", "title")] * 4 + [report("b", "title")])["level"] == "frequent"


@pytest.mark.parametrize("query", [{"days": "1"}, {"days": "abc"}, {"date": "2200-01-01"},
    {"date": "undated"}, {"activity": "attacking"}, {"sort": "sql"}, {"tz": "invalid"},
    {"page_size": "101"}, {"q": "a" * 201}, {"unknown": "1"}])
def test_invalid_queries(apt_repo, query):
    with pytest.raises(BadQuery):
        listing(apt_repo, **query)


def test_catalog_is_chinese_and_source_linked():
    catalog, _ = load_catalog()
    assert len(catalog["groups"]) >= 180
    assert len({g["id"] for g in catalog["groups"]}) == len(catalog["groups"])
    for item in catalog["groups"]:
        assert item["summary_zh"] and item["description_en"]
        assert item["source_url"].startswith("https://attack.mitre.org/groups/")
        assert item["translation"]["input_hash"]


def test_filter_search_pagination_and_read_updates(apt_repo, tmp_path):
    data = listing(apt_repo, q="", activity="mentioned", page_size="1", page="999")
    assert data["total"] == 2 and data["page"] == 2
    assert listing(apt_repo, q="Fancy Bear")["items"][0]["name"] == "APT28"
    assert not any(g["name"] == "APT28" for g in listing(apt_repo, activity="quiet")["items"])
    store = Store(tmp_path)
    store.save_article(article("APT28 new report", url="https://example.com/new"))
    store.close()
    assert listing(apt_repo)["items"][0]["activity"]["headline_articles"] == 2
