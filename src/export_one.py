"""Print, choose the Contented Form template, download, and return to the list."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from src.grid import click_req_number, wait_for_list_ready
from src.locators import (
    BACK_TO_LIST,
    PREVIEW_TEXT,
    TEMPLATE_NAME,
    ControlNotFound,
    RowExportError,
    wait_visible,
)

PREVIEW_TIMEOUT_MS = 90000
DOWNLOAD_TIMEOUT_MS = 90000


def sanitize_req_number(value: str) -> str:
    cleaned = re.sub(r"\s+", "_", value.strip())
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", cleaned)
    return cleaned.strip("._") or "record"


def pdf_filename(req_number: str, active_id: str) -> str:
    return f"{sanitize_req_number(req_number)}__{active_id}.pdf"


def export_row(page: Page, req_number: str, pdf_dir: Path) -> tuple[str, Path]:
    """Run the print flow for one grid row and return to the list.

    Raises ControlNotFound when a control cannot be identified.
    Raises RowExportError when the preview or download does not finish.
    If the PDF was saved but the return click failed, RowExportError carries pdf_path.
    """
    pdf_dir.mkdir(parents=True, exist_ok=True)
    click_req_number(page, req_number)
    print_page = _open_print(page)
    active_id = _active_id(print_page)
    _select_template(print_page)
    try:
        _wait_for_preview(print_page)
        dest = pdf_dir / pdf_filename(req_number, active_id)
        _download(print_page, dest)
    except RowExportError as exc:
        exc.active_id = exc.active_id or active_id
        raise
    try:
        _return_to_list(print_page, page)
    except Exception as exc:
        raise RowExportError(
            f"Downloaded but could not return to the list: {exc}",
            pdf_path=dest,
            active_id=active_id,
        ) from exc
    return active_id, dest


def _open_print(page: Page) -> Page:
    page.get_by_role(
        "heading", name=re.compile(r"Edit Product Development Request 3")
    ).wait_for(state="visible", timeout=45000)
    button = page.locator("button").filter(has=page.locator(".glyphicon-print"))
    if button.count() == 0:
        button = page.get_by_role("button", name=re.compile(r"^Print$"))
    target = wait_visible(button, "Print", timeout=45000)
    # The button is in the DOM before its click handler is attached.
    for _ in range(3):
        target.click()
        try:
            page.wait_for_url(re.compile(r"print_pd_request_3"), timeout=8000)
            return page
        except PlaywrightTimeout:
            for candidate in page.context.pages:
                if "print_pd_request_3" in candidate.url:
                    return candidate
    raise ControlNotFound("Print page", page)


def _active_id(page: Page) -> str:
    query = parse_qs(urlparse(page.url).query)
    active_id = (query.get("active_id") or [""])[0].strip()
    if not active_id:
        raise ControlNotFound("active_id", page)
    return active_id


def _select_template(page: Page) -> None:
    label = page.locator("label.nf-field-label", has_text="Report Template")
    select = label.locator("xpath=following::select[1]")
    try:
        select.wait_for(state="visible", timeout=30000)
        select.locator("option", has_text=TEMPLATE_NAME).first.wait_for(
            state="attached", timeout=30000
        )
    except PlaywrightTimeout as exc:
        raise ControlNotFound("Report Template", page) from exc
    select.select_option(label=TEMPLATE_NAME)


def _wait_for_preview(page: Page) -> None:
    deadline_ms = PREVIEW_TIMEOUT_MS
    try:
        page.get_by_text(PREVIEW_TEXT, exact=False).first.wait_for(
            state="visible", timeout=deadline_ms
        )
        return
    except PlaywrightTimeout:
        pass
    for frame in page.frames:
        try:
            if frame.get_by_text(PREVIEW_TEXT).count():
                return
        except Exception:
            continue
    raise RowExportError("PDF preview did not appear.")


def _download(page: Page, dest: Path) -> None:
    button = page.locator("button.btn-primary").filter(has_text="Download PDF")
    target = wait_visible(button, "Download PDF", timeout=60000)
    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT_MS) as download_info:
            target.click()
        download_info.value.save_as(str(dest))
    except PlaywrightTimeout as exc:
        raise RowExportError("Download PDF did not start a download.") from exc
    if not dest.exists() or dest.stat().st_size == 0:
        raise RowExportError("Download PDF saved an empty file.", pdf_path=dest)


def _return_to_list(print_page: Page, origin: Page) -> None:
    if print_page is not origin:
        print_page.close()
        edit_page = origin
    else:
        print_page.go_back(wait_until="domcontentloaded")
        edit_page = print_page
    link = edit_page.get_by_role("link", name=BACK_TO_LIST)
    if link.count() == 0:
        link = edit_page.get_by_role("button", name=BACK_TO_LIST)
    if link.count() == 0:
        link = edit_page.get_by_text(BACK_TO_LIST)
    wait_visible(link, "Back to list of pd request", timeout=30000).click()
    edit_page.get_by_role("heading", name="PD Request 3", exact=True).wait_for(
        state="visible", timeout=30000
    )
    wait_for_list_ready(edit_page)
