"""Fixed-origin API proxy + three static assets; deployable without engine/service/data."""
import json
import logging
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo

ROUTES = {"/api/v1/health", "/api/v1/sources", "/api/v1/overview", "/api/v1/calendar", "/api/v1/articles", "/api/v1/kev"}
ROUTES.update({"/api/v1/apt/groups", "/api/v1/apt/articles"})
ASSETS = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/styles.css": ("styles.css", "text/css; charset=utf-8")}


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    for key, name in (("host", "ANQUANDAY_WEB_HOST"), ("api_base_url", "ANQUANDAY_API_BASE_URL")):
        if name in os.environ:
            config[key] = os.environ[name]
    if "ANQUANDAY_WEB_PORT" in os.environ:
        config["port"] = int(os.environ["ANQUANDAY_WEB_PORT"])
    validate_config(config)
    return config


def validate_config(config):
    if not isinstance(config["host"], str) or not config["host"]:
        raise ValueError("host is required")
    for key, low, high in (("port", 1, 65535), ("api_timeout_seconds", 1, 120), ("refresh_seconds", 5, 86400)):
        value = config[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError("invalid_" + key)
    ZoneInfo(config["display_timezone"])
    url = urlsplit(config["api_base_url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in ("", "/"):
        raise ValueError("api_base_url_must_be_fixed_origin")
    config["api_base_url"] = config["api_base_url"].rstrip("/")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def create_server(config, assets):
    class WebHandler(BaseHTTPRequestHandler):
        server_version = "AnquanDayWeb/1"

        def respond(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if len(self.path) > 4096:
                return self.respond(414, {"error": "request_too_long"})
            url = urlsplit(self.path)
            if url.path == "/healthz" and not url.scheme and not url.netloc:
                return self.respond(200, {"status": "ok", "service": "website"})
            if url.path in ASSETS:
                name, mime = ASSETS[url.path]
                return self.respond(200, (assets / name).read_bytes(), mime)
            if url.path == "/config.json":
                return self.respond(200, {"display_timezone": config["display_timezone"], "refresh_seconds": config["refresh_seconds"]})
            if url.path not in ROUTES or url.scheme or url.netloc:
                return self.respond(404, {"error": "not_found"})
            upstream = config["api_base_url"] + url.path + ("?" + url.query if url.query else "")
            try:
                opener = build_opener(ProxyHandler({}), NoRedirect())
                with opener.open(Request(upstream, headers={"Accept": "application/json"}), timeout=config["api_timeout_seconds"]) as response:
                    body = response.read(2_000_001)
                    if len(body) > 2_000_000:
                        return self.respond(502, {"error": "api_response_too_large"})
                    self.respond(200, body)
            except HTTPError as exc:
                exc.close()
                self.respond(exc.code if exc.code in (400, 404, 503) else 502, {"error": "api_request_failed"})
            except (URLError, OSError, TimeoutError):
                self.respond(503, {"error": "api_unavailable"})

        def do_POST(self):
            self.respond(405, {"error": "read_only_api"})

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

        def log_message(self, fmt, *args):
            logging.getLogger("web").info(fmt, *args)

    server = ThreadingHTTPServer((config["host"], config["port"]), WebHandler)
    server.daemon_threads = True
    return server
