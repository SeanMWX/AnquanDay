"""Independent MITRE worker; runtime catalogue and budgets live under data/."""
import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from engine.apt_catalog import (POLICY, bootstrap, clean_intro, digest, download, refresh,
                                save, timestamp, translate_batch, valid_translation)
from engine.locking import FileLock, LockBusy
from engine.storage import Store
from engine.title_translation import read_api_key
from .config import ROOT, load_service
from .scheduler import Scheduler


def make_scheduler(config):
    settings = config["apt_sync"]
    job_config = dict(config, scheduler={"enabled": True, "sources": {}, "max_parallel_sources": 1,
        "run_on_startup": settings["run_on_startup"], "interval_seconds": settings["interval_seconds"],
        "failure_interval_seconds": settings["retry_seconds"], "source_timeout_seconds": settings["timeout_seconds"]})
    return Scheduler(job_config, [{"id": "_apt_catalog", "name": "MITRE ATT&CK 目录"}],
        command_factory=lambda sid: [sys.executable, "-X", "utf8", "-m", "service.apt_sync", "--config", config["config_path"]],
        initial_run_immediately=True)


def error_code(error, phase):
    value = str(error) if isinstance(error, ValueError) else ""
    allowed = {"incomplete_catalog_index", "invalid_catalog_index", "invalid_catalog_version",
        "invalid_catalog_download_url", "invalid_catalog_bundle", "invalid_catalog_license",
        "invalid_catalog_groups", "invalid_catalog_group_id", "invalid_catalog_group_field",
        "invalid_catalog_group_source", "invalid_catalog_associated_names", "invalid_catalog_sources",
        "profile_translation_network_error", "incomplete_profile_translation",
        "profile_translation_id_mismatch", "invalid_profile_translation"}
    if value in allowed or re.fullmatch(r"(?:catalog_download|profile_translation)_http_[1-5]\d{2}", value):
        return value
    return phase + "_failed"


def group_changed(before, after):
    return (any(before.get(k) != after.get(k) for k in ("name", "description_en", "modified")) or
            set(before.get("associated_names", [])) != set(after.get("associated_names", [])))


def _sync(config, store, result, fetcher, provider, refresh_catalog, translate):
    path = Path(config["data_dir"]) / "apt" / "catalog.json"
    data = bootstrap(path)
    before = {g["id"]: g for g in data["groups"]}
    if refresh_catalog:
        try:
            data = refresh(path, fetcher=fetcher)
        except Exception as error:
            result.update(status="failed", error=error_code(error, "catalog_update"), groups=len(before))
            return
    after = {g["id"]: g for g in data["groups"]}
    result.update(groups=len(after), added=len(set(after) - set(before)), removed=len(set(before) - set(after)),
        changed=sum(group_changed(before[gid], group) for gid, group in after.items() if gid in before),
        checked_at=data.get("last_checked_at"))
    pending = [g for g in data["groups"] if not valid_translation(g)]
    result["pending"] = len(pending)
    if not translate or not pending:
        return
    settings, translation = config["apt_sync"], config["translation"]
    key = read_api_key(translation["env_file"])
    if not key:
        result["status"] = "key_missing"
        return
    for offset in range(0, min(len(pending), settings["max_batches_per_run"] * 10), 10):
        # Reserve before the network call. Interrupted/failed calls still consume
        # the persisted daily allowance and cannot be reset by a container restart.
        day = datetime.now(timezone.utc).date().isoformat()
        budget_key = "apt_sync:requests:" + day
        used = store.state(budget_key, 0)
        if used >= settings["daily_max_requests"]:
            result["status"] = "daily_limit"
            break
        store.set_state(budget_key, used + 1)
        result["attempted_batches"] += 1
        batch = pending[offset:offset + 10]
        try:
            output, usage = provider(batch, key, translation["model"])
            if set(output) != {g["id"] for g in batch}:
                raise ValueError("profile_translation_id_mismatch")
            for value in output.values():
                if not isinstance(value, str) or not re.search(r"[\u4e00-\u9fff]", clean_intro(value)) or len(value) > 600 or re.search(r"[<>\x00-\x1f]", value):
                    raise ValueError("invalid_profile_translation")
            for group in batch:
                group["summary_zh"] = clean_intro(output[group["id"]])
                group["translation"] = {"model": translation["model"], "input_hash": digest(group),
                    "policy_version": POLICY, "translated_at": timestamp()}
            data["translation_runs"] = (data.get("translation_runs", []) + [{"count": len(batch),
                "input_tokens": usage.get("input_tokens"), "output_tokens": usage.get("output_tokens"), "at": timestamp()}])[-100:]
            save(path, data)
        except Exception as error:
            result.update(status="failed", error=error_code(error, "catalog_translation"))
            break
        result["translated"] += len(batch)
        result["pending"] -= len(batch)
    if result["status"] == "ok" and result["pending"]:
        result["status"] = "partial"


def run_sync(config, fetcher=download, provider=translate_batch, refresh_catalog=True, translate=None):
    result = {"source_id": "_apt_catalog", "source_name": "MITRE ATT&CK 目录", "started_at": timestamp(),
        "status": "ok", "translated": 0, "attempted_batches": 0}
    if translate is None:
        translate = config["apt_sync"]["translate"]
    try:
        with FileLock(Path(config["data_dir"]) / "locks" / "apt-catalog.lock"):
            store = Store(config["data_dir"])
            try:
                try:
                    _sync(config, store, result, fetcher, provider, refresh_catalog, translate)
                except Exception as error:
                    result.update(status="failed", error=error_code(error, "catalog_sync"))
                result["finished_at"] = timestamp()
                store.set_state("apt_sync:last_run", result)
                store.log_run(result)
            finally:
                store.close()
    except LockBusy:
        result.update(status="skipped_busy", finished_at=timestamp())
    return result


def main():
    parser = argparse.ArgumentParser(description="同步 MITRE ATT&CK 并增量翻译组织档案")
    parser.add_argument("--config", default=str(ROOT / "config" / "service.json"))
    args = parser.parse_args()
    config, _ = load_service(args.config)
    result = run_sync(config)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result["status"] not in ("ok", "skipped_busy"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
