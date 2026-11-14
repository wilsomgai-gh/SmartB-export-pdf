"""JSONL run log and the end-of-run summary."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


RESULTS = ("success", "skipped_date", "skipped_existing", "error")


@dataclass
class RunLog:
    jsonl_path: Path
    summary_path: Path
    counts: Counter = field(default_factory=Counter)
    exported: dict[str, str] = field(default_factory=dict)

    @classmethod
    def create(cls, logs_dir: Path) -> "RunLog":
        logs_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log = cls(
            jsonl_path=logs_dir / f"run-{stamp}.jsonl",
            summary_path=logs_dir / f"run-{stamp}.log",
        )
        log.load_prior(logs_dir)
        return log

    def load_prior(self, logs_dir: Path) -> None:
        for path in sorted(logs_dir.glob("run-*.jsonl")):
            if path == self.jsonl_path:
                continue
            for event in _read_jsonl(path):
                if event.get("result") != "success":
                    continue
                req_number = str(event.get("req_number") or "")
                pdf_path = str(event.get("pdf_path") or "")
                if req_number and pdf_path and Path(pdf_path).is_file():
                    self.exported[req_number] = pdf_path

    def already_exported(self, req_number: str) -> str | None:
        path = self.exported.get(req_number)
        if path and Path(path).is_file():
            return path
        return None

    def record(
        self,
        *,
        req_number: str,
        active_id: str = "",
        req_date: str = "",
        result: str,
        pdf_path: str = "",
        error: str = "",
    ) -> None:
        if result not in RESULTS:
            raise ValueError(f"Unknown result {result}")
        event = {
            "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
            "req_number": req_number,
            "active_id": active_id,
            "req_date": req_date,
            "result": result,
            "pdf_path": pdf_path,
            "error": error,
        }
        with self.jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.counts[result] += 1
        if result == "success" and req_number and pdf_path:
            self.exported[req_number] = pdf_path
        print(
            f"{result}: {req_number or '(no req number)'}"
            + (f" -> {pdf_path}" if pdf_path else "")
            + (f" ({error})" if error else ""),
            flush=True,
        )

    def write_summary(self) -> str:
        lines = [
            f"Success: {self.counts['success']}",
            f"Skipped (date): {self.counts['skipped_date']}",
            f"Skipped (already exported): {self.counts['skipped_existing']}",
            f"Errors: {self.counts['error']}",
            f"Event log: {self.jsonl_path}",
        ]
        text = "\n".join(lines) + "\n"
        self.summary_path.write_text(text, encoding="utf-8")
        return text


def _read_jsonl(path: Path) -> list[dict]:
    events: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            events.append(item)
    return events
