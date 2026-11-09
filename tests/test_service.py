import copy
import json
import sys
import subprocess
import threading
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen, Request
import pytest
from engine.storage import Store
from engine.collector import collect_source
from engine.locking import source_lock, LockBusy
from service.config import ROOT, load_service
from service.repository import Repository, utc_boundary, get_zone
from service.server import create_server
from service.scheduler import Scheduler
from web.server import create_server as create_web
from test_engine import article, SOURCE


def request(base, path, method="GET"):
    try:
        with urlopen(Request(base + path, method=method), timeout=3) as response:
            return response.status, json.loads(response.read())
    except HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def prepared(tmp_path):
    store = Store(tmp_path)
    for i, published in enumerate(("2026-11-08T23:30:00Z", "2026-11-09T23:30:00Z", "2026-11-09", None)):
        store.save_article(article(url="https://example.com/" + str(i), published=published))
    store.close()
    return tmp_path


@pytest.fixture
def api(prepared):
    server = create_server("127.0.0.1", 0, Repository(prepared, [SOURCE]))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(server.server_port)
    server.shutdown()
    server.server_close()
    thread.join()


def test_api_timezone_date_only_and_undated(api):
    status, body = request(api, "/api/v1/articles?date=2026-11-09&tz=Europe%2FBrussels")
    assert status == 200 and body["total"] == 2
    assert {x["url"] for x in body["items"]} == {"https://example.com/0", "https://example.com/2"}
    assert request(api, "/api/v1/articles?date=2026-11-09&tz=UTC")[1]["items"][0]["url"] == "https://example.com/1"
    assert request(api, "/api/v1/articles?date=undated")[1]["total"] == 1
    assert all("text" not in x and "evidence" not in x for x in body["items"])
    assert body["items"][0]["evidence_count"] > 0


def test_api_calendar_counts_and_source_filter(api):
    status, body = request(api, "/api/v1/calendar?month=2026-11&source=test")
    assert status == 200
    assert body["days"] == {"2026-11-09": 2, "2026-11-10": 1}
    assert body["undated"] == 1
    overview = request(api, "/api/v1/overview?date=2026-11-09")[1]
    assert overview["news"] == 2 and overview["totals"]["articles"] == 4
    assert overview["source_counts"]["test"] == 2


def test_dst_day_has_correct_utc_boundaries():
    from datetime import date, datetime
    z = get_zone("Europe/Brussels")
    for start, end, hours in ((date(2026, 3, 29), date(2026, 3, 30), 23), (date(2026, 10, 25), date(2026, 10, 26), 25)):
        a = datetime.fromisoformat(utc_boundary(start, z).replace("Z", "+00:00"))
        b = datetime.fromisoformat(utc_boundary(end, z).replace("Z", "+00:00"))
        assert (b - a).total_seconds() == hours * 3600


@pytest.mark.parametrize("path", ["/api/v1/articles?date=2026-02-30", "/api/v1/calendar?month=2026-13",
    "/api/v1/articles?tz=not_a_zone", "/api/v1/articles?source=missing", "/api/v1/articles?page_size=10000",
    "/api/v1/articles?date=2026-11-09&date=2026-11-10", "/api/v1/articles?sort=sql", "/api/v1/articles?unknown=1"])
def test_invalid_queries_rejected(api, path):
    assert request(api, path)[0] == 400


def test_api_pagination_search_and_read_only(api):
    body = request(api, "/api/v1/articles?date=2026-11-09&page_size=1&page=2")[1]
    assert body["total"] == 2 and body["page"] == 2 and len(body["items"]) == 1
    assert request(api, "/api/v1/articles?date=2026-11-09&q=absent")[1]["total"] == 0
    assert request(api, "/api/v1/articles", "POST")[0] == 405
    assert request(api, "/data/anquanday.sqlite3")[0] == 404


def test_api_observes_new_commits_without_restarting(api, prepared):
    assert request(api, "/api/v1/overview?date=2026-11-09")[1]["news"] == 2
    store = Store(prepared)
    a = article(url="https://example.com/new")
    store.save_article(a)
    assert store.save_article(a)[1] == "unchanged"
    a["text"] += " Confirmed update."
    assert store.save_article(a)[1] == "updated"
    store.close()
    body = request(api, "/api/v1/overview?date=2026-11-09")[1]
    assert body["news"] == 3 and body["totals"]["articles"] == 5 and body["totals"]["revisions"] == 6


def test_source_lock_covers_manual_and_scheduled_collection(tmp_path):
    store = Store(tmp_path)
    with source_lock(tmp_path, "test"):
        result = collect_source(SOURCE, store, client_factory=lambda hosts: pytest.fail("must not start"))
        assert result["status"] == "skipped_busy"
        with pytest.raises(LockBusy):
            with source_lock(tmp_path, "test"):
                pass
    with source_lock(tmp_path, "test"):
        pass
    store.close()


def test_source_lock_is_shared_across_processes(tmp_path):
    sources_file = tmp_path / "sources.json"
    sources_file.write_text(json.dumps([SOURCE]), encoding="utf-8")
    store = Store(tmp_path)
    store.close()
    with source_lock(tmp_path, "test"):
        result = subprocess.run([sys.executable, "-m", "service.worker", "--sources-file", str(sources_file),
            "--source-id", "test", "--data-dir", str(tmp_path)], cwd=str(ROOT), capture_output=True,
            text=True, timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        assert result.returncode == 0
        assert json.loads(result.stdout)["status"] == "skipped_busy"


def test_client_constructor_failure_is_recorded(tmp_path):
    def fail(hosts):
        raise RuntimeError("constructor_failure")
    store = Store(tmp_path)
    result = collect_source(SOURCE, store, client_factory=fail)
    assert result["status"] == "failed" and result["error"] == "constructor_failure"
    assert store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    store.close()


def wait_until(predicate, timeout=10):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.05)
    pytest.fail("condition not reached before deadline")


def scheduler_config(tmp_path):
    return {"data_dir": str(tmp_path), "sources_file": str(ROOT / "config" / "sources.json"),
        "scheduler": {"enabled": True, "run_on_startup": True, "interval_seconds": 1,
        "source_timeout_seconds": 1, "max_parallel_sources": 3, "sources": {"slow": {"interval_seconds": 100}}}}


def test_scheduler_timeout_failure_isolation_repetition_and_restart(tmp_path):
    config = scheduler_config(tmp_path)
    sources = [{"id": x, "name": x} for x in ("slow", "fast", "failed")]
    def command(sid):
        if sid == "slow":
            return [sys.executable, "-c", "import time; time.sleep(30)"]
        if sid == "failed":
            return [sys.executable, "-c", "raise RuntimeError('controlled failure')"]
        code = "from pathlib import Path; p=Path({!r}); f=p.open('a'); f.write('run\\n'); f.close()".format(str(tmp_path / "fast.txt"))
        return [sys.executable, "-c", code]
    sch = Scheduler(config, sources, command)
    thread = threading.Thread(target=sch.run, daemon=True)
    thread.start()
    assert sch.ready.wait(5)
    try:
        wait_until(lambda: (tmp_path / "fast.txt").exists())
        assert sch.active["slow"][0].poll() is None
        wait_until(lambda: (tmp_path / "fast.txt").exists() and (tmp_path / "fast.txt").read_text().count("run") >= 2)
        store = Store(tmp_path)
        rows = [json.loads(r[0]) for r in store.db.execute("SELECT result FROM runs")]
        assert any(r.get("error") == "worker_timeout" and r["source_id"] == "slow" for r in rows)
        assert store.state("schedule:failed") is not None
        store.close()
    finally:
        sch.stop()
        thread.join(8)
    assert not thread.is_alive() and not sch.active
    # A restart with startup collection disabled respects persisted due times.
    config["scheduler"]["run_on_startup"] = False
    config["scheduler"]["sources"] = {"fast": {"enabled": False}, "failed": {"enabled": False}, "slow": {"interval_seconds": 100}}
    resumed = Scheduler(config, sources, command)
    rt = threading.Thread(target=resumed.run, daemon=True)
    rt.start()
    assert resumed.ready.wait(5)
    try:
        time.sleep(.3)
        assert resumed.due["slow"] > time.time() and not resumed.active
    finally:
        resumed.stop()
        rt.join(5)


def test_config_per_source_override_and_rejection(tmp_path):
    path = tmp_path / "service.json"
    cfg = scheduler_config(tmp_path)
    cfg["api"] = {"host": "127.0.0.1", "port": 8765}
    cfg["scheduler"]["sources"] = {"anwangxia": {"interval_seconds": 90, "enabled": False}}
    path.write_text(json.dumps(cfg))
    loaded, sources = load_service(path)
    assert "anwangxia" not in Scheduler(loaded, sources).sources
    cfg["scheduler"]["interval_seconds"] = 0
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError):
        load_service(path)


def test_web_proxies_only_api_and_serves_no_data_files(api):
    cfg = {"host": "127.0.0.1", "port": 0, "api_base_url": api, "api_timeout_seconds": 2,
           "display_timezone": "Europe/Brussels", "refresh_seconds": 60}
    server = create_web(cfg, Path(__file__).resolve().parents[1] / "website" / "frontend")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    base = "http://127.0.0.1:" + str(server.server_port)
    try:
        assert request(base, "/api/v1/overview?date=2026-11-09")[1]["news"] == 2
        assert request(base, "/config.json")[1]["refresh_seconds"] == 60
        assert request(base, "/data/anquanday.sqlite3")[0] == 404
        assert request(base, "/../config/service.json")[0] == 404
        assert request(base, "/api/v1/articles", "POST")[0] == 405
        with urlopen(base) as response:
            html = response.read().decode()
            assert '/app.js' in html and 'id="snapshot"' not in html
        cfg["api_base_url"] = "http://127.0.0.1:1"
        assert request(base, "/api/v1/health")[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        t.join()
