"""Playwright browser session shared by all adapters.

Adapters open booking pages the way a visitor would and read the JSON the
page itself loads. Nothing is requested that the page would not request.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from playwright.sync_api import Page, Response, sync_playwright
from playwright.sync_api import TimeoutError as PWTimeout

from .config import ROOT

BLOCK_TITLES = re.compile(
    r"access denied|attention required|just a moment|pardon our interruption|request blocked|"
    r"are you a robot|captcha|forbidden|bot detection", re.I)


@dataclass
class Capture:
    nav_status: int | None = None
    url: str = ""
    title: str = ""
    responses: list[dict[str, Any]] = field(default_factory=list)  # {"url", "status", "json"}
    error: str = ""

    @property
    def blocked(self) -> bool:
        return self.nav_status in (401, 403, 429) or bool(BLOCK_TITLES.search(self.title or ""))


class BrowserSession:
    def __init__(self, browser_settings: dict, headless: bool | None = None):
        self.cfg = browser_settings
        self.headless = self.cfg.get("headless", True) if headless is None else headless
        self._pw = None
        self.context = None

    def __enter__(self) -> "BrowserSession":
        self._pw = sync_playwright().start()
        kwargs = dict(
            headless=self.headless,
            locale=self.cfg.get("locale", "en-IN"),
            timezone_id=self.cfg.get("timezone", "Asia/Kolkata"),
            viewport={"width": 1440, "height": 900},
        )
        channel = self.cfg.get("channel", "chromium")
        if channel and channel != "chromium":
            kwargs["channel"] = channel
        profile = self.cfg.get("user_data_dir")
        if profile:
            path = Path(profile) if Path(profile).is_absolute() else ROOT / profile
            path.mkdir(parents=True, exist_ok=True)
            self.context = self._pw.chromium.launch_persistent_context(str(path), **kwargs)
        else:
            launch = {"headless": kwargs.pop("headless")}
            if "channel" in kwargs:
                launch["channel"] = kwargs.pop("channel")
            self.context = self._pw.chromium.launch(**launch).new_context(**kwargs)
        self.context.set_default_timeout(int(self.cfg.get("page_timeout_s", 60)) * 1000)
        return self

    def __exit__(self, *exc):
        try:
            if self.context:
                self.context.close()
        finally:
            if self._pw:
                self._pw.stop()

    def new_page(self) -> Page:
        return self.context.new_page()

    def capture(self, url: str, want: Callable[[Response], bool], *, wait_for_first: bool = True,
                settle_s: float = 3.0, timeout_s: float | None = None, keep_all_json: bool = False,
                page: Page | None = None) -> Capture:
        """Open `url` and collect JSON responses for which `want(response)` is true.

        wait_for_first: return once a wanted response has arrived (plus settle_s), or at timeout.
                        If False, simply wait settle_s after the page loads.
        keep_all_json: also keep every other JSON response (used by probes).
        """
        timeout_s = timeout_s or float(self.cfg.get("page_timeout_s", 60))
        own_page = page is None
        page = page or self.new_page()
        cap = Capture()

        def on_response(resp: Response):
            try:
                wanted = want(resp)
                if not wanted and not keep_all_json:
                    return
                if "json" not in (resp.headers.get("content-type") or "") and not wanted:
                    return
                body = resp.text()
                data = json.loads(body) if body and body.lstrip()[:1] in "{[" else None
                if data is not None:
                    cap.responses.append({"url": resp.url, "status": resp.status, "wanted": wanted, "json": data})
            except Exception:  # noqa: BLE001 - a bad response must not kill the capture
                pass

        page.on("response", on_response)
        try:
            nav = page.goto(url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
            cap.nav_status = nav.status if nav else None
            if wait_for_first:
                waited, step = 0, 500
                while waited < timeout_s * 1000:
                    if any(r["wanted"] for r in cap.responses):
                        page.wait_for_timeout(settle_s * 1000)
                        break
                    # Stop early on an obvious block page instead of waiting out the timeout.
                    if waited and waited % 5000 == 0:
                        cap.title = page.title()
                        if cap.blocked:
                            break
                    page.wait_for_timeout(step)
                    waited += step
            else:
                page.wait_for_timeout(settle_s * 1000)
            cap.title = page.title()
        except PWTimeout as e:
            cap.error = f"timeout: {e}".splitlines()[0]
        except Exception as e:  # noqa: BLE001
            cap.error = f"{type(e).__name__}: {e}".splitlines()[0]
        finally:
            cap.url = page.url
            try:
                cap.title = cap.title or page.title()
            except Exception:  # noqa: BLE001
                pass
            page.remove_listener("response", on_response)
            if own_page:
                page.close()
        return cap
