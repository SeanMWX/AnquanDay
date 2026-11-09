"""Bounded, independent source processes with persisted completion-based intervals."""
import logging
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from engine.storage import Store, now
from .config import ROOT

LOG = logging.getLogger(__name__)


class Scheduler:
    def __init__(self, config, sources, command_factory=None):
        self.config = config
        self.settings = config["scheduler"]
        self.sources = {s["id"]: s for s in sources if self.options(s["id"]).get("enabled", True)}
        self.stop_event = Event()
        self.active = {}
        self.due = {}
        self.command_factory = command_factory or self.command
        self.ready = Event()

    def options(self, sid):
        return dict(self.settings, **self.settings.get("sources", {}).get(sid, {}))

    def command(self, sid):
        return [sys.executable, "-m", "service.worker", "--sources-file", self.config["sources_file"],
                "--source-id", sid, "--data-dir", self.config["data_dir"]]

    def stop(self):
        self.stop_event.set()

    @staticmethod
    def terminate(process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def checkpoint(self, store, sid):
        self.due[sid] = time.time() + self.options(sid)["interval_seconds"]
        store.set_state("schedule:" + sid, {"next_due": self.due[sid], "finished_at": now()})

    def tick(self, store):
        for sid, job in list(self.active.items()):
            process, started, log, started_at = job
            timed_out = time.monotonic() - started >= self.options(sid)["source_timeout_seconds"]
            if process.poll() is not None or timed_out:
                if process.poll() is None and timed_out:
                    self.terminate(process)
                    store.log_run({"source_id": sid, "source_name": self.sources[sid]["name"],
                        "started_at": started_at, "finished_at": now(), "status": "failed", "error": "worker_timeout"})
                    LOG.warning("source=%s exceeded its deadline", sid)
                else:
                    LOG.info("source=%s completed exit=%s", sid, process.returncode)
                log.close()
                del self.active[sid]
                self.checkpoint(store, sid)
        for sid in sorted(self.sources, key=lambda x: self.due[x]):
            if self.stop_event.is_set() or len(self.active) >= self.settings["max_parallel_sources"]:
                break
            if sid in self.active or self.due[sid] > time.time():
                continue
            log_path = Path(self.config["data_dir"]) / "logs" / (sid + ".log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log = log_path.open("ab")
            try:
                process = subprocess.Popen(self.command_factory(sid), cwd=str(ROOT), stdin=subprocess.DEVNULL,
                    stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except OSError:
                log.close()
                self.checkpoint(store, sid)
                LOG.exception("source=%s could not start", sid)
                continue
            self.active[sid] = (process, time.monotonic(), log, now())
            LOG.info("source=%s started pid=%s", sid, process.pid)

    def run(self):
        store = Store(self.config["data_dir"])
        try:
            for sid in self.sources:
                saved = store.state("schedule:" + sid, {})
                self.due[sid] = time.time() if self.settings["run_on_startup"] else saved.get(
                    "next_due", time.time() + self.options(sid)["interval_seconds"])
            self.ready.set()
            while not self.stop_event.is_set():
                try:
                    self.tick(store)
                except Exception:
                    store.db.rollback()
                    LOG.exception("scheduler tick failed; retrying")
                self.stop_event.wait(0.2)
        finally:
            for sid, (process, started, log, started_at) in list(self.active.items()):
                self.terminate(process)
                log.close()
                self.checkpoint(store, sid)
            self.active.clear()
            store.close()
