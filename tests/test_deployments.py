"""Copy each deployment alone and exercise the actual server entrypoints."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import pytest

from service.config import load_service
from web.server import load_config
from test_engine import article

ROOT = Path(__file__).resolve().parents[1]


def test_container_environment_overrides_keep_native_configs_unchanged(monkeypatch, tmp_path):
    api_path = ROOT / "collector" / "config" / "service.json"
    web_path = ROOT / "website" / "config" / "web.json"
    before = (api_path.read_bytes(), web_path.read_bytes())
    monkeypatch.setenv("ANQUANDAY_API_HOST", "0.0.0.0")
    monkeypatch.setenv("ANQUANDAY_API_PORT", "18765")
    monkeypatch.setenv("ANQUANDAY_DATA_DIR", str(tmp_path / "mounted-data"))
    monkeypatch.setenv("ANQUANDAY_WEB_HOST", "0.0.0.0")
    monkeypatch.setenv("ANQUANDAY_WEB_PORT", "18766")
    monkeypatch.setenv("ANQUANDAY_API_BASE_URL", "http://192.0.2.20:18765/")
    cfg, sources = load_service(api_path)
    web = load_config(web_path)
    assert cfg["api"] == {"host": "0.0.0.0", "port": 18765}
    assert cfg["data_dir"] == str((tmp_path / "mounted-data").resolve())
    assert sources and Path(cfg["sources_file"]).parent == api_path.parent
    assert Path(cfg["translation"]["env_file"]) == api_path.parent.parent / ".env"
    assert web["host"] == "0.0.0.0" and web["port"] == 18766
    assert web["api_base_url"] == "http://192.0.2.20:18765"
    assert before == (api_path.read_bytes(), web_path.read_bytes())


@pytest.mark.parametrize("name,value,side", [
    ("ANQUANDAY_API_PORT", "65536", "collector"),
    ("ANQUANDAY_API_PORT", "invalid", "collector"),
    ("ANQUANDAY_API_HOST", "", "collector"),
    ("ANQUANDAY_WEB_PORT", "0", "website"),
    ("ANQUANDAY_WEB_HOST", "", "website"),
    ("ANQUANDAY_API_BASE_URL", "", "website"),
    ("ANQUANDAY_API_BASE_URL", "http://example.test/unexpected-path", "website"),
])
def test_invalid_container_configuration_is_rejected(monkeypatch, name, value, side):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        if side == "collector":
            load_service(ROOT / side / "config" / "service.json")
        else:
            load_config(ROOT / side / "config" / "web.json")


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def get(base, path):
    try:
        with urlopen(base + path, timeout=2) as response:
            return response.status, response.read()
    except HTTPError as error:
        try:
            return error.code, error.read()
        finally:
            error.close()


def ready(process, base):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        assert process.poll() is None, "server exited during startup"
        try:
            if get(base, "/api/v1/health")[0] == 200:
                return
        except (OSError, URLError):
            pass
        time.sleep(0.1)
    raise AssertionError("server did not become ready")


def stop(process):
    if process and process.poll() is None:
        try:
            process.communicate(b"stop\n", timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            raise


def test_two_standalone_folders_communicate_only_through_api(tmp_path):
    # Neither server has access to the other's packages, configuration, or DB.
    site = tmp_path / "server-a" / "website"
    collector = tmp_path / "server-b" / "collector"
    for name, target, folders in (("website", site, ("web", "config", "frontend")),
                                  ("collector", collector, ("engine", "service", "config"))):
        for folder in folders:
            shutil.copytree(ROOT / name / folder, target / folder,
                ignore=shutil.ignore_patterns("__pycache__", "*.png", "demo.html", "demo.template.html"))
        shutil.copy2(ROOT / name / "run.py", target / "run.py")
    assert not (site / "engine").exists() and not (collector / "frontend").exists()
    assert not (site / ".env").exists() and not (collector / ".env").exists()
    api_port, web_port = free_port(), free_port()
    while web_port == api_port:
        web_port = free_port()
    cfg = json.loads((collector / "config" / "service.json").read_text(encoding="utf-8"))
    cfg["api"]["port"] = api_port
    cfg["scheduler"]["enabled"] = False
    cfg["apt_sync"]["enabled"] = False  # No external network calls in this isolation test.
    cfg["translation"]["enabled"] = True
    (collector / "config" / "service.json").write_text(json.dumps(cfg), encoding="utf-8")
    web_cfg = json.loads((site / "config" / "web.json").read_text(encoding="utf-8"))
    web_cfg.update(port=web_port, api_base_url="http://127.0.0.1:" + str(api_port), api_timeout_seconds=1)
    (site / "config" / "web.json").write_text(json.dumps(web_cfg), encoding="utf-8")
    env = dict(os.environ, OPENAI_API_KEY="")
    env.pop("PYTHONPATH", None)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    api = web = None
    api_base, web_base = "http://127.0.0.1:" + str(api_port), "http://127.0.0.1:" + str(web_port)
    with (tmp_path / "api.log").open("w") as api_log, (tmp_path / "web.log").open("w") as web_log:
        try:
            api = subprocess.Popen([sys.executable, "run.py", "--stdin-control"], cwd=str(collector),
                stdin=subprocess.PIPE, stdout=api_log, stderr=api_log, env=env, creationflags=flags)
            ready(api, api_base)
            web = subprocess.Popen([sys.executable, "run.py", "--stdin-control"], cwd=str(site),
                stdin=subprocess.PIPE, stdout=web_log, stderr=web_log, env=env, creationflags=flags)
            ready(web, web_base)
            assert json.loads(get(web_base, "/healthz")[1]) == {"status": "ok", "service": "website"}
            assert get(web_base, "/")[0] == 200
            assert b"app.js" in get(web_base, "/")[1]
            # Insert on the collector's machine; website sees it via HTTP.
            insert = "import json,sys;from engine.storage import Store;s=Store('data');s.save_article(json.load(sys.stdin));s.close()"
            subprocess.run([sys.executable, "-c", insert], input=json.dumps(article()).encode(),
                cwd=str(collector), env=env, check=True, timeout=10, capture_output=True, creationflags=flags)
            payload = json.loads(get(web_base, "/api/v1/articles?date=2026-11-09")[1])
            assert payload["total"] == 1 and payload["items"][0]["title"] == "Company reports breach"
            assert get(web_base, "/.env")[0] == 404
            assert get(web_base, "/data/anquanday.sqlite3")[0] == 404
            assert not (site / "data" / "anquanday.sqlite3").exists()
            # The translator subprocess must also work from the isolated folder.
            deadline = time.monotonic() + 10
            log = collector / "data" / "logs" / "_translation.log"
            while time.monotonic() < deadline:
                if log.exists() and "key_missing" in log.read_text():
                    break
                time.sleep(0.1)
            assert "key_missing" in log.read_text()
            stop(api)
            assert api.returncode == 0
            assert get(web_base, "/")[0] == 200
            assert get(web_base, "/healthz")[0] == 200
            assert get(web_base, "/api/v1/articles")[0] == 503
        finally:
            stop(web)
            stop(api)
