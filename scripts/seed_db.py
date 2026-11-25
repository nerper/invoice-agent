"""Create schema and load PO CSVs into workspace/invoices.db (idempotent)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import db
from agent.config import ROOT, load_settings

if __name__ == "__main__":
    s = load_settings()
    conn = db.connect(s.workspace / "invoices.db")
    db.seed(conn, ROOT / "data")
    n = conn.execute("SELECT count(*) FROM purchase_orders").fetchone()[0]
    print(f"Seeded {n} purchase orders into {s.workspace / 'invoices.db'}")
