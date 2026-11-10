"""One source per child process; no API or scheduler is started here."""
import argparse
import json
from pathlib import Path
from engine.collector import collect_source
from engine.storage import Store


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sources-file", required=True)
    p.add_argument("--source-id", required=True)
    p.add_argument("--data-dir", required=True)
    args = p.parse_args()
    sources = json.loads(Path(args.sources_file).read_text(encoding="utf-8-sig"))
    source = next(s for s in sources if s["id"] == args.source_id)
    store = Store(args.data_dir)
    try:
        result = collect_source(source, store)
        print(json.dumps(result, ensure_ascii=True), flush=True)
        return 0 if result["status"] in ("ok", "partial", "empty_feed", "stale_ignored", "skipped_busy") else 2
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
