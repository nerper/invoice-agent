"""Append-only JSONL action log."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.db import now_iso


class ActionLog:
    def __init__(self, path: Path, run_id: str = "-") -> None:
        self.path = path
        self.run_id = run_id
        self._lock = threading.Lock()
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        self.current: dict[str, Any] = {}

    def log(
        self,
        event: str,
        invoice: str | None = None,
        fp: str | None = None,
        step: int | None = None,
        **data: Any,
    ) -> dict[str, Any]:
        rec = {
            "ts": now_iso(),
            "run_id": self.run_id,
            "invoice": invoice,
            "fp": fp[:8] if fp else None,
            "step": step,
            "event": event,
            "data": data,
        }
        line = json.dumps(rec, ensure_ascii=False, default=str)
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            if event == "invoice_started":
                self.current = {"file": invoice, "step": 0}
            elif event in ("decision", "invoice_skipped", "run_finished", "error"):
                self.current = {}
            elif step is not None and self.current:
                self.current["step"] = step
        for fn in list(self.listeners):
            fn(rec)
        return rec

    def read(
        self,
        run_id: str | None = None,
        invoice: str | None = None,
        event: str | None = None,
        fp: str | None = None,
    ) -> list[dict[str, Any]]:
        """All matching records, oldest first."""
        if not self.path.exists():
            return []
        out = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if run_id and r["run_id"] != run_id:
                    continue
                if invoice and r.get("invoice") != invoice:
                    continue
                if fp and r.get("fp") != fp[:8]:
                    continue
                if event and r["event"] != event:
                    continue
                out.append(r)
        return out
