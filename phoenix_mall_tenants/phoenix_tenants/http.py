"""HTTP session with polite throttling, retries, and a CA bundle that tolerates the
mall sites' misconfigured TLS.

Several Phoenix sites (phoenixmarketcity.com, phoenixmallofasia.com,
mallofthemillennium.com) serve only their leaf certificate and omit the
intermediate. Browsers paper over this by fetching the intermediate themselves;
Python does not, so verification fails. We fix it properly by appending the two
missing public intermediates (shipped in ./certs) to the normal trust store.
Verification stays on.
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

_CERT_DIR = Path(__file__).with_name("certs")
_BUNDLE: str | None = None


def ca_bundle() -> str:
    """Path to (base trust store + missing intermediates). Built once per process."""
    global _BUNDLE
    if _BUNDLE:
        return _BUNDLE
    base = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not base or not Path(base).exists():
        import certifi
        base = certifi.where()
    pem = Path(base).read_text()
    for extra in sorted(_CERT_DIR.glob("*.pem")):
        pem += "\n" + extra.read_text()
    fd, path = tempfile.mkstemp(prefix="phoenix_ca_", suffix=".pem")
    with os.fdopen(fd, "w") as fh:
        fh.write(pem)
    _BUNDLE = path
    return path


class Fetcher:
    """requests.Session wrapper.

    * per-host minimum interval between requests (be polite: these are small sites,
      and the Wayback Machine asks for <~1 req/s)
    * retries with exponential backoff on connection errors, 429 and 5xx
    * `verify` passed per request: requests lets REQUESTS_CA_BUNDLE override
      Session.verify, so setting it on the session is not enough
    """

    def __init__(self, min_interval: float = 1.0, retries: int = 4, timeout: float = 60.0,
                 host_intervals: dict[str, float] | None = None):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})
        self.min_interval = min_interval
        self.host_intervals = host_intervals or {}
        self.retries = retries
        self.timeout = timeout
        self._last: dict[str, float] = {}

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        gap = self.host_intervals.get(host, self.min_interval)
        wait = self._last.get(host, 0) + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def request(self, method: str, url: str, *, ok_statuses=(200,), **kw) -> requests.Response:
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("verify", ca_bundle())
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            try:
                r = self.s.request(method, url, **kw)
            except requests.RequestException as e:
                last_exc = e
            else:
                if r.status_code in ok_statuses or not (r.status_code == 429 or r.status_code >= 500):
                    return r
                last_exc = requests.HTTPError(f"{r.status_code} for {url}", response=r)
                # A 500 carrying an ASP.NET "missing value for parameter" message is a
                # client error dressed as a server error; the caller handles it.
                if r.status_code == 500 and "missing value for parameter" in r.text:
                    return r
            if attempt < self.retries:
                time.sleep(min(2 ** (attempt + 1), 60))
        raise last_exc  # type: ignore[misc]

    def get(self, url: str, **kw) -> requests.Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw) -> requests.Response:
        return self.request("POST", url, **kw)
