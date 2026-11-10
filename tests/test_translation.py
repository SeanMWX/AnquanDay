import json
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
import requests

from engine.locking import FileLock
from engine.storage import Store
from engine.title_translation import (TranslationError, read_api_key, title_fields,
    title_hash, translate_title)
from service.config import ROOT, load_service
from service.repository import Repository
from service.translate import run_translation
from test_engine import article, SOURCE


@pytest.fixture
def setup_translation(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    cfg, _ = load_service(ROOT / "config" / "service.json")
    cfg["data_dir"] = str(tmp_path)
    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=test-secret\n", encoding="utf-8")
    cfg["translation"]["env_file"] = str(env)
    store = Store(tmp_path)
    store.save_article(article())
    store.close()
    return cfg


def fake_provider(original, key, settings):
    return "公司报告遭遇入侵", {"input_tokens": 30, "output_tokens": 10}


def test_key_reader_is_literal_and_environment_takes_precedence(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert read_api_key(env) == ""
    env.write_text('# 注释\nUNRELATED=x\nOPENAI_API_KEY="literal-$NOT_EXPANDED"\n', encoding="utf-8-sig")
    assert read_api_key(env) == "literal-$NOT_EXPANDED"
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret")
    assert read_api_key(env) == "environment-secret"


def test_missing_key_and_disabled_make_no_requests(setup_translation):
    cfg = setup_translation
    from pathlib import Path
    Path(cfg["translation"]["env_file"]).write_text("OPENAI_API_KEY=\n")
    no_request = lambda *args: pytest.fail("must not call a provider")
    assert run_translation(cfg, no_request)["status"] == "key_missing"
    cfg["translation"]["enabled"] = False
    assert run_translation(cfg, no_request)["status"] == "disabled"


def test_cache_preserves_evidence_reuses_titles_and_invalidates_title_changes(setup_translation):
    cfg = setup_translation
    store = Store(cfg["data_dir"])
    original = store.db.execute("SELECT payload,revision_hash FROM articles").fetchone()
    duplicate = article(url="https://another.example/news")
    duplicate["source_id"] = "other"
    store.save_article(duplicate)
    store.save_article(article(title="企业报告安全事件", url="https://example.com/zh"))
    calls = []
    def provider(title, *args):
        calls.append(title)
        return fake_provider(title, *args)
    assert run_translation(cfg, provider)["translated"] == 1
    assert len(calls) == 1
    assert tuple(store.db.execute("SELECT payload,revision_hash FROM articles WHERE id=1").fetchone()) == tuple(original)
    assert title_fields(store.db, "企业报告安全事件")["translation"]["status"] == "original_zh"
    changed_body = article(text="Updated investigation. " * 20)
    store.save_article(changed_body)
    assert run_translation(cfg, provider)["attempted"] == 0
    changed_body["title"] = "Company corrects earlier breach report"
    store.save_article(changed_body)
    fields = title_fields(store.db, changed_body["title"])
    assert fields["display_title"] == "标题待翻译" and fields["title_zh"] is None
    assert run_translation(cfg, provider)["translated"] == 1
    assert len(calls) == 2
    store.close()


def test_chinese_search_original_reference_and_no_secret_in_api(setup_translation):
    cfg = setup_translation
    repo = Repository(cfg["data_dir"], [SOURCE])
    pending = repo.dispatch("/api/v1/articles", {"date": "2026-11-09"})["items"][0]
    assert pending["display_title"] == "标题待翻译"
    assert pending["title"] == "Company reports breach"
    assert run_translation(cfg, fake_provider)["translated"] == 1
    result = repo.dispatch("/api/v1/articles", {"date": "2026-11-09", "q": "遭遇入侵"})
    assert result["total"] == 1
    translated = result["items"][0]
    assert translated["display_title"] == "公司报告遭遇入侵"
    assert translated["title"] == "Company reports breach"
    assert translated["translation"]["input_hash"] == title_hash(translated["title"])
    assert translated["translation"]["translated_at"]
    assert "test-secret" not in json.dumps(result)
    assert "error_code" not in json.dumps(result)
    # A changed title cannot match search through a stale translation.
    store = Store(cfg["data_dir"])
    store.save_article(article(title="A revised report"))
    store.close()
    assert repo.dispatch("/api/v1/articles", {"date": "2026-11-09", "q": "遭遇入侵"})["total"] == 0


def test_failure_reserves_budget_and_retry_cannot_loop(setup_translation):
    cfg = setup_translation
    cfg["translation"]["daily_max_requests"] = 1
    def broken(*args):
        raise RuntimeError("test-secret")
    assert run_translation(cfg, broken)["failed"] == 1
    store = Store(cfg["data_dir"])
    row = store.db.execute("SELECT * FROM title_translations").fetchone()
    assert row["status"] == "failed" and row["attempts"] == 1
    assert row["error_code"] == "provider_error"
    assert run_translation(cfg, fake_provider)["attempted"] == 0
    store.db.execute("UPDATE title_translations SET retry_after=0")
    store.db.commit()
    assert run_translation(cfg, fake_provider)["status"] == "daily_limit"
    cfg["translation"]["daily_max_requests"] = 10
    assert run_translation(cfg, fake_provider)["translated"] == 1
    assert store.db.execute("SELECT attempts FROM title_translations").fetchone()[0] == 2
    store.close()


def test_lock_prevents_overlapping_translation_workers(setup_translation):
    cfg = setup_translation
    from pathlib import Path
    with FileLock(Path(cfg["data_dir"]) / "locks" / "translation.lock"):
        assert run_translation(cfg, lambda *args: pytest.fail("overlap"))["status"] == "skipped_busy"


def test_auth_failure_backoff_does_not_attempt_each_article(setup_translation):
    cfg = setup_translation
    store = Store(cfg["data_dir"])
    store.save_article(article(title="Another news report", url="https://example.com/two"))
    def unauthenticated(*args):
        raise TranslationError("http_401")
    result = run_translation(cfg, unauthenticated)
    assert result["attempted"] == 1 and result["failed"] == 1
    assert run_translation(cfg, fake_provider)["status"] == "cooldown"
    assert store.db.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
    store.close()


class Response:
    status_code = 200
    def __init__(self, payload):
        self.payload = payload
        self.closed = False
    def json(self):
        return self.payload
    def close(self):
        self.closed = True


def response_body(title):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps({"title_zh": title})}]}],
        "usage": {"input_tokens": 50, "output_tokens": 20}}


def test_responses_request_and_output_contract(monkeypatch):
    reply = Response(response_body("修复了 CVE-2026-12345 漏洞"))
    def post(url, **kwargs):
        assert url == "https://api.openai.com/v1/responses"
        assert kwargs["headers"]["Authorization"] == "Bearer test-secret"
        assert kwargs["allow_redirects"] is False
        assert kwargs["json"]["store"] is False
        assert kwargs["json"]["text"]["format"]["strict"] is True
        assert json.loads(kwargs["json"]["input"])["original_title"] == "CVE-2026-12345 fixed"
        return reply
    monkeypatch.setattr(requests, "post", post)
    value, usage = translate_title("CVE-2026-12345 fixed", "test-secret",
        {"model": "gpt-4.1-mini", "request_timeout_seconds": 30})
    assert value == "修复了 CVE-2026-12345 漏洞" and usage["input_tokens"] == 50 and reply.closed


@pytest.mark.parametrize("payload", [response_body("Still English"), response_body(""),
    response_body("<script>中文</script>"), response_body("中文\n解释"), response_body("漏洞已修复"),
    {"status": "incomplete", "output": []},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "No"}]}]},
    {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "invalid json"}]}]}])
def test_bad_outputs_are_not_published(monkeypatch, payload):
    reply = Response(payload)
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: reply)
    with pytest.raises(TranslationError):
        translate_title("CVE-2026-12345 fixed", "test-secret", {"model": "test", "request_timeout_seconds": 1})
    assert reply.closed


def test_kev_translation_and_chinese_search(setup_translation):
    cfg = setup_translation
    store = Store(cfg["data_dir"])
    payload = {"cveID": "CVE-2026-12345", "dateAdded": "2026-11-09", "vulnerabilityName": "A critical flaw"}
    store.db.execute("INSERT INTO kev_records VALUES (?,?,?,?,?)", (payload["cveID"], "hash", "now", "now", json.dumps(payload)))
    store.db.commit()
    assert run_translation(cfg, fake_provider)["translated"] == 2
    result = Repository(cfg["data_dir"], [SOURCE]).dispatch("/api/v1/kev", {"date": "2026-11-09", "q": "入侵"})
    assert result["total"] == 1
    assert result["items"][0]["vulnerabilityName"] == "A critical flaw"
    assert result["items"][0]["title_zh"] == "公司报告遭遇入侵"
    store.close()


def test_service_runs_translation_without_collection_and_does_not_serve_key(setup_translation):
    from pathlib import Path
    cfg = setup_translation
    root = Path(cfg["data_dir"])
    Path(cfg["translation"]["env_file"]).write_text("OPENAI_API_KEY=\n")
    cfg["scheduler"]["enabled"] = False
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        cfg["api"]["port"] = sock.getsockname()[1]
    path = root / "service.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with (root / "service-test.log").open("w") as log:
        proc = subprocess.Popen([sys.executable, "-m", "service", "--config", str(path), "--stdin-control"],
            cwd=str(ROOT), stdin=subprocess.PIPE, stdout=log, stderr=log,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + 10
            worker_log = root / "logs" / "_translation.log"
            while time.monotonic() < deadline:
                if worker_log.exists() and "key_missing" in worker_log.read_text():
                    break
                assert proc.poll() is None
                time.sleep(0.1)
            assert "key_missing" in worker_log.read_text()
            with urlopen("http://127.0.0.1:{}/api/v1/articles?date=2026-11-09".format(cfg["api"]["port"]), timeout=3) as response:
                payload = response.read().decode("utf-8")
                assert "标题待翻译" in payload and "OPENAI_API_KEY" not in payload
            assert not (root / "logs" / "freebuf.log").exists()
        finally:
            if proc.poll() is None:
                proc.communicate(b"stop\n", timeout=10)
            assert proc.returncode == 0


@pytest.mark.parametrize("zone,expected", [
    ("Europe/Brussels", {"Late UTC report", "Date-only report", "Latest vulnerability"}),
    ("UTC", {"Date-only report", "Latest vulnerability"}),
])
def test_latest_day_uses_display_timezone_and_excludes_history(setup_translation, zone, expected):
    cfg = setup_translation
    cfg["translation"].update(scope="latest_day", timezone=zone)
    store = Store(cfg["data_dir"])
    for title, url, published in (
        ("Older report", "old", "2026-11-08T08:00:00Z"),
        ("Late UTC report", "late", "2026-11-09T23:30:00Z"),
        ("Date-only report", "date", "2026-11-10"),
        ("Unknown date report", "unknown", None),
    ):
        store.save_article(article(title=title, url="https://example.com/" + url, published=published))
    for identifier, day, title in (("old", "2026-11-09", "Older vulnerability"),
                                    ("latest", "2026-11-10", "Latest vulnerability")):
        payload = {"dateAdded": day, "vulnerabilityName": title}
        store.db.execute("INSERT INTO kev_records VALUES (?,?,?,?,?)", (identifier, "hash", "now", "now", json.dumps(payload)))
    store.db.commit()
    calls = []
    def provider(title, *args):
        calls.append(title)
        return fake_provider(title, *args)
    result = run_translation(cfg, provider)
    assert result["date"] == "2026-11-10" and result["timezone"] == zone
    assert set(calls) == expected
    assert run_translation(cfg, provider)["attempted"] == 0
    assert store.db.execute("SELECT COUNT(*) FROM title_translations").fetchone()[0] == len(expected)
    store.close()


def test_latest_day_follows_newly_collected_day_without_backfilling(setup_translation):
    cfg = setup_translation
    cfg["translation"].update(scope="latest_day", include_kev=False)
    store = Store(cfg["data_dir"])
    store.save_article(article(title="Older report", url="https://example.com/old", published="2026-11-08"))
    assert run_translation(cfg, fake_provider)["translated"] == 1
    store.save_article(article(title="New day report", url="https://example.com/new", published="2026-11-10"))
    result = run_translation(cfg, fake_provider)
    assert result["date"] == "2026-11-10" and result["translated"] == 1
    assert title_fields(store.db, "Older report")["translation"]["status"] == "pending"
    store.close()


def test_latest_day_without_dated_news_does_not_translate_unknown_dates(setup_translation):
    cfg = setup_translation
    cfg["translation"]["scope"] = "latest_day"
    store = Store(cfg["data_dir"])
    store.save_article(article(published=None))
    store.close()
    assert run_translation(cfg, lambda *a: pytest.fail("no dated news"))["status"] == "no_dated_news"


def test_default_scope_translates_all_dates_and_resumes_cached_batches(setup_translation):
    cfg = setup_translation
    cfg["translation"]["max_items_per_run"] = 2
    store = Store(cfg["data_dir"])
    for title, slug, published in (
        ("Older report", "old", "2026-11-08"),
        ("Unknown date report", "unknown", None),
        ("Newer report", "new", "2026-11-10"),
        ("Older report", "duplicate", "2026-11-08"),
        ("中文新闻", "chinese", "2026-11-10"),
    ):
        store.save_article(article(title=title, url="https://example.com/" + slug, published=published))
    calls = []
    def provider(title, *args):
        calls.append(title)
        return fake_provider(title, *args)
    assert run_translation(cfg, provider)["translated"] == 2
    assert run_translation(cfg, provider)["translated"] == 2
    assert len(calls) == 4
    assert set(calls) == {"Company reports breach", "Older report", "Unknown date report", "Newer report"}
    assert run_translation(cfg, provider)["attempted"] == 0
    for title in calls:
        assert title_fields(store.db, title)["translation"]["status"] == "translated"
    # A later collection may discover an older article; the default must still pick it up.
    store.save_article(article(title="Late discovery", url="https://example.com/discovered", published="2026-11-07"))
    assert run_translation(cfg, provider)["translated"] == 1
    assert calls[-1] == "Late discovery" and len(calls) == 5
    assert run_translation(cfg, provider)["attempted"] == 0
    store.close()


def test_collector_can_commit_during_model_request(setup_translation):
    cfg = setup_translation
    store = Store(cfg["data_dir"])
    store.save_article(article(title="Second pending title", url="https://example.com/second"))
    store.close()
    def concurrent_provider(title, *args):
        # A separate collector connection commits while the model is responding.
        collector_store = Store(cfg["data_dir"])
        collector_store.save_article(article(title="Newly collected headline", url="https://example.com/new"))
        collector_store.close()
        return fake_provider(title, *args)
    result = run_translation(cfg, concurrent_provider)
    assert result["translated"] == 2 and result["failed"] == 0
    assert run_translation(cfg, fake_provider)["translated"] == 1


def test_abandoned_worker_resumes_without_erasing_budget(setup_translation):
    cfg = setup_translation
    class InterruptedWorker(BaseException):
        pass
    def interrupted(*args):
        raise InterruptedWorker()
    with pytest.raises(InterruptedWorker):
        run_translation(cfg, interrupted)
    store = Store(cfg["data_dir"])
    row = store.db.execute("SELECT status,attempts FROM title_translations").fetchone()
    assert row["status"] == "pending" and row["attempts"] == 1
    assert run_translation(cfg, fake_provider)["translated"] == 1
    assert store.db.execute("SELECT attempts FROM title_translations").fetchone()[0] == 2
    used = store.db.execute("SELECT value FROM state WHERE key LIKE 'translation:requests:%'").fetchone()[0]
    assert int(used) == 2
    store.close()


def test_cached_security_mistranslation_is_requeued_through_normal_worker(setup_translation):
    cfg = setup_translation
    original = "Iris Plaza Breach May Have Exposed Data of 7.7 Million Customers"
    store = Store(cfg["data_dir"])
    store.save_article(article(title=original))
    assert run_translation(cfg, fake_provider)["translated"] == 1
    store.db.execute("UPDATE title_translations SET title_zh=?", ("Iris Plaza 违规可能泄露770万客户数据",))
    store.db.commit()
    corrected = "Iris Plaza 数据泄露事件可能导致770万客户数据暴露"
    assert run_translation(cfg, lambda *args: (corrected, {}))["translated"] == 1
    assert title_fields(store.db, original)["display_title"] == corrected
    store.close()
