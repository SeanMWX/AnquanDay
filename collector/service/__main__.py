import argparse
import logging
import signal
import sys
from pathlib import Path
from threading import Thread
from engine.storage import Store
from engine.locking import FileLock, LockBusy
from .config import ROOT, load_service
from .repository import Repository
from .scheduler import Scheduler
from .server import create_server


def main():
    parser = argparse.ArgumentParser(description="AnquanDay 收集 engine + API 服务")
    parser.add_argument("--config", default=str(ROOT / "config" / "service.json"))
    parser.add_argument("--stdin-control", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    config, sources = load_service(args.config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with FileLock(Path(config["data_dir"]) / "locks" / "service.lock"):
            store = Store(config["data_dir"])
            store.close()
            server = create_server(config["api"]["host"], config["api"]["port"], Repository(config["data_dir"], sources))
            scheduler = Scheduler(config, sources)
            jobs = []
            if config["scheduler"]["enabled"]:
                jobs.append((scheduler, "source-scheduler"))
            if config["translation"]["enabled"]:
                t = config["translation"]
                translation_config = dict(config, scheduler={
                    "enabled": True, "run_on_startup": True, "sources": {}, "max_parallel_sources": 1,
                    "interval_seconds": t["interval_seconds"],
                    "source_timeout_seconds": t["max_items_per_run"] * (t["request_timeout_seconds"] + 5) + 30,
                })
                translator = Scheduler(translation_config, [{"id": "_translation", "name": "标题翻译"}],
                    command_factory=lambda sid: [sys.executable, "-m", "service.translate",
                        "--config", config["config_path"]])
                jobs.append((translator, "translation-scheduler"))
            threads = []
            for job, name in jobs:
                thread = Thread(target=job.run, name=name, daemon=True)
                thread.start()
                threads.append((thread, job))
            def stop_jobs():
                for job, _ in jobs:
                    job.stop()
            def stop(signum, frame):
                stop_jobs()
                raise KeyboardInterrupt()
            signal.signal(signal.SIGINT, stop)
            signal.signal(signal.SIGTERM, stop)
            if args.stdin_control:
                def watch_stdin():
                    # For an owning launcher/test harness, EOF also means stop.
                    for line in sys.stdin:
                        if line.strip() == "stop":
                            break
                    stop_jobs()
                    server.shutdown()
                Thread(target=watch_stdin, daemon=True).start()
            logging.info("API http://%s:%s ; scheduler=%s", *server.server_address, config["scheduler"]["enabled"])
            try:
                server.serve_forever(poll_interval=0.2)
            except KeyboardInterrupt:
                pass
            finally:
                stop_jobs()
                for thread, job in threads:
                    thread.join(timeout=job.settings["max_parallel_sources"] * 4 + 5)
                server.server_close()
    except LockBusy:
        parser.exit(2, "Another collection service already owns this data directory.\n")


if __name__ == "__main__":
    main()
