"""Search, grid reading, and pagination for PD Request 3."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import parse_qs, unquote, urlparse

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeout

from src.locators import (
    LIST_HEADING,
    ControlNotFound,
    is_visible,
    wait_visible,
)

DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")


@dataclass
class RowRecord:
    req_number: str
    status: str
    req_date: date | None
    req_date_raw: str


def parse_grid_date(value: str) -> date | None:
    text = " ".join(value.split())
    if not text:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def widget_date_text(when: date, field: Locator) -> str:
    field_type = (field.get_attribute("type") or "").lower()
    if field_type == "date":
        return when.isoformat()
    return when.strftime("%d/%m/%Y")


def apply_search(page: Page, start: date, end: date) -> str:
    """Open the search panel, enter Req Date, and submit. Returns the list URL."""
    open_search_panel(page)
    panel = search_panel(page)
    number = _field_group(panel, "Req Number").locator("input")
    if number.count():
        number.first.fill("")
    status = _field_group(panel, "Status").locator("select")
    if status.count():
        status.first.select_option(value="")
    start_box, end_box = date_inputs(panel)
    _set_date(start_box, widget_date_text(start, start_box), "Req Date start")
    _set_date(end_box, widget_date_text(end, end_box), "Req Date end")
    page.keyboard.press("Escape")
    submit = panel.locator("button.btn-primary").filter(has_text="Search")
    wait_visible(submit, "Search button").click()
    _wait_for_results(page, start, end)
    if not on_list(page):
        raise ControlNotFound("PD Request list", page)
    return page.url


def filter_intact(page: Page, list_url: str) -> bool:
    """True when the list still shows the search that produced list_url."""
    saved = _query_value(list_url, "search_condition")
    current = _query_value(page.url, "search_condition")
    if saved is None:
        return on_list(page)
    return current == saved


def _query_value(url: str, name: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(name)
    if not values:
        return None
    return unquote(values[0])


def on_list(page: Page) -> bool:
    if "/login" in page.url or "print_pd_request_3" in page.url:
        return False
    heading = page.get_by_role("heading", name=LIST_HEADING, exact=True)
    if is_visible(heading, timeout=1000):
        return True
    title = page.locator("h1, h2").filter(has_text=re.compile(rf"^{re.escape(LIST_HEADING)}$"))
    return is_visible(title, timeout=500)


def open_search_panel(page: Page) -> None:
    if _panel_open(page):
        return
    icon = _search_icon(page)
    if icon is None:
        raise ControlNotFound("search icon", page)
    icon.click()
    if not _panel_open(page):
        raise ControlNotFound("search panel", page)


def search_panel(page: Page) -> Locator:
    panel = page.locator("div.nf-search")
    return wait_visible(panel, "search panel")


def date_inputs(panel: Locator) -> tuple[Locator, Locator]:
    fields = _field_group(panel, "Req Date").locator("input")
    if fields.count() < 2:
        raise ControlNotFound("Req Date inputs", panel.page)
    return fields.nth(0), fields.nth(1)


def _field_group(panel: Locator, label: str) -> Locator:
    return panel.locator("div.form-group").filter(
        has_text=re.compile(rf"^{re.escape(label)}\b")
    )


def wait_for_list_ready(page: Page, timeout: float = 20) -> None:
    """Wait until the grid has rendered. The heading appears before the table."""
    deadline = datetime.now().timestamp() + timeout
    while datetime.now().timestamp() < deadline:
        if _find_grid_table(page) is not None and read_pager(page) is not None:
            return
        page.wait_for_timeout(250)
    if _find_grid_table(page) is None:
        raise ControlNotFound("grid", page)


def read_rows(page: Page) -> list[RowRecord]:
    wait_for_list_ready(page)
    table = _grid_table(page)
    headers = _header_texts(table)
    try:
        number_at = _column_index(headers, "Req Number")
        date_at = _column_index(headers, "Req Date")
    except ControlNotFound as exc:
        raise ControlNotFound(exc.control, page) from exc
    status_at = _column_index(headers, "Status", required=False)
    rows: list[RowRecord] = []
    for row in _body_rows(table).all():
        cells = row.locator("td, [role='cell']")
        if cells.count() <= max(number_at, date_at):
            continue
        req_number = _cell_text(cells.nth(number_at))
        if not req_number or req_number == "Req Number":
            continue
        raw_date = _cell_text(cells.nth(date_at))
        status = _cell_text(cells.nth(status_at)) if status_at is not None else ""
        rows.append(
            RowRecord(
                req_number=req_number,
                status=status,
                req_date=parse_grid_date(raw_date),
                req_date_raw=raw_date,
            )
        )
    return rows


def click_req_number(page: Page, req_number: str) -> None:
    table = _grid_table(page)
    headers = _header_texts(table)
    number_at = _column_index(headers, "Req Number")
    for row in _body_rows(table).all():
        cells = row.locator("td, [role='cell']")
        if cells.count() <= number_at:
            continue
        cell = cells.nth(number_at)
        if _cell_text(cell) != req_number:
            continue
        cell.scroll_into_view_if_needed()
        link = cell.get_by_role("link")
        target = link.first if link.count() else cell
        target.click()
        return
    raise ControlNotFound(f"Req Number link {req_number}", page)


def read_pager(page: Page) -> tuple[int, int] | None:
    pager = _visible_pagination(page)
    if pager is None:
        return None
    active = pager.locator("li.active")
    if active.count() == 0:
        return None
    raw = " ".join(active.first.inner_text().split())
    if not raw.isdigit():
        return None
    text = _page_select_text(page)
    if text is None:
        return None
    match = re.search(r"of\s+(\d+)", text)
    if not match:
        return None
    return int(raw), int(match.group(1))


def _visible_pagination(page: Page) -> Locator | None:
    pagers = page.locator("ul.pagination")
    for index in range(pagers.count()):
        pager = pagers.nth(index)
        try:
            if pager.is_visible():
                return pager
        except Exception:
            continue
    return None


def goto_page(page: Page, number: int) -> None:
    current = read_pager(page)
    if current is None:
        if number == 1:
            return
        raise ControlNotFound("pager", page)
    if current[0] == number:
        return
    _page_select(page).select_option(label=str(number))
    _wait_for_page_number(page, number)


def go_next(page: Page) -> bool:
    current = read_pager(page)
    if current is None:
        return False
    page_number, total = current
    if page_number >= total:
        return False
    nxt = page.locator("ul.pagination:visible a.page-link").filter(
        has_text=re.compile(r"^Next\b")
    )
    if nxt.count() == 0:
        raise ControlNotFound("pager Next", page)
    parent = nxt.first.locator("xpath=parent::li")
    if _disabled(nxt.first) or (parent.count() and _disabled(parent)):
        return False
    nxt.first.click()
    _wait_for_page_number(page, page_number + 1)
    return True


def ensure_list(
    page: Page,
    list_url: str,
    page_number: int,
    start: date,
    end: date,
) -> str:
    """Return to the filtered list on the requested page. Returns the list URL."""
    if not on_list(page):
        _recover_to_list(page, list_url)
    if not on_list(page):
        raise ControlNotFound("PD Request list", page)
    current_url = list_url
    if not filter_intact(page, list_url):
        current_url = apply_search(page, start, end)
    wait_for_list_ready(page)
    goto_page(page, page_number)
    wait_for_list_ready(page)
    return current_url


def _recover_to_list(page: Page, list_url: str) -> None:
    for _ in range(4):
        if on_list(page):
            return
        back = page.get_by_role("link", name=re.compile(r"Back to list of pd request", re.I))
        if back.count() == 0:
            back = page.get_by_text(re.compile(r"Back to list of pd request", re.I))
        if back.count() and back.first.is_visible():
            back.first.click()
            page.wait_for_timeout(500)
            continue
        if "print_pd_request_3" in page.url:
            page.go_back(wait_until="domcontentloaded")
            continue
        break
    if not on_list(page):
        page.goto(list_url, wait_until="domcontentloaded")


def _panel_open(page: Page) -> bool:
    return is_visible(page.locator("div.nf-search"), timeout=800)


def _search_icon(page: Page) -> Locator | None:
    # Toolbar icon only. The panel submit button is btn-primary.
    # The sidebar entry named Search is a different control.
    icon = page.locator("button.btn-default").filter(has=page.locator(".glyphicon-search"))
    return _first_visible(icon)


def _first_visible(locator: Locator) -> Locator | None:
    count = min(locator.count(), 10)
    for index in range(count):
        item = locator.nth(index)
        try:
            if item.is_visible():
                return item
        except Exception:
            continue
    return None


def _set_date(field: Locator, text: str, control: str) -> None:
    # Tempus Dominus only keeps a date that is typed. fill() updates the DOM
    # but not the React value, so the search ignores it. The open calendar
    # also covers the neighbouring input; click the page heading to close it.
    field.click()
    field.press("Control+A")
    field.type(text, delay=30)
    field.page.locator("h1").first.click()
    value = field.input_value().strip()
    if text not in value and value.replace("-", "/") != text:
        raise ControlNotFound(control, field.page)


def _wait_for_results(page: Page, start: date, end: date) -> None:
    try:
        page.wait_for_url(re.compile(r"search_condition"), timeout=20000)
    except PlaywrightTimeout:
        print(
            "Search did not put search_condition in the URL. "
            "Row dates will still be checked against the requested range.",
            flush=True,
        )
    # The search panel also contains the words "Req Number", so wait for the grid.
    header = page.locator("table thead").get_by_text("Req Number", exact=True)
    try:
        header.first.wait_for(state="visible", timeout=20000)
    except PlaywrightTimeout as exc:
        raise ControlNotFound("grid", page) from exc
    deadline = datetime.now().timestamp() + 20
    empty_since: float | None = None
    while datetime.now().timestamp() < deadline:
        try:
            rows = read_rows(page)
        except ControlNotFound:
            empty_since = None
            page.wait_for_timeout(300)
            continue
        if any(row.req_date is not None and start <= row.req_date <= end for row in rows):
            _settle_pager(page)
            return
        if not rows:
            now = datetime.now().timestamp()
            if empty_since is None:
                empty_since = now
            elif now - empty_since >= 2:
                _settle_pager(page)
                return
        else:
            empty_since = None
        page.wait_for_timeout(300)
    print(
        "The grid did not show a Req Date inside the range before the wait ended. "
        "Out-of-range rows will be skipped.",
        flush=True,
    )
    try:
        page.locator("ul.pagination li.active").first.wait_for(state="visible", timeout=5000)
    except PlaywrightTimeout:
        return


def _settle_pager(page: Page) -> None:
    deadline = datetime.now().timestamp() + 5
    while datetime.now().timestamp() < deadline:
        if read_pager(page) is not None:
            return
        page.wait_for_timeout(200)


def _find_grid_table(page: Page) -> Locator | None:
    tables = page.locator("table")
    count = tables.count()
    for index in range(count):
        table = tables.nth(index)
        headers = _header_texts(table)
        if "Req Number" in headers and "Req Date" in headers:
            return table
    grid = page.get_by_role("grid").filter(
        has=page.get_by_role("columnheader", name="Req Number", exact=True)
    )
    if grid.count():
        return grid.first
    return None


def _grid_table(page: Page) -> Locator:
    table = _find_grid_table(page)
    if table is None:
        raise ControlNotFound("grid", page)
    return table


def _header_texts(table: Locator) -> list[str]:
    headers = table.locator("thead th")
    if headers.count() == 0:
        headers = table.locator("[role='columnheader']")
    if headers.count() == 0:
        headers = table.locator("tr").first.locator("th, td")
    return [" ".join(text.split()) for text in headers.all_inner_texts()]


def _column_index(headers: list[str], name: str, required: bool = True) -> int | None:
    for index, header in enumerate(headers):
        if header == name:
            return index
    if required:
        raise ControlNotFound(f"column {name}")
    return None


def _body_rows(table: Locator) -> Locator:
    body = table.locator("tbody tr")
    if body.count() == 0:
        body = table.locator("[role='row']")
    return body


def _cell_text(cell: Locator) -> str:
    return " ".join(cell.inner_text().split())


def _page_select(page: Page) -> Locator:
    spans = page.locator("span").filter(has=page.locator("select"))
    for index in range(spans.count()):
        span = spans.nth(index)
        text = " ".join(span.inner_text().split())
        if text.startswith("Page") and re.search(r"of\s+\d+", text):
            return span.locator("select").first
    raise ControlNotFound("pager", page)


def _page_select_text(page: Page) -> str | None:
    try:
        select = _page_select(page)
    except ControlNotFound:
        return None
    return " ".join(select.locator("xpath=parent::span").inner_text().split())


def _disabled(item: Locator) -> bool:
    aria = item.get_attribute("aria-disabled")
    if aria == "true":
        return True
    if item.get_attribute("disabled") is not None:
        return True
    classes = item.get_attribute("class") or ""
    return "disabled" in classes


def _wait_for_page_number(page: Page, number: int) -> None:
    deadline = datetime.now().timestamp() + 20
    while datetime.now().timestamp() < deadline:
        current = read_pager(page)
        if current is not None and current[0] == number:
            try:
                page.get_by_text("Req Number", exact=True).first.wait_for(state="visible", timeout=5000)
            except PlaywrightTimeout as exc:
                raise ControlNotFound("grid", page) from exc
            return
        page.wait_for_timeout(250)
    raise ControlNotFound(f"pager page {number}", page)
