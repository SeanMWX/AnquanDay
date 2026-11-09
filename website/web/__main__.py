import argparse
import signal
import sys
from threading import Thread
from pathlib import Path
from .server import create_server, load_config


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="AnquanDay 展示端，通过 API 获取数据")
    parser.add_argument("--config", default=str(root / "config" / "web.json"))
    parser.add_argument("--stdin-control", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    config = load_config(args.config)
    server = create_server(config, root / "frontend")
    print("AnquanDay: http://{}:{}".format(*server.server_address), flush=True)
    if args.stdin_control:
        def watch_stdin():
            for line in sys.stdin:
                if line.strip() == "stop":
                    break
            server.shutdown()
        Thread(target=watch_stdin, daemon=True).start()
    def on_stop(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_stop)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
