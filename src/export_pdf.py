"""Export filtered PD Request 3 rows as individual PDF files."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from src.browser import ensure_logged_in, launch_browser, load_credentials, login_if_prompted
from src.export_one import export_row
from src.grid import (
    apply_search,
    ensure_list,
    filter_intact,
    go_next,
    read_pager,
    read_rows,
)
from src.locators import PROJECT_ROOT, ControlNotFound, RowExportError
from src.runlog import RunLog

RESTART_EVERY = 40


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    start, end = _ask_range(args.start, args.end)
    username, password = load_credentials()
    pdf_dir = PROJECT_ROOT / "output" / "pdfs"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    log = RunLog.create(PROJECT_ROOT / "output" / "logs")
    print(
        f"Exporting PD Request 3 from {start.isoformat()} through {end.isoformat()}.",
        flush=True,
    )
    holder: dict = {"session": None}

    def relaunch():
        old = holder["session"]
        if old is not None:
            try:
                old.close()
            except Exception:
                try:
                    old.playwright.stop()
                except Exception:
                    pass
        fresh = launch_browser(
            PROJECT_ROOT / ".browser-profile",
            headless=args.headless,
        )
        holder["session"] = fresh
        ensure_logged_in(fresh.page, username, password)
        return fresh.page

    exit_code = 0
    try:
        page = relaunch()
        list_url = apply_search(page, start, end)
        if not filter_intact(page, list_url):
            print(
                "Date filter was submitted. The list URL does not show both dates; "
                "each Req Date is still checked before download.",
                flush=True,
            )
        _export_pages(
            page,
            list_url,
            start,
            end,
            pdf_dir,
            log,
            username,
            password,
            relaunch,
        )
    except ControlNotFound as exc:
        print(str(exc), file=sys.stderr, flush=True)
        exit_code = 1
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(str(exc), file=sys.stderr, flush=True)
            exit_code = int(exc.code) if isinstance(exc.code, int) else 1
    finally:
        summary = log.write_summary()
        print(summary, end="", flush=True)
        session = holder["session"]
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
    return exit_code


def _export_pages(
    page,
    list_url: str,
    start: date,
    end: date,
    pdf_dir: Path,
    log: RunLog,
    username: str,
    password: str,
    relaunch,
) -> None:
    handled: set[str] = set()
    target_page = 1
    announced = False
    downloads_since_launch = 0
    consecutive_crashes = 0

    def revive(reason: str):
        nonlocal page, list_url, downloads_since_launch, announced
        print(reason, flush=True)
        page = relaunch()
        list_url = apply_search(page, start, end)
        downloads_since_launch = 0
        announced = False

    while True:
        try:
            if downloads_since_launch >= RESTART_EVERY:
                revive(f"Restarting the browser after {RESTART_EVERY} downloads.")
            list_url = ensure_list(page, list_url, target_page, start, end)
            pager = read_pager(page)
            if not announced:
                announced = True
                print(f"Pager: {pager}. Filter URL: {page.url}", flush=True)
            if pager is not None and pager[0] != target_page:
                raise ControlNotFound(f"pager page {target_page}", page)
            rows = read_rows(page)
            if not rows and target_page == 1 and not handled:
                print("The search returned no rows.", flush=True)
                return
            pending = [row for row in rows if row.req_number not in handled]
            label = f"{pager[0]}/{pager[1]}" if pager else str(target_page)
            if not pending:
                print(f"Page {label} has no remaining rows.", flush=True)
                if not go_next(page):
                    return
                moved = read_pager(page)
                target_page = moved[0] if moved else target_page + 1
                continue
            row = pending[0]
            req_date = row.req_date.isoformat() if row.req_date else ""
            if row.req_date is None:
                log.record(
                    req_number=row.req_number,
                    req_date=row.req_date_raw,
                    result="error",
                    error=f"Could not parse Req Date {row.req_date_raw!r}.",
                )
                handled.add(row.req_number)
                continue
            if row.req_date < start or row.req_date > end:
                log.record(
                    req_number=row.req_number,
                    req_date=req_date,
                    result="skipped_date",
                    error="Req Date is outside the requested range.",
                )
                handled.add(row.req_number)
                continue
            existing = log.already_exported(row.req_number)
            if existing:
                log.record(
                    req_number=row.req_number,
                    req_date=req_date,
                    result="skipped_existing",
                    pdf_path=existing,
                )
                handled.add(row.req_number)
                continue
            print(f"Page {label}: {row.req_number} ({req_date})", flush=True)
            try:
                login_if_prompted(page, username, password)
                active_id, path = export_row(page, row.req_number, pdf_dir)
            except RowExportError as exc:
                saved = exc.pdf_path if exc.pdf_path and exc.pdf_path.exists() else None
                if saved is not None:
                    log.record(
                        req_number=row.req_number,
                        active_id=exc.active_id,
                        req_date=req_date,
                        result="success",
                        pdf_path=str(saved),
                        error=str(exc),
                    )
                    downloads_since_launch += 1
                else:
                    log.record(
                        req_number=row.req_number,
                        active_id=exc.active_id,
                        req_date=req_date,
                        result="error",
                        error=str(exc),
                    )
                handled.add(row.req_number)
                list_url = ensure_list(page, list_url, target_page, start, end)
                consecutive_crashes = 0
                continue
            log.record(
                req_number=row.req_number,
                active_id=active_id,
                req_date=req_date,
                result="success",
                pdf_path=str(path),
            )
            handled.add(row.req_number)
            downloads_since_launch += 1
            consecutive_crashes = 0
            pager_now = read_pager(page)
            print(
                f"Returned to list page {pager_now}; "
                f"filter kept: {filter_intact(page, list_url)}",
                flush=True,
            )
            if not _still_on_target(page, list_url, target_page):
                list_url = ensure_list(page, list_url, target_page, start, end)
        except PlaywrightError as exc:
            if not _browser_dead(exc):
                raise
            consecutive_crashes += 1
            if consecutive_crashes > 3:
                raise
            revive(f"Browser stopped ({exc}). Relaunching and continuing.")


def _browser_dead(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        token in text
        for token in ("target crashed", "has been closed", "browser closed", "target closed")
    )


def _still_on_target(page, list_url: str, target_page: int) -> bool:
    pager = read_pager(page)
    return pager is not None and pager[0] == target_page and filter_intact(page, list_url)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export SmartB PD Request 3 rows in a date range to PDF files."
    )
    parser.add_argument("--start", help="Start date YYYY-MM-DD. Prompted when omitted.")
    parser.add_argument("--end", help="End date YYYY-MM-DD. Prompted when omitted.")
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Launch Chromium without a window. The default run is headed.",
    )
    return parser.parse_args(argv)


def _ask_range(start_arg: str | None, end_arg: str | None) -> tuple[date, date]:
    if start_arg or end_arg:
        if not start_arg or not end_arg:
            raise SystemExit("Pass both --start and --end, or neither to be prompted.")
        start = _parse_iso(start_arg)
        end = _parse_iso(end_arg)
        if start > end:
            raise SystemExit("Start date must be on or before end date.")
        return start, end
    while True:
        start = _prompt_date("Start date")
        end = _prompt_date("End date")
        if start <= end:
            return start, end
        print("Start date must be on or before end date.", flush=True)


def _prompt_date(label: str) -> date:
    while True:
        raw = input(f"{label} (YYYY-MM-DD): ").strip()
        try:
            return _parse_iso(raw)
        except SystemExit:
            print("Enter a date as YYYY-MM-DD.", flush=True)


def _parse_iso(value: str) -> date:
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"Invalid date {value!r}. Use YYYY-MM-DD.") from exc


if __name__ == "__main__":
    sys.exit(main())
