import json
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("{} must be an integer in {}..{}".format(name, low, high))
    return value


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(name + " must be boolean")
    return value


def load_service(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text(encoding="utf-8-sig"))
    if "ANQUANDAY_DATA_DIR" in os.environ:
        cfg["data_dir"] = os.environ["ANQUANDAY_DATA_DIR"]
    if "ANQUANDAY_API_HOST" in os.environ:
        cfg["api"]["host"] = os.environ["ANQUANDAY_API_HOST"]
    if "ANQUANDAY_API_PORT" in os.environ:
        cfg["api"]["port"] = int(os.environ["ANQUANDAY_API_PORT"])
    for key in ("data_dir", "sources_file"):
        cfg[key] = str((path.parent / cfg[key]).resolve())
    api = cfg["api"]
    integer(api["port"], "api.port", 1, 65535)
    if not isinstance(api["host"], str) or not api["host"]:
        raise ValueError("api.host is required")
    sources = json.loads(Path(cfg["sources_file"]).read_text(encoding="utf-8-sig"))
    ids = [s["id"] for s in sources]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", x) for x in ids):
        raise ValueError("invalid_or_duplicate_source_id")
    sch = cfg["scheduler"]
    boolean(sch["enabled"], "scheduler.enabled")
    boolean(sch["run_on_startup"], "scheduler.run_on_startup")
    integer(sch["interval_seconds"], "interval_seconds", 1, 31536000)
    integer(sch["source_timeout_seconds"], "source_timeout_seconds", 1, 86400)
    integer(sch["max_parallel_sources"], "max_parallel_sources", 1, 32)
    overrides = sch.get("sources", {})
    if not isinstance(overrides, dict) or set(overrides) - set(ids):
        raise ValueError("unknown_scheduler_source")
    for settings in overrides.values():
        if set(settings) - {"enabled", "interval_seconds", "source_timeout_seconds"}:
            raise ValueError("unknown_source_schedule_setting")
        if "enabled" in settings:
            boolean(settings["enabled"], "source.enabled")
        for key in ("interval_seconds", "source_timeout_seconds"):
            if key in settings:
                integer(settings[key], key, 1, 31536000 if key == "interval_seconds" else 86400)
    translation = {
        "enabled": False, "env_file": "../.env", "model": "gpt-4.1-mini",
        "interval_seconds": 60, "request_timeout_seconds": 30,
        "max_items_per_run": 20, "daily_max_requests": 200,
        "retry_seconds": 3600, "max_attempts": 3, "include_kev": True,
        "scope": "all", "timezone": "Europe/Brussels",
    }
    supplied = cfg.get("translation", {})
    if not isinstance(supplied, dict) or set(supplied) - set(translation):
        raise ValueError("unknown_translation_setting")
    translation.update(supplied)
    if translation["scope"] not in ("all", "latest_day"):
        raise ValueError("invalid_translation_scope")
    if not isinstance(translation["timezone"], str):
        raise ValueError("invalid_translation_timezone")
    ZoneInfo(translation["timezone"])
    for key in ("enabled", "include_kev"):
        boolean(translation[key], "translation." + key)
    for key, low, high in (("interval_seconds", 10, 86400), ("request_timeout_seconds", 1, 120),
            ("max_items_per_run", 1, 100), ("daily_max_requests", 1, 10000),
            ("retry_seconds", 60, 86400), ("max_attempts", 1, 10)):
        integer(translation[key], "translation." + key, low, high)
    if not isinstance(translation["model"], str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,100}", translation["model"]):
        raise ValueError("invalid_translation_model")
    if not isinstance(translation["env_file"], str) or not translation["env_file"]:
        raise ValueError("invalid_translation_env_file")
    translation["env_file"] = str((path.parent / translation["env_file"]).resolve())
    cfg["translation"] = translation
    apt = {"enabled": False, "run_on_startup": False, "interval_seconds": 86400,
        "retry_seconds": 3600, "timeout_seconds": 1800, "translate": True,
        "max_batches_per_run": 6, "daily_max_requests": 24}
    supplied = cfg.get("apt_sync", {})
    if not isinstance(supplied, dict) or set(supplied) - set(apt):
        raise ValueError("unknown_apt_sync_setting")
    apt.update(supplied)
    for key in ("enabled", "run_on_startup", "translate"):
        boolean(apt[key], "apt_sync." + key)
    for key, low, high in (("interval_seconds", 3600, 31536000), ("retry_seconds", 60, 86400),
            ("timeout_seconds", 30, 86400), ("max_batches_per_run", 1, 30), ("daily_max_requests", 1, 100)):
        integer(apt[key], "apt_sync." + key, low, high)
    cfg["apt_sync"] = apt
    cfg["config_path"] = str(path)
    return cfg, sources
