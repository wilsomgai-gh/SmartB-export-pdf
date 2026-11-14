"""Role and text locators for the PD Request 3 export flow.

Confirmed on the live list page:
- Login form: form.login-form (a hidden mobile copy also exists)
- Search icon: button.btn-default .glyphicon-search
- Search panel: div.nf-search, with Req Date as two plain text inputs
- Submit: button.btn-primary Search
- Grid: table.table-striped, Req Number is a link
- Pager: ul.pagination a.page-link "Next ›" and a Page select
"""

from __future__ import annotations

import re
from pathlib import Path

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeout

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LIST_URL = "https://inhesion.smartb.co/action?name=pd_request_3"
TEMPLATE_NAME = "PD Form (Contented Form) v3"
PREVIEW_TEXT = "New Product Development Request Form"
LIST_HEADING = "PD Request 3"

BACK_TO_LIST = re.compile(r"Back to list of pd request", re.I)


class ControlNotFound(RuntimeError):
    """A required control could not be identified. The run stops."""

    def __init__(self, control: str, page: Page | None = None):
        self.control = control
        detail = ""
        if page is not None:
            detail = _failure_detail(page, control)
        super().__init__(f"Could not identify control: {control}.{detail}")


class RowExportError(RuntimeError):
    """One row failed after its controls were identified."""

    def __init__(
        self,
        message: str,
        *,
        pdf_path: Path | None = None,
        active_id: str = "",
    ):
        super().__init__(message)
        self.pdf_path = pdf_path
        self.active_id = active_id


def _failure_detail(page: Page, control: str) -> str:
    logs = PROJECT_ROOT / "output" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    html_path = logs / "last-failure.html"
    png_path = logs / "last-failure.png"
    try:
        html_path.write_text(page.content(), encoding="utf-8")
    except Exception:
        html_path = None
    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Exception:
        png_path = None
    names = _visible_labels(page)
    parts = [f" URL: {page.url}", f" Visible controls: {names}"]
    if html_path is not None:
        parts.append(f" HTML: {html_path}")
    if png_path is not None:
        parts.append(f" Screenshot: {png_path}")
    return "".join(parts) + f" (while looking for {control})"


def _visible_labels(page: Page) -> list[str]:
    labels: list[str] = []
    for role in ("button", "link"):
        group = page.get_by_role(role)
        count = min(group.count(), 40)
        for index in range(count):
            item = group.nth(index)
            try:
                if not item.is_visible():
                    continue
                text = " ".join(item.inner_text().split())
            except Exception:
                continue
            if text:
                labels.append(f"{role}:{text[:60]}")
    return labels[:50]


def wait_visible(locator: Locator, control: str, timeout: int = 15000) -> Locator:
    try:
        locator.first.wait_for(state="visible", timeout=timeout)
    except PlaywrightTimeout as exc:
        page = locator.page
        raise ControlNotFound(control, page) from exc
    return locator.first


def is_visible(locator: Locator, timeout: int = 800) -> bool:
    try:
        locator.first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        return False
