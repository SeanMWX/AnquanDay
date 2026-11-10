"""Deterministic upstream releases, resumable translations and persistent scheduling."""
import copy
import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from engine import apt_catalog as catalog
from engine.locking import FileLock
from engine.storage import Store
from service.apt_sync import make_scheduler, run_sync
from service.config import ROOT, load_service
from service.repository import Repository
from test_engine import article, SOURCE
from test_service import wait_until


def group(number, name=None, text="Original public description.", translated=True):
    gid = "G{:04d}".format(number)
    value = {"id": gid, "name": name or "TestActor" + str(number), "description_en": text,
        "summary_zh": "已有中文简介。" if translated else "", "associated_names": [],
        "source_url": "https://attack.mitre.org/groups/" + gid, "modified": "2026-11-01T00:00:00Z",
        "source_version": "1.0", "domains": ["enterprise-attack"], "references": []}
    if translated:
        value["translation"] = {"input_hash": catalog.digest(value), "model": "test-model"}
    return value


def index(version="1.0"):
    return {"collections": [{"name": name, "versions": [{"version": version,
        "modified": "2026-11-01T00:00:00Z", "url": catalog.BASE + domain + "/" + domain + "-" + version + ".json"}]}
        for name, domain in catalog.DOMAINS.items()]}


def data(groups, version="1.0"):
    return {"schema_version": 1, "provider": "MITRE ATT&CK", "groups": groups,
        "retrieved_at": "2026-11-01T00:00:00Z", "sources": catalog.select_sources(index(version)), "scope_zh": "测试目录"}


class Response:
    def __init__(self, value=None, text=None):
        self.value, self.text = value, text

    def json(self):
        return copy.deepcopy(self.value)


class Upstream:
    def __init__(self, groups, version="1.1"):
        self.groups, self.version, self.calls = groups, version, []

    def __call__(self, url):
        self.calls.append(url)
        if url == catalog.BASE + "index.json":
            return Response(index(self.version))
        if url == catalog.BASE + "LICENSE.txt":
            return Response(text="MITRE test licence. " * 20)
        if "/enterprise-attack/" in url:
            objects = [{"type": "intrusion-set", "name": g["name"], "description": g["description_en"],
                "modified": g["modified"], "aliases": [g["name"]] + g["associated_names"],
                "x_mitre_domains": g["domains"], "revoked": g.get("revoked", False),
                "external_references": [{"source_name": "mitre-attack", "external_id": g["id"], "url": g["source_url"]}]}
                for g in self.groups]
        else:
            objects = [{"type": "identity", "name": "test identity"}]
        return Response({"type": "bundle", "objects": objects})


@pytest.fixture
def sync_config(tmp_path, monkeypatch):
    cfg, _ = load_service(ROOT / "config" / "service.json")
    cfg["data_dir"] = str(tmp_path / "data")
    cfg["translation"]["env_file"] = str(tmp_path / "absent.env")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    seed = tmp_path / "seed" / "apt_catalog.json"
    catalog.save(seed, data([group(9991), group(9992)]))
    (seed.parent / "ATTACK-LICENSE.txt").write_text("MITRE test licence. " * 20)
    monkeypatch.setattr(catalog, "SEED_CATALOG", seed)
    monkeypatch.setattr(catalog, "SEED_DIR", seed.parent)
    return cfg


def read_runtime(cfg):
    return json.loads((Path(cfg["data_dir"]) / "apt" / "catalog.json").read_text(encoding="utf-8"))


def provider(batch, key, model):
    assert key == "test-only-secret"
    return {g["id"]: "新整理的中文简介：" + g["name"] for g in batch}, {"input_tokens": 20, "output_tokens": 10}


def test_unchanged_release_fetches_only_index_and_uses_no_key(sync_config, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "")
    upstream = Upstream([], "1.0")
    result = run_sync(sync_config, fetcher=upstream, provider=lambda *a: pytest.fail("no GPT needed"))
    assert result["status"] == "ok" and result["attempted_batches"] == 0
    assert result["groups"] == 2 and result["pending"] == 0
    assert upstream.calls == [catalog.BASE + "index.json"]
    stored = read_runtime(sync_config)
    assert stored["last_checked_at"] and stored["retrieved_at"] == "2026-11-01T00:00:00Z"
    assert (Path(sync_config["data_dir"]) / "apt" / "ATTACK-LICENSE.txt").exists()


def test_new_changed_removed_and_renamed_groups_publish_and_translate_only_changes(sync_config):
    old = json.loads(catalog.SEED_CATALOG.read_text(encoding="utf-8"))
    old["groups"].append(group(9990))
    catalog.save(catalog.SEED_CATALOG, old)
    revoked = dict(group(9990), revoked=True)
    release = [group(9991), group(9992, name="RenamedActor"), group(9993, text="New source.", translated=False), revoked]
    calls = []
    def tracked(batch, key, model):
        calls.append([g["id"] for g in batch])
        return provider(batch, key, model)
    upstream = Upstream(release)
    result = run_sync(sync_config, fetcher=upstream, provider=tracked)
    assert (result["added"], result["changed"], result["removed"]) == (1, 1, 1)
    assert result["translated"] == 2 and result["pending"] == 0
    assert set(calls[0]) == {"G9992", "G9993"}
    stored = {g["id"]: g for g in read_runtime(sync_config)["groups"]}
    assert stored["G9991"]["summary_zh"] == "已有中文简介。"
    assert stored["G9992"]["name"] == "RenamedActor"
    result = run_sync(sync_config, fetcher=upstream, provider=tracked)
    assert result["attempted_batches"] == 0 and len(calls) == 1
    # A subsequent description edit requires exactly one new translation.
    release[0]["description_en"] = "Revised published evidence."
    changed = run_sync(sync_config, fetcher=Upstream(release, "1.2"), provider=tracked)
    assert changed["translated"] == 1 and calls[-1] == ["G9991"]


def test_partial_batches_resume_and_api_observes_updates_without_restart(sync_config):
    sync_config["apt_sync"]["max_batches_per_run"] = 1
    upstream = Upstream([group(n, translated=False) for n in range(9900, 9912)])
    repo = Repository(sync_config["data_dir"], [SOURCE])
    first = run_sync(sync_config, fetcher=upstream, provider=provider)
    assert first["status"] == "partial" and first["translated"] == 10 and first["pending"] == 2
    listing = repo.dispatch("/api/v1/apt/groups", {"date": "2026-11-09", "page_size": "100"})
    assert listing["total"] == 12 and sum(bool(g["summary_zh"]) for g in listing["items"]) == 10
    second = run_sync(sync_config, fetcher=upstream, provider=provider)
    assert second["status"] == "ok" and second["translated"] == 2
    listing = repo.dispatch("/api/v1/apt/groups", {"date": "2026-11-09", "page_size": "100"})
    assert all(g["summary_zh"] for g in listing["items"]) and listing["catalog"]["last_checked_at"]
    assert sum("enterprise-attack/" in u for u in upstream.calls) == 1


def test_alias_order_changes_do_not_count_as_updated_profiles(sync_config):
    original = group(9991)
    original["associated_names"] = ["Z Alias", "A Alias"]
    catalog.save(catalog.SEED_CATALOG, data([original]))
    result = run_sync(sync_config, fetcher=Upstream([original]), provider=lambda *a: pytest.fail("no translation needed"))
    assert result["status"] == "ok" and result["changed"] == 0 and result["translated"] == 0


def test_download_failure_retains_last_catalog_and_does_not_disclose_error(sync_config):
    run_sync(sync_config, fetcher=Upstream([], "1.0"))
    before = read_runtime(sync_config)
    def failing(url):
        raise RuntimeError("do not log test-only-secret")
    result = run_sync(sync_config, fetcher=failing)
    assert result["status"] == "failed" and result["error"] == "catalog_update_failed"
    assert read_runtime(sync_config) == before
    assert "test-only-secret" not in json.dumps(result)


def test_incomplete_or_empty_release_cannot_replace_catalog(sync_config):
    run_sync(sync_config, fetcher=Upstream([], "1.0"))
    before = read_runtime(sync_config)
    incomplete = index("1.1")
    incomplete["collections"].pop()
    result = run_sync(sync_config, fetcher=lambda url: Response(incomplete))
    assert result["error"] == "incomplete_catalog_index"
    result = run_sync(sync_config, fetcher=Upstream([]))
    assert result["status"] == "failed" and read_runtime(sync_config) == before


def test_index_ignores_historical_betas_and_chooses_latest_stable():
    release = index("20.1")
    mobile = next(c for c in release["collections"] if c["name"] == "Mobile ATT&CK")
    mobile["versions"].insert(0, {"version": "99.0-beta", "url": "https://example.com/beta"})
    mobile["versions"].append({"version": "11.2-beta", "url": "https://example.com/old-beta"})
    assert {s["version"] for s in catalog.select_sources(release)} == {"20.1"}
    mobile["versions"] = [{"version": "99.0-beta"}]
    with pytest.raises(ValueError, match="invalid_catalog_version"):
        catalog.select_sources(release)


def test_translation_failure_and_missing_key_preserve_new_directory(sync_config, monkeypatch):
    upstream = Upstream([group(9991), group(9992, text="Updated description.")])
    monkeypatch.setenv("OPENAI_API_KEY", "")
    result = run_sync(sync_config, fetcher=upstream)
    assert result["status"] == "key_missing" and result["pending"] == 1
    records = {g["id"]: g for g in read_runtime(sync_config)["groups"]}
    assert records["G9991"]["summary_zh"] and not records["G9992"]["summary_zh"]
    assert records["G9992"]["description_en"] == "Updated description."
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    result = run_sync(sync_config, fetcher=upstream, provider=provider)
    assert result["status"] == "ok" and result["translated"] == 1


def test_daily_budget_charged_before_failure_and_survives_new_runs(sync_config):
    sync_config["apt_sync"]["daily_max_requests"] = 1
    upstream = Upstream([group(9991, text="Changed.")])
    def fail(*args):
        raise RuntimeError("test-only-secret")
    result = run_sync(sync_config, fetcher=upstream, provider=fail)
    assert result["attempted_batches"] == 1 and result["error"] == "catalog_translation_failed"
    result = run_sync(copy.deepcopy(sync_config), fetcher=upstream, provider=lambda *a: pytest.fail("budget exceeded"))
    assert result["status"] == "daily_limit" and result["attempted_batches"] == 0
    store = Store(sync_config["data_dir"])
    try:
        day = datetime.now(timezone.utc).date().isoformat()
        assert store.state("apt_sync:requests:" + day) == 1
        assert "test-only-secret" not in json.dumps(store.state("apt_sync:last_run"))
    finally:
        store.close()


def test_manual_and_scheduled_updates_share_lock(sync_config):
    with FileLock(Path(sync_config["data_dir"]) / "locks" / "apt-catalog.lock"):
        result = run_sync(sync_config, fetcher=lambda u: pytest.fail("must not download"))
    assert result["status"] == "skipped_busy"


def test_atomic_write_failure_retains_old_complete_file(tmp_path, monkeypatch):
    path = tmp_path / "catalog.json"
    catalog.save(path, data([group(9991)]))
    before = path.read_bytes()
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(catalog.os, "replace", fail)
    with pytest.raises(OSError):
        catalog.save(path, data([group(9992)]))
    assert path.read_bytes() == before and not list(tmp_path.glob("*.tmp"))


def test_slow_sync_does_not_hold_news_database_lock(sync_config):
    entered, release = threading.Event(), threading.Event()
    def slow(url):
        entered.set()
        assert release.wait(5)
        return Response(index())
    thread = threading.Thread(target=run_sync, args=(sync_config,), kwargs={"fetcher": slow})
    thread.start()
    try:
        assert entered.wait(3)
        store = Store(sync_config["data_dir"])
        store.save_article(article())
        store.close()
        repo = Repository(sync_config["data_dir"], [SOURCE])
        assert repo.dispatch("/api/v1/articles", {"date": "2026-11-09"})["total"] == 1
        assert repo.dispatch("/api/v1/apt/groups", {"date": "2026-11-09"})["total"] == 2
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()


def test_first_run_immediate_retry_then_restart_resumes_schedule(sync_config, tmp_path):
    cfg = copy.deepcopy(sync_config)
    cfg["apt_sync"].update(interval_seconds=100, retry_seconds=1, run_on_startup=False)
    marker = tmp_path / "attempts.txt"
    scheduler = make_scheduler(cfg)
    assert scheduler.command_factory("_apt_catalog")[-3:] == ["service.apt_sync", "--config", cfg["config_path"]]
    code = "from pathlib import Path; p=Path({!r}); old=p.read_text() if p.exists() else ''; p.write_text(old+'x'); raise SystemExit(0 if old else 1)".format(str(marker))
    scheduler.command_factory = lambda sid: [sys.executable, "-c", code]
    thread = threading.Thread(target=scheduler.run, daemon=True)
    thread.start()
    try:
        assert scheduler.ready.wait(3)
        wait_until(lambda: marker.exists() and marker.read_text() == "xx" and not scheduler.active, timeout=8)
        assert scheduler.due["_apt_catalog"] > time.time() + 90
    finally:
        scheduler.stop()
        thread.join(5)
    resumed = make_scheduler(cfg)
    resumed.command_factory = lambda sid: pytest.fail("must honor persisted next_due")
    thread = threading.Thread(target=resumed.run, daemon=True)
    thread.start()
    try:
        assert resumed.ready.wait(3)
        time.sleep(.3)
        assert marker.read_text() == "xx" and not resumed.active
    finally:
        resumed.stop()
        thread.join(5)


@pytest.mark.parametrize("field,value", [("enabled", "yes"), ("interval_seconds", 1), ("retry_seconds", 0),
    ("timeout_seconds", 0), ("max_batches_per_run", 31), ("daily_max_requests", 0), ("translate", 1), ("unknown", 1)])
def test_invalid_sync_config_rejected(tmp_path, field, value):
    cfg = json.loads((ROOT / "config" / "service.json").read_text(encoding="utf-8"))
    cfg["sources_file"] = str(ROOT / "config" / "sources.json")
    cfg["apt_sync"][field] = value
    path = tmp_path / "service.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ValueError):
        load_service(path)
