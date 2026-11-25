"""SQLite connection, schema and queries."""

from __future__ import annotations

import csv
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.models import to_centavos

SCHEMA = """
CREATE TABLE IF NOT EXISTS purchase_orders (
  po_number    TEXT PRIMARY KEY,
  vendor_name  TEXT NOT NULL,
  vendor_tin   TEXT,
  po_date      TEXT NOT NULL,
  status       TEXT NOT NULL CHECK (status IN ('open','closed'))
);
CREATE TABLE IF NOT EXISTS po_lines (
  po_number              TEXT NOT NULL REFERENCES purchase_orders(po_number),
  line_no                INTEGER NOT NULL,
  sku                    TEXT NOT NULL,
  description            TEXT NOT NULL,
  qty                    INTEGER NOT NULL CHECK (qty > 0),
  unit_price_centavos    INTEGER NOT NULL CHECK (unit_price_centavos >= 0),
  PRIMARY KEY (po_number, line_no)
);
CREATE TABLE IF NOT EXISTS processed_invoices (
  fingerprint      TEXT PRIMARY KEY,
  file_name        TEXT NOT NULL,
  current_path     TEXT NOT NULL,
  invoice_number   TEXT,
  vendor_name      TEXT,
  po_number        TEXT,
  total_centavos   INTEGER,
  decision         TEXT CHECK (decision IN
                     ('approved','flagged','needs_approval','rejected','error')),
  reason           TEXT,
  findings_json    TEXT,
  status           TEXT NOT NULL CHECK (status IN ('processing','done','awaiting_human')),
  steps            INTEGER NOT NULL DEFAULT 0,
  cost_usd         REAL NOT NULL DEFAULT 0,
  model            TEXT,
  decided_by       TEXT CHECK (decided_by IN ('agent','human','guardrail')),
  created_at       TEXT NOT NULL,
  decided_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_vendor_invoice
  ON processed_invoices(vendor_name, invoice_number)
  WHERE invoice_number IS NOT NULL AND decision != 'error';
CREATE TABLE IF NOT EXISTS runs (
  run_id        TEXT PRIMARY KEY,
  trigger       TEXT NOT NULL,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  invoices      INTEGER NOT NULL DEFAULT 0,
  cost_usd      REAL NOT NULL DEFAULT 0
);
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def seed(conn: sqlite3.Connection, data_dir: Path) -> None:
    """Idempotent: replaces PO tables from the CSVs."""
    with conn:
        conn.execute("DELETE FROM po_lines")
        conn.execute("DELETE FROM purchase_orders")
        with (data_dir / "purchase_orders.csv").open(newline="") as f:
            for r in csv.DictReader(f):
                conn.execute(
                    "INSERT INTO purchase_orders VALUES (?,?,?,?,?)",
                    (
                        r["po_number"],
                        r["vendor_name"],
                        r["vendor_tin"],
                        r["po_date"],
                        r["status"],
                    ),
                )
        with (data_dir / "po_lines.csv").open(newline="") as f:
            for r in csv.DictReader(f):
                conn.execute(
                    "INSERT INTO po_lines VALUES (?,?,?,?,?,?)",
                    (
                        r["po_number"],
                        int(r["line_no"]),
                        r["sku"],
                        r["description"],
                        int(r["qty"]),
                        to_centavos(r["unit_price_php"]),
                    ),
                )


def get_po(conn: sqlite3.Connection, po_number: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM purchase_orders WHERE upper(po_number)=upper(?)",
        (po_number.strip(),),
    ).fetchone()
    if not row:
        return None
    lines = conn.execute(
        "SELECT * FROM po_lines WHERE po_number=? ORDER BY line_no", (row["po_number"],)
    ).fetchall()
    return {**dict(row), "lines": [dict(x) for x in lines]}


def list_pos(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        get_po(conn, r["po_number"])
        for r in conn.execute(  # type: ignore[misc]
            "SELECT po_number FROM purchase_orders ORDER BY po_number"
        )
    ]


def open_pos_for_vendor(conn: sqlite3.Connection, vendor_hint: str, limit: int = 3) -> list[dict[str, Any]]:
    from agent.matching import normalize_vendor

    want = normalize_vendor(vendor_hint)
    out = []
    for r in conn.execute("SELECT po_number, vendor_name FROM purchase_orders WHERE status='open'"):
        if want and (
            want in normalize_vendor(r["vendor_name"]) or normalize_vendor(r["vendor_name"]) in want
        ):
            po = get_po(conn, r["po_number"])
            if po:
                out.append(po)
    return out[:limit]


def find_existing(conn: sqlite3.Connection, vendor_name: str, invoice_number: str) -> dict[str, Any] | None:
    from agent.matching import normalize_vendor

    want = normalize_vendor(vendor_name)
    for r in conn.execute(
        "SELECT * FROM processed_invoices WHERE invoice_number=? AND decision IS NOT NULL AND decision!='error'"
        " AND status!='processing'",
        (invoice_number.strip(),),
    ):
        if normalize_vendor(r["vendor_name"] or "") == want:
            return dict(r)
    return None
