"""Persistent Chromium profile and conditional SmartB login."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.locators import LIST_HEADING, LIST_URL, PROJECT_ROOT, ControlNotFound, is_visible

LOGIN_URL_MARK = "/login"


@dataclass
class BrowserSession:
    playwright: Playwright
    context: BrowserContext
    page: Page

    def close(self) -> None:
        try:
            self.context.close()
        finally:
            self.playwright.stop()


def load_credentials() -> tuple[str, str]:
    load_dotenv(PROJECT_ROOT / ".env")
    username = os.environ.get("SMARTB_USERNAME", "").strip()
    password = os.environ.get("SMARTB_PASSWORD", "").strip()
    if not username or not password:
        raise SystemExit(
            "Set SMARTB_USERNAME and SMARTB_PASSWORD in .env "
            "(see .env.example). The script does not read the steps file."
        )
    return username, password


def launch_browser(profile_dir: Path, *, headless: bool = False) -> BrowserSession:
    _prepare_library_path()
    profile_dir.mkdir(parents=True, exist_ok=True)
    downloads = PROJECT_ROOT / "output" / "downloads-tmp"
    downloads.mkdir(parents=True, exist_ok=True)
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            accept_downloads=True,
            downloads_path=str(downloads),
            viewport={"width": 1440, "height": 900},
        )
    except Exception as exc:
        playwright.stop()
        message = str(exc)
        if not headless and _display_error(message):
            raise SystemExit(
                "Headed Chromium could not open a window. "
                "WSLg or another display is required. "
                "The launch mode was not changed.\n"
                f"{message}"
            ) from exc
        raise
    context.set_default_timeout(20000)
    page = context.pages[0] if context.pages else context.new_page()
    return BrowserSession(playwright, context, page)


def login_if_prompted(page: Page, username: str, password: str) -> None:
    """Log in only when the login form is actually on screen."""
    if _login_visible(page, timeout=400):
        _submit_login(page, username, password)
        _wait_for_list_or_login(page)
        if _login_visible(page, timeout=400):
            raise SystemExit(f"Login failed. Still on {page.url}.")


def ensure_logged_in(page: Page, username: str, password: str) -> None:
    if "name=pd_request_3" not in page.url and LOGIN_URL_MARK not in page.url:
        page.goto(LIST_URL, wait_until="domcontentloaded")
    if _login_visible(page):
        _submit_login(page, username, password)
    _wait_for_list_or_login(page)
    if _login_visible(page):
        _submit_login(page, username, password)
        _wait_for_list_or_login(page)
    if _login_visible(page):
        raise SystemExit(f"Login failed. Still on {page.url}.")


def _prepare_library_path() -> None:
    """Use locally unpacked libraries when the system does not provide them."""
    lib_dir = PROJECT_ROOT / ".deps" / "usr" / "lib" / "x86_64-linux-gnu"
    if not lib_dir.is_dir():
        return
    current = os.environ.get("LD_LIBRARY_PATH", "")
    entries = [entry for entry in current.split(":") if entry]
    lib = str(lib_dir)
    if lib not in entries:
        os.environ["LD_LIBRARY_PATH"] = ":".join([lib, *entries])


def _display_error(message: str) -> bool:
    lowered = message.lower()
    tokens = (
        "missing x server",
        "failed to launch",
        "cannot open display",
        "xdg_runtime",
        "no display",
    )
    return any(token in lowered for token in tokens)


def _login_visible(page: Page, timeout: int = 1500) -> bool:
    if LOGIN_URL_MARK in page.url:
        return True
    password = page.locator("input[type='password']")
    if is_visible(password, timeout=timeout) and is_visible(
        page.get_by_role("button", name=re.compile(r"^Login$", re.I)),
        timeout=timeout,
    ):
        return True
    return False


def _login_form(page: Page):
    form = page.locator("form.login-form")
    if form.count() and form.first.is_visible():
        return form.first
    visible = page.locator("form").filter(has=page.locator("input[name='password']:visible"))
    if visible.count():
        return visible.first
    raise ControlNotFound("login form", page)


def _submit_login(page: Page, username: str, password: str) -> None:
    form = _login_form(page)
    user_box = form.locator("input[name='username']")
    password_box = form.locator("input[name='password']")
    try:
        user_box.wait_for(state="visible", timeout=15000)
        password_box.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeout as exc:
        raise ControlNotFound("login password", page) from exc
    user_box.fill(username)
    password_box.fill(password)
    form.get_by_role("button", name=re.compile(r"^Login$", re.I)).click()
    try:
        page.wait_for_url(lambda url: LOGIN_URL_MARK not in url, timeout=30000)
    except PlaywrightTimeout as exc:
        raise SystemExit(f"Login failed. Still on {page.url}.") from exc


def _wait_for_list_or_login(page: Page) -> None:
    try:
        page.wait_for_function(
            """(heading) => {
                const text = document.body ? document.body.innerText : '';
                return text.includes(heading) || text.includes('Login to your account');
            }""",
            arg=LIST_HEADING,
            timeout=30000,
        )
    except PlaywrightTimeout as exc:
        raise ControlNotFound("PD Request list", page) from exc
