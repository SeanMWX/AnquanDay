"""Bounded GET transport for a configured public-source allowlist."""
import ipaddress
import socket
import time
from urllib.parse import urlsplit, urljoin
import requests

USER_AGENT = "AnquanDay/0.1 (source compatibility check)"
MAX_BYTES = 5_000_000

class FetchError(Exception):
    pass

def validate_target(url, hosts):
    p = urlsplit(url)
    if p.scheme not in ("https", "http") or p.hostname not in hosts or p.username or p.password:
        raise FetchError("target_not_allowed")
    if p.port not in (None, 80, 443):
        raise FetchError("port_not_allowed")
    try:
        addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError:
        raise FetchError("dns_resolution_failed")
    if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
        raise FetchError("non_public_target")

class Client:
    def __init__(self, hosts, timeout=15):
        self.hosts = set(hosts)
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT,
            "Accept": "application/rss+xml, application/atom+xml, application/json, text/html;q=0.8, */*;q=0.5"})
        self.last_request = 0.0

    def close(self):
        self.session.close()

    def get(self, url, validators=None):
        headers = dict(validators or {})
        current = url
        for hop in range(5):
            validate_target(current, self.hosts)
            for attempt in range(2):
                wait = 0.25 - (time.monotonic() - self.last_request)
                if wait > 0:
                    time.sleep(wait)
                self.last_request = time.monotonic()
                try:
                    response = self.session.get(current, headers=headers, timeout=(5, self.timeout),
                                                allow_redirects=False, stream=True)
                    status = response.status_code
                    if status in (429, 500, 502, 503, 504) and attempt == 0:
                        delay = response.headers.get("Retry-After", "1")
                        response.close()
                        # Long server-requested backoff is left to the next run.
                        if not delay.isdigit() or int(delay) > 2:
                            raise FetchError("http_{}_retry_later".format(status))
                        time.sleep(max(1, int(delay)))
                        continue
                    if status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        response.close()
                        if not location:
                            raise FetchError("redirect_without_location")
                        current = urljoin(current, location)
                        headers = {}  # Validators only belong to their original resource.
                        break
                    raw = bytearray()
                    if status != 304:
                        for chunk in response.iter_content(65536):
                            raw.extend(chunk)
                            if len(raw) > MAX_BYTES:
                                raise FetchError("response_too_large")
                    result = {"status": status, "url": current,
                        "content_type": response.headers.get("Content-Type", ""),
                        "etag": response.headers.get("ETag"),
                        "last_modified": response.headers.get("Last-Modified"), "raw": bytes(raw)}
                    response.close()
                    if status not in (200, 304):
                        raise FetchError("http_{}".format(status))
                    return result
                except requests.RequestException as exc:
                    if attempt == 0:
                        continue
                    # Do not echo headers, credentials or remote body in errors.
                    raise FetchError(type(exc).__name__)
                finally:
                    if "response" in locals():
                        response.close()
            else:
                raise FetchError("request_failed")
        raise FetchError("too_many_redirects")
