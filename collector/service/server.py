"""Small local HTTP API. Never serves SQLite/raw files or exposes collection mutations."""
import json
import logging
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from .repository import BadQuery


def handler_for(repository):
    class APIHandler(BaseHTTPRequestHandler):
        server_version = "AnquanDayAPI/1"

        def respond(self, status, obj):
            body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if len(self.path) > 4096:
                return self.respond(414, {"error": "request_too_long"})
            try:
                url = urlsplit(self.path)
                args = parse_qs(url.query, keep_blank_values=True, max_num_fields=20)
                if any(len(values) != 1 for values in args.values()):
                    raise BadQuery("duplicate_parameter")
                result = repository.dispatch(url.path, {k: v[0] for k, v in args.items()})
                self.respond(200, result)
            except (BadQuery, ValueError) as exc:
                self.respond(400, {"error": str(exc)})
            except KeyError:
                self.respond(404, {"error": "not_found"})
            except sqlite3.Error:
                logging.exception("database read failed")
                self.respond(503, {"error": "database_unavailable"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                logging.exception("API request failed")
                self.respond(500, {"error": "internal_error"})

        def do_POST(self):
            self.respond(405, {"error": "read_only_api"})

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

        def log_message(self, fmt, *args):
            logging.getLogger("api").info(fmt, *args)

    return APIHandler


def create_server(host, port, repository):
    server = ThreadingHTTPServer((host, port), handler_for(repository))
    server.daemon_threads = True
    return server
